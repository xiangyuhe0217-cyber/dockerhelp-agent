"""流式事件的"旁路"：让图节点能把 token / 步骤实时推给 HTTP 层。

为什么用 contextvar 而不是给每个节点传参：
  LangGraph 的节点签名是固定的 `(state) -> dict`，塞一个 sink 参数会污染整张图。
  contextvar 对线程隔离（SSE 端点在独立线程里 set_sink），用完即销毁，最省事。
"""

from __future__ import annotations

import contextvars
from typing import Any, Callable

Sink = Callable[[str, Any], None]

_sink: contextvars.ContextVar[Sink | None] = contextvars.ContextVar("stream_sink", default=None)


def set_sink(fn: Sink | None) -> None:
    _sink.set(fn)


def get_sink() -> Sink | None:
    return _sink.get()


def emit(kind: str, payload: Any) -> None:
    """kind: step | token | done | error"""
    fn = _sink.get()
    if fn is None:
        return
    try:
        fn(kind, payload)
    except Exception:
        # 推送失败绝不能影响主流程
        pass
