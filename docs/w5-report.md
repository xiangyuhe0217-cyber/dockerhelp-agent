# W5 完成报告：Agent Runtime（含复现指南）

> 目标：把"能搜到"变成"能办事"——ReAct 循环 + 5 个工具 + 人工确认 + 全链路 Trace。
> 本文档的**第二部分是给你逐条复现验收用的**，命令都能直接复制。

---

## 一、做了什么

```
用户问题
   ↓
┌─ 意图路由（确定性规则，不花 token）─────────────┐
│  纯知识问答 → 直接 RAG（1 次检索 + 1 次生成）      │
│  涉及工单/SLA/建单 → ReAct 循环（最多 8 步）       │
└──────────────────────────────────────────────┘
   ↓
LLM 决策 ⇄ 工具执行（每次写操作先人工确认）
   ↓
带出处的回答 + runs/steps 落库 + 可按 run_id 回放
```

### 五个工具

| 工具 | 类型 | 作用 | 数据来源 |
|---|---|---|---|
| `search_knowledge` | 读 | 知识库检索（用 W4 最优配置：混合+改写+排除噪声） | 8,788 chunk |
| `query_tickets` | 读 | 按关键词/状态查历史工单 | 530 条工单 |
| `check_sla` | 读 | 查 SLA 承诺 | 5 条 SLA 规则 |
| `create_ticket` | **写** | 建工单 | 需人工确认 |
| `escalate_ticket` | **写** | 升级工单 | 需人工确认 |

### 中间件（实测有效）

| 中间件 | 解决什么 | 实测效果 |
|---|---|---|
| 重复调用检测 | 模型换个词反复查同一件事 | 同签名第 3 次直接拦截 |
| 观察截断（1200 字）| 单次检索结果吃掉上万 token | — |
| token 预算（30k）| 死循环烧钱 | 超限强制收口 |
| 意图路由 | 简单问题不该走 8 步 | 纯知识题 2 步完成 |

**同一题对比**（"帮我建个 0x80370102 的工单"）：

| | 步数 | tokens | 耗时 |
|---|---|---|---|
| 加中间件前 | 6 | 17,208 | 22.8s |
| 加中间件后 | 3 | **5,138（−70%）** | 18.2s |

---

## 二、复现指南（逐条验收）

### 0. 前置检查（30 秒）

```bash
cd D:\dsh_pro\dockerhelp-agent

# 环境自检（应当全绿，Milvus 那行 ⚠️ 无所谓，我们用 FAISS）
.venv\Scripts\python.exe scripts\check_env.py

# 确认索引在位
.venv\Scripts\python.exe -c "import sys;sys.path.insert(0,'.');from app.rag.index import get_store;s=get_store(create=False);print('向量条数:', s.count())"
# 期望: 向量条数: 8788
```

**预期**：`check_env` 里 Python / 依赖 / CUDA / SQLite / .env 全 ✅。
`.env` 里必须有 `BAILIAN_API_KEY`（Agent 的 LLM 和 embedding 都用它；填 `DEEPSEEK_API_KEY` 会自动切到 DeepSeek）。

---

### 1. 单条体验·纯知识（走 RAG 兜底，不进 ReAct）

```bash
.venv\Scripts\python.exe scripts\agent_demo.py "Docker Desktop 装完之后一直卡在 Starting 起不来，怎么办？"
```

**预期看到**：
- `路由: rag`（因为不含工单/SLA 关键词）
- 2 步：`tool → search_knowledge`，然后 `answer`
- 回答里带 `manuals/desktop/...` 这样的出处
- 末行给出 `--replay run-xxx`

**验收点**：路由是 rag，而不是 agent。这说明"确定性兜底"生效了——不为一个纯问答烧 8 步。

---

### 2. 单条体验·工具编排（走 ReAct）

```bash
.venv\Scripts\python.exe scripts\agent_demo.py "一个 premium 客户的 P1 工单，SLA 要求多久内首次响应？"
```

**预期看到**：
```
路由: agent
[1] tool → check_sla({"tier": "premium", "priority": "P1"})
    观察: premium 客户 P1 工单：首次响应 1 小时内，解决时限 8 小时内。
[2] answer
最终回答: ...1 小时内首次响应，8 小时内解决...
```

**验收点**：回答里的 `1 小时 / 8 小时` 必须和数据库一致（见第 5 步的 SQL）。
模型不能编数字——这是工具调用的意义。

---

### 3. 批量剧本（一次跑完 8 个场景）

```bash
# 先看看有哪些剧本
.venv\Scripts\python.exe scripts\agent_demo.py --list

# 全跑（--yes = 写操作自动确认）
.venv\Scripts\python.exe scripts\agent_demo.py --scenarios data\scenarios.jsonl --yes
```

**8 个剧本覆盖 4 类路径**：

