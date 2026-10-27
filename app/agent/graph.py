"""LangGraph 版 Agent：StateGraph + SqliteSaver checkpoint + interrupt 人工确认。

图结构：

    START → router ─┬─(rag)──→ rag_search → answer → END
                    └─(agent)→ agent ─┬─(有工具调用)→ tools ─┐
                                      │                     │ 还有待执行 → tools
                                      └─(无工具调用)→ END    └─ 执行完 → agent
                                                     tools 内的写操作走 interrupt()

三条设计约定（很重要，决定了能不能 resume）：
  1. **tools 节点一次只执行一个工具**。因为 interrupt 恢复时节点会从头重跑，
     一次执行一个才能保证"重跑 → 只补那一个"，不会重复副作用。
  2. **每轮 run 开始时 router 重置 steps/pending/answer**，而 messages 由 checkpoint 续接。
     这样 trace 是"每次提问一条"，会话历史是"整条 thread"。
  3. **长期记忆只在会话首次注入 system prompt**。记忆是给模型看的背景，不参与 add_messages。
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Callable, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Command, interrupt

from app.agent.llm import LLMClient, LLMReply, get_llm
from app.agent.memory import get_memory
from app.agent.stream import emit, get_sink
from app.agent.tools import TOOLS, execute, openai_tools
from app.agent.trace import StepRecord, TraceRecorder
from app.config import settings

SYSTEM_PROMPT = """你是「Docker 服务台」的资深技术支持 Agent。你的职责是用知识库和历史工单帮一线工程师解决问题，必要时创建或升级工单。

工作规则（必须遵守）：
1. **先查再答**。任何产品/报错/配置类问题，第一步必须调用 search_knowledge，不允许凭记忆回答。
2. **答案必须带出处**。引用知识库时写出文档路径（如 manuals/desktop/...）。没有检索到依据时，明确说"知识库中没有找到"，并说明你还能做什么。
3. **区分事实与推测**。检索结果里没有的内容，必须标注"这是我的推测，建议核实"。
4. **写操作要克制**。只有用户明确要求「建单/登记/记录下来」时才调 create_ticket；只有 SLA 将超时或用户明确要求时才调 escalate_ticket。
5. **不编造**。工单号、SLA 数字、客户信息必须来自工具返回，不能自己生成。
6. **回答用中文**，结构清晰，先给结论再给步骤。
7. 用户信息不足时（比如没说客户等级），先问清楚，不要自己假设。
8. **知识库没找到时不要就此拒答**：调用 web_search 去网上搜一次，
   把结果当作"旁证"补充给用户。引用网上内容必须显式写「来自网络搜索」，
   **绝不能说成知识库依据**；也不要把网上的链接伪装成内部文档路径。

