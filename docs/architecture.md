# 架构与关键决策

> 这份文档回答一个问题：**为什么长这样**。
> 每个"为什么"都对应一次实测或一次踩坑，不是拍脑袋。

---

## 一、分层

```
┌──────────────────────────────────────────────────────────────┐
│  前端  Vue 3 + Vite（web/）                                    │
│  ├─ 对话区：流式追加 + Markdown 渲染                            │
│  ├─ 抽屉：执行轨迹 / 长期记忆（点按钮才出现）                    │
│  └─ 弹窗：写操作确认（由后端 interrupt 驱动，不是前端自己判断）   │
└──────────────────────────┬───────────────────────────────────┘
                           │ SSE：step / token / reset / done
┌──────────────────────────▼───────────────────────────────────┐
│  接口层  FastAPI（app/api/server.py）                          │
│  ├─ GET  /api/chat/stream    流式（前端默认）                  │
│  ├─ POST /api/chat           轮询式（自动化测试用）             │
│  ├─ POST /api/resume         中断恢复                          │
│  ├─ GET  /api/memory/{user}  长期记忆                          │
│  └─ GET  /api/health         健康检查                          │
└──────────────────────────┬───────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────┐
│  Agent 层  LangGraph StateGraph（app/agent/graph.py）          │
│                                                                │
│   router ──┬─▶ rag_search ──▶ answer ──▶ END                   │
│            └─▶ agent ⇄ tools ──▶ answer                        │
│                                                                │
│   · checkpoint：SqliteSaver（thread_id）                       │
│   · 记忆：Redis（user_id，事实 + 事件）                         │
│   · 中间件：重复调用拦截 / 观察截断 / token 预算                │
│   · 流式：contextvar 旁路 emit                                 │
└──────────────────────────┬───────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────┐
│  检索层  RAG（app/rag/）                                       │
│  ├─ chunker    结构感知切片（标题路径 breadcrumb 前缀）          │
│  ├─ bm25       jieba+正则分词；双语（英文改写 / 原始中文）       │
│  ├─ embed      百炼 text-embedding-v3，dim=1024                │
│  ├─ index      FAISS（IndexFlatIP + meta.jsonl）                │
│  ├─ retrieve   RRF(k=60) 融合 → 可选重排                       │
│  ├─ rewrite    中文 query → 英文关键词（qwen-plus，带缓存）      │
│  ├─ websearch  360 搜索兜底（Bing 备用）                        │
│  └─ cases      工单 → 案例文档 → 同一套索引                     │
└──────────────────────────┬───────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────┐
│  存储  SQLite（helpdesk.db） + FAISS 目录 + BM25 pickle         │
│  documents / chunks / tickets / customers / sla / runs / steps  │
│  eval_cases / eval_results / case_index / memories              │
└──────────────────────────────────────────────────────────────┘
```

---

## 二、九个关键决策

### ① 为什么自己做，而不是部署 Dify / RAGFlow

| 维度 | Dify/RAGFlow | 自建 |
|---|---|---|
| 上手 | 半天出 Demo | 两周 |
| 可解释性 | 一堆默认参数说不清为什么 | 每个数字都是自己测的 |
| 定制 | 要读源码、改插件 | 想改就改 |

**结论**：目标岗位考的是"能不能讲清取舍"，不是"能不能搭平台"。
所以复用它们的**设计思路**（结构感知切片、RRF、混合检索），自己实现。

### ② 向量库：Milvus → Chroma → FAISS

- **Milvus**：镜像拉取失败（网络环境限制），放弃。
- **Chroma**：能用，但在 **3,441 行**时开始静默丢写——写入返回成功，数据没进去。
  根因查了很久：**系统 TEMP 目录对 Python 不可达**，SQLite 临时文件创建失败被吞掉。
  修复方式是把 `TMP/TEMP/SQLITE_TMPDIR` 重定向到 `data/tmp`。
- **FAISS**：最终选择。索引文件 + `meta.jsonl`，零服务依赖、零网络、可 diff、可校验。

**教训**：中间件"能用"和"可靠"是两件事。**永远验证副作用，不要相信返回值。**

### ③ 切片：为什么带标题路径

一个 chunk 如果只有正文，检索时丢掉了"它属于哪一节"这个强信号。
所以每个 chunk 前面拼上 breadcrumb：

```
Troubleshooting › Windows › Docker Desktop fails to start › 3. 检查 WSL2
<正文>
```

代价是 chunk 变长约 30 token，收益是 multi-hop 题型命中率明显提升。

### ④ 混合检索：BM25 单独只有 0.217，融合后 0.733

| 配置 | hit@5 |
|---|---|
| 纯 BM25 | 0.217 |
| 纯向量 | 0.650 |
| RRF 融合 | **0.733** |

说明两路召回**互补**：向量抓语义（"起不来"≈"fails to start"），BM25 抓精确词（错误码、路径、配置项名）。

### ⑤ 重排：实测是负收益

