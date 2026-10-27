"""Agent 层自测（不需要 LLM、不需要网络）。

覆盖：
  - 意图路由（确定性兜底规则）
  - 四个工具的真实行为（读 DB → 写 DB → 再读验证）
  - execute() 的错误兜底（未知工具 / 参数不匹配不抛异常）
  - ReAct 循环的重复调用中间件（用假 LLM 触发）

运行：python tests/test_agent_tools.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agent.llm import LLMReply, ToolCall  # noqa: E402
from app.agent.loop import Agent, classify  # noqa: E402
from app.agent.tools import TOOLS, execute, is_write_tool  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import dao  # noqa: E402
from app.utils.console import setup_console  # noqa: E402


# --------------------------------------------------------------------------- #
# 路由
# --------------------------------------------------------------------------- #
def test_classify_pure_knowledge() -> None:
    assert classify("Docker 装完起不来怎么办") == "rag"
    assert classify("怎么限制容器内存") == "rag"


def test_classify_needs_tools() -> None:
    assert classify("帮我看下这个工单") == "agent"
    assert classify("客户 P1 工单升级") == "agent"
    assert classify("查一下 SLA 要求") == "agent"


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def test_check_sla_reads_real_data() -> None:
    out = execute("check_sla", {"tier": "premium", "priority": "P1"})[0]
    assert "1 小时" in out and "8 小时" in out, out
    bad = execute("check_sla", {"tier": "gold", "priority": "P9"})[0]
    assert "没有" in bad or "可用" in bad, bad


def test_query_tickets_finds_synthetic() -> None:
    out = execute("query_tickets", {"keyword": "0x80370102", "limit": 3})[0]
    assert "工单" in out, out


def test_create_and_escalate_roundtrip() -> None:
    out = execute(
        "create_ticket",
        {"title": "单元测试工单", "description": "测试用", "priority": "P3", "customer_id": "cust-001"},
    )[0]
    assert "已创建工单" in out, out
    tid = out.split("已创建工单 ")[1].split("：")[0].strip()

    conn = dao.connect(settings.db_path)
    row = dao.fetch_one(conn, "SELECT state FROM tickets WHERE id = ?", (tid,))
    assert row and row["state"] == "open", f"工单未落库：{row}"

    out2 = execute("escalate_ticket", {"ticket_id": tid, "reason": "单测"})[0]
    assert "已升级" in out2, out2
    row2 = dao.fetch_one(conn, "SELECT state FROM tickets WHERE id = ?", (tid,))
    assert row2["state"] == "escalated"

    # 重复升级应被拒绝而不是重复改
    out3 = execute("escalate_ticket", {"ticket_id": tid, "reason": "再升一次"})[0]
    assert "已经是升级状态" in out3, out3

    conn.execute("DELETE FROM tickets WHERE id = ?", (tid,))
    conn.commit()


def test_write_tools_are_flagged() -> None:
    assert is_write_tool("create_ticket")
    assert is_write_tool("escalate_ticket")
    assert not is_write_tool("search_knowledge")
    assert not is_write_tool("check_sla")


def test_execute_never_raises() -> None:
    msg, _ = execute("no_such_tool", {})
    assert "不存在" in msg
    msg2, _ = execute("check_sla", {"tier": "premium"})  # 缺参数
    assert "错误" in msg2, msg2


def test_search_knowledge_returns_citations() -> None:
    out = execute("search_knowledge", {"query": "端口被占用", "top_k": 3})[0]
    assert "出处" in out, out[:200]


# --------------------------------------------------------------------------- #
# ReAct 中间件：重复调用拦截
# --------------------------------------------------------------------------- #
class _RepeatLLM:
    """永远返回同一个工具调用，用来验证"第 3 次被拦"。"""

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, tools=None, temperature=None, max_tokens=None):
        self.calls += 1
        # 图里传进来的是 LangChain 消息对象，要用 getattr 而不是 dict 的 .get()
        if any(
            getattr(m, "type", "") == "tool" and "错误" in str(getattr(m, "content", ""))
            for m in messages
        ):
            return LLMReply(content="好的，我基于已有信息直接回答。", prompt_tokens=10, completion_tokens=5)
        return LLMReply(
            tool_calls=[ToolCall(id=f"c{self.calls}", name="check_sla",
                                 arguments={"tier": "basic", "priority": "P1"})],
            prompt_tokens=10,
            completion_tokens=5,
        )


def test_duplicate_tool_call_is_blocked() -> None:
    from uuid import uuid4

    # 必须用独立 thread_id：checkpoint 是持久化的，复用同一个 thread 会带上历史，
    # 导致假 LLM 一上来就看到旧消息（写测试时踩过这个坑）
    agent = Agent(llm=_RepeatLLM(), confirm=lambda n, a: True, persist_trace=False)
    res = agent.run(
        "查一下 basic 客户 P1 的 SLA",
        session_id=f"test-{uuid4().hex[:8]}",
        force_agent=True,
    )
    guard_steps = [s for s in res.steps if s.type == "guard"]
    assert guard_steps, f"没有触发重复调用拦截，steps={[s.type for s in res.steps]}"
    assert "完全相同的参数" in guard_steps[0].observation, guard_steps[0].observation
    # 被拦后应当收敛，而不是把 8 步打满
    assert len([s for s in res.steps if s.type == "tool"]) <= 2, [s.type for s in res.steps]


# --------------------------------------------------------------------------- #
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