涉及工单、SLA、客户等级时，必须调用 query_tickets / check_sla 取真实数据。"""

AGENT_KEYWORDS = (
    "工单", "建单", "开单", "升级", "结单", "sla", "客户", "投诉", "登记",
    "记录一下", "之前", "历史", "类似问题", "派单", "ticket", "resolve",
    # 用户对上一条"要不要建单/提交"的确认，也属于需要工具的场景
    "确认提交", "确认建单", "提交吧", "就建", "建吧", "好的，建", "同意提交",
)


def classify(question: str) -> str:
    """意图路由：能一条规则判断的事，不要浪费 LLM 调用。"""
    q = question.lower()
    return "agent" if any(k in q for k in AGENT_KEYWORDS) else "rag"


@dataclass
class RunResult:
    run_id: str
    answer: str
    route: str
    steps: list[StepRecord] = field(default_factory=list)
    elapsed_ms: int = 0
    tokens: int = 0
    status: str = "done"
    trace: TraceRecorder | None = None
    thread_id: str = ""
    interrupt: dict | None = None  # 非 None 表示暂停在人工确认


class AgentState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    question: str
    route: str
    pending: list[dict]
    steps: list[dict]
    tokens: int
    status: str
    answer: str
    seen: dict[str, int]
    user_id: str
    web_used: bool


def _kb_empty(obs: str) -> bool:
    """判断知识库是否"确实没命中"。"""
    return "没有找到相关内容" in obs or "知识库中没有找到" in obs


UNSURE_MARKERS = (
    "知识库中没有",
    "知识库里没有",
    "知识库未",
    "没有找到",
    "未找到",
    "没有相关",
    "无法回答",
    "没有覆盖",
    "资料不足",
    "没有专门",
)


def _looks_unsure(answer: str) -> bool:
    """模型在答案里表达了"我手上资料不足"。这类回答应当先补一次网络搜索再重写。

    注意：必须先把 markdown 符号去掉再匹配 —— 模型很爱写「知识库中**没有**」，
    `**` 会把「没有」和「知识库中」拆开，直接子串匹配会漏。
    """
    if not answer:
        return False
    head = answer[:800]
    # 只去掉 ASCII 的 markdown 符号，避免引入中文标点导致的编码问题
    plain = re.sub(r"[*`#>\s_~|\[\]()\"'\-]", "", head)
    return any(m in plain for m in UNSURE_MARKERS)


def _step(state: AgentState, **kw) -> dict:
    steps = list(state.get("steps") or [])
    step = {"step_no": len(steps) + 1, **kw}
    steps.append(step)
    # 推给 SSE 订阅者（没有订阅者时是空操作）
    emit("step", step)
    return {"steps": steps}


class GraphAgent:
    def __init__(
        self,
        llm: LLMClient | None = None,
        confirm: Callable[[str, dict], bool] | None = None,
        max_steps: int | None = None,
        persist_trace: bool = True,
        memory: bool = True,
    ):
        self.llm = llm or get_llm()
        # confirm 为 None 时走 interrupt()（真正的暂停-恢复）；给了回调就走回调（便于测试/自动确认）
        self.confirm = confirm
        self.max_steps = max_steps or settings.agent_max_steps
        self.persist_trace = persist_trace
        self.memory_enabled = memory
        self._saver_conn = None
        self._graph = self._build()

    # ------------------------------------------------------------------ #
    # 图构建
    # ------------------------------------------------------------------ #
    def _build(self):
        g = StateGraph(AgentState)
        g.add_node("router", self._router)
        g.add_node("rag_search", self._rag_search)
        g.add_node("answer", self._answer)
        g.add_node("agent", self._agent)
        g.add_node("tools", self._tools)

        g.add_edge(START, "router")
        g.add_conditional_edges(
            "router", lambda s: s.get("route", "rag"), {"rag": "rag_search", "agent": "agent"}
        )
        g.add_edge("rag_search", "answer")
        g.add_edge("answer", END)
        g.add_conditional_edges("agent", self._after_agent, {"tools": "tools", "end": END})
        g.add_conditional_edges("tools", self._after_tools, {"tools": "tools", "agent": "agent"})

        ckpt = Path(settings.data_dir) / "checkpoints.sqlite"
        ckpt.parent.mkdir(parents=True, exist_ok=True)
        self._saver_conn = sqlite3.connect(str(ckpt), check_same_thread=False)
        return g.compile(checkpointer=SqliteSaver(self._saver_conn))

    # ---------- 路由 ----------
    def _router(self, state: AgentState) -> dict:
        q = state["question"]
        route = state.get("route") or classify(q)

        delta: list[BaseMessage] = []
        if not state.get("messages"):
            sys_prompt = SYSTEM_PROMPT
            if self.memory_enabled:
                block = get_memory(state.get("user_id", "local")).block()
                if block:
                    sys_prompt += f"\n\n{block}"
            delta.append(SystemMessage(content=sys_prompt))
        delta.append(HumanMessage(content=q))

        # 每次提问都重置 trace 字段（会话历史由 checkpoint 续接）
        out: dict[str, Any] = {
            "messages": delta,
            "route": route,
            "pending": [],
            "steps": [],
            "tokens": 0,
            "status": "running",
            "answer": "",
            "seen": {},
        }
        out.update(_step({**state, "steps": []}, type="plan", thought=f"路由判定：{route}"))
        return out

    def _rag_search(self, state: AgentState) -> dict:
        t0 = time.time()
        obs, _ = execute("search_knowledge", {"query": state["question"], "top_k": 5})
        latency = int((time.time() - t0) * 1000)

        # 降级：知识库明确没命中 → 去网上搜一次，把结果作为"旁证"附上。
        # 必须显式标注来源，否则模型会把网上内容当官方依据引用。
        used_web = False
        if settings.web_fallback and _kb_empty(obs):
            t1 = time.time()
            web_obs, _ = execute("web_search", {"query": state["question"], "top_k": settings.web_topk})
            if not web_obs.startswith("（网页搜索失败"):
                obs = f"{obs}\n\n{web_obs}"
                used_web = True
            latency = int((time.time() - t0) * 1000)
            _ = t1

        step = _step(
            state,
            type="tool",
            thought=(
                "知识库未命中，已降级到网页搜索"
                if used_web
                else "纯知识问答，跳过 ReAct 直接检索（确定性兜底）"
            ),
            tool="search_knowledge" + (" + web_search" if used_web else ""),
            args={"query": state["question"]},
            observation=obs,
            latency_ms=latency,
        )
        return {
            **step,
            "web_used": used_web,
            "messages": [
                SystemMessage(content=f"以下是检索到的资料，请据此作答：\n\n{obs}")
            ],
        }

    def _generate(self, messages: list, tools: list | None = None) -> LLMReply:
        """统一生成入口：有订阅者就流式（含工具决策），否则一次性返回。"""
        if get_sink() is None:
            return self.llm.chat(messages, tools=tools)

        parts: list[str] = []
        for delta in self.llm.chat_stream(messages, tools=tools):
            parts.append(delta)
            emit("token", delta)
        content = "".join(parts)
        calls = list(getattr(self.llm, "last_tool_calls", []) or [])
        # 流式拿不到精确 usage，按字符数粗估（只影响界面显示）
        return LLMReply(
            content=content,
            tool_calls=calls,
            prompt_tokens=0,
            completion_tokens=max(1, len(content) // 2),
        )

    def _answer(self, state: AgentState) -> dict:
        t0 = time.time()
        messages = list(state["messages"])
        reply = self._generate(messages)
        extra_steps: list[dict] = []
        web_used = bool(state.get("web_used"))

        # 降级②：模型自己承认"知识库没有/没找到" → 去网上补一次，再重新生成。
        # 这是最贴近用户预期的一条：不是等零命中，而是模型判断"资料不够"时就搜。
        if settings.web_fallback and not web_used and _looks_unsure(reply.content):
            web_obs, _ = execute(
                "web_search", {"query": state.get("question", ""), "top_k": settings.web_topk}
            )
            if not web_obs.startswith("（网页搜索失败"):
                extra_steps.append(
                    _step(
                        state,
                        type="tool",
                        thought="模型判断知识库不足，降级到网页搜索",
                        tool="web_search",
                        args={"query": state.get("question", "")},
                        observation=web_obs,
                    )
                )
                # 已经流式推给前端的草稿要作废，通知前端清空
                emit("reset", {})
                messages = messages + [
                    SystemMessage(
                        content=(
                            "【补充：网络搜索结果】（非内部知识库，引用时必须注明来自网络搜索）\n\n"
                            + web_obs
                        )
                    ),
                    HumanMessage(content="请结合上面的网络搜索结果，重新给出完整回答。"),
                ]
                reply = self._generate(messages)
                web_used = True

        merged_steps = list(state.get("steps") or [])
        for s in extra_steps:
            merged_steps.append(s["steps"][-1])

        step = _step(
            {**state, "steps": merged_steps},
            type="answer",
            thought="基于检索结果生成回答" + ("（已补充网络搜索）" if web_used else ""),
            observation=reply.content,
            latency_ms=int((time.time() - t0) * 1000),
            tokens=reply.total_tokens,
        )
        return {
            **step,
            "messages": [AIMessage(content=reply.content)],
            "answer": reply.content,
            "status": "done",
            "web_used": web_used,
            "tokens": state.get("tokens", 0) + reply.total_tokens,
        }

    # ---------- ReAct ----------
    def _agent(self, state: AgentState) -> dict:
        if len(state.get("steps") or []) > self.max_steps * 2:
            return {"status": "max_steps", "answer": "（步数过多，已中止）"}

        t0 = time.time()
        reply = self._generate(list(state["messages"]), tools=openai_tools())
        tokens = state.get("tokens", 0) + reply.total_tokens
        latency = int((time.time() - t0) * 1000)

        if not reply.tool_calls:
            step = _step(
                state,
                type="answer",
                thought="信息足够，结束循环",
                observation=reply.content,
                latency_ms=latency,
                tokens=reply.total_tokens,
            )
            return {
                **step,
                "messages": [AIMessage(content=reply.content)],
                "answer": reply.content,
                "status": "done",
                "tokens": tokens,
            }

        ai = AIMessage(
            content=reply.content or "",
            tool_calls=[
                {"id": tc.id, "name": tc.name, "args": tc.arguments} for tc in reply.tool_calls
            ],
        )
        step = _step(
            state,
            type="plan",
            thought=(reply.content or "")[:400],
            tool=",".join(tc.name for tc in reply.tool_calls),
            args={"calls": [tc.name for tc in reply.tool_calls]},
            latency_ms=latency,
            tokens=reply.total_tokens,
        )
        return {
            **step,
            "messages": [ai],
            "pending": [
                {"id": tc.id, "name": tc.name, "args": tc.arguments} for tc in reply.tool_calls
            ],
            "tokens": tokens,
        }

    def _after_agent(self, state: AgentState) -> str:
        return "tools" if state.get("pending") else "end"

    # ---------- 工具节点：一次一个 ----------
    def _tools(self, state: AgentState) -> dict:
        pending = list(state.get("pending") or [])
        if not pending:
            return {"status": "done"}
        call = pending[0]
        rest = pending[1:]
        name, args, cid = call["name"], call["args"], call["id"]
        seen = dict(state.get("seen") or {})
        sig = f"{name}:{json.dumps(args, sort_keys=True, ensure_ascii=False)}"
        seen[sig] = seen.get(sig, 0) + 1

        # ---- 中间件①：重复调用检测 ----
        if seen[sig] >= 3:
            obs = (
                "错误：你已经用完全相同的参数调用过该工具两次，返回结果不会变化。"
                "请改用不同的查询词，或基于已有信息直接回答。"
            )
            step = _step(state, type="guard", thought="重复调用拦截", tool=name, args=args,
                         observation=obs)
            return {
                **step,
                "messages": [ToolMessage(content=obs, tool_call_id=cid)],
                "pending": rest,
                "seen": seen,
            }

        # ---- 中间件②：写操作人工确认 ----
        if name in TOOLS and TOOLS[name].write:
            if self.confirm is not None:
                allowed = self.confirm(name, args)
            else:
                # interrupt 会在此暂停并落 checkpoint，恢复时节点从头重跑、
                # 但这里会拿到 resume 值，所以只会执行一次。
                allowed = bool(interrupt({"tool": name, "args": args, "question": state.get("question", "")}))
            if not allowed:
                obs = "用户拒绝了这次写操作，未执行。请询问用户下一步。"
                step = _step(state, type="confirm", thought="写操作需人工确认", tool=name,
                             args=args, observation="❌ 用户拒绝")
                return {
                    **step,
                    "messages": [ToolMessage(content=obs, tool_call_id=cid)],
                    "pending": rest,
                    "seen": seen,
                }

        t0 = time.time()
        obs, is_write = execute(name, args)
        latency = int((time.time() - t0) * 1000)

        # 写成功 → 记一条长期事件（跨会话可回忆）
        if is_write and self.memory_enabled and not obs.startswith("错误"):
            try:
                get_memory(state.get("user_id", "local")).add_event(
                    f"{name}({args.get('ticket_id') or args.get('title') or ''}) → {obs[:80]}"
                )
            except Exception:
                pass

        step = _step(state, type="tool", thought="", tool=name, args=args,
                     observation=obs, latency_ms=latency)
        return {
            **step,
            "messages": [ToolMessage(content=obs[: settings.agent_obs_limit], tool_call_id=cid)],
            "pending": rest,
            "seen": seen,
        }

    def _after_tools(self, state: AgentState) -> str:
        return "tools" if state.get("pending") else "agent"

    # ------------------------------------------------------------------ #
    # 对外 API
    # ------------------------------------------------------------------ #
    def _config(self, session_id: str, user_id: str) -> dict:
        return {
            "configurable": {"thread_id": f"{user_id}:{session_id}"},
            "recursion_limit": self.max_steps * 4 + 10,
        }

    def run(
        self,
        question: str,
        session_id: str = "cli",
        user_id: str = "local",
        force_agent: bool = False,
        resume: bool | None = None,
        run_id: str | None = None,
    ) -> RunResult:
        trace = TraceRecorder(
            question, session_id, user_id, persist=self.persist_trace, run_id=run_id
        )
        cfg = self._config(session_id, user_id)
        t0 = time.time()

        # 记忆类工具需要知道"当前是谁"
        from app.agent.tools import set_current_user  # noqa: PLC0415

        set_current_user(user_id)

        # 首次调用传初始 state；resume 时传 Command(resume=...)
        if resume is not None:
            payload: Any = Command(resume=resume)
        else:
            payload = {
                "question": question,
                "user_id": user_id,
                "route": "agent" if force_agent else "",
                "messages": [],
                "pending": [],
                "steps": [],
                "seen": {},
            }
            # 注意：不要把 messages 置空写进 checkpoint，add_messages 会追加而非覆盖，
            # 所以这里用空列表是安全的（没有新消息）。

        try:
            out = self._graph.invoke(payload, cfg)
        except Exception as e:  # noqa: BLE001
            trace.finish(f"执行失败：{type(e).__name__}: {e}", "failed")
            return RunResult(
                run_id=trace.run_id,
                answer=f"执行失败：{type(e).__name__}: {e}",
                route="error",
                elapsed_ms=int((time.time() - t0) * 1000),
                status="failed",
                trace=trace,
            )

        steps = [StepRecord(**s) for s in (out.get("steps") or [])]
        for s in steps:
            trace.add(s)

        intr = out.get("__interrupt__")
        if intr:
            payload_info = intr[0].value if intr else {}
            trace.finish("（等待人工确认）", "need_confirm")
            return RunResult(
                run_id=trace.run_id,
                answer="",
                route=out.get("route", ""),
                steps=steps,
                elapsed_ms=int((time.time() - t0) * 1000),
                tokens=out.get("tokens", 0),
                status="need_confirm",
                trace=trace,
                thread_id=f"{user_id}:{session_id}",
                interrupt=payload_info,
            )

        answer = out.get("answer") or "（无输出）"
        trace.finish(answer, out.get("status", "done"))
        return RunResult(
            run_id=trace.run_id,
            answer=answer,
            route=out.get("route", ""),
            steps=steps,
            elapsed_ms=int((time.time() - t0) * 1000),
            tokens=out.get("tokens", 0),
            status=out.get("status", "done"),
            trace=trace,
            thread_id=f"{user_id}:{session_id}",
        )

    def history(self, session_id: str, user_id: str = "local") -> list[dict]:
        """列出某条会话的 checkpoint 快照（time travel 用）。"""
        cfg = self._config(session_id, user_id)
        out = []
        for snap in self._graph.get_state_history(cfg):
            md = snap.metadata or {}
            out.append({"step": md.get("step"), "next": list(snap.next or ()), "values": snap.values})
        return out


Agent = GraphAgent
