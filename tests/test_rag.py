"""RAG 检索链路自测（不依赖 Milvus / 模型，可随时跑）。

覆盖：
  - rrf_fuse 的数学正确性（名次融合、跨路互补、k 值影响）
  - Retriever 在 vector/bm25/hybrid 三种模式下的路由
  - 中文分词 + 中英混合 tokenize
  - chunker 的结构保持（代码块不被切断、面包屑存在）

运行：python tests/test_rag.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from app.rag.bm25 import tokenize  # noqa: E402
from app.rag.chunker import chunk_document, estimate_tokens  # noqa: E402
from app.rag.retrieve import Hit, Retriever, rrf_fuse  # noqa: E402
from app.utils.console import setup_console  # noqa: E402


# ------------------------------------------------------------------ #
# RRF
# ------------------------------------------------------------------ #
def test_rrf_prefers_doc_in_both_lists() -> None:
    vec = [("a", 0.9), ("b", 0.8), ("c", 0.7)]
    bm = [("c", 12.0), ("a", 8.0), ("d", 5.0)]
    fused = rrf_fuse([vec, bm], k=60)
    ids = [x[0] for x in fused]
    # a 在两路都靠前，应排第一
    assert ids[0] == "a", f"期望 a 第一，实际 {ids}"
    # c 在两路都在，也应靠前
    assert ids.index("c") < ids.index("b") or ids.index("c") <= 2, ids


def test_rrf_score_formula() -> None:
    fused = dict(rrf_fuse([[("x", 99.0)], [("x", 1.0)]], k=60))
    # 两路都排第 1：2 * 1/(60+1)
    assert abs(fused["x"] - 2 / 61) < 1e-9, fused["x"]


def test_rrf_ignores_score_scale() -> None:
    """向量分数 0-1、BM25 分数 0-20，尺度差异不应影响融合结果。"""
    a = [("p", 0.99), ("q", 0.5)]
    b = [("q", 20.0), ("p", 1.0)]
    fused = dict(rrf_fuse([a, b], k=60))
    assert abs(fused["p"] - fused["q"]) < 1e-12  # 名次对称 → 分数相同


def test_rrf_k_smooths() -> None:
    ranking = [("a", 1.0), ("b", 1.0)]
    small = dict(rrf_fuse([ranking], k=1))
    large = dict(rrf_fuse([ranking], k=100))
    # k 越大，首尾差距越小
    assert (small["a"] - small["b"]) > (large["a"] - large["b"])


# ------------------------------------------------------------------ #
# 分词
# ------------------------------------------------------------------ #
def test_tokenize_mixed() -> None:
    toks = tokenize("Docker Desktop 端口被占用，报 port already allocated")
    assert "docker" in toks and "port" in toks
    assert "端口" in toks or "占用" in toks
    assert all(t == t.lower() for t in toks)


def test_tokenize_keeps_code_like_tokens() -> None:
    toks = tokenize("报错 0x80370102 与 wsl --update 无关")
    assert any("0x80370102" in t for t in toks), toks


# ------------------------------------------------------------------ #
# Retriever 路由（用假 store / 假 embedder，不碰网络）
# ------------------------------------------------------------------ #
class FakeStore:
    def __init__(self) -> None:
        self.collection = "fake"
        self.rows = {
            "c1": {"id": "c1", "content": "port already allocated", "heading_path": "Network > Ports", "doc_id": "d1"},
            "c2": {"id": "c2", "content": "WSL2 backend requirements", "heading_path": "Install > WSL2", "doc_id": "d2"},
        }

    def search(self, vec, top_k=50, expr=None, output_fields=None):
        return [{"id": "c1", "score": 0.9}, {"id": "c2", "score": 0.5}][:top_k]

    def get_by_ids(self, ids):
        """重构后 Retriever 走这个接口回填正文。"""
        return [self.rows[i] for i in ids if i in self.rows]

    class _Client:
        def __init__(self, rows):
            self.rows = rows

        def query(self, collection_name, filter, output_fields, limit):
            import re

            ids = re.findall(r'"([^"]+)"', filter)
            return [self.rows[i] for i in ids if i in self.rows]

    @property
    def client(self):
        return FakeStore._Client(self.rows)


class FakeEmbedder:
    name = "fake"
    dim = 4

    def encode(self, texts, batch_size=32):
        return np.ones((len(texts), self.dim), dtype=np.float32)


class FakeBM25:
    def search(self, query, top_k=50):
        return [("c2", 9.0), ("c1", 3.0)]


def test_retriever_modes() -> None:
    r = Retriever(FakeStore(), FakeBM25(), embedder=FakeEmbedder())
    for mode in ("vector", "bm25", "hybrid"):
        hits = r.search("端口被占用", mode=mode, top_k=2)
        assert hits, f"{mode} 无结果"
        assert all(isinstance(h, Hit) for h in hits)
        assert all(h.content for h in hits), f"{mode} 没有回填正文"


def test_hybrid_merges_both_lists() -> None:
    r = Retriever(FakeStore(), FakeBM25(), embedder=FakeEmbedder())
    ids = [h.id for h in r.search("端口被占用", mode="hybrid", top_k=2, recall_k=10)]
    assert set(ids) == {"c1", "c2"}, ids


# ------------------------------------------------------------------ #
# Chunker
# ------------------------------------------------------------------ #
def test_code_fence_is_not_split() -> None:
    md = (
        "## Install\n\nRun this:\n\n```bash\ndocker pull nginx\n"
        "docker run -d -p 80:80 nginx\n```\n\nThen open localhost.\n"
    )
    res = chunk_document(md, "doc1", limit=400, overlap=80, min_tokens=1)
    for c in res.chunks:
        assert c.content.count("```") % 2 == 0, "代码围栏被切断"


def test_breadcrumb_is_prepended() -> None:
    md = "# A\n\n## B\n\n一些内容 " + "x" * 300
    res = chunk_document(md, "doc2", limit=400, overlap=80, min_tokens=1)
    assert res.chunks
    assert res.chunks[0].content.startswith("A › B"), res.chunks[0].content[:40]


def test_estimate_tokens_monotonic() -> None:
    assert estimate_tokens("") < estimate_tokens("a")
    assert estimate_tokens("中文十个字") > estimate_tokens("abc")


def test_long_doc_respects_budget_roughly() -> None:
    md = "## S\n\n" + ("句子。" * 2000)
    res = chunk_document(md, "doc3", limit=400, overlap=80, min_tokens=1)
    assert len(res.chunks) > 5
    # 允许面包屑与块边界带来的少量溢出
    assert max(c.n_tokens for c in res.chunks) < 600


def main() -> int:
    setup_console()
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"✅ {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"❌ {fn.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"❌ {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
