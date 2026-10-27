"""Agent 入口（兼容层）。

W6 起 Agent 由 LangGraph StateGraph 实现（见 `app/agent/graph.py`），
这里只做 re-export，保证 `from app.agent.loop import Agent` 的老代码继续可用。
"""

from __future__ import annotations

from app.agent.graph import Agent, GraphAgent, RunResult, classify  # noqa: F401

__all__ = ["Agent", "GraphAgent", "RunResult", "classify"]
