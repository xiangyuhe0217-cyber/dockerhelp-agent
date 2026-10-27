"""FastAPI 服务：给 Vue 前端提供聊天 / Trace / 记忆接口。

两套接口并存：
  - **SSE 流式**：`GET /api/chat/stream` → 逐 token 推答案、逐步推 trace（前端默认用这个）
  - **轮询**：`POST /api/chat` + `GET /api/chat/{run_id}`（自动化测试 / 无 EventSource 的客户端）

为什么 SSE 能这么简单：图节点在写 step 时顺手 emit 一次，token 在 LLM 流式回调里 emit，
中间不落任何队列状态，断开重连最多丢一次渲染（数据早已在库里）。

启动：
  python scripts/serve.py            # 生产：托管 web/dist
  python scripts/serve.py --reload   # 开发：改后端自动重载
"""

from __future__ import annotations

import json
import queue
import threading
import time
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.agent.graph import Agent
from app.agent.memory import get_memory
from app.agent.stream import set_sink
from app.config import BASE_DIR, settings
from app.db import dao

app = FastAPI(title="Docker 服务台 Agent", version="0.7.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 单进程演示：一次只跑一个 Agent，避免 checkpointer 的 sqlite 连接并发写
_LOCK = threading.Lock()
_AGENT: Agent | None = None
_RESULTS: dict[str, dict] = {}  # run_id -> {status, interrupt, answer}


def agent() -> Agent:
    global _AGENT
    if _AGENT is None:
        _AGENT = Agent(confirm=None)  # None → 走 interrupt，由前端弹确认
    return _AGENT


class ChatIn(BaseModel):
    question: str
    user: str = "web"
    thread: str = "web"
    force_agent: bool = False


class ResumeIn(BaseModel):
    user: str = "web"
    thread: str = "web"
    decision: bool = True
    question: str = ""


def _run_in_thread(run_id: str, question: str, user: str, thread: str,
                   force_agent: bool, resume: bool | None) -> None:
    try:
        with _LOCK:
            res = agent().run(
                question,
                session_id=thread,
                user_id=user,
                force_agent=force_agent,
                resume=resume,
                run_id=run_id,
            )
        _RESULTS[run_id] = {
            "status": res.status,
            "answer": res.answer,
            "route": res.route,
            "tokens": res.tokens,
            "interrupt": res.interrupt,
            "elapsed_ms": res.elapsed_ms,
        }
    except Exception as e:  # noqa: BLE001
        _RESULTS[run_id] = {"status": "failed", "answer": f"{type(e).__name__}: {e}"}
        conn = dao.connect(settings.db_path)
        conn.execute("UPDATE runs SET status='failed' WHERE id=?", (run_id,))
        conn.commit()


# --------------------------------------------------------------------------- #
@app.get("/api/health")
def health() -> dict:
    conn = dao.connect(settings.db_path)
    return {
        "ok": True,
        "chunks": dao.count(conn, "chunks"),
        "documents": dao.count(conn, "documents"),
        "tickets": dao.count(conn, "tickets"),
        "memory_backend": settings.memory_backend,
        "redis": f"{settings.redis_host}:{settings.redis_port}/db{settings.redis_db}",
    }


@app.post("/api/chat")
def chat(body: ChatIn) -> dict:
    run_id = f"run-{uuid.uuid4().hex[:12]}"
    _RESULTS[run_id] = {"status": "running"}
    t = threading.Thread(
        target=_run_in_thread,
        args=(run_id, body.question, body.user, body.thread, body.force_agent, None),
        daemon=True,
    )
    t.start()
    return {"run_id": run_id, "thread": f"{body.user}:{body.thread}"}


# --------------------------------------------------------------------------- #
# SSE：逐 token 推答案 + 逐步骤推 trace
# --------------------------------------------------------------------------- #
def _sse(kind: str, payload) -> str:
    return f"event: {kind}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _stream_run(question: str, user: str, thread: str, force_agent: bool, resume):
    """把一次 run 的事件流式吐出来。

    约定：图节点 emit('step', …)、LLM 流式回调 emit('token', …)、结束时 emit('done', …)。
    """
    q: queue.Queue = queue.Queue()

    def sink(kind: str, payload) -> None:
        q.put((kind, payload))

    def worker() -> None:
        set_sink(sink)
        try:
            with _LOCK:
                res = agent().run(
                    question,
                    session_id=thread,
                    user_id=user,
                    force_agent=force_agent,
                    resume=resume,
                )
            q.put(
                (
                    "done",
                    {
                        "run_id": res.run_id,
                        "status": res.status,
                        "route": res.route,
                        "answer": res.answer,
                        "tokens": res.tokens,
                        "interrupt": res.interrupt,
                        "thread": res.thread_id,
                    },
                )
            )
        except Exception as e:  # noqa: BLE001
            q.put(("failed", {"message": f"{type(e).__name__}: {e}"}))
        finally:
            set_sink(None)
            q.put(("__end__", None))

    def gen():
        threading.Thread(target=worker, daemon=True).start()
        while True:
            kind, payload = q.get()
            if kind == "__end__":
                break
            yield _sse(kind, payload)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@app.get("/api/chat/stream")
def chat_stream(
    question: str, user: str = "web", thread: str = "web", force_agent: bool = False
):
    return _stream_run(question, user, thread, force_agent, None)


@app.get("/api/resume/stream")
def resume_stream(
    user: str = "web", thread: str = "web", decision: bool = True, question: str = ""
):
    return _stream_run(question or "继续", user, thread, False, decision)


@app.post("/api/resume")
def resume(body: ResumeIn) -> dict:
    run_id = f"run-{uuid.uuid4().hex[:12]}"
    _RESULTS[run_id] = {"status": "running"}
    t = threading.Thread(
        target=_run_in_thread,
        args=(run_id, body.question or "继续", body.user, body.thread, False, body.decision),
        daemon=True,
    )
    t.start()
    return {"run_id": run_id}


@app.get("/api/chat/{run_id}")
def chat_status(run_id: str) -> dict:
    """轮询用：把 DB 里的 runs/steps 直接映射成前端要的结构。"""
    conn = dao.connect(settings.db_path)
    run = dao.fetch_one(conn, "SELECT * FROM runs WHERE id = ?", (run_id,))
    rows = dao.fetch_all(
        conn, "SELECT * FROM steps WHERE run_id = ? ORDER BY step_no", (run_id,)
    )
    steps = [
        {
            "step_no": r["step_no"],
            "type": r["type"],
            "thought": r["thought"] or "",
            "tool": r["tool"] or "",
            "args": json.loads(r["args"] or "{}"),
            "observation": r["observation"] or "",
            "latency_ms": r["latency_ms"] or 0,
            "tokens": r["tokens"] or 0,
        }
        for r in rows
    ]
    extra = _RESULTS.get(run_id, {})
    status = (run or {}).get("status") or extra.get("status") or "unknown"
    return {
        "run_id": run_id,
        "status": status,
        "running": status == "running",
        "answer": extra.get("answer") or (run or {}).get("answer") or "",
        "route": extra.get("route", ""),
        "tokens": extra.get("tokens", (run or {}).get("total_tokens", 0)),
        "interrupt": extra.get("interrupt"),
        "steps": steps,
    }


@app.get("/api/memory/{user}")
def memory(user: str) -> dict:
    store = get_memory(user)
    return {
        "user": user,
        "backend": store.backend,
        "block": store.block(),
        "facts": store.facts(),
        "events": store.recent_events(10),
    }


@app.delete("/api/memory/{user}")
def memory_clear(user: str) -> dict:
    n = get_memory(user).clear()
    return {"cleared": n}


@app.get("/api/runs")
def runs(user: str = "", limit: int = 20) -> dict:
    conn = dao.connect(settings.db_path)
    sql = "SELECT id, session_id, user_id, question, status, total_steps, total_tokens, started_at FROM runs"
    params: list = []
    if user:
        sql += " WHERE user_id = ?"
        params.append(user)
    sql += " ORDER BY started_at DESC LIMIT ?"
    params.append(limit)
    return {"runs": dao.fetch_all(conn, sql, params)}


@app.get("/api/trace/{run_id}")
def trace(run_id: str) -> dict:
    from app.agent.trace import TraceRecorder  # noqa: PLC0415

    return {"replay": TraceRecorder.replay(run_id)}


# --------------------------------------------------------------------------- #
# 静态资源（Vue 构建产物）
# --------------------------------------------------------------------------- #
DIST = BASE_DIR / "web" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(DIST / "index.html")
