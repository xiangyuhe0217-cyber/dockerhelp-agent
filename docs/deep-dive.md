# 深挖指南：这个项目该从哪读、数据怎么看、参数怎么调、方案为什么这么选

> 目标：让你在面试里能把任意一句结论，**落到某一行代码 + 某一组数字**上。
> 全文行号基于当前仓库（commit `54b9e1f`）。

---

## 零、先给结论：从「失败的 14 道题」开始，不是从架构图开始

大多数人复习项目是从"我做了什么"开始（画架构图、背技术栈）。
**正确的顺序是从"它哪里不行"开始**——因为面试官一定问这个，而你答不出来就露馅。

我用 [`scripts/analyze_failures.py`](../scripts/analyze_failures.py) 做了自动归因
（产物：[`reports/failure_analysis.md`](../reports/failure_analysis.md)）。
E10 配置下 60 道题错 14 道：

| 类型 | 数量 | 判定规则 | 代表 | 该做什么 |
|---|---|---|---|---|
| **A. 语料缺失** | **3** | gold 文档不在库里，或题目英文关键词在语料中无同现 | q046「TLS handshake timeout」——`tls+handshake+timeout` 三词在 8,794 个 chunk 中**无一处同现** | 修正评测标注（标为不可答）或补语料 |
| **B. 有语料但没召回** | **7** | 文档在库，但 Top-10 无相关 | q054「改了 Dockerfile 镜像没变化」——`build/cache/invalidation` 存在却没进 Top-10 | 查切片位置 / 查询改写 / 词面差距 |
| **C. 召回了但排不进 Top-5** | **4** | 第 6~10 名才命中 | q001@8、q007@6、q051@6、q057@6 | 融合权重 / 重排（这里才有重排的用武之地） |

**这张表就是你的"深入起点"**。改任何参数之前，先能说清这 14 道各自属于哪类。

### 额外发现：案例库在污染主检索

量化结果（同一份归因报告）：

- 工单案例（`cases/*`）出现在 **17/60 题**的 Top-5 里；
- 这 17 次**全部是噪声**（该位置相关性为 0）；
- 对照实验 `E12`（E10 但排除 `cases/`）：

| 配置 | hit@1 | hit@5 | hit@10 | MRR | nDCG@10 |
|---|---|---|---|---|---|
| E10（含案例） | 0.333 | 0.767 | 0.833 | 0.491 | 0.567 |
| **E12（排除案例）** | **0.383** | **0.767** | **0.900** | **0.536** | **0.609** |

**结论**：案例不会导致"漏答"（hit@5 不变），但会**把更好的结果挤下去**（MRR +4.5pp、nDCG +4.2pp）。
这也修正了我之前的错误归因——E10 的 MRR 从 0.553 掉到 0.491，
其中约 4.5pp 是案例污染，**不是**全赖双语 BM25。

修法（待做）：案例不进主排序，改为「主检索为空 / 用户明确问历史工单」时的补充召回。

---

## 一、源码地图（4 条线，按面试提问顺序）

### 线 A：数据线（30 分钟看懂）

| 顺序 | 文件 | 关键位置 | 要能回答 |
|---|---|---|---|
| 1 | [`app/db/schema.sql`](../app/db/schema.sql) | 全表 | 为什么 documents 和 chunks 拆两张？`case_index` 干什么 |
| 2 | [`scripts/build_db.py`](../scripts/build_db.py) | `main` | 文档/工单/客户/SLA 怎么装配，合成数据怎么标记 |
| 3 | [`app/rag/chunker.py`](../app/rag/chunker.py) | `split_sections:55`、`pack_section:207`、`chunk_document:301` | 切片怎么保证不切断语义 |
| 4 | [`scripts/build_index.py`](../scripts/build_index.py) | `main` | 向量和 BM25 怎么落盘、怎么校验条数 |

**必看细节**：`pack_section:207` 里每个 chunk 前面拼 `breadcrumb`（`_breadcrumb:90`）——
即"标题路径 + 正文"。这是"结构感知切片"的全部实现，20 行代码，但决定了检索质量。

**另一个必看**：`chunk_document:301` 里的 `allow_drop` 逻辑（约 315-322 行）。
它是我踩坑后加的：短文档（工单案例只有 100 多 token）会被 `min_tokens=80` 一刀切全丢，
导致"结单成功但 0 chunk"。**这段是很好的面试素材**（体现你会验证副作用）。

---

### 线 B：检索线（1 小时，核心中的核心）

