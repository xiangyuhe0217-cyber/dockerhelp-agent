"""把已解决的工单批量沉淀成知识库案例。

用法：
  python scripts/ingest_cases.py --ticket synthetic#0001        # 单条
  python scripts/ingest_cases.py --closed-limit 50              # 批量（冷启动）
  python scripts/ingest_cases.py --list                         # 看哪些能摄
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.db import dao  # noqa: E402
from app.rag.cases import ingest_closed_tickets, ingest_ticket_case  # noqa: E402
from app.utils.console import setup_console  # noqa: E402


def main() -> int:
    setup_console()
    p = argparse.ArgumentParser()
    p.add_argument("--ticket", default="")
    p.add_argument("--closed-limit", type=int, default=0)
    p.add_argument("--list", action="store_true")
    p.add_argument("--force", action="store_true", help="没有 resolution 也强制摄取")
    args = p.parse_args()

    conn = dao.connect(settings.db_path)

    if args.list:
        rows = dao.fetch_all(
            conn,
            "SELECT t.id, t.title, t.state, "
            "  (SELECT COUNT(*) FROM case_index c WHERE c.ticket_id = t.id) AS indexed "
            "FROM tickets t WHERE t.resolution != '' ORDER BY t.created_at DESC LIMIT 30",
        )
        print(f"{'工单号':<22}{'状态':<12}{'已入库':<8}标题")
        for r in rows:
            print(f"{r['id']:<22}{r['state']:<12}{'是' if r['indexed'] else '否':<8}{r['title'][:44]}")
        return 0

    if args.ticket:
        res = ingest_ticket_case(args.ticket, force=args.force)
        print(res)
        return 0 if res.get("ok") else 1

    if args.closed_limit:
        results = ingest_closed_tickets(args.closed_limit)
        ok = [r for r in results if r.get("ok")]
        print(f"摄取 {len(ok)}/{len(results)} 条")
        for r in results[:10]:
            print("  ", r)
        print(f"当前知识库：{dao.count(conn, 'documents')} 篇 / {dao.count(conn, 'chunks')} chunk")
        return 0

    p.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
