"""混合召回 + RRF 融合 + 重排。

为什么要 RRF（Reciprocal Rank Fusion）而不是"分数加权和"：
  向量相似度是 [-1,1] 的余弦值，BM25 是无上界的正数，两者**尺度不可比**。
  直接 0.7*vec + 0.3*bm25 需要针对每个语料调权重，换个数据集就失效。
  RRF 只用**名次**：score = Σ 1/(k + rank)，k 通常取 60 —— 不需要调参、跨数据集稳定。

流程（hybrid_rerank 模式）：
  向量 Top50  ┐
              ├→ RRF 融合 → 取前 20 → cross-encoder 重排 → Top5
  BM25  Top50 ┘
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np

from app.config import settings

MODES = ("vector", "bm25", "hybrid", "hybrid_rerank")


def rrf_fuse(
    rankings: Sequence[Sequence[tuple[str, float]]],
    k: int = 60,
    top_k: int | None = None,
) -> list[tuple[str, float]]:
    """rankings: 多路 [(id, score)] 已按相关度降序。返回融合后的 [(id, rrf_score)]。"""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, (cid, _score) in enumerate(ranking, start=1):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
    fused = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return fused[:top_k] if top_k else fused


@dataclass
class Hit:
    id: str
    score: float
    content: str = ""
    heading_path: str = ""
    doc_id: str = ""
    source: str = ""  # vector / bm25 / fused / rerank

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "score": round(self.score, 6),
            "doc_id": self.doc_id,
            "heading_path": self.heading_path,
            "content": self.content,
            "source": self.source,
        }


class Reranker:
    """cross-encoder 重排（bge-reranker-v2-m3）。懒加载，用时才占显存。"""

    def __init__(self, model_path: str | None = None, device: str | None = None):
        from sentence_transformers import CrossEncoder  # noqa: PLC0415

        path = model_path or str(settings.rerank_model_path)
        self.device = device or ("cuda" if settings.use_cuda else "cpu")
        self.model = CrossEncoder(path, device=self.device, max_length=512)
        self.name = "bge-reranker-v2-m3"

    def rerank(self, query: str, candidates: list[Hit], top_k: int) -> list[Hit]:
        if not candidates:
            return []
        pairs = [(query, c.content) for c in candidates]
        scores = self.model.predict(pairs, batch_size=8, show_progress_bar=False)
        ranked = sorted(
            zip(candidates, np.asarray(scores).tolist()),
            key=lambda x: x[1],
            reverse=True,
        )[:top_k]
        out: list[Hit] = []
        for hit, s in ranked:
            out.append(
                Hit(
                    id=hit.id,
                    score=float(s),
                    content=hit.content,
                    heading_path=hit.heading_path,
                    doc_id=hit.doc_id,
                    source="rerank",
                )
            )
        return out


class BailianReranker:
    """阿里百炼 gte-rerank（HTTP API）。

    为什么用它而不是本地 bge-reranker：
      - 不用下 2.27 GB 模型，也不占显存；
      - 与现有 embedding（text-embedding-v3）同一家，跨语言表现一致；
      - 代价是每次查询多一次网络往返（约 200–400 ms）。
    """

    name = "bailian:gte-rerank"

    def __init__(self, model: str | None = None):
        import requests  # noqa: PLC0415

        if not settings.bailian_api_key:
            raise RuntimeError("未配置 BAILIAN_API_KEY，无法使用百炼重排")
        self._requests = requests
        self.model = model or settings.bailian_rerank_model
        self.url = settings.bailian_rerank_url
        self.headers = {
            "Authorization": f"Bearer {settings.bailian_api_key}",
            "Content-Type": "application/json",
        }

    def _post(self, model: str, query: str, docs: list[str], top_k: int) -> dict:
        payload = {
            "model": model,
            "input": {"query": query, "documents": docs},
            "parameters": {"return_documents": False, "top_n": top_k},
        }
        r = self._requests.post(self.url, json=payload, headers=self.headers, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
        return r.json()

    def rerank(self, query: str, candidates: list[Hit], top_k: int) -> list[Hit]:
        if not candidates:
            return []
        # 重排看的是相关性，超长 chunk 截断即可
        docs = [c.content[:4000] for c in candidates]
        try:
            data = self._post(self.model, query, docs, len(candidates))
        except Exception:
            if self.model != "gte-rerank":  # 兼容只开通旧模型名的账号
                self.model = "gte-rerank"
                data = self._post(self.model, query, docs, len(candidates))
            else:
                raise

        results = (data.get("output") or {}).get("results") or []
        out: list[Hit] = []
        for item in results[:top_k]:
            idx = int(item.get("index", -1))
            if not (0 <= idx < len(candidates)):
                continue
            c = candidates[idx]
            out.append(
                Hit(
                    id=c.id,
                    score=float(item.get("relevance_score", 0.0)),
                    content=c.content,
                    heading_path=c.heading_path,
                    doc_id=c.doc_id,
                    source="rerank",
                )
            )
        return out


def get_reranker(provider: str | None = None):
    """按配置返回重排器：bailian（API）或 local（bge-reranker）。"""
    provider = (provider or settings.rerank_provider).lower()
    if provider in {"bailian", "api", "dashscope"}:
        return BailianReranker()
    if provider in {"local", "bge", "cross-encoder"}:
        return Reranker()
    raise ValueError(f"未知 RERANK_PROVIDER: {provider}")


class Retriever:
    """把向量库 + BM25 + 重排串起来。embedder / reranker 都懒加载。"""

    def __init__(
        self,
        store,
        bm25,
        embedder=None,
        reranker: Reranker | None = None,
        rewriter=None,
        chunk_to_doc: dict[str, str] | None = None,
    ):
        self.store = store
        self.bm25 = bm25
        self._embedder = embedder
        self._reranker = reranker
        self._rewriter = rewriter
        self.chunk_to_doc = chunk_to_doc or {}

    # ---------- 懒加载 ----------
    @property
    def embedder(self):
        if self._embedder is None:
            from app.rag.embed import get_embedder  # noqa: PLC0415

            self._embedder = get_embedder()
        return self._embedder

    @property
    def reranker(self):
        if self._reranker is None:
            self._reranker = get_reranker()
        return self._reranker

    # ---------- 单路召回 ----------
    def _vector_search(self, query: str, top_k: int) -> list[tuple[str, float]]:
        vec = self.embedder.encode([query])[0]
        hits = self.store.search(vec, top_k=top_k)
        return [(h["id"], h["score"]) for h in hits]

    def _bm25_search(self, query: str, top_k: int, rewrite: bool = False) -> list[tuple[str, float]]:
        """BM25 是词面匹配，中文 query 在英文语料上命中率接近 0。

        rewrite=True 时先用 LLM 把中文问题改写成英文关键词再检索（W4 实验项）。
        """
        q = query
        if rewrite:
            if self._rewriter is None:
                from app.rag.rewrite import QueryRewriter  # noqa: PLC0415

                self._rewriter = QueryRewriter()
            q = self._rewriter.rewrite(query)
        return self.bm25.search(q, top_k=top_k)

    # ---------- 主入口 ----------
    def search(
        self,
        query: str,
        mode: str = "hybrid_rerank",
        top_k: int | None = None,
        recall_k: int | None = None,
        rrf_k: int | None = None,
        rerank_in: int | None = None,
        rewrite: bool = False,
        exclude: Sequence[str] | None = None,
    ) -> list[Hit]:
        """可注入参数的检索。

        rrf_k / rerank_in / rewrite / exclude 都是给 W4 消融实验用的；
        生产调用走默认值（settings）即可。

        exclude：文档路径前缀黑名单（如 release-notes）。这类页面把每个功能都提过一遍，
        对几乎任何 query 都有表面相关性，是典型的"万能吸铁石"，会把真正答案挤下去。
        """
        if mode not in MODES:
            raise ValueError(f"mode 必须是 {MODES}")

        top_k = top_k or settings.final_topk
        recall_k = recall_k or settings.retrieve_topk
        k_rrf = rrf_k or settings.rrf_k
        n_rerank = rerank_in or settings.rerank_topk

        # 有黑名单时多召回一些，过滤后仍有足够的候选
        fetch_k = recall_k * 3 if exclude else recall_k

        def keep(ranked: list[tuple[str, float]]) -> list[tuple[str, float]]:
            if not exclude:
                return ranked
            return [(cid, s) for cid, s in ranked if not self._is_excluded(cid, exclude)]

        def keep_hits(hits: list[Hit]) -> list[Hit]:
            if not exclude:
                return hits
            return [h for h in hits if not self._is_excluded(h.id, exclude)]

        if mode == "vector":
            ranked = keep(self._vector_search(query, fetch_k))
            return self._hydrate(ranked[:top_k], "vector")

        if mode == "bm25":
            ranked = keep(self._bm25_search(query, fetch_k, rewrite=rewrite))
            return self._hydrate(ranked[:top_k], "bm25")

        vec_rank = self._vector_search(query, fetch_k)

        if rewrite:
            # 双语 BM25：改写后的英文查官方英文文档，原始中文查中文案例（cases/*）。
            # 只走英文那一路的话，中文工单案例在 BM25 上永远零命中。
            bm_en = self._bm25_search(query, fetch_k, rewrite=True)
            rankings = [vec_rank, bm_en]
            if settings.bm25_bilingual:
                bm_zh = self._bm25_search(query, fetch_k, rewrite=False)
                if bm_zh:
                    rankings.append(bm_zh)
        else:
            rankings = [vec_rank, self._bm25_search(query, fetch_k, rewrite=False)]

        fused = rrf_fuse(rankings, k=k_rrf, top_k=fetch_k)
        fused = keep(fused)

        if mode == "hybrid":
            return self._hydrate(fused[:top_k], "fused")

        candidates = keep_hits(self._hydrate(fused[:n_rerank], "fused"))
        return self.reranker.rerank(query, candidates, top_k)

    # ---------- 黑名单 ----------
    def _is_excluded(self, chunk_id: str, exclude: Sequence[str]) -> bool:
        path = self.chunk_to_doc.get(chunk_id, "")
        return any(path.startswith(p) for p in exclude)

    # ---------- 回填正文 ----------
    def _hydrate(self, ranked: list[tuple[str, float]], source: str) -> list[Hit]:
        """批量取回正文（后端无关，一次调用）。"""
        if not ranked:
            return []
        ids = [cid for cid, _ in ranked]
        rows = self.store.get_by_ids(ids)
        by_id = {r["id"]: r for r in rows}
        score_map = dict(ranked)
        out: list[Hit] = []
        for cid in ids:  # 保持召回顺序
            r = by_id.get(cid)
            if not r:
                continue
            out.append(
                Hit(
                    id=cid,
                    score=score_map.get(cid, 0.0),
                    content=r.get("content", ""),
                    heading_path=r.get("heading_path", ""),
                    doc_id=r.get("doc_id", ""),
                    source=source,
                )
            )
        return out
