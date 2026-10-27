"""多轮对话验证：checkpoint 记忆 + 长期记忆。

用 Python 直接调，避免 Windows 管道输入的中文编码问题。

跑法：python scripts/demo_multiturn.py --user u_demo --thread t_demo
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agent.graph import Agent  # noqa: E402
from app.agent.memory import get_memory  # noqa: E402
from app.agent.trace import TraceRecorder  # noqa: E402
from app.config import settings  # noqa: E402
from app.utils.console import setup_console  # noqa: E402

TURNS = [
    "我是 standard 客户，我们团队用的是 Docker 4.34。",
    "我的 P1 工单 SLA 要求多久响应？",
    "刚才我说我们用的是哪个版本？",
]


def main() -> int:
    setup_console()
    p = argparse.ArgumentParser()
    p.add_argument("--user", default="u_demo")
    p.add_argument("--thread", default="t_demo")
    p.add_argument("--clear", action="store_true", help="先清空该用户记忆和会话")
    args = p.parse_args()

    store = get_memory(args.user)
    if args.clear:
        print(f"清空记忆 {store.clear()} 条")
        ckpt = Path(settings.data_dir) / "checkpoints.sqlite"
        if ckpt.exists():
            import sqlite3

            conn = sqlite3.connect(str(ckpt))
            tid = f"{args.user}:{args.thread}"
            n1 = conn.execute("DELETE FROM checkpoints WHERE thread_id = ?", (tid,)).rowcount
            n2 = conn.execute("DELETE FROM writes WHERE thread_id = ?", (tid,)).rowcount
            conn.commit()
            conn.close()
            print(f"清空会话快照 {n1} 条 / {n2} 条写记录")

    agent = Agent(confirm=lambda n, a: True)

    for i, q in enumerate(TURNS, 1):
        print(f"\n{'=' * 74}")
        print(f"[第 {i} 轮] 用户：{q}")
        res = agent.run(q, session_id=args.thread, user_id=args.user)
        print(f"路由={res.route} 主题={len(res.steps)} 步  tokens={res.tokens}")
        for s in res.steps:
            tag = s.type
            if s.tool:
                tag += f"({s.tool})"
            print(f"   - {tag}")
        print(f"助手：{res.answer.strip()[:400]}")

    print(f"\n{'=' * 74}")
    print("长期记忆内容：")
    print(store.block() or "（空）")
    snaps = agent.history(args.thread, args.user)
    print(f"\n会话 checkpoint 快照数：{len(snaps)}")
    print(f"回放：python -c \"import sys;sys.path.insert(0,'.');from app.agent.trace import TraceRecorder;print(TraceRecorder.replay('run-xxx'))\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