| id | 类型 | 考察点 | 期望路由 |
|---|---|---|---|
| s1 | 纯知识 | 走 RAG 兜底 | rag |
| s2 | SLA | 调 check_sla 取真实数字 | agent |
| s3 | 历史工单 | 调 query_tickets | agent |
| s4 | 建单（写）| 先查知识再建单 + 人工确认 | agent |
| s5 | 升级（写）| 调 escalate_ticket 改状态 | agent |
| s6 | 组合任务 | 多个工具串联（步骤最多）| agent |
| s7 | 拒答 | 知识库没有的企业信息 | rag |
| s8 | 拒答 | 知识库没有的集群状态 | rag |

> ⚠️ **要反复演示 s5，先重置工单**：`python scripts/reset_demo_ticket.py`
> 否则第二次跑会返回「已经是升级状态，无需重复操作」——这本身是**幂等性的正确表现**，
> 但看起来不像"升级成功"。

**我实测的结果**（你的数字会略有不同，取决于模型返回）：

```
id   route  steps  tokens      ms  status
s1   rag        2    1612   19854  done
s2   agent      2    2228    3203  done
s3   agent      2    2566   11637  done
s4   agent      3    5290   19904  done
s5   agent      3    2382    5134  done
s6   agent      7   12774   19407  done
s7   rag        2    1941   27179  done
s8   rag        2    1382   15572  done
```

结果同时落到 `reports/agent_runs.json`（含每个剧本的完整回答，方便你逐条读）。

**验收点**：
- `route` 列符合上表（尤其 s1/s7/s8 是 rag）
- s4/s5 的 `status` 是 `done` 且回答里有真实工单号
- 步数没有打满 8（打满说明中间件在兜底，需要看是哪一步卡住）

---

### 4. 回放任意一次执行（这是 trace 的价值）

```bash
.venv\Scripts\python.exe scripts\agent_demo.py --replay <run_id>
```

真实输出（我这次的 s5）：

```
=== 回放 run-beec357385c1 ===
问题: 工单 synthetic#0007 客户已经催了两次，帮我把这个工单升级一下，原因写「SLA 即将超时」。
状态: done  步数: 3  耗时: 2026-10-27 18:26:15

[1] tool → check_sla({"priority": "P2", "tier": "standard"})  1ms
    思考: 我需要先检查这个工单的 SLA 状态，确认是否确实即将超时，然后再进行升级操作。
    观察: standard 客户 P2 工单：首次响应 12 小时内，解决时限 72 小时内。
[2] tool → escalate_ticket({"ticket_id": "synthetic#0007", "reason": "SLA 即将超时"})  3ms
    观察: 工单 synthetic#0007 已升级。原因：SLA 即将超时。原状态=closed
[3] answer  1760ms
    观察: 工单 synthetic#0007 已成功升级...

最终回答: 工单 synthetic#0007 已成功升级，升级原因为「SLA 即将超时」。
```

**验收点**：能看清每一步的**思考 → 工具 → 参数 → 观察**。这就是 JD 里说的 trace / replay / observability。

---

### 5. 验证写操作真的落库（不要只信回答）

```bash
.venv\Scripts\python.exe -c "import sys,sqlite3;c=sqlite3.connect('data/helpdesk.db');print([r for r in c.execute(\"select id,state,title from tickets where source='agent' order by created_at desc limit 5\")])"
```

或者查升级结果：

```bash
.venv\Scripts\python.exe -c "import sys,sqlite3;c=sqlite3.connect('data/helpdesk.db');print([r for r in c.execute(\"select id,state,labels from tickets where id='synthetic#0007'\")])"
```

**预期**：`state='escalated'`，`labels` 变成合法 JSON：`["synthetic", "p3", "escalated:SLA 即将超时"]`。

> 这里修过一个真 bug：最初用 SQLite 的 `labels || ?` 拼字符串，
> 产出的是 `["synthetic","p3"]["escalated:..."]`（非法 JSON）。
> 排查过程：升级后读 labels 发现 `json.loads` 报错 → 改成在 Python 里解析 list 再追加。

**这一步最重要**：模型说"已创建"不代表真创建了。**永远用数据库验证副作用。**

---

### 6. 体验人工确认（去掉 `--yes`）

```bash
.venv\Scripts\python.exe scripts\agent_demo.py "帮我把「测试确认流程」登记成一个 P3 工单"
```

**预期**：Agent 准备调 `create_ticket` 时会停下来问：

```
  ⚠ 写操作待确认：create_ticket
    参数：{"title": "测试确认流程", "priority": "P3"}
    执行吗？[y/N]
```

- 输入 `n` → trace 里出现一条 `[N] confirm ... ❌ 用户拒绝`，模型会改口问你要不要换方式
- 输入 `y` → 正常建单

**验收点**：拒绝后**没有**产生工单（再跑第 5 步的 SQL 确认）。

---

## 三、验收清单（逐条打勾）