| 顺序 | 文件 | 关键位置 | 要能回答 |
|---|---|---|---|
| 1 | [`app/rag/bm25.py`](../app/rag/bm25.py) | `tokenize` | 中文分词 + 正则混合怎么做的，为什么不用纯 jieba |
| 2 | [`app/rag/embed.py`](../app/rag/embed.py) | `BailianEmbedder` | 维度 1024、batch、为什么不用本地 bge-m3 |
| 3 | [`app/rag/store_faiss.py`](../app/rag/store_faiss.py) | `FaissStore` | IndexFlatIP + meta.jsonl 怎么对应；为什么不用 HNSW |
| 4 | [`app/rag/retrieve.py`](../app/rag/retrieve.py) | `rrf_fuse:26`、`Retriever.search:230` | **融合公式**、完整检索流程 |
| 5 | [`app/rag/rewrite.py`](../app/rag/rewrite.py) | `QueryRewriter` | 中文→英文关键词的动机和缓存 |

**`search:230` 是整个项目最该讲清楚的函数**，流程：

```
1. 向量召回 topk=50
2. BM25 召回：改写后英文（查官方文档）
3. BM25 召回：原始中文（查工单案例）   ← bm25_bilingual
4. 三路 RRF 融合（k=60）
5. 按 doc 路径黑名单过滤（_is_excluded:302）
6. [可选] 重排
7. 水合（_hydrate:307）→ Hit 对象
```

**能白板画出这一步，等于讲清了 70% 的 RAG。**

---

### 线 C：Agent 线（1 小时）

| 顺序 | 文件 | 关键位置 | 要能回答 |
|---|---|---|---|
| 1 | [`app/agent/graph.py`](../app/agent/graph.py) | `classify:67`、`_router:187` | 为什么用规则路由而不是让 LLM 判断 |
| 2 | 同上 | `_rag_search:215`、`_answer:272` | 纯知识问答为什么跳过 ReAct |
| 3 | 同上 | `_agent:332`、`_after_agent:382` | ReAct 循环怎么收敛 |
| 4 | 同上 | `_tools:386`、`_after_tools:453` | **interrupt 在哪、为什么一次只执行一个工具** |
| 5 | 同上 | `run:465`、`_config:459` | thread_id 怎么拼、recursion_limit 怎么算 |
| 6 | [`app/agent/tools.py`](../app/agent/tools.py) | `openai_tools`、`execute` | 工具 schema 怎么生成，为什么 execute 永不抛异常 |
| 7 | [`app/agent/trace.py`](../app/agent/trace.py) | `TraceRecorder`、`safe_text` | 为什么每一步都落库 |
| 8 | [`app/agent/memory.py`](../app/agent/memory.py) | `get_memory` | 三层记忆的边界 |

**代码里最值得讲的两处**：

- `_tools:386`：**一次只取一个 tool_call 执行**。因为 `interrupt()` 恢复时节点会从头重跑，
  一次执行多个就会重复副作用。这是设计约束，不是妥协。
- `trace.py` 的 `safe_text`：从管道读进来的中文可能带代理字符，直接写库会抛
  `UnicodeEncodeError`。**外部输入永远不可信。**

---

### 线 D：评测线（40 分钟，面试加分项）

| 顺序 | 文件 | 关键位置 |
|---|---|---|
| 1 | [`app/eval/metrics.py`](../app/eval/metrics.py) | `is_relevant:16`、`hit_at_k:24`、`recall_at_k:29`、`mrr:44`、`ndcg_at_k:51` |
| 2 | [`scripts/run_eval.py`](../scripts/run_eval.py) | `CONFIGS`（E1~E11 的定义） |
| 3 | [`reports/eval_report.md`](../reports/eval_report.md) | 5 个 section |

---

## 二、评测数据：在哪、什么含义

### 1. 题目集：[`data/eval_cases.jsonl`](../data/eval_cases.jsonl)（65 行）

```json
{"id":"q001","question":"Docker Desktop 装完之后一直转圈停在 Starting 起不来",
 "type":"simple","difficulty":"easy","topic":"install",
 "gold_paths":["manuals/desktop/troubleshoot-and-support/troubleshoot",
               "manuals/desktop/troubleshoot-and-support/faqs"],
 "gold_points":[],"answerable":true}
```

| 字段 | 含义 | 面试要点 |
|---|---|---|
| `id` | q001~q065 | — |
| `type` | `simple` / `multi_hop` / `unanswerable` | 33 / 27 / 5 |
| `difficulty` | easy / medium / hard | 用于按难度拆解 |
| `topic` | install / network / storage… | 用于定位弱项板块 |
| **`gold_paths`** | **文档路径前缀**（不是文件名！） | **这是最关键的设计**：用前缀匹配，切片大小改了也不影响评测 |
| `answerable` | false 表示该题应该拒答 | 目前只统计，未参与 hit 计算 |

