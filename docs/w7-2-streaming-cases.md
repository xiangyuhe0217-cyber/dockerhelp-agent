# W7.1 增量改造：流式输出 · Markdown 渲染 · 抽屉式面板 · 工单案例闭环

> 本次四项改动：
> ① 回答从"一次性吐一大段"改为**逐 token 流式**；② 回答**按 Markdown 渲染**；
> ③ 轨迹/记忆改成**点按钮才展开的抽屉**，顶栏去杂；④ 新增**工单结单 → 自动进 RAG** 的闭环。

---

## 一、流式输出

### 怎么实现的

```
浏览器 EventSource
   │ GET /api/chat/stream?question=...
   ▼
FastAPI StreamingResponse（每行一个 SSE 事件）
   │ event: step   ← 图节点写 step 时顺手 emit
   │ event: token  ← LLM 流式回调 emit
   │ event: done   ← 结束，带 answer/route/tokens/interrupt
   ▼
graph 节点 ──emits──▶ contextvar sink ──▶ queue ──▶ 生成器
```

三个技术点：

1. **contextvar 做旁路**：LangGraph 节点签名固定 `(state) -> dict`，塞 sink 参数会污染整张图。
   用 `contextvars` 在线程内挂一个 sink，SSE 端点 set、结束 unset，对图零侵入。
2. **流式也要接工具调用**：只在"确定是最终回答"时才流，就无法处理"模型先说话再调工具"。
   所以 `chat_stream` 边流边累积 `tool_calls`，流完再看：有调用就是决策，没有就是答案。
3. **不维护队列状态**：事件直接透传，断开重连最多丢一次渲染——数据早已落库（steps 表）。

### 实测

```bash
.venv\Scripts\python.exe - <<'PY'
# 见 scripts/test_sse.py 思路
PY
```

实测输出：

```
[step 1] plan
[step 2] tool search_knowledge
[首个 token] 3.37s 起，开始流式输出
[step 3] answer
步骤事件: 3
token 事件: 670          ← 逐块推送
done: status=done route=rag tokens=695
```

首 token 延迟 3.4s（主要是检索 + embedding 往返），之后持续增量输出。

---

## 二、Markdown 渲染

只改前端，不动后端：

- `marked` 解析 → `DOMPurify` 消毒 → `v-html` 注入
- 样式补齐 `h1~h4 / ul / ol / code / pre / table / blockquote / hr`
- 用户消息保持纯文本（`white-space: pre-wrap`），只有助手消息走 Markdown

实测回答里的标题、列表、表格、行内代码都能正常渲染（原先是裸的 `##` 和 `**`）。

---

## 三、顶栏瘦身 + 抽屉

| 改动 | 之前 | 现在 |
|---|---|---|
| 顶栏 | 标题 + chunks 徽章 + 用户/会话输入框 | 标题 + 三个按钮（⚙身份 / 轨迹 / 记忆） |
| 轨迹 / 记忆 | 固定右侧栏，一直占 460px | **点按钮才从右侧滑出抽屉**，再点关闭 |
| 身份设置 | 常驻输入框 | 收进 ⚙ 下拉条 |

抽屉带阴影、`max-width: 90vw`，占据右侧 480px；关闭后对话区全宽。

---

## 四、工单案例闭环（新增能力）

### 你问的问题

> 用户说建工单，工单问题解决了会变成真实案例再记录进 RAG 吗？

**改造前：不会。** 工单只是 `tickets` 表里的一行数据，检索链路（FAISS + BM25）完全看不见它。
Agent 每次都要从官方文档重新推一遍，而企业内部的处置细节文档里往往没有。

**改造后：会。** 新增工具 `resolve_ticket(ticket_id, resolution)`：
1. 工单状态改为 `resolved`，写入 `resolution`
2. 把「问题描述 + 解决方案」拼成一篇 markdown 案例
3. 走**和官方文档完全相同**的切片 → 向量化 → 索引流程
4. 出处显示为 `cases/<工单号>`，可点回原始工单
5. 记录到 `case_index` 表防重复

```
resolve_ticket
  ├─ tickets: state=resolved, resolution=...
  ├─ documents: 新增一行 section='cases'
  ├─ chunks: 3 个 chunk（按标题切：总览 / 问题描述 / 解决方案）
  ├─ FAISS: upsert 这 3 个向量
  ├─ BM25: 重建索引（纯 CPU，0.5 秒）
  └─ case_index: 记录已摄取
```

### 关键配套改动：双语 BM25

改造后发现一个问题：**案例是中文，而 BM25 查询被改写成英文**，所以中文案例在 BM25 上永远零命中。
解决：hybrid 模式下同时跑两路 BM25——改写后的英文（命中官方英文文档）+ 原始中文（命中中文案例），
再和向量一起做三路 RRF。

**代价（诚实记录）**：E10 配置 hit@5 从 **0.783 → 0.767**，MRR 0.553 → 0.509（多了中文那一路的噪声）。
**收益**：中文案例从"进不了 Top10"变成 **Top-1**。

