"""W1 自测（不依赖网络，可反复跑）。

覆盖：schema 可建、upsert 幂等、级联删除、合成数据确定性、meta 读写。

运行：
  python tests/test_w1.py          # 无需 pytest
  pytest tests/test_w1.py -q       # 有 pytest 时
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import dao  # noqa: E402
from app.utils.console import setup_console  # noqa: E402

EXPECTED_TABLES = {
    "meta",
    "documents",
    "tickets",
    "customers",
    "sla",
    "chunks",
    "runs",
    "steps",
    "eval_cases",
    "eval_results",
}


def _tmp_db() -> Path:
    return Path(tempfile.mkdtemp(prefix="helpdesk_test_")) / "t.db"


def test_schema_creates_all_tables() -> None:
    db = _tmp_db()
    with dao.session(db) as conn:
        dao.init_schema(conn)
        rows = dao.fetch_all(conn, "SELECT name FROM sqlite_master WHERE type='table'")
    names = {r["name"] for r in rows}
    missing = EXPECTED_TABLES - names
    assert not missing, f"缺表: {missing}"


def test_init_schema_is_idempotent() -> None:
    db = _tmp_db()
    with dao.session(db) as conn:
        dao.init_schema(conn)
        dao.init_schema(conn)  # 第二次不应报错
        assert dao.table_exists(conn, "documents")


def test_upsert_is_idempotent() -> None:
    db = _tmp_db()
    rows = [
        {
            "id": "d1",
            "rel_path": "manuals/x.md",
            "url": "u",
            "title": "t",
            "description": "",
            "section": "manuals",
            "content_md": "hello",
            "word_count": 1,
            "has_code": 0,
            "has_table": 0,
            "lang": "en",
            "sha256": "abc",
            "fetched_at": "2026-01-01",
        }
    ]
    with dao.session(db) as conn:
        dao.init_schema(conn)
        dao.upsert_many(conn, "documents", rows)
        dao.upsert_many(conn, "documents", rows)  # 再写一次
        assert dao.count(conn, "documents") == 1
        # 内容相同、id 相同 → 覆盖而非新增
        rows[0]["title"] = "new title"
        dao.upsert_many(conn, "documents", rows)
        got = dao.fetch_one(conn, "SELECT title FROM documents WHERE id='d1'")
        assert got and got["title"] == "new title"


def test_chunks_cascade_on_document_delete() -> None:
    db = _tmp_db()
    doc = {
        "id": "d1",
        "rel_path": "a.md",
        "url": "",
        "title": "t",
        "description": "",
        "section": "manuals",
        "content_md": "x",
        "word_count": 1,
        "has_code": 0,
        "has_table": 0,
        "lang": "en",
        "sha256": "s",
        "fetched_at": "2026-01-01",
    }
    chunk = {
        "id": "c1",
        "doc_id": "d1",
        "ord": 0,
        "heading_path": "A > B",
        "content": "chunk text",
        "n_tokens": 3,
        "has_code": 0,
    }
    with dao.session(db) as conn:
        dao.init_schema(conn)
        dao.upsert_many(conn, "documents", [doc])
        dao.upsert_many(conn, "chunks", [chunk])
        assert dao.count(conn, "chunks") == 1
        conn.execute("DELETE FROM documents WHERE id='d1'")
        assert dao.count(conn, "chunks") == 0, "chunks 未随 documents 级联删除"


def test_synthetic_is_deterministic() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import build_db  # noqa: PLC0415

    a = build_db.build_synthetic()
    b = build_db.build_synthetic()
    assert a == b, "同一个 seed 应产出完全一致的数据"
    customers, sla, tickets = a
    assert len(customers) == 20
    assert len(sla) == 5
    assert len(tickets) == 30
    assert all(t["is_synthetic"] == 1 for t in tickets)
    assert all(t["id"].startswith("synthetic#") for t in tickets)


def test_meta_roundtrip() -> None:
    db = _tmp_db()
    with dao.session(db) as conn:
        dao.init_schema(conn)
        dao.set_meta(conn, "docs_commit", "deadbeef")
        assert dao.get_meta(conn, "docs_commit") == "deadbeef"
        dao.set_meta(conn, "docs_commit", "cafebabe")
        assert dao.get_meta(conn, "docs_commit") == "cafebabe"
        assert dao.get_meta(conn, "nope") is None


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
