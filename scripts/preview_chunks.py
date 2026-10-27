"""切片预览：把结构化切片和「512 字符硬切」放一起对比。

用法：
  python scripts/preview_chunks.py                      # 自动挑：最长文档 + 含表格文档
  python scripts/preview_chunks.py --rel-path manuals/desktop/setup/install/windows-install.md
  python scripts/preview_chunks.py --show 3 --width 120
"""

from __future__ import annotations

import argparse
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.db import dao  # noqa: E402
from app.rag.chunker import chunk_document, estimate_tokens  # noqa: E402
from app.utils.console import setup_console  # noqa: E402


def pick_docs(conn, rel_paths: list[str]) -> list[dict]:
    if rel_paths:
        rows = []
        for rp in rel_paths:
            row = dao.fetch_one(conn, "SELECT * FROM documents WHERE rel_path = ?", (rp,))
            if not row:
                print(f"⚠ 找不到 {rp}")
                continue
            rows.append(row)
        return rows

    longest = dao.fetch_one(
        conn, "SELECT * FROM documents ORDER BY word_count DESC LIMIT 1"
    )
    with_table = dao.fetch_one(
        conn,
        "SELECT * FROM documents WHERE has_table = 1 AND word_count BETWEEN 400 AND 2000 "
        "ORDER BY word_count DESC LIMIT 1",
    )
    picks = [r for r in (longest, with_table) if r]
    # 去重
    seen, out = set(), []
    for r in picks:
        if r["id"] not in seen:
            seen.add(r["id"])
            out.append(r)
    return out


def naive_split(text: str, size: int = 512) -> list[str]:
    """对照组：不分结构，按固定字符数硬切。"""
    return [text[i : i + size] for i in range(0, len(text), size)]


def show(text: str, width: int = 100, max_lines: int = 14) -> str:
    lines = text.splitlines()
    shown = lines[:max_lines]
    body = "\n".join(textwrap.shorten(l, width=width, placeholder=" …") for l in shown)
    if len(lines) > max_lines:
        body += f"\n  …（共 {len(lines)} 行）"
    return body


def main() -> int:
    setup_console()
    p = argparse.ArgumentParser(description="切片效果预览")
    p.add_argument("--rel-path", action="append", default=None)
    p.add_argument("--limit", type=int, default=settings.chunk_size)
    p.add_argument("--overlap", type=int, default=settings.chunk_overlap)
    p.add_argument("--show", type=int, default=2, help="每篇展示几个 chunk")
    args = p.parse_args()

    conn = dao.connect(settings.db_path)
    docs = pick_docs(conn, args.rel_path or [])
    if not docs:
        raise SystemExit("没有可预览的文档")

    print(f"\n切片参数：chunk_size={args.limit} tokens, overlap={args.overlap}\n")
    print("=" * 100)

    for doc in docs:
        md = doc["content_md"]
        res = chunk_document(md, doc["id"], limit=args.limit, overlap=args.overlap)
        toks = [c.n_tokens for c in res.chunks]
        code_chunks = sum(c.has_code for c in res.chunks)

        print(f"\n📄 {doc['rel_path']}")
        print(f"   {doc['word_count']:,} 词 | {len(md):,} 字符 | 表格={doc['has_table']} 代码={doc['has_code']}")

        naive = naive_split(md)
        print("\n   对比：")
        print(f"   ├─ 结构化切片 : {len(res.chunks):>3} 块   平均 {sum(toks) / max(len(toks), 1):.0f} tokens"
              f"  (min {min(toks) if toks else 0} / max {max(toks) if toks else 0})，来自 {res.sections} 个标题段")
        print(f"   └─ 512 硬切   : {len(naive):>3} 块   平均 {sum(estimate_tokens(x) for x in naive) / max(len(naive), 1):.0f} tokens"
              f"  ← 会切断代码块和表格")

        print(f"\n   有代码块的 chunk：{code_chunks}/{len(res.chunks)}")
        print("   面包屑覆盖率：", end="")
        with_bc = sum(1 for c in res.chunks if c.heading_path)
        print(f"{with_bc}/{len(res.chunks)}")

        for i, c in enumerate(res.chunks[: args.show]):
            print(f"\n   ── chunk #{i}  [{c.n_tokens} tokens] code={c.has_code}")
            print(f"   面包屑: {c.heading_path or '(无)'}")
            print("   " + "\n   ".join(show(c.content).splitlines()))

        # 硬切的破坏性演示
        broken = [x for x in naive if x.count("```") % 2 == 1]
        print(f"\n   硬切的副作用：{len(broken)} 个块里代码围栏 ``` 数量是奇数 → 说明代码块被从中间切断了")
    print("\n" + "=" * 100)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
