"""把 SQLite `memories` 表里的历史记忆一次性迁到 Redis。

幂等：重复跑不会重复写（facts 是 HSET 覆盖，events 会按文本去重后只补缺的）。

用法：
  python scripts/migrate_memory_to_redis.py            # 迁移
  python scripts/migrate_memory_to_redis.py --dry-run  # 只看会迁什么
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agent.memory import RedisMemoryStore  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import dao  # noqa: E402
from app.utils.console import setup_console  # noqa: E402


def main() -> int:
    setup_console()
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    conn = dao.connect(settings.db_path)
    if not dao.table_exists(conn, "memories"):
        print("没有 memories 表，无需迁移")
        return 0

    # 按用户 + 时间正序，保证事件顺序与原来一致
    rows = dao.fetch_all(
        conn, "SELECT user_id, kind, key, value, created_at FROM memories ORDER BY created_at ASC"
    )
    print(f"[migrate] SQLite 里共 {len(rows)} 条记忆 → Redis {settings.redis_host}:{settings.redis_port} db={settings.redis_db}")

    grouped: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        grouped[r["user_id"]].append(r)

    migrated = 0
    for user_id, items in grouped.items():
        facts = [i for i in items if i["kind"] == "fact"]
        events = [i for i in items if i["kind"] == "event"]
        print(f"  {user_id}: {len(facts)} 条事实 / {len(events)} 条事件")
        if args.dry_run:
            continue

        store = RedisMemoryStore(user_id)
        existing_events = set(store.recent_events(limit=1000) and [e["value"] for e in store.client.lrange(store.k_events, 0, -1)] or [])

        for f in facts:
            store.add_fact(f["key"], f["value"])
            migrated += 1
        # 事件要按原时间顺序写（Redis 是 LPUSH 最新在前），所以倒序写
        for e in reversed(events):
            if e["value"] in existing_events:
                continue
            store.add_event(e["value"])
            migrated += 1

    if args.dry_run:
        print("\n[dry-run] 未写入")
        return 0

    print(f"\n[migrate] 完成，写入 {migrated} 条")
    for user_id in grouped:
        store = RedisMemoryStore(user_id)
        print(f"  校验 {user_id}: facts={len(store.facts())} events={len(store.recent_events(3))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
