"""记忆层自测（不需要 LLM）。

覆盖：
  - 事实的覆盖写（同 key 只留最新）
  - 事件追加与读取顺序
  - user_id 隔离（A 的记忆不会出现在 B 的 block 里）
  - checkpoint 隔离（不同 thread 不共享会话）
  - 记忆块注入 system prompt

运行：python tests/test_memory.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agent.graph import Agent  # noqa: E402
from app.agent.llm import LLMReply  # noqa: E402
from app.agent.memory import get_memory  # noqa: E402
from app.utils.console import setup_console  # noqa: E402


def _uid(tag: str) -> str:
    return f"test-{tag}-{uuid4().hex[:6]}"


def test_fact_overwrite() -> None:
    uid = _uid("fact")
    store = get_memory(uid)
    store.clear()
    store.add_fact("tier", "basic")
    store.add_fact("tier", "premium")
    facts = store.facts()
    assert len(facts) == 1, facts
    assert facts[0]["value"] == "premium"
    store.clear()


def test_event_append_and_block() -> None:
    uid = _uid("event")
    store = get_memory(uid)
    store.clear()
    store.add_fact("tier", "standard")
    store.add_event("建单 T-1 → 已创建")
    store.add_event("升级 T-1 → 已升级")
    block = store.block()
    assert "tier: standard" in block, block
    assert "T-1" in block
    assert "最近操作" in block
    store.clear()


def test_user_isolation() -> None:
    a, b = _uid("a"), _uid("b")
    get_memory(a).clear()
    get_memory(b).clear()
    get_memory(a).add_fact("tier", "premium")
    assert "premium" not in get_memory(b).block()
    assert "premium" in get_memory(a).block()
    get_memory(a).clear()


def test_checkpoint_thread_isolation() -> None:
    """两个 thread 的会话历史不能互相污染。"""

    class EchoLLM:
        def chat(self, messages, tools=None, temperature=None, max_tokens=None):
            return LLMReply(
                content=f"我看到了 {len(messages)} 条消息", prompt_tokens=1, completion_tokens=1
            )

    def count(ans: str) -> int:
        return int(ans.split("看到了 ")[1].split(" 条")[0])

    agent = Agent(llm=EchoLLM(), persist_trace=False)
    t1, t2 = f"t-{uuid4().hex[:6]}", f"t-{uuid4().hex[:6]}"

    n1 = count(agent.run("第一句", session_id=t1, user_id="u1").answer)
    n2 = count(agent.run("第二句", session_id=t1, user_id="u1").answer)
    n3 = count(agent.run("第一句", session_id=t2, user_id="u1").answer)

    assert n2 > n1, f"同 thread 第二轮应当看到更多消息：{n1} → {n2}"
    assert n3 == n1, f"新 thread 应当与首轮一致：{n3} vs {n1}"


def test_backend_is_redis_by_default() -> None:
    """默认后端应当是 Redis；连不上会回退 SQLite 并打警告。"""
    from app.config import settings

    store = get_memory(_uid("backend"))
    assert settings.memory_backend in {"redis", "sqlite"}
    if settings.memory_backend == "redis":
        assert store.backend == "redis", f"期望 redis，实际 {store.backend}（Redis 没连上？）"


def test_redis_key_layout() -> None:
    """Redis 里应当能看到独立前缀的 key，且 facts 是 HASH、events 是 LIST。"""
    store = get_memory(_uid("layout"))
    if store.backend != "redis":
        print("   (跳过：当前不是 Redis 后端)")
        return
    store.clear()
    store.add_fact("tier", "premium")
    store.add_event("建单 T-9 → 已创建")

    client = store.client
    assert client.type(store.k_facts) == "hash"
    assert client.type(store.k_events) == "list"
    assert client.hget(store.k_facts, "tier") == "premium"
    assert store.k_facts.startswith("dockerhelp")
    ttl = client.ttl(store.k_events)
    assert ttl > 0, f"事件列表应当有过期时间，实际 ttl={ttl}"
    store.clear()
    assert client.type(store.k_facts) == "none"


def test_memory_injected_into_prompt() -> None:
    """记忆块必须出现在喂给模型的 system prompt 里。"""
    uid = _uid("inject")
    store = get_memory(uid)
    store.clear()
    store.add_fact("tier", "premium")

    captured: list[str] = []

    class SpyLLM:
        def chat(self, messages, tools=None, temperature=None, max_tokens=None):
            for m in messages:
                if getattr(m, "type", "") == "system":
                    captured.append(str(getattr(m, "content", "")))
            return LLMReply(content="ok", prompt_tokens=1, completion_tokens=1)

    agent = Agent(llm=SpyLLM(), persist_trace=False)
    agent.run("随便问一句", session_id=f"s-{uuid4().hex[:6]}", user_id=uid)
    assert captured, "没有捕获到 system prompt"
    assert "premium" in captured[0], captured[0][:300]
    store.clear()


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
