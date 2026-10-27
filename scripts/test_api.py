"""前端所用 API 的端到端验收（不需要浏览器）。

覆盖：
  1. 健康检查
  2. 知识问答（rag 路径）→ 轮询到 done，有 answer 和 steps
  3. 工具编排（agent 路径）→ 有 tool 步骤
  4. 写操作 → 停在 need_confirm → 拒绝 → 库里不新增工单
  5. 记忆接口（Redis）

用法：python scripts/test_api.py            # 默认 http://127.0.0.1:8000
      python scripts/test_api.py --base http://127.0.0.1:8000
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.utils.console import setup_console  # noqa: E402


def call(base: str, path: str, method: str = "GET", body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        base + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def wait(base: str, run_id: str, timeout: float = 180.0, on_step=None) -> dict:
    t0 = time.time()
    last = -1
    while time.time() - t0 < timeout:
        d = call(base, f"/api/chat/{run_id}")
        if on_step and len(d["steps"]) != last:
            last = len(d["steps"])
            on_step(d)
        if not d["running"]:
            return d
        time.sleep(0.4)
    raise TimeoutError(f"{run_id} 超时")


def main() -> int:
    setup_console()
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="http://127.0.0.1:8000")
    args = p.parse_args()
    base = args.base
    ok = 0
    fail = 0
    # 每次跑用全新 user/thread：checkpoint 是持久化的，复用 thread 会让模型看到上次的工具结果
    # （实测复用后 check_sla 不再被调用，测试会假失败）
    from uuid import uuid4

    TAG = uuid4().hex[:6]
    USER = f"api_{TAG}"

    def check(name: str, cond: bool, detail: str = "") -> None:
        nonlocal ok, fail
        if cond:
            ok += 1
            print(f"✅ {name}")
        else:
            fail += 1
            print(f"❌ {name}  {detail}")

    # 1. 健康检查
    h = call(base, "/api/health")
    check("健康检查", h.get("ok") is True, str(h))
    print(f"   chunks={h['chunks']} 记忆={h['memory_backend']} redis={h['redis']}")

    # 2. 知识问答
    r = call(base, "/api/chat", "POST", {"question": "Docker 装完一直卡在 Starting 怎么办", "user": USER, "thread": TAG+"1"})
    d = wait(base, r["run_id"])
    check("知识问答有回答", bool(d["answer"]) and d["status"] == "done", d["status"])
    check("知识问答走 rag", d["route"] == "rag", d["route"])
    check("步骤已落库", len(d["steps"]) >= 2, str(len(d["steps"])))
    print(f"   步数={len(d['steps'])} tokens={d['tokens']} 回答={d['answer'][:60]}…")

    # 3. 工具编排
    r = call(base, "/api/chat", "POST", {"question": "premium 客户的 P1 工单 SLA 多久响应", "user": USER, "thread": TAG+"2"})
    d = wait(base, r["run_id"])
    tools = [s["tool"] for s in d["steps"] if s["type"] == "tool"]
    check("工具编排调用 check_sla", "check_sla" in tools, str(tools))
    print(f"   工具调用: {tools}")

    # 4. 写操作 → 需要确认 → 拒绝
    conn = sqlite3.connect(settings.db_path)
    before = conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
    r = call(
        base,
        "/api/chat",
        "POST",
        {
            "question": "直接建单，不要再问我：标题「API 验收工单」，描述「验证确认链路」，优先级 P2，客户 cust-005。",
            "user": USER,
            "thread": TAG+"3",
        },
    )
    d = wait(base, r["run_id"])
    check("写操作停在确认", d["status"] == "need_confirm" and d["interrupt"], d["status"])
    if d.get("interrupt"):
        print(f"   待确认: {d['interrupt']['tool']}({json.dumps(d['interrupt']['args'], ensure_ascii=False)[:80]})")

    r2 = call(base, "/api/resume", "POST", {"user": USER, "thread": TAG+"3", "decision": False})
    d2 = wait(base, r2["run_id"])
    after = conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
    check("拒绝后未建单", after == before, f"{before} -> {after}")
    check("拒绝后有回答", bool(d2["answer"]), d2["answer"][:60])

    # 5. 记忆接口
    call(base, "/api/memory/USER", "DELETE")
    m = call(base, "/api/memory/USER")
    check("记忆接口可读", m.get("user") == "USER", str(m)[:80])
    print(f"   记忆后端={m['backend']} facts={len(m['facts'])} events={len(m['events'])}")

    print(f"\n{ok} 通过 / {fail} 失败")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