| # | 检查项 | 命令/位置 | 通过标准 |
|---|---|---|---|
| 1 | 环境自检 | `check_env.py` | Python/依赖/CUDA/SQLite 全 ✅ |
| 2 | 索引在位 | 上面第 0 步 | 8788 条 |
| 3 | 路由正确 | 第 1 步 | 纯知识题 `route=rag` |
| 4 | 工具取数正确 | 第 2 步 | SLA 数字与库一致 |
| 5 | 批量 8 剧本 | 第 3 步 | 全部 `done`，无打满 8 步 |
| 6 | 回放可用 | 第 4 步 | 能看到逐步 trace |
| 7 | 写操作落库 | 第 5 步 | DB 里状态真的变了、labels 是合法 JSON |
| 8 | 人工确认生效 | 第 6 步 | 拒绝后 DB 无变化 |
| 9 | 单测 | `tests/test_agent_tools.py` | 9/9 通过 |

**重复演示前**：`python scripts/reset_demo_ticket.py`（把 s5 的工单恢复成 closed）。

---

## 附：所有测试套件（一条命令全跑）

```bash
.venv\Scripts\python.exe tests\test_w1.py          # 数据层 6/6
.venv\Scripts\python.exe tests\test_rag.py         # 检索层 12/12
.venv\Scripts\python.exe tests\test_store.py       # 向量后端 3/3
.venv\Scripts\python.exe tests\test_agent_tools.py # Agent 层 9/9
```

---

## 四、三个设计要点（面试可讲）

**① 确定性兜底：不是所有问题都值得走 Agent**

`classify()` 用关键词判断意图。纯知识问答直接 RAG（2 步），只有涉及工单/SLA 才进 ReAct。
实测 s1/s7/s8 都走了 RAG，省掉 3–5 次 LLM 调用。
这正是 JD 原话「通过 LLM 或**确定性逻辑兜底**实现」。

**② 工具返回值是"给人看的证据"，不是裸 dict**

`search_knowledge` 返回的是带 `[1] 标题 / 出处 / 内容` 的文本块，不是 JSON。
因为模型对结构化文本的引用能力远好于嵌套 JSON，而且 trace 里人能直接读。

**③ 异常不抛出，转成错误文本给模型**

`execute()` 捕获所有异常，返回 `错误：xxx`。
抛异常会中断整个循环；而模型看到"参数不匹配"还能自己修正——这是 ReAct 自愈的基础。

---

## 五、已知问题

| 问题 | 影响 | 下阶段处理 |
|---|---|---|
| s1 单题耗时 28s | 回答太长（1500 token 上限）| 调小 `AGENT_MAX_TOKENS` 或加"回答不超过 300 字"约束 |
| s5 里模型自选了 standard/P2 查 SLA | 用户没说等级，模型自己猜 | 加"信息不足时先问"的 prompt 约束 |
| 多轮对话记忆没接 | 目前每次 run 独立 | W6：Memory（窗口 + 摘要 + 长期偏好） |
| 没有跨会话记忆 | 不记得用户上次问过什么 | W6 |

---

## 六、W5 学习清单（约 3 小时）

| 主题 | 时长 | 关键点 |
|---|---|---|
| ReAct 循环的本质 | 1h | 不是"更聪明的 prompt"，而是"让模型能观察工具结果再决策" |
| Function Calling 协议 | 1h | tools schema、tool_calls、tool role 消息的三段式 |
| 为什么需要中间件 | 0.5h | 重复调用、token 爆炸、死循环——harness 层的价值 |
| trace 的价值 | 0.5h | 可回放 = 能定位"第几步走偏"，这是可运维的前提 |

### 自检题

1. 为什么纯知识问题要走"确定性兜底"而不是也交给 ReAct？（提示：算一笔 token 账）
2. 中间件里"重复调用检测"为什么用 `工具名 + 参数签名` 做 key，而不是只看工具名？
3. `execute()` 为什么不把异常往上抛，而是返回错误文本？
4. 你怎么证明"Agent 说建了工单"是真的建了？（这一步怎么做才算严谨）
5. 如果模型连续 8 步都没给出答案，你会怎么处理？现在的实现是什么？
6. 写操作的人工确认，放在**工具内部**和放在**循环里**有什么区别？

---

## 七、面试话术（W5 版本）

> "Agent 层我做了三件事：
> **第一，确定性兜底**——用关键词判断意图，纯知识问答直接走 RAG，只有涉及工单/SLA 才进 ReAct，
> 实测 8 个场景里有 3 个被路由到 RAG，省掉 3–5 次模型调用。
> **第二，中间件**——第一次跑时模型连续 4 次重复检索，token 冲到 17k；
> 我加了重复调用检测、观察截断和 token 预算，同一题降到 5.1k（−70%）。
> **第三，全链路 trace**——每次运行的每一步（思考/工具/参数/观察）都落库，
> 可以按 run_id 回放。定位问题是'第几步走偏'，而不是'结果不对'。
> 另外写操作全部走人工确认，并且我坚持用数据库验证副作用——模型说'已创建'不算数。"
