"""检索 CLI：单条 query 跑四种模式，肉眼对比效果。

用法：
  python scripts/search.py "Docker Desktop 装完起不来怎么办"
  python scripts/search.py "端口被占用" --mode hybrid --top-k 5
  python scripts/search.py "0x80370102" --mode bm25 --show-content
  python scripts/search.py "怎么限制容器内存" --compare     # 四种模式并排
"""

from __future__ import annotations

import argparse
import sys
import textwrap
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.rag.bm25 import BM25Index, default_bm25_path  # noqa: E402
from app.rag.retrieve import MODES, Retriever  # noqa: E402
from app.utils.console import setup_console  # noqa: E402


def make_retriever() -> Retriever:
    from app.rag.index import get_store  # noqa: PLC0415

    store = get_store(create=False)
    if not store.exists():
        hint = (
            "docker compose -f docker/milvus-compose.yml up -d"
            if store.backend == "milvus"
            else "（chroma 无需启动服务，建库即可）"
        )
        raise SystemExit(
            f"[{store.backend}] collection `{store.name}` 不存在。\n{hint}\n"
            f"再建库：python scripts/build_index.py"
        )
    bm25_path = default_bm25_path()
    if not bm25_path.exists():
        raise SystemExit(f"BM25 索引不存在：{bm25_path}\n先跑：python scripts/build_index.py --bm25-only")
    bm25 = BM25Index.load(bm25_path)
    return Retriever(store, bm25)


def render(query: str, hits, show_content: bool, width: int) -> None:
    print(f"\n>>> {query}")
    if not hits:
        print("    (无结果)")
        return
    for i, h in enumerate(hits, 1):
        head = h.heading_path or "(无面包屑)"
        print(f"  {i}. [{h.score:+.4f}] {head[:70]}")
        print(f"     chunk={h.id} doc={h.doc_id[:10]} src={h.source}")
        if show_content:
            snippet = " ".join(h.content.split())[: width]
            print(textwrap.fill(snippet, width=width, initial_indent="     ", subsequent_indent="     "))


def main() -> int:
    setup_console()
    p = argparse.ArgumentParser(description="检索测试")
    p.add_argument("query")
    p.add_argument("--mode", default="hybrid_rerank", choices=list(MODES))
    p.add_argument("--top-k", type=int, default=settings.final_topk)
    p.add_argument("--show-content", action="store_true")
    p.add_argument("--width", type=int, default=110)
    p.add_argument("--compare", action="store_true", help="四种模式并排跑")
    args = p.parse_args()

    retriever = make_retriever()
    modes = list(MODES) if args.compare else [args.mode]

    for mode in modes:
        t0 = time.time()
        try:
            hits = retriever.search(args.query, mode=mode, top_k=args.top_k)
        except Exception as e:  # 例如 reranker 模型还没下载
            print(f"\n>>> {args.query}\n    ⚠ mode={mode} 跳过：{type(e).__name__}: {str(e)[:90]}")
            continue
        dt = (time.time() - t0) * 1000
        render(args.query, hits, args.show_content, args.width)
        print(f"    ⏱ {dt:.0f} ms   mode={mode}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
