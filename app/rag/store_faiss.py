"""FAISS 后端：进程内向量索引 + JSONL 元数据。

为什么保留第三个后端：
  - Chroma 0.5.x 在本机实测会**静默丢写**（3,441 条只落 1,556，且不抛异常）；
  - Milvus 需要 1.2 GB 镜像 + 2–3 GB 内存，当前拉不动；
  - FAISS 是纯内存索引，没有后台消费者，写多少就是多少，且落盘只有两个文件。

代价：不支持标量过滤的原生实现（本项目暂时用不到），删除/更新需要重建索引（3.4k 规模可接受）。

文件布局：
  data/faiss/{collection}.index    faiss.write_index
  data/faiss/{collection}.meta.jsonl  每行 {"id","doc_id","ord","heading_path","content","n_tokens","has_code"}
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from app.config import settings
from app.rag.index import VectorStore


class FaissStore(VectorStore):
    backend = "faiss"

    def __init__(
        self,
        path: str | Path | None = None,
        collection: str | None = None,
        dim: int | None = None,
    ):
        self.dir = Path(path or (settings.data_dir / "faiss"))
        self.dir.mkdir(parents=True, exist_ok=True)
        self.name = collection or settings.chroma_collection
        self.dim = dim or settings.embed_dim
        self.index_path = self.dir / f"{self.name}.index"
        self.meta_path = self.dir / f"{self.name}.meta.jsonl"
        self._ids: list[str] = []
        self._meta: dict[str, dict] = {}
        self._index = None
        self._load()

    # ---------- 持久化 ----------
    def _new_index(self):
        import faiss  # noqa: PLC0415

        # 向量已 L2 归一化 → 内积等价于余弦相似度
        return faiss.IndexFlatIP(self.dim)

    def _load(self) -> None:
        if self.index_path.exists() and self.meta_path.exists():
            import faiss  # noqa: PLC0415

            self._index = faiss.read_index(str(self.index_path))
            self._ids = []
            self._meta = {}
            with self.meta_path.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    row = json.loads(line)
                    self._meta[row["id"]] = row
                    self._ids.append(row["id"])
        else:
            self._index = self._new_index()

    def flush(self) -> None:
        import faiss  # noqa: PLC0415

        faiss.write_index(self._index, str(self.index_path))
        tmp = self.meta_path.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for cid in self._ids:
                f.write(json.dumps(self._meta[cid], ensure_ascii=False) + "\n")
        tmp.replace(self.meta_path)

    # ---------- 契约 ----------
    def exists(self) -> bool:
        return self.index_path.exists() and self.meta_path.exists() and len(self._ids) > 0

    def create(self, reset: bool = False) -> None:
        if reset:
            self.index_path.unlink(missing_ok=True)
            self.meta_path.unlink(missing_ok=True)
            self._ids, self._meta = [], {}
            self._index = self._new_index()
            self.flush()

    def add(self, rows, vectors, batch: int = 256) -> int:
        arr = np.asarray(vectors, dtype=np.float32)
        if arr.ndim != 2 or arr.shape[1] != self.dim:
            raise ValueError(f"向量维度不符：{arr.shape} vs dim={self.dim}")
        self._index.add(arr)
        for r in rows:
            self._ids.append(r["id"])
            self._meta[r["id"]] = {
                "id": r["id"],
                "doc_id": r["doc_id"],
                "ord": int(r["ord"]),
                "heading_path": r.get("heading_path") or "",
                "content": r["content"],
                "n_tokens": int(r.get("n_tokens") or 0),
                "has_code": int(r.get("has_code") or 0),
            }
        self.flush()
        return len(rows)

    def upsert(self, rows, vectors, batch: int = 256) -> int:
        """有重复 id 时整体重建（FAISS Flat 不支持原地更新）。"""
        dup = {r["id"] for r in rows} & set(self._meta)
        if not dup:
            return self.add(rows, vectors)

        all_vecs = self._index.reconstruct_n(0, len(self._ids))
        keep_idx = [i for i, cid in enumerate(self._ids) if cid not in dup]
        kept_ids = [self._ids[i] for i in keep_idx]

        self._index = self._new_index()
        if keep_idx:
            self._index.add(np.asarray([all_vecs[i] for i in keep_idx], dtype=np.float32))
        self._ids = kept_ids
        self._meta = {cid: self._meta[cid] for cid in kept_ids}
        return self.add(rows, vectors)

    def search(self, vector, top_k: int = 50) -> list[dict[str, Any]]:
        if not self._ids:
            return []
        vec = np.asarray(vector, dtype=np.float32).reshape(1, -1)
        scores, idx = self._index.search(vec, min(top_k, len(self._ids)))
        out: list[dict[str, Any]] = []
        for score, i in zip(scores[0], idx[0]):
            if i < 0:
                continue
            cid = self._ids[i]
            meta = self._meta.get(cid, {})
            out.append(
                {
                    "id": cid,
                    "score": float(score),
                    "content": meta.get("content", ""),
                    "doc_id": meta.get("doc_id", ""),
                    "ord": meta.get("ord", 0),
                    "heading_path": meta.get("heading_path", ""),
                }
            )
        return out

    def get_by_ids(self, ids: Sequence[str]) -> list[dict[str, Any]]:
        return [
            {
                "id": cid,
                "content": self._meta[cid].get("content", ""),
                "heading_path": self._meta[cid].get("heading_path", ""),
                "doc_id": self._meta[cid].get("doc_id", ""),
            }
            for cid in ids
            if cid in self._meta
        ]

    def count(self) -> int:
        return len(self._ids)

    def drop(self) -> None:
        self.create(reset=True)

    def close(self) -> None:
        if self._ids:
            self.flush()
