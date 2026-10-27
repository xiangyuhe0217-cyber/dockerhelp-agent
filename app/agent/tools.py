"""Agent 可调用的工具集。

设计要点：
  1. 每个工具都是**普通 Python 函数 + JSON Schema**，没有魔法；
  2. 写操作单独标记 `write=True`，由上层循环触发人工确认；
  3. 工具返回**结构化文本**（而不是 dict 直接丢给模型），让模型看到的是"可读证据"；
  4. 所有工具都有超时/异常兜底，失败时返回错误文本而不是抛异常——
     抛异常会中断整个 ReAct 循环，而模型看到报错还能自己换策略。
"""

from __future__ import annotations

import contextvars
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Callable

from app.config import settings
from app.db import dao

# 当前用户（由 Agent 在每次 run 开始前设置），供记忆类工具使用
_current_user: contextvars.ContextVar[str] = contextvars.ContextVar("current_user", default="local")


def set_current_user(user_id: str) -> None:
    _current_user.set(user_id)


def get_current_user() -> str:
    return _current_user.get()


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    fn: Callable[..., str]
    write: bool = False


# --------------------------------------------------------------------------- #
# 延迟初始化的检索器（知识库工具用）
# --------------------------------------------------------------------------- #
_retriever = None
_chunk_to_doc: dict[str, str] | None = None


def reset_retriever() -> None:
    """摄取新案例后调用：丢掉缓存的检索器，下次调用会重新加载 BM25 与映射。"""
    global _retriever, _chunk_to_doc
    _retriever = None
    _chunk_to_doc = None


def _get_retriever():
    global _retriever, _chunk_to_doc
    if _retriever is None:
        from app.rag.bm25 import BM25Index, default_bm25_path
        from app.rag.index import get_store
        from app.rag.retrieve import Retriever

        store = get_store(create=False)
        bm25 = BM25Index.load(default_bm25_path())
        if _chunk_to_doc is None:
            conn = dao.connect(settings.db_path)
            _chunk_to_doc = {
                r["id"]: r["rel_path"]
                for r in dao.fetch_all(
                    conn,
                    "SELECT c.id AS id, d.rel_path AS rel_path "
                    "FROM chunks c JOIN documents d ON c.doc_id = d.id",
                )
            }
        _retriever = Retriever(store, bm25, chunk_to_doc=_chunk_to_doc)
    return _retriever


# --------------------------------------------------------------------------- #
# 工具实现
# --------------------------------------------------------------------------- #
def search_knowledge(query: str, top_k: int = 5) -> str:
    """在 Docker 官方文档里检索，返回带出处的片段。"""
    r = _get_retriever()
    # W4 的最优配置：混合召回 + 查询改写 + 排除 release-notes
    hits = r.search(
        query,
        mode="hybrid",
        top_k=top_k,
        rewrite=True,
        exclude=settings.exclude_prefixes,
    )
    if not hits:
        return "知识库中没有找到相关内容。"
    lines = []
    for i, h in enumerate(hits, 1):
        doc = _chunk_to_doc.get(h.id, "") if _chunk_to_doc else ""
        body = " ".join(h.content.split())[:320]
        lines.append(f"[{i}] {h.heading_path or '(无标题)'}\n    出处: {doc}\n    内容: {body}")
    return "\n".join(lines)


def web_search(query: str, top_k: int = 5) -> str:
    """网页搜索兜底（知识库没有答案时用）。"""
    from app.rag.websearch import format_results, search_web  # noqa: PLC0415

    k = max(1, min(int(top_k or settings.web_topk), 8))
    resp = search_web(query, top_k=k, timeout=settings.web_timeout)
    return format_results(resp)


def query_tickets(keyword: str = "", state: str = "", limit: int = 5) -> str:
    limit = max(1, min(int(limit or 5), 20))
    conn = dao.connect(settings.db_path)
    sql = "SELECT id, title, state, labels, resolution, created_at FROM tickets WHERE 1=1"
    params: list[Any] = []
    if keyword:
        sql += " AND (title LIKE ? OR body LIKE ?)"
        kw = f"%{keyword}%"
        params += [kw, kw]
    if state:
        sql += " AND state = ?"
        params.append(state)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    rows = dao.fetch_all(conn, sql, params)
    if not rows:
        return f"没有找到匹配「{keyword or '任意'}」的工单。"
    out = [f"找到 {len(rows)} 条工单："]
    for r in rows:
        has_fix = "有解决方案" if r.get("resolution") else "无解决方案"
        out.append(
            f"- {r['id']}｜{r['title'][:70]}｜状态={r['state']}｜{has_fix}\n"
            f"  标签={r.get('labels','')[:80]}\n"
            f"  解决摘要={(r.get('resolution') or '')[:160]}"
        )
    return "\n".join(out)


