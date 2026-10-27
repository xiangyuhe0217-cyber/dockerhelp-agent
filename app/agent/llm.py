"""LLM 客户端（W5 Agent 用）。

自动选后端：
  1. 有 DEEPSEEK_API_KEY → DeepSeek
  2. 否则有 BAILIAN_API_KEY → 百炼 qwen（OpenAI 兼容端点）
两者都是 OpenAI 协议，所以只用一个 openai SDK。

为什么不用 LangChain：这个项目的卖点之一是"每一层都是自己写的"，
而且工具调用协议只有几十行，引框架反而看不清 trace 的每一步。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from app.config import settings


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMReply:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class LLMClient:
    def __init__(self, provider: str | None = None, model: str | None = None):
        from openai import OpenAI  # noqa: PLC0415

        provider = (provider or settings.agent_provider).lower()
        if provider == "auto":
            provider = "deepseek" if settings.deepseek_api_key else "bailian"

        if provider == "deepseek":
            if not settings.deepseek_api_key:
                raise RuntimeError("AGENT_PROVIDER=deepseek 但未配置 DEEPSEEK_API_KEY")
            self.base_url = settings.deepseek_base_url
            self.model = model or "deepseek-chat"
            api_key = settings.deepseek_api_key
        elif provider == "bailian":
            if not settings.bailian_api_key:
                raise RuntimeError("未配置 BAILIAN_API_KEY")
            self.base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
            self.model = model or settings.agent_model
            api_key = settings.bailian_api_key
        else:
            raise ValueError(f"未知 agent provider: {provider}")

        self.provider = provider
        self.client = OpenAI(api_key=api_key, base_url=self.base_url)
        self.calls = 0

    # ------------------------------------------------------------------ #
    def chat(
        self,
        messages: list,
        tools: list[dict] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMReply:
        self.calls += 1
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": to_openai_messages(messages),
            "temperature": settings.agent_temperature if temperature is None else temperature,
            "max_tokens": max_tokens or settings.agent_max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        resp = self.client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message
        usage = getattr(resp, "usage", None)

        out = LLMReply(
            content=msg.content or "",
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )
        for tc in msg.tool_calls or []:
            raw = tc.function.arguments or "{}"
            try:
                args = json.loads(raw)
            except json.JSONDecodeError:
                args = {"_raw": raw}
            out.tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))
        return out

    # ------------------------------------------------------------------ #
    def chat_stream(
        self,
        messages: list,
        tools: list[dict] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ):
        """流式生成：逐段 yield 文本，同时把 tool_calls 累积到 self.last_tool_calls。

        为什么连工具一起流：
          如果只在"确定是最终回答"时才流，就无法在流里发现工具调用——
          模型可能先输出一段文字、再调工具，我们就得回滚已推送的文本。
          统一流程：边流边累积，流完再看有没有 tool_calls，有就当决策、没有就当答案。
        """
        self.calls += 1
        self.last_tool_calls: list[ToolCall] = []
        acc: dict[int, dict] = {}

        stream = self.client.chat.completions.create(
            model=self.model,
            messages=to_openai_messages(messages),
            temperature=settings.agent_temperature if temperature is None else temperature,
            max_tokens=max_tokens or settings.agent_max_tokens,
            stream=True,
            stream_options={"include_usage": True},
            **({"tools": tools, "tool_choice": "auto"} if tools else {}),
        )
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta is None:
                continue
            if delta.content:
                yield delta.content
            for tc in getattr(delta, "tool_calls", None) or []:
                idx = getattr(tc, "index", 0) or 0
                cur = acc.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                if getattr(tc, "id", None):
                    cur["id"] = tc.id
                fn = getattr(tc, "function", None)
                if fn is not None:
                    if getattr(fn, "name", None):
                        cur["name"] = fn.name
                    if getattr(fn, "arguments", None):
                        cur["arguments"] += fn.arguments

        for idx in sorted(acc):
            raw = acc[idx]["arguments"] or "{}"
            try:
                args = json.loads(raw)
            except json.JSONDecodeError:
                args = {"_raw": raw}
            self.last_tool_calls.append(
                ToolCall(
                    id=acc[idx]["id"] or f"call_{idx}",
                    name=acc[idx]["name"],
                    arguments=args,
                )
            )


def to_openai_messages(messages: list) -> list[dict]:
    """把 LangChain 消息对象转成 OpenAI 协议 dict。

    为什么要转：LangGraph 的状态里存的是 BaseMessage 对象，
    而 openai SDK 只接受 {"role","content",...} 的 dict，
    直接传会报 422 `missing field role`。
    """
    role_map = {
        "human": "user",
        "ai": "assistant",
        "system": "system",
        "tool": "tool",
        "function": "tool",
    }
    out: list[dict] = []
    for m in messages:
        if isinstance(m, dict):
            out.append(m)
            continue

        mtype = getattr(m, "type", "") or getattr(m, "role", "")
        role = role_map.get(mtype, "user")
        content = getattr(m, "content", "") or ""

        if role == "assistant":
            item: dict[str, Any] = {"role": "assistant", "content": content or None}
            calls = getattr(m, "tool_calls", None) or []
            if calls:
                item["tool_calls"] = []
                for tc in calls:
                    if isinstance(tc, dict):
                        cid, name, args = tc.get("id"), tc.get("name"), tc.get("args")
                    else:  # pragma: no cover - 兼容对象形式
                        cid, name, args = tc.id, tc.name, tc.args
                    item["tool_calls"].append(
                        {
                            "id": cid,
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(args or {}, ensure_ascii=False),
                            },
                        }
                    )
            out.append(item)
            continue

        if role == "tool":
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": getattr(m, "tool_call_id", "") or "",
                    "content": content,
                }
            )
            continue

        out.append({"role": role, "content": content})
    return out


def get_llm() -> LLMClient:
    return LLMClient()