### 2. 结果：[`reports/eval_summary.json`](../reports/eval_summary.json)（约 740KB）

结构：

```
{ "E1": { "agg": {...}, "cases": [ {...}, ... ] },
  "E2": {...}, ... "E11": {...} }
```

单条 case 的字段（15 个）：

```
case_id, question, topic, type, difficulty,
doc_paths,      ← 本次实际召回的文档路径（对照 gold 看就知错在哪）
rel,            ← 相关性向量 [0,1,0,...]，逐条标注
hit@1, hit@5, hit@10, recall@5, recall@10, mrr, ndcg@10,
latency_ms
```

**`doc_paths` 是排错的金矿**：拿它和 `gold_paths` 一对比，立刻知道是"没召回"还是"排太后"。

### 3. 指标定义（`metrics.py`）

| 指标 | 定义 | 什么时候看它 |
|---|---|---|
| `hit@k` | Top-k 里**至少一条**相关 → 1 | 用户"能不能看到答案" |
| `recall@k` | gold 前缀被覆盖的比例 | 多跳题（要多个板块），惩罚"只中一个" |
| `mrr` | 第一个相关结果的倒数名次 | 答案排得靠不靠前 |
| `nDCG@10` | 考虑位置的排序质量 | 整体排序质量 |

**相关性判定**（`is_relevant:16`）：召回 chunk 的文档路径**以任一 gold_path 开头**即算命中。
所以 `gold_paths=["manuals/compose/how-tos"]` 会覆盖它下面所有文件。

---

## 三、读数字的三个陷阱（面试官爱抓）

1. **样本只有 60 条** → **1 条 = 1.67pp**。差值 < 3.3pp（2 条）**不要当结论**。
   所以"E6 (k=20) 和 E3 (k=60) 都是 0.733"不能说明"参数不敏感"，只能说明"在噪声范围内"。
2. **5 条不可答题不参与 hit 计算** → 分母是 60 不是 65。被问"65 条怎么是 60"要能答。
3. **latency 含网络往返** → E1 的 191ms 里主要是 embedding API 的 RTT，不是纯检索计算。
   E4 的 751ms 主要是 rerank API 往返，不代表"重排算法慢"。

---

## 四、调优手册：参数 → 方向 → 实测

| 参数 | 位置 | 调大 | 调小 | 我实测的结论 |
|---|---|---|---|---|
| `chunk_size=400` | config:106 | 上下文更完整，但一个 chunk 混多个主题 | 更精准，但可能切碎语义 | **已消融**：256/400 打平（0.750），600 掉到 0.700；见 [`reports/chunk_size_ablation.md`](../reports/chunk_size_ablation.md) |
| `chunk_overlap=80` | config:107 | 边界信息更全，索引变大 | 省空间，边界可能丢 | 同上 |
| `retrieve_topk=50` | config:108 | 召回更全，融合更慢 | 快，可能漏 | 50 与 20 未做 A/B |
| `rrf_k=60` | config:111 | 更平滑（各名次差距小） | 更看头部 | E6(k=20)=0.733，E7(k=100)=0.717 → **20~60 无差异，100 变差** |
| `exclude_paths` | config:120 | 排掉更多噪声 | — | **E8 0.683 → E10 0.767（+8.4pp）**，最划算的一刀 |
| `rewrite` | run_eval CONFIGS | — | — | E3 0.733 → E8 0.683（**-5pp**，但让中文案例可检索） |
| `rerank` | config:127 | 候选扩到 50 | 候选 20 | 无改写时 **-6.6pp**（0.733→0.667）；改写后 +1.7pp（不稳定）→ **不用** |
| `bm25_bilingual=true` | config:115 | — | — | E10 0.783→0.767（**-1.6pp**），但中文案例从 Top10 外 → Top-1 |
| `agent_max_steps=8` | config:72 | 多跳更充分 | 省 token，可能没答完 | 8 步时 s6 场景用了 11 step 仍在预算内 |
| `agent_obs_limit=1200` | config:74 | 观察更完整，但吃 token | 省 token，可能丢关键信息 | 配合压到 1200 后 token 降 70% |
| `agent_token_budget=30000` | config:75 | — | — | 超预算强制收口，防止死循环烧钱 |

### 调优的正确顺序（我踩过反的）

```
① 先查失败归因（哪类错误最多）  ← 你现在该做的
② 再清数据（黑名单 / 语料缺失 / gold 标注）     ← 收益最大、最便宜
③ 再调召回（chunk 大小、topk）
④ 再调融合（rrf_k）
⑤ 最后才是重排 / 改写这类"加组件"的方案          ← 收益最不确定，还可能倒扣
```

