"""documents → chunks（切片入库）。

这一步不依赖模型，可以先跑；它是后面所有检索实验的输入。

用法：
  python scripts/build_chunks.py            # 增量（已存在的 doc 会被替换）
  python scripts/build_chunks.py --reset    # 清空 chunks 重建
  python scripts/build_chunks.py --limit 50 # 只处理前 50 篇（调试用）
"""

from __future__ import annotations

import argparse
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.db import dao  # noqa: E402
from app.rag.chunker import chunk_document  # noqa: E402
from app.utils.console import setup_console  # noqa: E402


def main() -> int:
    setup_console()
    p = argparse.ArgumentParser(description="文档切片入库")
    p.add_argument("--reset", action="store_true", help="清空 chunks 后重建")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--chunk-size", type=int, default=settings.chunk_size)
    p.add_argument("--overlap", type=int, default=settings.chunk_overlap)
    args = p.parse_args()

    conn = dao.connect(settings.db_path)
    docs = dao.fetch_all(
        conn,
        "SELECT id, rel_path, content_md, has_table, lang FROM documents ORDER BY rel_path"
        + (f" LIMIT {args.limit}" if args.limit else ""),
    )
    if not docs:
        raise SystemExit("documents 为空，先跑 scripts/build_db.py")

    if args.reset:
        n = dao.count(conn, "chunks")
        conn.execute("DELETE FROM chunks")
        conn.commit()
        print(f"[chunks] --reset：清空 {n} 行")

    total = 0
    per_doc: list[int] = []
    tok_all: list[int] = []
    code_chunks = 0
    empty_docs: list[str] = []

    for doc in docs:
        res = chunk_document(
            doc["content_md"],
            doc["id"],
            limit=args.chunk_size,
            overlap=args.overlap,
        )
        if not res.chunks:
            empty_docs.append(doc["rel_path"])
            continue
        rows = [
            {
                "id": c.id,
                "doc_id": doc["id"],
                "ord": c.ord,
                "heading_path": c.heading_path,
                "content": c.content,
                "n_tokens": c.n_tokens,
                "has_code": c.has_code,
            }
            for c in res.chunks
        ]
        dao.upsert_many(conn, "chunks", rows)
        total += len(rows)
        per_doc.append(len(rows))
        tok_all.extend(c.n_tokens for c in res.chunks)
        code_chunks += sum(c.has_code for c in res.chunks)

    print(f"\n[chunks] 完成：{len(per_doc)} 篇 → {total:,} 个 chunk")
    if tok_all:
        print(f"  token 分布：min {min(tok_all)} / 中位 {statistics.median(tok_all):.0f} / "
              f"均值 {statistics.mean(tok_all):.0f} / max {max(tok_all)}")
        buckets = Counter()
        for t in tok_all:
            if t < 100:
                buckets["<100"] += 1
            elif t < 300:
                buckets["100-300"] += 1
            elif t < 400:
                buckets["300-400"] += 1
            else:
                buckets[">=400"] += 1
        print("  分档：", dict(sorted(buckets.items())))
    print(f"  含代码的 chunk：{code_chunks}（{code_chunks / max(total, 1):.1%}）")
    print(f"  每篇平均 chunk 数：{statistics.mean(per_doc):.1f}" if per_doc else "")
    if empty_docs:
        print(f"  ⚠ {len(empty_docs)} 篇没有产出 chunk：{empty_docs[:3]}")

    dao.set_meta(conn, "chunks_count", str(dao.count(conn, "chunks")))
    dao.set_meta(conn, "chunk_size", str(args.chunk_size))
    dao.set_meta(conn, "chunk_overlap", str(args.overlap))
    print(f"  库内 chunk 总数：{dao.count(conn, 'chunks'):,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
