"""长期记忆：结构化事实 + 事件，按 user_id 隔离。

**默认后端是 Redis**（`MEMORY_BACKEND=redis`），SQLite 作为兜底实现。

为什么不用"把对话都存下来"：
  - 对话原文已经由 LangGraph checkpoint 保存（按 thread_id），再做一份就是重复；
  - 一次性检索结果会过期，记下来只会污染后续推理；
  - 真正需要跨会话记住的只有两类：
      fact  —— 用户明确陈述的事实（"我是 standard 客户"、"我们用的是 4.34"）
      event —— 工具产生的副作用（建了单 T-123、升级了 synthetic#0007）

Redis 数据结构（前缀可配，默认 `dockerhelp:mem`）：
  {prefix}:{user}:facts      HASH    fact_key -> fact_value
  {prefix}:{user}:facts:ts   ZSET    fact_key -> 更新时间戳（用于按新旧排序）
  {prefix}:{user}:events     LIST    LPUSH 最新在前，LTRIM 保留最近 100 条，TTL 90 天

读取策略：拼成一段 ≤500 token 的"记忆块"塞进 system prompt，
而不是塞进 messages —— 后者会破坏 assistant/tool_call 的配对结构。
"""

from __future__ import annotations

import time
import uuid

from app.config import settings
from app.db import dao

MAX_FACTS = 12
MAX_EVENTS = 3
EVENT_KEEP = 100
EVENT_TTL_SECONDS = 90 * 24 * 3600


# --------------------------------------------------------------------------- #
# Redis 实现（默认）
# --------------------------------------------------------------------------- #
class RedisMemoryStore:
    backend = "redis"

    def __init__(self, user_id: str = "local"):
        import redis  # noqa: PLC0415

        self.user_id = user_id
        self.client = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            password=settings.redis_password or None,
            socket_timeout=3,
            decode_responses=True,
        )
        self.client.ping()  # 连不上就直接抛，交给 get_memory 兜底
        p = settings.redis_prefix
        self.k_facts = f"{p}:{user_id}:facts"
        self.k_ts = f"{p}:{user_id}:facts:ts"
        self.k_events = f"{p}:{user_id}:events"

    # ---------- 写 ----------
    def add_fact(self, key: str, value: str, source_run: str = "") -> None:
        key = (key or "").strip()[:60]
        value = (value or "").strip()[:300]
        if not key or not value:
            return
        pipe = self.client.pipeline()
        pipe.hset(self.k_facts, key, value)
        pipe.zadd(self.k_ts, {key: time.time()})
        pipe.execute()

    def add_event(self, text: str, source_run: str = "") -> None:
        text = (text or "").strip()[:300]
        if not text:
            return
        pipe = self.client.pipeline()
        pipe.lpush(self.k_events, text)
        pipe.ltrim(self.k_events, 0, EVENT_KEEP - 1)
        pipe.expire(self.k_events, EVENT_TTL_SECONDS)
        pipe.execute()

    # ---------- 读 ----------
    def facts(self, limit: int = MAX_FACTS) -> list[dict]:
        keys = self.client.zrevrange(self.k_ts, 0, limit - 1)
        if not keys:
            return []
        values = self.client.hmget(self.k_facts, keys)
        return [
            {"key": k, "value": v} for k, v in zip(keys, values) if v is not None
        ]

    def recent_events(self, limit: int = MAX_EVENTS) -> list[dict]:
        items = self.client.lrange(self.k_events, 0, limit - 1)
        return [{"value": v, "created_at": ""} for v in items]

    def block(self) -> str:
        return _render_block(self.user_id, self.facts(), self.recent_events())

    def clear(self) -> int:
        n = len(self.client.hgetall(self.k_facts)) + self.client.llen(self.k_events)
        self.client.delete(self.k_facts, self.k_ts, self.k_events)
        return n

    def ttl_info(self) -> dict:
        """给文档/排查用：各 key 的类型与 TTL。"""
        return {
            "facts": self.client.type(self.k_facts),
            "events": self.client.type(self.k_events),
            "events_ttl": self.client.ttl(self.k_events),
        }