**为什么这个顺序**：② 是"去掉错误答案"，⑤ 是"重新排列答案"。
如果候选里根本没有正确答案，重排再强也没用（E4 就是这么倒扣的）。

---

## 五、方案对比：为什么选这个，不选那个

### ① 向量库：FAISS ✅ / Milvus ❌ / Chroma ❌

| 方案 | 优点 | 为什么没用 |
|---|---|---|
| Milvus | 生产级、HNSW、标量过滤 | 镜像拉不下来（网络环境限制），放弃 |
| Chroma | 免容器、API 简单 | **3,441 行时静默丢写**（写入返回成功、数据没进去）；根因是系统 TEMP 对 Python 不可达，SQLite 临时文件创建失败被吞 |
| **FAISS** | 零服务、零网络、索引文件可 diff | 无标量过滤（用 meta.jsonl 补） |

> 面试答法："中间件'能用'和'可靠'是两件事。Chroma 那两天教会我：**永远验证副作用，别信返回值。**"

### ② 融合：RRF ✅ / 加权求和 ❌

- 向量分是 **cosine 相似度**（0~1），BM25 分是 **无上界的词频加权**，两者**量纲不可比**。
- 加权求和要先归一化，而归一化依赖每题的分数分布（max-min 归一化对异常值敏感）。
- **RRF 只用名次不用分数**：`score = Σ 1/(k + rank)`，天然免疫量纲问题。

**这就是为什么选 RRF**——不是因为它新，是因为它**不要求分数可比**。

### ③ 重排：不用 ✅（实测负收益）

| 配置 | hit@5 | 延迟 |
|---|---|---|
| E3 无重排 | **0.733** | 201ms |
| E4 重排(候选20) | 0.667 | 751ms |
| E5 重排(候选50) | 0.683 | 1091ms |

原因：query 中文、文档英文，cross-encoder 跨语言打分噪声大，还会把原本排对的压下去。
**这是整份报告里最值钱的一个结论**——它证明你没有盲从"RAG 最佳实践"。

### ④ 查询改写：有条件用

- 收益：让英文 BM25 能命中中文提问（E8 单独看 -5pp，因为引入了语义漂移）
- 真实价值在**配合黑名单**：E8+exclude = E10 = 0.767（最高）
- 额外好处：中文案例检索

### ⑤ 切片：结构感知 ✅ / 固定窗口 ❌

固定窗口（每 400 token 切一刀）会把一个步骤说明切成两半，且丢失"这段属于哪一节"。
结构感知先按标题切 section，再在 section 内打包，**每个 chunk 带标题路径**。
代价：chunk 长约 +30 token；收益：可解释（出处能定位到小节）+ 边界完整。

### ⑥ Agent 框架：LangGraph ✅ / 手写 while ❌ / CrewAI ❌

| 需求 | 手写 while | CrewAI/AutoGen | LangGraph |
|---|---|---|---|
| 多轮工具循环 | 要自己写 | 有 | 有 |
| **断点恢复（interrupt）** | 自己存状态，很痛 | 弱 | **原生 checkpoint** |
| 快照 / 回放 | 无 | 无 | `get_state_history` |

选 LangGraph 的**唯一硬理由**：`interrupt()` + checkpoint 能让我"暂停在图中间等人确认"。
手写循环要做这件事，得把整个执行栈序列化——W6 迁移前就是手写，深有体会。

### ⑦ 记忆：Redis ✅ / SQLite ✅（兜底）/ 向量记忆 ❌

- 记的是**结构化事实**（`tier=premium`）和事件，是 KV 语义，**不需要语义检索**。
- 向量记忆（Mem0 那类）适合"记住大段偏好描述"，我们这里用不上，反而增加不确定性和依赖。
- Redis 的收益：多进程安全、TTL 一行搞定、延迟低；代价：多一个运行时依赖 → 所以保留 SQLite 兜底。

### ⑧ 前端：Vue3 ✅ / Gradio ❌

- Gradio 出界面快，但**做不到**：逐步 trace 面板、SSE 流式 + reset、写操作弹窗。
- Vue 的代价是要写构建链（还踩了 pnpm 拦截 esbuild 的坑）。

### ⑨ 整体：自建 ✅ / 部署 Dify·RAGFlow ❌

见 [`docs/architecture.md`](architecture.md)：平台能跑，但你讲不清每个默认参数为什么是那个值。
这个项目的目的就是"每个数字都是我测的"。

---

