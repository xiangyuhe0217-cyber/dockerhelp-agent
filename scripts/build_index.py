"""chunks → 向量库 + BM25 索引。

分两步，可单独执行：
  1. BM25 索引（纯 CPU，秒级）：data/bm25.pkl
  2. 向量入库（需要模型 + Milvus）：collection helpdesk_chunks

用法：
  python scripts/build_index.py --bm25-only          # 先建 BM25（不需要 Milvus）
  python scripts/build_index.py                      # 建向量 + BM25
  python scripts/build_index.py --reset              # 重建 collection
  python scripts/build_index.py --provider bailian   # E5 对照实验
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.db import dao  # noqa: E402
from app.rag.bm25 import BM25Index, default_bm25_path  # noqa: E402
from app.utils.console import setup_console  # noqa: E402


def load_chunks(conn, limit: int = 0) -> list[dict]:
    sql = "SELECT id, doc_id, ord, heading_path, content, n_tokens, has_code FROM chunks ORDER BY doc_id, ord"
    if limit:
        sql += f" LIMIT {limit}"
    return dao.fetch_all(conn, sql)


def build_bm25(rows: list[dict]) -> None:
    t0 = time.time()
    print(f"[bm25] 分词并建索引（{len(rows):,} 个 chunk）…")
    idx = BM25Index.build(rows)
    path = default_bm25_path()
    idx.save(path)
    print(f"[bm25] 完成：{path}（{path.stat().st_size / 1e6:.1f} MB，{time.time() - t0:.1f}s）")


def build_vectors(rows: list[dict], reset: bool, provider: str | None) -> None:
    from app.rag.embed import get_embedder  # noqa: PLC0415
    from app.rag.index import get_store  # noqa: PLC0415

    print(f"[vec] 加载向量模型（provider={provider or settings.embed_provider}）…")
    t0 = time.time()
    embedder = get_embedder(provider)
    print(f"[vec] {embedder.name} dim={embedder.dim} device={getattr(embedder, 'device', 'api')} ({time.time() - t0:.1f}s)")

    store = get_store(dim=embedder.dim, reset=reset)
    print(f"[vec] backend={store.backend} collection={store.name} 当前条数={store.count()}")

    t0 = time.time()
    batch = settings.embed_batch_size
    written = 0
    # 全新构建用 add（Chroma 的 upsert 会丢写）；增量才用 upsert
    fresh = reset or store.count() == 0
    writer = getattr(store, "add", None) if fresh else None
    for i in range(0, len(rows), batch):
        chunk_rows = rows[i : i + batch]
        texts = [r["content"] for r in chunk_rows]
        vectors = embedder.encode(texts)
        if writer is not None:
            written += writer(chunk_rows, vectors)
        else:
            written += store.upsert(chunk_rows, vectors)
        if (i // batch) % 5 == 0 or written == len(rows):
            elapsed = time.time() - t0
            speed = written / max(elapsed, 0.01)
            eta = (len(rows) - written) / max(speed, 0.01)
            print(
                f"\r[vec] {written:,}/{len(rows):,} ({written / len(rows):.1%}) "
                f"{speed:5.1f} chunk/s  ETA {eta / 60:.1f} min",
                end="",
                flush=True,
            )
    print()
    print(f"[vec] 写入完成：{written:,} 条，耗时 {(time.time() - t0) / 60:.1f} min")

    # ---- 写后校验 + 补漏 ----
    # Chroma 在大批量写入时可能静默丢数据，这里按 id 全量核对并补写缺失项。
    expected = {r["id"]: r for r in rows}
    for round_no in range(1, 4):
        present: set[str] = set()
        ids = list(expected)
        for i in range(0, len(ids), 500):
            got = store.get_by_ids(ids[i : i + 500])
            present.update(g["id"] for g in got)
        missing = [cid for cid in ids if cid not in present]
        print(f"[vec] 校验第 {round_no} 轮：存在 {len(present):,}/{len(expected):,}，缺 {len(missing):,}")
        if not missing:
            break
        for i in range(0, len(missing), 64):
            sub = [expected[cid] for cid in missing[i : i + 64]]
            vecs = embedder.encode([r["content"] for r in sub])
            store.upsert(sub, vecs, batch=64)
    else:
        print("[vec] ⚠ 补漏 3 轮后仍有缺失，请检查 Chroma 日志")

    print(f"[vec] collection 条数：{store.count():,}（应为 {len(rows):,}）")
    store.close()


def main() -> int:
    setup_console()
    p = argparse.ArgumentParser(description="构建检索索引")
    p.add_argument("--bm25-only", action="store_true")
    p.add_argument("--vec-only", action="store_true")
    p.add_argument("--reset", action="store_true", help="重建 Milvus collection")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--provider", default=None, help="local | bailian")
    args = p.parse_args()

    conn = dao.connect(settings.db_path)
    rows = load_chunks(conn, args.limit)
    if not rows:
        raise SystemExit("chunks 为空，先跑 scripts/build_chunks.py")
    print(f"[index] 待索引 chunk：{len(rows):,}")

    if not args.vec_only:
        build_bm25(rows)
    if not args.bm25_only:
        build_vectors(rows, args.reset, args.provider)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