def check_sla(tier: str, priority: str) -> str:
    """查客户等级 + 优先级的 SLA 承诺。"""
    conn = dao.connect(settings.db_path)
    row = dao.fetch_one(
        conn,
        "SELECT * FROM sla WHERE tier = ? AND priority = ?",
        (tier.lower().strip(), priority.upper().strip()),
    )
    if not row:
        tiers = [
            r["tier"]
            for r in dao.fetch_all(conn, "SELECT DISTINCT tier FROM sla ORDER BY tier")
        ]
        return f"没有 {tier}/{priority} 的 SLA 记录。可用等级：{tiers}；优先级：P1/P2/P3"
    return (
        f"{tier} 客户 {priority} 工单：首次响应 {row['first_response_h']} 小时内，"
        f"解决时限 {row['resolution_h']} 小时内。"
    )


def create_ticket(
    title: str, description: str = "", priority: str = "P2", customer_id: str = ""
) -> str:
    """创建工单（写操作）。"""
    conn = dao.connect(settings.db_path)
    n = dao.count(conn, "tickets") + 1
    tid = f"T-{int(time.time()) % 100000}-{n}"
    now = time.strftime("%Y-%m-%d")
    dao.upsert_many(
        conn,
        "tickets",
        [
            {
                "id": tid,
                "source": "agent",
                "number": n,
                "title": title[:200],
                "body": description[:2000],
                "labels": json.dumps([priority.lower(), customer_id or "unknown"]),
                "state": "open",
                "created_at": now,
                "closed_at": "",
                "resolution": "",
                "url": "",
                "product_version": "",
                "is_synthetic": 1,
                "sha256": "",
                "fetched_at": now,
            }
        ],
    )
    return f"已创建工单 {tid}：{title}（优先级 {priority}，客户 {customer_id or '未指定'}）"


def escalate_ticket(ticket_id: str, reason: str = "") -> str:
    """升级工单（写操作）。"""
    conn = dao.connect(settings.db_path)
    row = dao.fetch_one(
        conn, "SELECT id, title, state, labels FROM tickets WHERE id = ?", (ticket_id,)
    )
    if not row:
        return f"工单 {ticket_id} 不存在。"
    if row["state"] == "escalated":
        return f"工单 {ticket_id} 已经是升级状态，无需重复操作。"

    # 注意：不能用 SQLite 的 `labels || ?` 拼字符串，那会产出非法 JSON
    # （["a","b"]["c"]）。必须在 Python 里解析成 list 再追加。
    try:
        labels = json.loads(row.get("labels") or "[]")
        if not isinstance(labels, list):
            labels = []
    except (json.JSONDecodeError, TypeError):
        labels = []
    labels.append(f"escalated:{reason[:60]}")

    conn.execute(
        "UPDATE tickets SET state = 'escalated', labels = ? WHERE id = ?",
        (json.dumps(labels, ensure_ascii=False), ticket_id),
    )
    conn.commit()
    return f"工单 {ticket_id} 已升级。原因：{reason or '未说明'}。原状态={row['state']}"


def resolve_ticket(ticket_id: str, resolution: str = "") -> str:
    """结单 + 把「问题 + 解法」写进知识库（写操作）。

    这是闭环的关键：工单解决后不再是一行死数据，而是变成可被检索的案例文档。
    """
    conn = dao.connect(settings.db_path)
    row = dao.fetch_one(conn, "SELECT id, title, state FROM tickets WHERE id = ?", (ticket_id,))
    if not row:
        return f"工单 {ticket_id} 不存在。"
    if not (resolution or "").strip():
        return "错误：请给出解决方案（resolution），否则这条工单没有沉淀价值。"

    conn.execute(
        "UPDATE tickets SET state = 'resolved', resolution = ?, closed_at = ? WHERE id = ?",
        (resolution.strip()[:4000], time.strftime("%Y-%m-%d"), ticket_id),
    )
    conn.commit()

    from app.rag.cases import ingest_ticket_case  # 避免循环导入  # noqa: PLC0415

    res = ingest_ticket_case(ticket_id, force=True)
    if not res.get("ok"):
        return f"工单 {ticket_id} 已结单，但入库失败：{res.get('reason')}"
    return (
        f"工单 {ticket_id} 已结单，并沉淀为知识库案例："
        f"{res['rel_path']}（{res['chunks']} 个 chunk）。下次同类问题可被检索到。"
    )


# --------------------------------------------------------------------------- #
# 注册表
# --------------------------------------------------------------------------- #
def remember_fact(key: str, value: str) -> str:
    """记住用户明确陈述的事实（跨会话）。"""
    from app.agent.memory import get_memory  # 避免循环导入  # noqa: PLC0415

    key = (key or "").strip()
    value = (value or "").strip()
    if not key or not value:
        return "错误：key 和 value 都不能为空。"
    store = get_memory(get_current_user())
    store.add_fact(key, value)
    return f"已记住：{key} = {value}（用户 {store.user_id}）"