## 六、已知弱点（主动交代，比被问穿好）

| 弱点 | 影响 | 怎么补 |
|---|---|---|
| 60 条样本 | 置信区间宽，±3.3pp 以内都是噪声 | 扩到 200+ |
| `gold_paths` 偏窄 | q029（pruning.md）、q045 可能标注过窄，导致指标偏保守 | 复核标注 |
| 5 条拒答题无生成层评测 | 没有"该拒答时是否拒答"的指标 | 加 refusal accuracy |
| ~~`chunk_size` 没做消融~~ | 已补：256/400 打平（0.750），600 -5pp；400 是成本折中 | 见 [`reports/chunk_size_ablation.md`](../reports/chunk_size_ablation.md) |
| ~~默认配置与实际不符~~ | 已修：`vector_backend` 默认 faiss、`embed_provider` 默认 bailian | 已改 `config.py:89/101` |
| **案例库污染** | `cases/*` 出现在 17/60 题 Top-5 且全是噪声；排除后 MRR +4.5pp（E12） | 案例改为补充召回，不参与主排序 |
| 二级降级靠文本匹配 | 模型换措辞就漏判 | 改成结构化判断 |
| 单进程全局锁 | 不能并发 | 每请求一个 Agent 实例 |

---

## 七、一周深挖计划（每天 2 小时）

| Day | 任务 | 产出 |
|---|---|---|
| 1 | 读 `retrieve.py` 的 `search:230`，白板画出 7 步流程 | 一张手绘图 |
| 2 | 把 14 个失败题按 A/B/C 分类，逐个查 `doc_paths` vs `gold_paths` | 一张失败归因表（**最有价值**） |
| 3 | 读 `graph.py` 的 `_tools:386` + `_after_tools:453`，讲清 interrupt 为什么不能并发执行多工具 | 一段 2 分钟口述 |
| 4 | 补 `chunk_size` 消融（256/400/600），记录 hit@5 | 一张对照表，补全最大的缺口 |
| 5 | 读 `metrics.py`，手算一道题的 hit@5 / MRR / nDCG | 能徒手算指标 |
| 6 | 复述 9 组方案对比，每组给出"为什么不选另一个" | 一张对比表 |
| 7 | 修 `config.py` 的默认值 + 复核 q029/q045 的 gold 标注 | 一次提交 |

---

## 八、面试 8 问 → 直接指向代码

| 问题 | 答案锚点 |
|---|---|
| 你的切片策略？ | `chunker.py:207` pack_section + `:90` breadcrumb；`allow_drop` 那段讲短文档坑 |
| 混合检索怎么融合？ | `retrieve.py:26` rrf_fuse；为什么不用加权求和（量纲不可比） |
| 为什么不用重排？ | E3 0.733 vs E4 0.667，跨语言 CE 噪声 |
| 评测怎么保证可信？ | `metrics.py:16` 前缀匹配 + `gold_paths` 设计；60 条 → ±1.67pp |
| Agent 怎么保证写操作安全？ | `graph.py:386` 一次一工具 + interrupt；`_after_tools:453` |
| 上下文和记忆怎么区分？ | `memory.py` get_memory + `graph.py` 注入 system prompt 的位置 |
| 工具调用失败怎么处理？ | `tools.py` execute 永不抛 + 返回错误字符串给模型自愈 |
| 延迟怎么定位？ | `trace.py` 每步 latency_ms + `eval_summary.json` 的 latency 字段 |

---

## 九、下一步：已完成的三项 + 待办

**已完成（W8.1）**

1. ✅ **修 `config.py` 默认值**：`vector_backend="faiss"`、`embed_provider="bailian"`，与 `.env.example` 对齐。
2. ✅ **失败归因表**：[`reports/failure_analysis.md`](../reports/failure_analysis.md)，
   由 [`scripts/analyze_failures.py`](../scripts/analyze_failures.py) 生成（A=3 / B=7 / C=4）。
3. ✅ **chunk_size 消融**：[`reports/chunk_size_ablation.md`](../reports/chunk_size_ablation.md)
   （256/400 打平，600 -5pp），脚本可复现。

**待办（按性价比）**

1. **案例污染修复**（最高优先级）：E12 证明排除 `cases/` 后 MRR +4.5pp。
   做法：案例不进主排序，仅在「主检索为空 / 用户提到历史工单」时作为补充召回。
2. **A 类 3 题改标**：q026/q041/q050 的关键术语在语料中无同现，确认后标 `answerable=false`。
3. **B 类 7 题查切片**：用 `eval_summary.json` 的 `doc_paths` 对照 gold，逐题看为什么没召回。
