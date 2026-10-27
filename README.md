# dockerhelp-agent

> 一个**可评测、可观测、能闭环**的 IT 服务台 Agent。
>
> 员工用中文问「Docker Desktop 装完起不来怎么办」→ 检索英文官方文档 + 历史工单 → 给出**带出处**的处置建议 →
> 需要时调工具查 SLA / 建工单 / 结单沉淀 → **写操作前人工确认** → 全过程留 Trace，可回放、可评测。
>
> 工单解决后**自动变成知识库案例**，下次同类问题能直接检索到——这是闭环。

---

## 30 秒看懂

```
┌────────────────────────────────────────────────────────────┐
│  Vue 3 前端（流式渲染 + Trace 抽屉 + 写操作确认弹窗）        │
└───────────────────────┬────────────────────────────────────┘
                        │ SSE（逐 token / 逐步骤）
┌───────────────────────▼────────────────────────────────────┐
│  FastAPI                                                    │
│  ├─ LangGraph 图：router → rag_search → agent → tools → answer│
│  ├─ 6 个工具：检索 / 查工单 / 查SLA / 记事实 / 建单 / 结单沉淀 │
│  ├─ 记忆：checkpoint（会话）+ Redis（长期事实）              │
│  └─ 降级：知识库没命中 → 网页搜索                            │
└───────────────────────┬────────────────────────────────────┘
                        │
┌───────────────────────▼────────────────────────────────────┐
│  检索：932 篇文档 / 8,794 chunk                              │
│  ├─ 结构感知切片（标题路径 breadcrumb）                       │
│  ├─ 向量（text-embedding-v3）+ BM25（jieba）+ RRF 融合        │
│  └─ 双语 BM25：英文改写查官方文档 + 原始中文查案例            │
└────────────────────────────────────────────────────────────┘
```

**一句话技术栈**：Python 3.11 · LangGraph · FAISS · SQLite · Redis · FastAPI · Vue 3 · 百炼（qwen-plus / embedding / rerank）

---

## 快速开始（3 条命令）

```bash
# 1. 装依赖
python -m venv .venv && .venv\Scripts\pip install -r requirements.txt

# 2. 配 key（至少 BAILIAN_API_KEY；REDIS 可选，不配自动回退 SQLite）
copy .env.example .env

# 3. 起服务
.venv\Scripts\python.exe scripts\serve.py --port 8000
```

打开 **http://127.0.0.1:8000** 即可。

> 没有现成数据？按 [`docs/w8-runbook.md`](docs/w8-runbook.md) 从零重建（抓文档 → 建库 → 切片 → 索引 → 评测），约 15 分钟。

---

## 效果（65 条中文评测集，其中 60 条有 gold 文档参与打分）

| 配置 | hit@1 | hit@5 | recall@5 | MRR | nDCG@10 | p50 |
|---|---|---|---|---|---|---|
| E1 纯向量 | 0.333 | 0.650 | 0.442 | 0.477 | 0.541 | 191ms |
| E2 纯 BM25 | 0.100 | 0.217 | 0.142 | 0.151 | 0.190 | 21ms |
| E3 向量+BM25+RRF | 0.250 | 0.733 | 0.508 | 0.436 | 0.533 | 201ms |
| E4 E3+重排 | 0.233 | 0.667 | 0.481 | 0.437 | 0.557 | 751ms |
| E8 + 查询改写 | 0.317 | 0.683 | 0.475 | 0.459 | 0.528 | 734ms |
| **E10 E8 + 排除 release-notes（最终）** | **0.333** | **0.767** | **0.533** | **0.491** | **0.567** | **215ms** |

> 完整 11 个配置见 `reports/eval_report.md`，可用 `python scripts/run_eval.py` 复现。
>
> 两个反直觉的实测结论：
> ① **重排反而降分**：E4 比 E3 的 hit@5 低 6.6 pp（跨语言 cross-encoder 噪声大），所以最终配置**不用重排**；
> ② **BM25 单独只有 0.217**，但和向量融合后到 0.733 —— 说明两路召回的**互补性**才是关键。

---

## 五个能聊的设计决策