这是典型的"为了新能力牺牲一点旧指标"，所以做成了开关 `BM25_BILINGUAL=true`。

### 实测

**Agent 自主结单**（不是手动调工具）：

```
[2] plan → resolve_ticket
[3] tool → resolve_ticket({"ticket_id": "synthetic#0002", "resolution": "根因：BIOS 未开启 VT-x..."})
    观察: 工单 synthetic#0002 已结单，并沉淀为知识库案例：cases/synthetic#0002（3 个 chunk）
[4] answer
```

**结单后立刻能被检索到**：

```
query: BIOS 没开虚拟化导致 WSL2 起不来
  1. ★ 工单案例：WSL2 backend 启动报 0x80370102 › 解决方案
  2. ★ 工单案例：磁盘空间不足导致镜像无法构建 › 解决方案
  3.   Topics for Windows › Docker Desktop fails due to Virtualizat

query: synthetic#0002 那个问题怎么解决的
  1. ★ 工单案例：WSL2 backend 启动报 0x80370102
```

知识库：**2 篇案例 / 8,794 chunk**（原 8,788 + 两个案例各 3）。

---

## 五、复现步骤

### 0. 前置

```bash
cd D:\dsh_pro\dockerhelp-agent

# 服务在跑（改过代码要重启）
.venv\Scripts\python.exe scripts\serve.py --port 8000
```

### 1. 流式 + Markdown

浏览器打开 http://127.0.0.1:8000 ，输入：

```
Docker Desktop 装完一直卡在 Starting 怎么办
```

**预期**：回答**逐字出现**（不是等几秒一起蹦出来），且标题/列表/代码块是**渲染后的样子**（不是 `##` 和 `**`）。

### 2. 抽屉

- 顶栏只有：标题、⚙身份、轨迹、记忆
- 点「轨迹」→ 右侧滑出执行过程；再点关闭 → 对话区全宽
- 点「记忆」→ 看事实/事件

### 3. 工单闭环（核心验收）

```bash
# 让 Agent 结单并沉淀（--yes 自动确认写操作）
.venv\Scripts\python.exe scripts\agent_demo.py "工单 synthetic#0002 已经解决了，根因是我没在 BIOS 里开虚拟化；解法是开启 VT-x 并执行 wsl --update。帮我结单，并把解法沉淀到知识库。" --yes
```

**预期**：Trace 里出现 `resolve_ticket`，观察里写着"沉淀为知识库案例：cases/synthetic#0002（3 个 chunk）"。

然后**立刻**问同类问题验证：

```bash
.venv\Scripts\python.exe scripts\agent_demo.py "BIOS 里没开虚拟化导致 WSL2 起不来怎么解决" --no-steps
```

**预期**：回答引用的出处是 `cases/synthetic#0002`，而不是纯官方文档。

或者直接查库：

```bash
.venv\Scripts\python.exe -c "import sqlite3;c=sqlite3.connect('data/helpdesk.db');print(list(c.execute(\"select rel_path,title from documents where section='cases'\")))"
docker exec ruoyi-ai-redis redis-cli -n 1 keys "dockerhelp:*"
```

### 4. 批量沉淀历史工单（可选冷启动）

```bash
.venv\Scripts\python.exe scripts\ingest_cases.py --list            # 看哪些能摄
.venv\Scripts\python.exe scripts\ingest_cases.py --closed-limit 20 # 批量
```

### 5. 自动化验收

```bash
.venv\Scripts\python.exe scripts\test_api.py      # 9/9
.venv\Scripts\python.exe tests\test_rag.py        # 12/12
.venv\Scripts\python.exe tests\test_agent_tools.py # 9/9
```

---

## 六、这次踩的三个坑

| # | 问题 | 现象 | 修法 |
|---|---|---|---|
| 1 | **`min_tokens` 一刀切** | 结单后案例"沉淀成功"但 0 chunk，检索不到 | 整篇都很短时不丢碎片（`total >= min*3` 才丢） |
| 2 | **BM25 只有英文一路** | 中文案例永远进不了 Top10 | 双语 BM25（英文改写 + 原始中文） |
| 3 | **测试复用 thread** | 第二次跑 `check_sla` 不再被调用，假失败 | 测试用 uuid 生成 user/thread |

第 1 个最隐蔽：`resolve_ticket` 返回值里写着"0 个 chunk"却没报错，
如果只看"结单成功"就会以为功能好了。**永远要验证副作用真的可检索。**

---

## 七、自检题

1. 为什么流式要用 `contextvar` 而不是给节点加参数？如果不用 contextvar，还有别的办法吗？
2. 流式模式下如果模型先输出一段话、再调工具，前端已经显示了那段话——这算 bug 吗？你怎么处理？
3. 工单案例为什么要走"和官方文档相同的切片流程"，而不是单独建一个案例索引？
4. 双语 BM25 让 hit@5 掉了 1.6pp，你认为值不值？如果让你优化，怎么把两者都拿到？
5. `resolve_ticket` 是写操作，为什么还要 `interrupt` 确认？它和 `create_ticket` 的确认有什么区别？
