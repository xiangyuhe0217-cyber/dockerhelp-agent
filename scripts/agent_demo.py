"""Agent 命令行演示：单条提问 / 批量剧本 / 回放历史。

用法：
  python scripts/agent_demo.py "Docker 装完起不来怎么办"          # 单条
  python scripts/agent_demo.py "帮我建个 P1 工单" --yes           # 自动确认写操作
  python scripts/agent_demo.py --scenarios data/scenarios.jsonl    # 跑全部剧本
  python scripts/agent_demo.py --list                              # 列出剧本
  python scripts/agent_demo.py --replay run-xxxxx                  # 回放某次执行
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agent.loop import Agent  # noqa: E402
from app.agent.trace import TraceRecorder  # noqa: E402
from app.config import settings  # noqa: E402
from app.utils.console import setup_console  # noqa: E402

SCENARIOS = settings.data_dir / "scenarios.jsonl"


def ask_confirm(name: str, args: dict) -> bool:
    print(f"\n  ⚠ 写操作待确认：{name}")
    print(f"    参数：{json.dumps(args, ensure_ascii=False)}")
    try:
        ans = input("    执行吗？[y/N] ").strip().lower()
    except EOFError:
        return False
    return ans in {"y", "yes"}


def print_run(result, show_steps: bool = True) -> None:
    print(f"\n{'=' * 78}")
    print(f"路由: {result.route}   状态: {result.status}   run_id: {result.run_id}")
    print(f"耗时: {result.elapsed_ms} ms   tokens: {result.tokens}   步数: {len(result.steps)}")
    print("=" * 78)

    if show_steps:
        print("\n--- 执行过程 ---")
        for s in result.steps:
            print(s.to_line())

    print("\n--- 最终回答 ---")
    print(result.answer.strip())
    print("\n" + "-" * 78)
    print(f"回放本次执行：python scripts/agent_demo.py --replay {result.run_id}")


def load_scenarios() -> list[dict]:
    if not SCENARIOS.exists():
        return []
    rows = []
    with SCENARIOS.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def main() -> int:
    setup_console()
    p = argparse.ArgumentParser(description="服务台 Agent 演示")
    p.add_argument("question", nargs="?", default="", help="单条提问")
    p.add_argument("--scenarios", default="", help="批量剧本文件")
    p.add_argument("--only", default="", help="只跑指定 id 的剧本，逗号分隔")
    p.add_argument("--yes", action="store_true", help="写操作自动确认")
    p.add_argument("--no-trace", action="store_true", help="不写 runs/steps 表")
    p.add_argument("--no-steps", action="store_true", help="不打印执行过程")
    p.add_argument("--force-agent", action="store_true", help="跳过意图路由，强制走 ReAct")
    p.add_argument("--list", action="store_true", help="列出剧本")
    p.add_argument("--replay", default="", help="回放 run_id")
    # ---- W6 记忆相关 ----
    p.add_argument("--user", default="local", help="用户 id（记忆按它隔离）")
    p.add_argument("--thread", default="cli", help="会话 id（checkpoint 按它隔离）")
    p.add_argument("--multi", action="store_true", help="多轮对话模式（同一 thread 连续提问）")
    p.add_argument("--remember", default="", help="手动写入一条事实，格式 key=value")
    p.add_argument("--memory", action="store_true", help="打印当前用户的长期记忆")
    p.add_argument("--clear-memory", action="store_true", help="清空当前用户的长期记忆")
    p.add_argument("--history", action="store_true", help="打印该会话的 checkpoint 快照数")
    args = p.parse_args()

    # ---------- 记忆管理（不需要建 Agent） ----------
    if args.memory or args.clear_memory or args.remember:
        from app.agent.memory import get_memory  # noqa: PLC0415

        store = get_memory(args.user)
        if args.remember:
            if "=" not in args.remember:
                raise SystemExit("--remember 格式应为 key=value")
            k, v = args.remember.split("=", 1)
            store.add_fact(k.strip(), v.strip())
            print(f"已记住：{k.strip()} = {v.strip()}")
        if args.clear_memory:
            n = store.clear()
            print(f"已清空用户 {args.user} 的 {n} 条记忆")
        if args.memory:
            block = store.block()
            print(block or f"用户 {args.user} 目前没有记忆")
        if not args.question and not args.scenarios:
            return 0

    if args.replay:
        print(TraceRecorder.replay(args.replay))
        return 0

    if args.list:
        rows = load_scenarios()
        print(f"共 {len(rows)} 个剧本（{SCENARIOS}）：\n")
        for r in rows:
            print(f"  {r['id']:<4} [{r.get('tag',''):<10}] {r['question']}")
            if r.get("expect"):
                print(f"       预期: {r['expect']}")
        return 0

    agent = Agent(
        # 不给 confirm 回调 → 走 LangGraph interrupt()，可以真正暂停再恢复
        confirm=(lambda n, a: True) if args.yes else None,
        persist_trace=not args.no_trace,
    )

    def run_once(question: str, thread: str | None = None):
        """跑一次；若停在人工确认，就地问用户，然后 resume。

        thread 显式传入时用它（批量剧本要保证每个场景独立会话，
        否则 checkpoint 会让场景之间串历史）。
        """
        tid = thread or args.thread
        res = agent.run(
            question,
            session_id=tid,
            user_id=args.user,
            force_agent=args.force_agent,
        )
        while res.status == "need_confirm":
            info = res.interrupt or {}
            print(f"\n  ⚠ 写操作待确认：{info.get('tool')}")
            print(f"    参数：{json.dumps(info.get('args'), ensure_ascii=False)}")
            try:
                ans = input("    执行吗？[y/N] ").strip().lower()
            except EOFError:
                ans = "n"
            res = agent.run(
                question,
                session_id=tid,
                user_id=args.user,
                resume=(ans in {"y", "yes"}),
            )
        return res

    # ---------- 多轮对话 ----------
    if args.multi:
        print(f"多轮对话模式（user={args.user} thread={args.thread}，输入 exit 退出）")
        while True:
            try:
                q = input("\n你> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not q or q.lower() in {"exit", "quit"}:
                break
            res = run_once(q)
            print(f"\n助手> {res.answer.strip()}")
            print(f"      [route={res.route} steps={len(res.steps)} tokens={res.tokens} "
                  f"thread={res.thread_id}]")
        return 0

    # ---------- 会话历史 ─----------
    if args.history:
        snaps = agent.history(args.thread, args.user)
        print(f"线程 {args.user}:{args.thread} 共 {len(snaps)} 个 checkpoint 快照")
        for s in snaps[:10]:
            print(f"  step={s['step']:<4} next={s['next']}")
        if not args.question and not args.scenarios:
            return 0

    # ---------- 批量剧本 ----------
    if args.scenarios:
        rows = load_scenarios()
        if not rows:
            raise SystemExit(f"没有找到剧本文件 {SCENARIOS}")
        if args.only:
            want = {x.strip() for x in args.only.split(",")}
            rows = [r for r in rows if r["id"] in want]

        results = []
        for r in rows:
            print(f"\n\n{'#' * 78}")
            print(f"# {r['id']} [{r.get('tag','')}] {r['question']}")
            if r.get("expect"):
                print(f"# 预期：{r['expect']}")
            print("#" * 78)
            t0 = time.time()
            # 每个剧本独立 thread：否则 checkpoint 会让场景互相串历史
            res = run_once(r["question"], thread=f"{args.thread}-{r['id']}")
            print_run(res, show_steps=not args.no_steps)
            results.append(
                {
                    "id": r["id"],
                    "question": r["question"],
                    "route": res.route,
                    "status": res.status,
                    "steps": len(res.steps),
                    "tokens": res.tokens,
                    "elapsed_ms": res.elapsed_ms,
                    "run_id": res.run_id,
                    "answer": res.answer,
                }
            )

        out = settings.reports_dir / "agent_runs.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n\n{'=' * 78}")
        print(f"剧本结果汇总 → {out}")
        print(f"{'id':<4} {'route':<6} {'steps':>5} {'tokens':>7} {'ms':>7}  status")
        for r in results:
            print(
                f"{r['id']:<4} {r['route']:<6} {r['steps']:>5} {r['tokens']:>7} "
                f"{r['elapsed_ms']:>7}  {r['status']}"
            )
        return 0

    # ---------- 单条提问 ----------
    if not args.question:
        p.print_help()
        return 1

    res = run_once(args.question)
    print_run(res, show_steps=not args.no_steps)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
