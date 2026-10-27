"""把 data/eval_cases.jsonl 校验后写入 eval_cases 表。

校验很重要：**如果某条 gold_paths 在语料里一篇都匹配不到，这条题永远不可能被答对**，
它的 recall 会恒为 0，把整体指标拉低，而你却会以为是检索不行。

用法：
  python scripts/build_evalset.py            # 校验 + 重建
  python scripts/build_evalset.py --dry-run  # 只校验不写库
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.db import dao  # noqa: E402
from app.utils.console import setup_console  # noqa: E402

CASES_PATH = settings.data_dir / "eval_cases.jsonl"


def load_cases() -> list[dict]:
    if not CASES_PATH.exists():
        raise SystemExit(f"找不到 {CASES_PATH}")
    cases = []
    with CASES_PATH.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def main() -> int:
    setup_console()
    p = argparse.ArgumentParser(description="构建评测集")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    cases = load_cases()
    conn = dao.connect(settings.db_path)
    paths = [r["rel_path"] for r in dao.fetch_all(conn, "SELECT rel_path FROM documents")]

    print(f"[evalset] 载入 {len(cases)} 条，语料 {len(paths)} 篇")

    # ---------- 校验 ----------
    bad_paths: list[tuple[str, str]] = []
    for c in cases:
        for gp in c.get("gold_paths") or []:
            if not any(pp.startswith(gp) for pp in paths):
                bad_paths.append((c["id"], gp))

    if bad_paths:
        print(f"\n❌ {len(bad_paths)} 个 gold_path 在语料里匹配不到：")
        for cid, gp in bad_paths:
            print(f"   {cid}: {gp}")
        print("\n这些题会永远得 0 分。请修正前缀，或从评测集里删掉。")
        return 1

    # ---------- 统计 ----------
    by_type = Counter(c["type"] for c in cases)
    by_topic = Counter(c.get("topic", "-") for c in cases)
    by_diff = Counter(c.get("difficulty", "-") for c in cases)
    dup = [k for k, v in Counter(c["id"] for c in cases).items() if v > 1]
    if dup:
        print(f"❌ 重复 id：{dup}")
        return 1

    print(f"  类型：{dict(by_type)}")
    print(f"  难度：{dict(by_diff)}")
    print(f"  主题：{dict(by_topic)}")

    if args.dry_run:
        print("\n[dry-run] 校验通过，未写库")
        return 0

    # ---------- 写库（重建 eval 表） ----------
    conn.execute("DROP TABLE IF EXISTS eval_results")
    conn.execute("DROP TABLE IF EXISTS eval_cases")
    conn.commit()
    dao.init_schema(conn)

    rows = [
        {
            "id": c["id"],
            "question": c["question"],
            "type": c["type"],
            "difficulty": c.get("difficulty", ""),
            "gold_doc_ids": json.dumps(c.get("gold_doc_ids") or [], ensure_ascii=False),
            "gold_paths": json.dumps(c.get("gold_paths") or [], ensure_ascii=False),
            "gold_points": json.dumps(c.get("gold_points") or [], ensure_ascii=False),
            "answerable": 1 if c.get("answerable", True) else 0,
            "topic": c.get("topic", ""),
            "note": c.get("note", ""),
        }
        for c in cases
    ]
    n = dao.upsert_many(conn, "eval_cases", rows)
    print(f"\n[evalset] 已写入 {n} 条 → {settings.db_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
