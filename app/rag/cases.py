"""把「已解决的工单」变成知识库案例（闭环的关键一环）。

为什么需要：
  工单系统每天都在产生"真实问题 + 真实解法"，但这些解法躺在 tickets 表里，
  检索链路（FAISS + BM25）完全看不见。于是 Agent 每次都要从官方文档重新推一遍，
  而官方文档往往没有企业内部的处置细节。

  这个模块把工单**转成一篇 markdown 文档**（section=cases），走和官方文档完全相同的
  切片 → 向量化 → 索引流程，于是：
    · 下次有人问同类问题，检索能直接命中这条案例
    · 出处显示为 `cases/<工单号>`，可以点回去看原始工单

幂等：同一工单重复摄取会先删旧 chunk 再写新的，doc_id 固定为 sha1('cases/'+id)。
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from app.config import settings
from app.db import dao
from app.rag.chunker import chunk_document, estimate_tokens


def _case_doc_id(ticket_id: str) -> str:
    return hashlib.sha1(f"cases/{ticket_id}".encode()).hexdigest()[:16]


def _build_markdown(t: dict) -> str:
    labels = t.get("labels") or "[]"
    try:
        labels = "、".join(json.loads(labels)) if labels.strip().startswith("[") else labels
    except Exception:  # noqa: BLE001
        pass
    body = (t.get("body") or "").strip() or "（无详细描述）"
    res = (t.get("resolution") or "").strip() or "（未记录解决方案）"
    return "\n".join(
        [
            f"# 工单案例：{t['title']}",
            "",
            f"- 工单号：{t['id']}",
            f"- 状态：{t.get('state', '')}",
            f"- 标签：{labels or '无'}",
            f"- 产品版本：{t.get('product_version') or '未标注'}",
            "",
            "## 问题描述",
            "",
            body[:4000],
            "",
            "## 解决方案",
            "",
            res[:4000],
        ]
    )


def ingest_ticket_case(ticket_id: str, force: bool = False) -> dict:
    """把一个工单写成知识库文档并索引。返回统计信息。"""
    conn = dao.connect(settings.db_path)
    # 老库可能缺 case_index（schema 后加的），自愈一次
    if not dao.table_exists(conn, "case_index"):
        dao.init_schema(conn)

    t = dao.fetch_one(conn, "SELECT * FROM tickets WHERE id = ?", (ticket_id,))
    if not t:
        return {"ok": False, "reason": f"工单 {ticket_id} 不存在"}

    if not (t.get("resolution") or "").strip() and not force:
        return {
            "ok": False,
            "reason": "该工单没有 resolution（解决方案）。解决后才有摄取价值；确需强制可用 force=True",
        }

    md = _build_markdown(t)
    doc_id = _case_doc_id(ticket_id)
    rel_path = f"cases/{ticket_id}"
    now = time.strftime("%Y-%m-%d %H:%M:%S")

    # 1) 文档行
    doc_row = {
        "id": doc_id,
        "rel_path": rel_path,
        "url": t.get("url") or "",
        "title": f"工单案例：{t['title']}"[:200],
        "description": (t.get("resolution") or "")[:300],
        "section": "cases",
        "content_md": md,
        "word_count": estimate_tokens(md),
        "has_code": 0,
        "has_table": 1,
        "lang": "mixed",
        "sha256": hashlib.sha256(md.encode()).hexdigest(),
        "fetched_at": now,
    }
    dao.upsert_many(conn, "documents", [doc_row])

    # 2) 切片（先清旧 chunk，保证幂等）
    conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
    conn.commit()
    res = chunk_document(md, doc_id, limit=settings.chunk_size, overlap=settings.chunk_overlap)
    chunk_rows = [
        {
            "id": c.id,
            "doc_id": doc_id,
            "ord": c.ord,
            "heading_path": c.heading_path,
            "content": c.content,
            "n_tokens": c.n_tokens,
            "has_code": c.has_code,
        }
        for c in res.chunks
    ]
    if chunk_rows:
        dao.upsert_many(conn, "chunks", chunk_rows)

    # 3) 向量化 + 入向量库
    from app.rag.embed import get_embedder  # noqa: PLC0415
    from app.rag.index import get_store  # noqa: PLC0415

    embedder = get_embedder()
    vectors = embedder.encode([r["content"] for r in chunk_rows]) if chunk_rows else None
    store = get_store(create=False)
    if chunk_rows:
        store.upsert(chunk_rows, vectors)

    # 4) 重建 BM25（纯 CPU，0.5 秒；增量维护不值得）
    from app.rag.bm25 import BM25Index, default_bm25_path  # noqa: PLC0415

    all_chunks = dao.fetch_all(conn, "SELECT id, content FROM chunks")
    BM25Index.build(all_chunks).save(default_bm25_path())

    # 5) 记录已摄取
    dao.upsert_many(
        conn,
        "case_index",
        [{"ticket_id": ticket_id, "doc_id": doc_id, "chunks": len(chunk_rows), "created_at": now}],
    )

    # 6) 让检索器重新加载（否则它手里的 BM25/映射还是旧的）
    try:
        from app.agent.tools import reset_retriever  # noqa: PLC0415

        reset_retriever()
    except Exception:  # noqa: BLE001
        pass

    return {
        "ok": True,
        "ticket_id": ticket_id,
        "doc_id": doc_id,
        "chunks": len(chunk_rows),
        "rel_path": rel_path,
    }


def ingest_closed_tickets(limit: int = 50) -> list[dict]:
    """批量摄取"已关闭且有 resolution"的历史工单，用于冷启动。"""
    conn = dao.connect(settings.db_path)
    rows = dao.fetch_all(
        conn,
        "SELECT id FROM tickets WHERE is_synthetic = 0 AND resolution != '' "
        "AND state IN ('closed','resolved') ORDER BY created_at DESC LIMIT ?",
        (limit,),
    )
    out = []
    for r in rows:
        out.append(ingest_ticket_case(r["id"]))
    return out