| 决策 | 为什么 | 详见 |
|---|---|---|
| **Milvus → Chroma → FAISS** | Milvus 镜像拉不下来；Chroma 在 3,441 行时**静默丢写**（`unable to open database file`），根因是系统 TEMP 对 Python 不可达 → 最终用 FAISS（索引文件 + meta.jsonl，零依赖、可校验） | [w2](docs/w2-progress.md) |
| **不部署 Dify / RAGFlow** | 两者是"平台"，我们的目标是"能讲清每个取舍的 Agent"；直接复用会拿到一堆说不清为什么的默认参数 | [选型报告](../../AI-Agent实习_技术栈归纳与开源项目选型报告.md) |
| **跨语言不做重排** | 中文 query 与英文文档的 cross-encoder 分数噪声大，实测 hit@5 掉 5~6 pp | [w4](docs/w4-report.md) |
| **记忆分三层** | checkpoint 管"能接着聊"、Redis 管"能记住你"、runs/steps 管"能解释为什么"——合并会互相拖累 | [w6](docs/w6-report.md) |
| **工单结单自动入 RAG** | 真实解法躺在 tickets 表里检索不到，等于每次从零推理；转成 `cases/<id>` 文档走同一套索引即可 | [w7.1](docs/w7-2-streaming-cases.md) |

---

## 目录结构

```
dockerhelp-agent/
├─ app/
│  ├─ agent/          LangGraph 图、工具、记忆、trace、流式
│  ├─ api/            FastAPI（SSE + REST）
│  ├─ rag/            切片、BM25、向量、融合、改写、网页搜索、案例摄取
│  ├─ eval/           指标与评测执行
│  └─ db/             schema.sql + 薄 DAO
├─ scripts/           抓取/建库/索引/评测/服务/验收
├─ web/               Vue 3 前端（Vite）
├─ tests/             35 项单测
├─ docs/              每周报告（w1~w8）
├─ reports/           数据体检 + 评测报告
└─ data/              gitignore，可由脚本重建
```

---

## 验收（可复现）

```bash
# 单测：35 项
.venv\Scripts\python.exe tests\test_w1.py            # 6/6
.venv\Scripts\python.exe tests\test_rag.py           # 12/12
.venv\Scripts\python.exe tests\test_store.py         # 3/3
.venv\Scripts\python.exe tests\test_agent_tools.py   # 9/9
.venv\Scripts\python.exe tests\test_memory.py        # 7/7

# API 端到端（需服务在跑）
.venv\Scripts\python.exe scripts\test_api.py         # 9/9

# 场景回归（8 个剧本）
.venv\Scripts\python.exe scripts\agent_demo.py --scenarios data\scenarios.jsonl --yes --no-steps
```

一键自检：`python scripts/selfcheck.py`

---

## 数据说明（面试会被问）

- **知识库与工单主体是真实开源数据**：Docker 官方文档仓库（锁 commit）+ `docker/for-win`、`moby/moby` 的 Issues。
- **仅业务元数据为合成**：`customers` / `sla` / `synthetic#xxxx` 工单，`is_synthetic=1`，只用于演示「查 SLA」「升级工单」。
- **案例文档是运行时产生的**：`cases/<工单号>`，由 `resolve_ticket` 自动写入。
- 文档抓取锁定 commit（`data/docs_commit.txt` + `meta` 表），保证可复现。

---

## 已知限制

| 限制 | 说明 |
|---|---|
| 单进程串行执行 | 一把全局锁，一次跑一个 Agent；并发需改队列或多实例 |
| 二级网页降级靠文本匹配 | 模型换了措辞会漏判；更稳的做法是让模型输出结构化判断 |
| 事实无 TTL | 事件有 90 天过期，事实永久保留 |
| 评测集 65 条（60 有 gold） | 规模小，置信区间宽；个别 gold 路径偏窄导致指标偏保守 |

---

## 文档索引

| 周 | 内容 |
|---|---|
| [W1](docs/w1-torch-setup-and-learning.md) | 数据地基（文档/Issues 抓取、CUDA torch 断点续传） |
| [W2](docs/w2-progress.md) · [学习](docs/w2-learning.md) | 切片、embedding、向量库选型（Milvus→Chroma→FAISS）、BM25 |
| [W3](docs/w3-report.md) | 评测集构建（65 条，gold doc 路径标注） |
| [W4](docs/w4-report.md) | E1~E11 实验与调参（RRF / 改写 / 重排 / 排除 release-notes） |
| [W5](docs/w5-report.md) | Agent Runtime（ReAct、6 工具、Trace、中间件） |
| [W6](docs/w6-report.md) | LangGraph 迁移、checkpoint 记忆、Redis 长期记忆、interrupt 确认 |
| [W7](docs/w7-report.md) · [7.1](docs/w7-2-streaming-cases.md) · [7.2](docs/w7-3-fixes-websearch.md) | Vue 3 前端、流式、Markdown、工单案例闭环、网页搜索降级 |
| [W8](docs/w8-report.md) | 交付：评测报告、README、演示脚本、运行手册 |

---

## 许可

MIT