TOOLS: dict[str, Tool] = {
    "search_knowledge": Tool(
        name="search_knowledge",
        description=(
            "在企业知识库（Docker 官方文档）中检索。遇到任何产品使用、报错、配置类问题都必须先调它，"
            "不要凭记忆回答。返回带出处（文档路径）的片段。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索语句，用中文或英文都可以"},
                "top_k": {"type": "integer", "description": "返回条数，默认 5", "default": 5},
            },
            "required": ["query"],
        },
        fn=search_knowledge,
    ),
    "query_tickets": Tool(
        name="query_tickets",
        description="查询历史工单。用户提到「之前的工单」「类似问题」「别人怎么解决的」时使用。",
        parameters={
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "标题/正文关键词，留空表示不限"},
                "state": {"type": "string", "description": "open / closed / escalated，留空不限"},
                "limit": {"type": "integer", "description": "返回条数，默认 5"},
            },
            "required": [],
        },
        fn=query_tickets,
    ),
    "web_search": Tool(
        name="web_search",
        description=(
            "网页搜索。**只在 search_knowledge 明确说没有找到时**才用，"
            "用来补充官方文档没覆盖的问题。返回结果带真实链接，引用时必须注明"
            "「来自网络搜索」而不是知识库。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索词，尽量具体（含报错码/版本/系统）"},
                "top_k": {"type": "integer", "description": "返回条数，默认 5"},
            },
            "required": ["query"],
        },
        fn=web_search,
    ),
    "check_sla": Tool(
        name="check_sla",
        description="查 SLA 承诺（首次响应/解决时限）。需要判断是否超时、是否该升级时使用。",
        parameters={
            "type": "object",
            "properties": {
                "tier": {"type": "string", "description": "客户等级：basic / standard / premium"},
                "priority": {"type": "string", "description": "优先级：P1 / P2 / P3"},
            },
            "required": ["tier", "priority"],
        },
        fn=check_sla,
    ),
    "remember_fact": Tool(
        name="remember_fact",
        description=(
            "记住用户陈述的长期事实（如客户等级、使用的产品版本、团队约定）。"
            "只在这句话是稳定的背景信息时使用，不要记一次性的问题描述。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "事实的短键，如 tier / version / team"},
                "value": {"type": "string", "description": "事实内容，如 standard / 4.34"},
            },
            "required": ["key", "value"],
        },
        fn=remember_fact,
    ),
    "create_ticket": Tool(
        name="create_ticket",
        description="创建新工单。只有在用户明确要求「建单/登记/记录下来」时才调用。",
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "工单标题"},
                "description": {"type": "string", "description": "问题描述"},
                "priority": {"type": "string", "description": "P1/P2/P3，默认 P2"},
                "customer_id": {"type": "string", "description": "客户 id，如 cust-001"},
            },
            "required": ["title"],
        },
        fn=create_ticket,
        write=True,
    ),
    "escalate_ticket": Tool(
        name="escalate_ticket",
        description="升级工单到二线。只有在 SLA 即将超时或用户明确要求升级时才调用。",
        parameters={
            "type": "object",
            "properties": {
                "ticket_id": {"type": "string", "description": "要升级的工单 id"},
                "reason": {"type": "string", "description": "升级原因"},
            },
            "required": ["ticket_id"],
        },
        fn=escalate_ticket,
        write=True,
    ),
    "resolve_ticket": Tool(
        name="resolve_ticket",
        description=(
            "结单并沉淀知识：把工单的「问题 + 解决方案」写成一篇知识库案例，"
            "以后遇到同类问题就能检索到。用户说「解决了/搞定了/结单/记录下解法」时使用，"
            "必须给出具体解决方案。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "ticket_id": {"type": "string", "description": "要结单的工单 id"},
                "resolution": {
                    "type": "string",
                    "description": "解决方案/根因/处置步骤，会原样写进知识库案例",
                },
            },
            "required": ["ticket_id", "resolution"],
        },
        fn=resolve_ticket,
        write=True,
    ),
}


def openai_tools() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            },
        }
        for t in TOOLS.values()
    ]


def execute(name: str, args: dict) -> tuple[str, bool]:
    """执行工具。返回 (结果文本, 是否写操作)。异常不抛，转成错误文本给模型。"""
    tool = TOOLS.get(name)
    if tool is None:
        return f"错误：不存在名为 {name} 的工具。可用工具：{list(TOOLS)}", False
    try:
        cleaned = {k: v for k, v in args.items() if not k.startswith("_")}
        return str(tool.fn(**cleaned)), tool.write
    except TypeError as e:
        return f"错误：参数不匹配（{e}）。请检查 schema：{json.dumps(tool.parameters, ensure_ascii=False)}", tool.write
    except Exception as e:  # noqa: BLE001
        return f"错误：{type(e).__name__}: {e}", tool.write


def is_write_tool(name: str) -> bool:
    return TOOLS.get(name, Tool("", "", {}, lambda: "")).write


def extract_ticket_id(text: str) -> str:
    m = re.search(r"(T-\d+-\d+|docker/[\w-]+#\d+|synthetic#\d+)", text)
    return m.group(1) if m else ""
