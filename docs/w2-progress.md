# W2 完成报告：检索链路已跑通（2026-10-25）

## 一、最终结果

| 环节 | 状态 | 实测 |
|---|---|---|
| 结构化切片 | ✅ | 396 篇 → **3,441 chunk**，中位 195 tokens，56.6% 含代码 |
| BM25 关键词索引 | ✅ | 3,441 chunk，建索引 0.2 s，3.3 MB |
| 向量索引 | ✅ | **3,441 / 3,441 全部落盘**，13.4 MB 索引 + 3.6 MB 元数据 |
| 跨语言检索 | ✅ | 中文 query 命中英文文档，语义正确 |
| 混合召回（RRF） | ✅ | 向量 + BM25 融合，RRF k=60 |
| 重排（rerank） | ⛔ 受阻 | 依赖冲突，见 §四 |
| 单元测试 | ✅ | chunker/RRF/分词/路由 **12/12**，后端 **3/3** |

**关键技术验证**（`search.py` 实测）：

```
>>> 容器里访问不到宿主机上的服务
  1. [+0.6156] Local services › Accessing host services from a sandbox   ← 语义正确
  2. [+0.6098] Local services › Accessing services in the sandbox
>>>
  1. [+0.6156] ...（同上，向量路）
  BM25: (无结果)  ← 中文词在英文语料里没有对应 token
```

---

## 二、向量库：Milvus → Chroma → FAISS 的三次转向

这部分值得写进文档，因为它完整展示了"按证据做技术决策"的过程。

### 1. Milvus 出局：镜像拉不下来

- `docker compose pull` 失败：`short read: expected 459MB but got 246MB`（层被截断）
- 直接 `docker pull` 失败：`failed to authorize ... auth.docker.io ... unexpected EOF`
- 换 `docker.m.daocloud.io` 代理，长时间无进展
- 同时另一个现实约束：**本机只剩 3.8 GB 可用内存**，而 ruoyi-ai 7 个容器还在跑

### 2. Chroma 出局：**静默丢数据**（重要发现）

按你的建议切到 Chroma，装了 `chromadb 1.5.9` → Windows 上 Rust 绑定直接报
`unable to open database file`，换路径格式（相对/绝对/正斜杠）都无效。

降到 `chromadb 0.5.23` 能用了，但写入 3,441 条只落 **1,556** 条，且**不抛异常**。
逐层排查过程：

| 实验 | 结果 | 结论 |
|---|---|---|
| 200 条小数据 | 200/200 ✅ | 小规模正常 |
| 1,000 条 × 1024 维 | 1000/1000 ✅ | 中规模正常 |
| 3,441 条 `upsert` | 1556/3441 ❌ | 大规模丢写 |
| 3,441 条 `add` | 608/3441 ❌ | 换 API 也一样 |
| 加校验补漏循环 | 1556→1957→2323→2583 | 能收敛但每轮都丢，且要重复调 API |
| 独立脚本 `add` 3,441 | **3441/3441 ✅** | 单跑没问题 → 与建库流程有交互 |

后台线程报 `Exception occurred invoking consumer ... unable to open database file`，
但**主线程不报错**，所以业务代码完全感知不到，只是数据悄悄少了。
这种"静默丢失"比报错危险得多——如果没加校验，我会以为索引建好了。

### 3. FAISS 胜出

`faiss-cpu 1.15.1`，纯进程内、无后台消费者：
- 索引 13.4 MB + 元数据 3.6 MB，两个文件
- **3,441 / 3,441 一次通过校验**
- 检索耗时：向量 1.5 s（几乎全是 embedding API 延迟）、混合 0.2 s

> **代码里三个后端都保留**：`VECTOR_BACKEND=faiss|chroma|milvus` 一行切换。
> 抽象层是 `app/rag/index.py` 的 `VectorStore` 契约 + `get_store()` 工厂。
> 换后端不影响任何上层代码，这也是当初做抽象的回报。

---

## 三、BM25 的结论（已在真实链路中确认）

之前只是分词层面的推测，现在是端到端实测：

| query | 向量 | BM25 | 混合 |
|---|---|---|---|
| `容器里访问不到宿主机上的服务` | ✅ 命中正确 | ❌ 无结果 | = 向量（BM25 无贡献） |
| `磁盘空间被占满了怎么清理` | ✅ 命中 volume 相关 | ❌ 无结果 | = 向量 |
| `port already allocated` | ✅ Publishing to ephemeral ports | ✅ Ports（+10.27） | ✅ 两路都命中 |

**结论**：BM25 只在**英文/错误码/命令**类查询上有效；中文语义查询完全靠向量。
这意味着：
- 评测集 60 条全是中文 → E3（混合）相比 E1（纯向量）的提升会很小
- 想讲"混合检索的价值"，必须加**查询改写（中文→英文关键词）**，或者专门设计一组英文/错误码查询做对照

这会是 W3 评测里一个很有意思的结论 —— **"混合检索在跨语言场景下可能不成立"**，
比无脑说"混合更好"有价值得多。

---

## 四、唯一未完成项：rerank

`ImportError: cannot import name 'ResolvedRevision' from 'huggingface_hub'`

原因链：`chromadb 0.5.23` 依赖旧版 `huggingface-hub`，把版本降到 0.36.2，
而 `transformers 5.20.2` 需要更新的 hub。三者在同一环境里打架。

两条修复路径（W4 前解决，不影响 W3 评测）：

| 方案 | 做法 | 代价 |
|---|---|---|
| **A. 百炼 API 重排**（推荐） | 用 `gte-rerank` HTTP 接口，不装本地模型 | 与现有 API embedding 一致；多一次网络调用 |
| B. 修依赖 | 升级 `huggingface-hub` 到兼容版本，再下 2.27 GB 的 reranker 模型 | 又要下载，且可能再次冲突 |

> 顺带：rerank 本来就是 W4 的事（E4 实验），W2 不做它不影响任何结论。

---

## 五、W3 要做什么

1. 把 `data/eval_draft.md` 的 60 条草稿落成 `eval_cases` 表（补 `gold_doc_ids`）
2. 写 `eval/runner.py` + `metrics.py`（recall@5 / recall@10 / MRR / nDCG）
3. 跑 **E1（纯向量）** 和 **E2（纯 BM25）** 基线
4. 出第一张指标表 —— 这是后面所有优化的参照点

W3 的验收标准：**一条命令跑完 60 条评测集并输出表格**。
