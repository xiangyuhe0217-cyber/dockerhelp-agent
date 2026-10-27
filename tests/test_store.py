"""向量库后端自测（不需要模型、不需要 Milvus 容器）。

用固定 seed 的随机向量验证契约：建库 / 写入 / 检索 / 按 id 取回 / 幂等 / 删除。
Chroma 必定跑；Milvus 只有在本地 19530 可连时才跑（否则跳过）。

运行：python tests/test_store.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.utils.console import setup_console  # noqa: E402

DIM = 16


def _rows(n: int = 5) -> list[dict]:
    return [
        {
            "id": f"c{i}",
            "doc_id": f"d{i % 2}",
            "ord": i,
            "heading_path": f"Sec {i}",
            "content": f"这是第 {i} 个测试 chunk",
            "n_tokens": 10 + i,
            "has_code": i % 2,
        }
        for i in range(n)
    ]


def _vecs(n: int = 5, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    m = rng.normal(size=(n, DIM)).astype(np.float32)
    m /= np.linalg.norm(m, axis=1, keepdims=True)
    return m


def test_chroma_roundtrip() -> None:
    from app.rag.index import ChromaStore

    tmp = Path(tempfile.mkdtemp(prefix="chroma_test_"))
    try:
        store = ChromaStore(path=tmp, collection="test_col", dim=DIM)
        store.create(reset=True)
        assert store.exists()
        assert store.count() == 0

        rows, vecs = _rows(5), _vecs(5)
        written = store.upsert(rows, vecs)
        assert written == 5
        assert store.count() == 5

        # 检索：用第一条自己的向量，最近邻应该是它自己
        hits = store.search(vecs[0], top_k=3)
        assert hits, "检索无结果"
        assert hits[0]["id"] == "c0", f"最近邻应为 c0，实际 {hits[0]['id']}"
        assert hits[0]["score"] > 0.9, hits[0]["score"]
        assert hits[0]["content"], "没有回填正文"

        # 按 id 取回
        got = store.get_by_ids(["c0", "c3"])
        assert {g["id"] for g in got} == {"c0", "c3"}
        assert all(g["content"] for g in got)

        # 幂等：同 id 再写一次不应变多
        store.upsert(rows, vecs)
        assert store.count() == 5, "重复 upsert 导致条数变化"

        # 删除
        store.drop()
        assert not store.exists()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_chroma_persists_across_reopen() -> None:
    from app.rag.index import ChromaStore

    tmp = Path(tempfile.mkdtemp(prefix="chroma_persist_"))
    try:
        s1 = ChromaStore(path=tmp, collection="test_persist", dim=DIM)
        s1.create(reset=True)
        s1.upsert(_rows(3), _vecs(3))
        assert s1.count() == 3

        # 重新打开（模拟进程重启）
        s2 = ChromaStore(path=tmp, collection="test_persist", dim=DIM)
        assert s2.count() == 3, "落盘后重开数据丢了"
        assert s2.search(_vecs(3)[1], top_k=1)[0]["id"] == "c1"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_milvus_optional() -> None:
    from app.rag.index import MilvusStore

    try:
        store = MilvusStore(collection="test_store_probe", dim=DIM)
        _ = store.client.list_collections()
    except Exception as e:  # noqa: BLE001
        print(f"   (跳过 Milvus：{type(e).__name__})")
        return

    store.drop()
    store.create(reset=True)
    store.upsert(_rows(3), _vecs(3))
    assert store.count() == 3
    assert store.search(_vecs(2)[2], top_k=1)[0]["id"] == "c2"
    store.drop()


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