| 配置 | hit@5 | p50 |
|---|---|---|
| E3 融合 | **0.733** | 201ms |
| E4 E3+重排 | 0.667 | 751ms |
| E9 改写+重排 | 0.700 | 918ms |

**为什么**：query 是中文，文档是英文，cross-encoder 在跨语言上打分噪声大；
重排还会把原本排对的英文文档压下去。

**这个发现比"用了重排"更有价值**——它说明我没有盲从"RAG 最佳实践"。

### ⑥ 黑名单：release-notes 是"磁铁文档"

发布日志里高频出现版本号、组件名，几乎能匹配任何技术问题，但内容是"某版本改了什么"，
对排障没有帮助。所以在检索时按 doc 路径前缀排除：

```
manuals/desktop/release-notes
manuals/desktop/previous-versions
manuals/engine/release-notes
```

**效果**：E8 → E10，hit@5 从 0.683 → 0.767。

### ⑦ 记忆分三层

| 层 | 存什么 | 载体 | 作用 |
|---|---|---|---|
| 会话状态 | 完整消息、工具调用、执行位置 | LangGraph checkpoint（thread_id） | 能接着聊 |
| 长期记忆 | 事实（等级/版本）+ 事件（建单/升级） | Redis（user_id） | 能记住你 |
| Trace | 分步日志 | SQLite runs/steps | 能解释为什么 |

**为什么不合并**：checkpoint 是给机器恢复的（结构复杂、量大），
记忆是给模型看的（精简、可注入 prompt），trace 是给人看的（可读、可回放）。
合并会让三者互相拖累。

### ⑧ interrupt 为什么必须"一次只执行一个工具"

LangGraph 的 `interrupt()` 恢复时**节点会从头重跑**。
如果 `tools` 节点一次执行 5 个工具，恢复就会重复 5 次副作用。

所以设计成：取一个 → 执行 → 返回，循环由图的边驱动。**副作用要么幂等，要么一次一个。**

### ⑨ 工单 → 知识（闭环）

真实解法躺在 `tickets` 表里，检索链路看不见，等于每次从零推理。
`resolve_ticket` 把"问题 + 解法"写成 `cases/<工单号>` 文档，走**和官方文档完全相同**的
切片 → 向量化 → 索引流程。

配套发现：**BM25 只查改写后的英文时，中文案例永远零命中**。
所以加了双语 BM25（英文改写查官方文档 + 原始中文查案例，三路 RRF）。

代价：E10 hit@5 从 0.783 → 0.767；收益：中文案例从"进不了 Top10"到 **Top-1**。
做成开关 `BM25_BILINGUAL`。

---

## 三、数据流（一次提问的完整路径）

```
1. 前端 POST/GET /api/chat/stream?question=...
2. FastAPI 起线程，set_sink(队列)
3. GraphAgent.run()
   ├─ 建 TraceRecorder（run_id 由外部指定，便于前端轮询）
   ├─ router：classify(question) → rag / agent
   ├─ [rag] rag_search
   │    ├─ Retriever.search：向量 + BM25(英) + BM25(中) → RRF
   │    ├─ 排除黑名单
   │    ├─ 若零命中 → web_search（一级降级）
   │    └─ emit step
   ├─ [agent] agent ⇄ tools
   │    ├─ LLM 决策（流式，同时累积 tool_calls）
   │    ├─ 写操作 → interrupt() → 前端弹窗 → resume
   │    └─ 中间件：重复调用拦截 / 观察截断 / token 预算
   ├─ answer
   │    ├─ _generate（有 sink 就流式 emit token）
   │    └─ 若答案自称"没找到" → reset + web_search + 重写（二级降级）
   └─ 每步 emit step + 落库 steps 表
4. SSE 推 done → 前端收尾
```

---

## 四、目录职责

| 目录 | 职责 | 不该放什么 |
|---|---|---|
| `app/agent/` | 图、工具、记忆、trace、流式 | 不要放检索算法 |
| `app/rag/` | 切片、索引、检索、改写、网页搜索 | 不要放业务工具 |
| `app/api/` | HTTP 适配（参数校验、SSE 编码） | 不要放业务逻辑 |
| `app/eval/` | 指标与执行 | — |
| `app/db/` | schema + 薄 DAO | 不要写复杂业务 SQL |
| `scripts/` | 一次性任务（抓取/建库/评测） | 不要被 app 反向依赖 |
| `web/` | 纯展示 | 不要在这里做业务判断 |

---

## 五、如果要继续做

| 方向 | 具体动作 |
|---|---|
| 并发 | 把全局锁换成按 thread 的路由 / 每请求一个 Agent 实例 |
| 评测 | 扩到 300+ 条，加"引用正确率""拒答率"两个指标 |
| 案例去重 | 结单前先检索相似案例，避免同一问题反复沉淀 |
| 结构化判断 | 二级降级改成让模型先输出 `needs_web: true/false`，比文本匹配稳 |
| 微调 | 用 trace 里的人工确认数据做偏好对，QLoRA 微调工具选择 |
