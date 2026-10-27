"""Trace 记录：把每一次 Agent 运行落成 runs + steps 两行表。

为什么要落库而不是只打日志：
  - 能按 run_id 回放（`--replay`），定位"第几步走偏了"
  - 能统计（平均步数、工具成功率、P95 时延），这就是 JD 里的 observability
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.config import settings
from app.db import dao


@dataclass
class StepRecord:
    step_no: int
    type: str
    thought: str = ""
    tool: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    observation: str = ""
    latency_ms: int = 0
    tokens: int = 0

    def to_line(self, width: int = 150) -> str:
        head = f"[{self.step_no}] {self.type}"
        if self.tool:
            head += f" → {self.tool}({json.dumps(self.args, ensure_ascii=False)[:90]})"
        if self.latency_ms:
            head += f"  {self.latency_ms}ms"
        parts = [head]
        if self.thought:
            parts.append(f"    思考: {self.thought[:width]}")
        if self.observation:
            obs = self.observation.replace("\n", " | ")
            parts.append(f"    观察: {obs[:width]}")
        return "\n".join(parts)


def safe_text(value: object, limit: int | None = None) -> str:
    """清洗成可以安全写进 SQLite 的文本。

    踩过的坑：从 Windows 控制台管道读入的中文可能是 UTF-16 代理对，
    直接写库会抛 `UnicodeEncodeError: surrogates not allowed`，把整个 run 干掉。
    任何外部输入（用户问题、模型输出、工具结果）入库前都过一遍。
    """
    if value is None:
        return ""
    text = str(value)
    try:
        text = text.encode("utf-8", "replace").decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        text = text.encode("ascii", "replace").decode("ascii", "replace")
    return text[:limit] if limit else text


class TraceRecorder:
    def __init__(
        self,
        question: str,
        session_id: str = "cli",
        user_id: str = "local",
        persist: bool = True,
        run_id: str | None = None,
    ):
        # 允许外部指定 run_id：Web 端要先返回 run_id 再轮询进度
        self.run_id = run_id or f"run-{uuid.uuid4().hex[:12]}"
        self.question = safe_text(question)
        self.steps: list[StepRecord] = []
        self.persist = persist
        self.t0 = time.time()
        self.conn = dao.connect(settings.db_path) if persist else None
        if self.conn is not None:
            dao.upsert_many(
                self.conn,
                "runs",
                [
                    {
                        "id": self.run_id,
                        "session_id": safe_text(session_id, 100),
                        "user_id": safe_text(user_id, 100),
                        "question": safe_text(question, 4000),
                        "answer": "",
                        "status": "running",
                        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "ended_at": None,
                        "total_steps": 0,
                        "total_tokens": 0,
                        "total_cost": 0.0,
                    }
                ],
            )

    def add(self, rec: StepRecord) -> StepRecord:
        self.steps.append(rec)
        if self.conn is not None:
            dao.upsert_many(
                self.conn,
                "steps",
                [
                    {
                        "id": f"{self.run_id}-{rec.step_no}",
                        "run_id": self.run_id,
                        "step_no": rec.step_no,
                        "type": rec.type,
                        "thought": safe_text(rec.thought, 4000),
                        "tool": safe_text(rec.tool, 100),
                        "args": safe_text(json.dumps(rec.args, ensure_ascii=False), 4000),
                        "observation": safe_text(rec.observation, 8000),
                        "latency_ms": rec.latency_ms,
                        "tokens": rec.tokens,
                        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    }
                ],
            )
        return rec

    def finish(self, answer: str, status: str = "done") -> None:
        if self.conn is None:
            return
        self.conn.execute(
            "UPDATE runs SET answer = ?, status = ?, ended_at = ?, total_steps = ?, total_tokens = ? "
            "WHERE id = ?",
            (
                safe_text(answer, 8000),
                status,
                time.strftime("%Y-%m-%d %H:%M:%S"),
                len(self.steps),
                sum(s.tokens for s in self.steps),
                self.run_id,
            ),
        )
        self.conn.commit()

    @property
    def elapsed_ms(self) -> int:
        return int((time.time() - self.t0) * 1000)

    @staticmethod
    def replay(run_id: str) -> str:
        """按 run_id 回放一次执行。"""
        conn = dao.connect(settings.db_path)
        run = dao.fetch_one(conn, "SELECT * FROM runs WHERE id = ?", (run_id,))
        if not run:
            return f"没有找到 run {run_id}"
        rows = dao.fetch_all(
            conn, "SELECT * FROM steps WHERE run_id = ? ORDER BY step_no", (run_id,)
        )
        out = [
            f"=== 回放 {run_id} ===",
            f"问题: {run['question']}",
            f"状态: {run['status']}  步数: {run['total_steps']}  耗时: {run['ended_at']}",
            "",
        ]
        for r in rows:
            out.append(
                StepRecord(
                    step_no=r["step_no"],
                    type=r["type"],
                    thought=r["thought"] or "",
                    tool=r["tool"] or "",
                    args=json.loads(r["args"] or "{}"),
                    observation=r["observation"] or "",
                    latency_ms=r["latency_ms"] or 0,
                    tokens=r["tokens"] or 0,
                ).to_line()
            )
        out += ["", f"最终回答: {run['answer']}"]
        return "\n".join(out)