# --------------------------------------------------------------------------- #
# SQLite 实现（兜底，也是迁移来源）
# --------------------------------------------------------------------------- #
class MemoryStore:
    backend = "sqlite"

    def __init__(self, user_id: str = "local"):
        self.user_id = user_id
        self.conn = dao.connect(settings.db_path)
        # 老库可能还没有 memories 表（schema 是后加的），这里自愈一次
        if not dao.table_exists(self.conn, "memories"):
            dao.init_schema(self.conn)

    # ---------- 写 ----------
    def add_fact(self, key: str, value: str, source_run: str = "") -> None:
        """同 key 只保留最新一条（覆盖式），避免记忆越积越乱。"""
        key = (key or "").strip()[:60]
        value = (value or "").strip()[:300]
        if not key or not value:
            return
        self.conn.execute(
            "DELETE FROM memories WHERE user_id = ? AND kind = 'fact' AND key = ?",
            (self.user_id, key),
        )
        self.conn.execute(
            "INSERT INTO memories (id, user_id, kind, key, value, source_run) VALUES (?,?,?,?,?,?)",
            (f"m-{uuid.uuid4().hex[:10]}", self.user_id, "fact", key, value, source_run),
        )
        self.conn.commit()

    def add_event(self, text: str, source_run: str = "") -> None:
        text = (text or "").strip()[:300]
        if not text:
            return
        self.conn.execute(
            "INSERT INTO memories (id, user_id, kind, key, value, source_run) VALUES (?,?,?,?,?,?)",
            (f"m-{uuid.uuid4().hex[:10]}", self.user_id, "event", "", text, source_run),
        )
        self.conn.commit()

    # ---------- 读 ----------
    def facts(self, limit: int = MAX_FACTS) -> list[dict]:
        return dao.fetch_all(
            self.conn,
            "SELECT key, value FROM memories WHERE user_id = ? AND kind = 'fact' "
            "ORDER BY created_at DESC LIMIT ?",
            (self.user_id, limit),
        )

    def recent_events(self, limit: int = MAX_EVENTS) -> list[dict]:
        return dao.fetch_all(
            self.conn,
            "SELECT value, created_at FROM memories WHERE user_id = ? AND kind = 'event' "
            "ORDER BY created_at DESC LIMIT ?",
            (self.user_id, limit),
        )

    def block(self) -> str:
        """生成注入 system prompt 的记忆块。空记忆返回空串。"""
        return _render_block(self.user_id, self.facts(), self.recent_events())

    def clear(self) -> int:
        n = dao.fetch_one(
            self.conn,
            "SELECT COUNT(*) AS c FROM memories WHERE user_id = ?",
            (self.user_id,),
        )["c"]
        self.conn.execute("DELETE FROM memories WHERE user_id = ?", (self.user_id,))
        self.conn.commit()
        return n


# --------------------------------------------------------------------------- #
# 共用：渲染 + 工厂
# --------------------------------------------------------------------------- #
def _render_block(user_id: str, facts: list[dict], events: list[dict]) -> str:
    if not facts and not events:
        return ""
    lines = [f"【关于用户 {user_id} 的记忆】（来自历史会话，可直接使用）"]
    if facts:
        lines.append("已知事实：")
        lines += [f"- {f['key']}: {f['value']}" for f in facts]
    if events:
        lines.append("最近操作：")
        lines += [f"- {e['value']}" for e in events]
    return "\n".join(lines)


def get_memory(user_id: str = "local", backend: str | None = None):
    """按配置返回记忆后端。

    Redis 连不上时（容器没起、端口变了）自动回退到 SQLite，并打印一次警告——
    记忆层不该成为整个 Agent 的单点故障。
    """
    backend = (backend or settings.memory_backend).lower()
    if backend == "redis":
        try:
            return RedisMemoryStore(user_id)
        except Exception as e:  # noqa: BLE001
            if not settings.memory_fallback_sqlite:
                raise
            print(
                f"[memory] ⚠ Redis 不可用（{type(e).__name__}: {str(e)[:60]}），"
                f"本次回退 SQLite。检查：{settings.redis_host}:{settings.redis_port} db={settings.redis_db}"
            )
            return MemoryStore(user_id)
    return MemoryStore(user_id)
