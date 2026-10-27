"""向量库后端抽象：Milvus 与 Chroma 同一套接口。

为什么要抽象：
  - Milvus 需要单独起容器（1.2 GB 镜像 + 2–3 GB 内存），有时拉不动；
  - Chroma 是进程内嵌入式（pip 装完就能用，落盘即持久），本项目 3.4k chunk 完全够；
  - 上层 retrieve / build_index / search 完全不关心用的哪个后端。

切换：.env 里 VECTOR_BACKEND=chroma|milvus
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from app.config import settings

CONTENT_MAX = 8192
HEADING_MAX = 1024


class VectorStore(ABC):
    """最小契约：建库 / 写 / 查 / 按 id 取回 / 计数。"""

    backend: str = "base"
    dim: int = 0
    name: str = ""

    @abstractmethod
    def exists(self) -> bool: ...

    @abstractmethod
    def create(self, reset: bool = False) -> None: ...

    @abstractmethod
    def upsert(self, rows: Sequence[dict[str, Any]], vectors: np.ndarray, batch: int = 256) -> int: ...

    @abstractmethod
    def search(self, vector: np.ndarray | list[float], top_k: int = 50) -> list[dict[str, Any]]: ...

    @abstractmethod
    def get_by_ids(self, ids: Sequence[str]) -> list[dict[str, Any]]:
        """按 id 批量取回，返回 [{id, content, heading_path, doc_id}]，顺序不保证。"""

    @abstractmethod
    def count(self) -> int: ...

    @abstractmethod
    def drop(self) -> None: ...

    def close(self) -> None:  # 可选
        return None


# --------------------------------------------------------------------------- #
# Milvus Standalone / Zilliz Cloud
# --------------------------------------------------------------------------- #
class MilvusStore(VectorStore):
    backend = "milvus"

    def __init__(
        self,
        uri: str | None = None,
        collection: str | None = None,
        dim: int | None = None,
    ):
        from pymilvus import MilvusClient  # noqa: PLC0415

        self.uri = uri or settings.milvus_uri
        self.name = collection or settings.milvus_collection
        self.dim = dim or settings.embed_dim
        self.client = MilvusClient(
            uri=self.uri, token=settings.milvus_token or None, timeout=10
        )

    def exists(self) -> bool:
        return self.name in self.client.list_collections()

    def create(self, reset: bool = False) -> None:
        from pymilvus import DataType  # noqa: PLC0415

        if self.exists():
            if not reset:
                return
            self.client.drop_collection(self.name)

        schema = self.client.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("id", DataType.VARCHAR, is_primary=True, max_length=64)
        schema.add_field("doc_id", DataType.VARCHAR, max_length=64)
        schema.add_field("ord", DataType.INT32)
        schema.add_field("heading_path", DataType.VARCHAR, max_length=HEADING_MAX)
        schema.add_field("content", DataType.VARCHAR, max_length=CONTENT_MAX)
        schema.add_field("n_tokens", DataType.INT32)
        schema.add_field("has_code", DataType.INT8)
        schema.add_field("vector", DataType.FLOAT_VECTOR, dim=self.dim)

        index_params = self.client.prepare_index_params()
        index_params.add_index(
            field_name="vector",
            index_type=settings.milvus_index,
            metric_type=settings.milvus_metric,
            params={"M": 16, "efConstruction": 200},
        )
        self.client.create_collection(
            collection_name=self.name, schema=schema, index_params=index_params
        )

    def upsert(self, rows, vectors, batch: int = 256) -> int:
        assert len(rows) == len(vectors)
        written = 0
        for i in range(0, len(rows), batch):
            data = [
                {
                    "id": r["id"],
                    "doc_id": r["doc_id"],
                    "ord": int(r["ord"]),
                    "heading_path": (r.get("heading_path") or "")[:HEADING_MAX],
                    "content": r["content"][:CONTENT_MAX],
                    "n_tokens": int(r.get("n_tokens") or 0),
                    "has_code": int(r.get("has_code") or 0),
                    "vector": vec.tolist(),
                }
                for r, vec in zip(rows[i : i + batch], vectors[i : i + batch])
            ]
            self.client.upsert(collection_name=self.name, data=data)
            written += len(data)
        return written

    def search(self, vector, top_k: int = 50) -> list[dict[str, Any]]:
        vec = vector.tolist() if isinstance(vector, np.ndarray) else vector
        res = self.client.search(
            collection_name=self.name,
            data=[vec],
            limit=top_k,
            output_fields=["doc_id", "ord", "heading_path", "content"],
            search_params={
                "metric_type": settings.milvus_metric,
                "params": {"ef": max(64, top_k)},
            },
        )
        out: list[dict[str, Any]] = []
        if not res:
            return out
        for h in res[0]:
            item = {"id": h.get("id"), "score": float(h.get("distance", 0.0))}
            item.update(h.get("entity", {}) or {})
            out.append(item)
        return out

    def get_by_ids(self, ids: Sequence[str]) -> list[dict[str, Any]]:
        if not ids:
            return []
        id_list = ", ".join(f'"{i}"' for i in ids)
        return self.client.query(
            collection_name=self.name,
            filter=f"id in [{id_list}]",
            output_fields=["id", "content", "heading_path", "doc_id"],
            limit=len(ids),
        )

    def count(self) -> int:
        if not self.exists():
            return 0
        return int(self.client.get_collection_stats(self.name).get("row_count", 0))

    def drop(self) -> None:
        if self.exists():
            self.client.drop_collection(self.name)

    def close(self) -> None:
        try:
            self.client.close()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# Chroma（嵌入式，落盘持久）
# --------------------------------------------------------------------------- #
class ChromaStore(VectorStore):
    backend = "chroma"

    def __init__(
        self,
        path: str | Path | None = None,
        collection: str | None = None,
        dim: int | None = None,
    ):
        import chromadb  # noqa: PLC0415
        from chromadb.config import Settings as ChromaSettings  # noqa: PLC0415

        self.path = Path(path or settings.chroma_dir)
        self.path.mkdir(parents=True, exist_ok=True)
        self.name = collection or settings.chroma_collection
        self.dim = dim or settings.embed_dim
        self.client = chromadb.PersistentClient(
            path=str(self.path),
            settings=ChromaSettings(anonymized_telemetry=False, allow_reset=True),
        )
        # 关键：句柄只取一次并缓存。
        # 每批都调 get_or_create_collection 会不断新建内部消费者，SQLite 锁争用后
        # 后台消费者抛 "unable to open database file"，写入被静默丢弃（实测 3441 条只落了 1556）。
        self._col = None

    def _collection(self):
        if self._col is None:
            self._col = self.client.get_or_create_collection(
                name=self.name, metadata={"hnsw:space": "cosine"}
            )
        return self._col

    def exists(self) -> bool:
        try:
            self.client.get_collection(self.name)
            return True
        except Exception:
            return False

    def create(self, reset: bool = False) -> None:
        if reset and self.exists():
            self.client.delete_collection(self.name)
            self._col = None
        # cosine：与 Milvus 的 COSINE 语义对齐，向量已归一化
        self._col = self.client.get_or_create_collection(
            name=self.name,
            metadata={"hnsw:space": "cosine"},
        )

    def add(self, rows, vectors, batch: int = 128) -> int:
        """往**空集合**里新增。

        为什么不用 upsert：Chroma 0.5.x 的 upsert 是"先删再写"，
        实测 3,441 条会稳定只落 1,556 条（后台消费者丢写且不报错）。
        新建库时用 add 是干净的，实测 3,441/3,441 全部落盘。
        """
        col = self._collection()
        written = 0
        for i in range(0, len(rows), batch):
            sub_rows = rows[i : i + batch]
            col.add(
                ids=[r["id"] for r in sub_rows],
                embeddings=[v.tolist() for v in vectors[i : i + batch]],
                documents=[r["content"] for r in sub_rows],
                metadatas=[
                    {
                        "doc_id": r["doc_id"],
                        "ord": int(r["ord"]),
                        "heading_path": (r.get("heading_path") or "")[:HEADING_MAX],
                        "n_tokens": int(r.get("n_tokens") or 0),
                        "has_code": int(r.get("has_code") or 0),
                    }
                    for r in sub_rows
                ],
            )
            written += len(sub_rows)
        return written

    def upsert(self, rows, vectors, batch: int = 128) -> int:
        import sqlite3  # noqa: PLC0415
        import time  # noqa: PLC0415

        col = self._collection()
        written = 0
        for i in range(0, len(rows), batch):
            sub_rows = rows[i : i + batch]
            sub_vecs = vectors[i : i + batch]
            payload = dict(
                ids=[r["id"] for r in sub_rows],
                embeddings=[v.tolist() for v in sub_vecs],
                documents=[r["content"] for r in sub_rows],
                metadatas=[
                    {
                        "doc_id": r["doc_id"],
                        "ord": int(r["ord"]),
                        "heading_path": (r.get("heading_path") or "")[:HEADING_MAX],
                        "n_tokens": int(r.get("n_tokens") or 0),
                        "has_code": int(r.get("has_code") or 0),
                    }
                    for r in sub_rows
                ],
            )
            # Chroma 0.5.x 在大批量写入时偶发 sqlite3.OperationalError
            # ("unable to open database file")，重试即可；upsert 幂等，不会写重。
            for attempt in range(4):
                try:
                    col.upsert(**payload)
                    break
                except sqlite3.OperationalError as e:
                    if attempt == 3:
                        raise
                    print(f"\n  ⚠ sqlite 写入失败，重试 {attempt + 1}/3：{e}")
                    time.sleep(0.5 * (attempt + 1))
            written += len(sub_rows)
        return written

    def search(self, vector, top_k: int = 50) -> list[dict[str, Any]]:
        col = self._collection()
        vec = vector.tolist() if isinstance(vector, np.ndarray) else vector
        res = col.query(
            query_embeddings=[vec],
            n_results=top_k,
            include=["documents", "metadatas", "distances"],
        )
        out: list[dict[str, Any]] = []
        ids = (res.get("ids") or [[]])[0]
        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        for i, cid in enumerate(ids):
            meta = metas[i] or {}
            out.append(
                {
                    "id": cid,
                    # cosine distance -> 相似度，和 Milvus 的分数量级对齐
                    "score": float(1.0 - (dists[i] if i < len(dists) else 0.0)),
                    "content": docs[i] if i < len(docs) else "",
                    "doc_id": meta.get("doc_id", ""),
                    "ord": meta.get("ord", 0),
                    "heading_path": meta.get("heading_path", ""),
                }
            )
        return out

    def get_by_ids(self, ids: Sequence[str]) -> list[dict[str, Any]]:
        if not ids:
            return []
        col = self._collection()
        out: list[dict[str, Any]] = []
        # 分批：一次传几千个 id 会被静默截断（实测 3,441 个只回 608）
        for i in range(0, len(ids), 200):
            sub = list(ids[i : i + 200])
            res = col.get(ids=sub, include=["documents", "metadatas"])
            for k, cid in enumerate(res.get("ids") or []):
                meta = (res.get("metadatas") or [{}])[k] or {}
                out.append(
                    {
                        "id": cid,
                        "content": (res.get("documents") or [""])[k],
                        "heading_path": meta.get("heading_path", ""),
                        "doc_id": meta.get("doc_id", ""),
                    }
                )
        return out

    def count(self) -> int:
        if not self.exists():
            return 0
        return int(self._collection().count())

    def drop(self) -> None:
        if self.exists():
            self.client.delete_collection(self.name)
            self._col = None


def get_store(
    backend: str | None = None,
    dim: int | None = None,
    reset: bool = False,
    create: bool = True,
) -> VectorStore:
    """工厂：按配置返回后端实例。create=False 用于只读场景（搜索）。"""
    backend = (backend or settings.vector_backend).lower()
    if backend in {"chroma", "milvus"}:
        if backend == "chroma":
            store: VectorStore = ChromaStore(dim=dim)
        else:
            store = MilvusStore(dim=dim)
    elif backend == "faiss":
        from app.rag.store_faiss import FaissStore  # 避免顶层循环导入  # noqa: PLC0415

        store = FaissStore(dim=dim)
    else:
        raise ValueError(f"未知 VECTOR_BACKEND: {backend}（可选 faiss / chroma / milvus）")
    if create:
        store.create(reset=reset)
    return store
