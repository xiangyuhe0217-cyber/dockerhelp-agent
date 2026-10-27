# W2 学习清单与自检题库

> 先回答你的问题：**你跑出来的结果确实不对**，而且是两个独立的 bug。修完之后同一条 query 的表现完全不同。

---

## 一、你那次查询为什么不理想（诊断记录）

### 问题 1：知识库被字母序截断了（W1 埋的坑）

`fetch_docs.py` 按 `sorted(rglob("*.md"))` 取前 400 篇，而 Docker 文档的目录是按字母排的：

```
get-started/ → guides/ → manuals/accounts → manuals/ai → manuals/build
→ manuals/compose … 然后就到 400 篇上限了
```

**`manuals/desktop/troubleshoot-and-support/` 整块没进来**——而它才是"装完起不来"的标准答案所在。

实测证据（修复前）：

| 检查项 | 结果 |
|---|---|
| `troubleshoot` 关键词命中 | **1 篇** |
| `manuals/*` 覆盖的二级目录 | 只有 accounts / agentic-platform / ai / build / build-cloud / compose |
| 最大路径 | `manuals/compose/how-tos/provider-services.md` ← 到这里就截断了 |
| 语料构成 | `manuals/ai` 146 篇 + `manuals/build` 90 篇 = **59%** |

所以你问"无法启动"，模型只能在一堆 AI/Build 文档的 "Prerequisites" 里找最像的那个。

**修复**：`DOCS_MAX_FILES` 400 → 1200，全量抓取。结果：932 篇 / 113 万字 / 8,788 chunk。

### 问题 2：没有 rerank，纯向量排序会偏向"通用页"

向量检索是 **bi-encoder**：query 和 document 各自独立编码，再算余弦相似度。
它擅长"语义大致相关"，但**分不清"讲安装"和"讲装完起不来"**——两者都含 Docker、安装、Desktop。

而 "Prerequisites" 这类页面措辞极其通用（"Before you begin, make sure you have Docker Desktop…"），
跟任何"安装相关"的 query 相似度都偏高，于是霸榜。

### 修复前后对比（同一条 query）

`Docker安装了但是无法启动怎么办`

| 模式 | Top-1 | 评价 |
|---|---|---|
| 修复前 hybrid | Installation | 泛泛而谈 |
| 修复前 hybrid | Prerequisites（Laravel） | 完全跑偏 |
| **修复后 vector** | 4.30.0 › Known issues › For Linux | 平台都错了 |
| **修复后 bm25** | Reference | 垃圾 |
| **修复后 hybrid** | 同上，融合没救回来 | — |
| **修复后 hybrid_rerank** | Installation methods › Install from a package | 还行 |
| | **Docker Desktop fails to start when anti-virus software…** | ✅ 正确方向 |

再测两条，说明不是偶然：

```
>>> 容器里访问不到宿主机上的服务
  1. Local services › Accessing host services from a sandbox        ✅
  2. How do I connect from a container to a service on the host?     ✅
  3. Core networking how-tos › Connect a container to a service...   ✅

>>> 端口被占用起不来
  1. Topics for all platforms › `port already allocated` errors › Solution   ✅ 精确命中
```

**这是 W2 最重要的一课**：检索系统的效果，**数据覆盖度 > 模型 > 参数**。
你如果只调 top_k、换模型，永远救不回一个缺了 troubleshooting 章节的知识库。

---

## 二、还有一个"隐形杀手"：SQLite 临时目录

排查过程中发现我们自己的 SQLite 也报 `unable to open database file`（和 Chroma 一模一样的错）。
根因：

```python
tempfile.gettempdir()  →  D:\dsh_pro\dockerhelp-agent   # 不是 C:\...\Temp！
```

在这台机器的受限环境里，**Python 进程访问系统 TEMP 被拒绝**，`tempfile` 静默回退到工作目录。
而 SQLite 走的是系统 TEMP，大事务（900+ 篇文档 / 8000+ 向量）需要落临时文件时就挂。

表现极具迷惑性：**小数据一切正常，数据一多就随机失败，还不报错**（Chroma 那次就是静默丢一半）。

修复：`app/config.py` 里进程启动时把 `SQLITE_TMPDIR / TMPDIR / TMP / TEMP` 和 `tempfile.tempdir` 全部指到 `data/tmp`。

> 这个 bug 值得你记住：**"数据量小时正常、一大就出问题"通常不是算法问题，而是临时文件 / 内存 / 句柄这类系统资源问题。**

---

## 三、W2 学习清单（按 ROI 排序，约 7 小时）

| # | 主题 | 为什么必须懂 | 时长 | 你要能说出的关键词 |
|---|---|---|---|---|
| 1 | **Chunking 策略** | 决定召回上限，后面调什么都救不回来 | 1.5h | 结构感知、面包屑前缀、代码块原子性、overlap、token 预算 |
| 2 | **Embedding 与相似度** | 不懂这个就解释不了"为什么中文能搜英文" | 1.5h | bi-encoder、L2 归一化、cosine vs 内积、维度、多语言对齐 |
| 3 | **向量 vs BM25** | 决定你的"混合检索"到底有没有价值 | 1h | 语义匹配 / 词面匹配、精确串、OOV、跨语言失效 |
| 4 | **RRF 融合** | 面试高频：为什么不用加权和 | 1h | 名次而非分数、尺度不可比、k=60 的平滑作用 |
| 5 | **Rerank 两阶段** | 讲清"召回-精排"架构 | 1h | cross-encoder、全交叉注意力、延迟换精度、top_n 漏斗 |
| 6 | **召回链路参数** | 你能解释每个数字 | 0.5h | 召回 50 → 融合 → rerank 20 → final 5 |
| 7 | **数据工程的坑** | 这次两个 bug 都在这 | 1h | 覆盖度、字母序截断、临时目录、写后校验 |

### 明确不用在 W2 学的

- Milvus / Chroma 的运维细节（我们最终用 FAISS，知道抽象层怎么切就行）
- Agent / 工具调用（W5）
- Gradio（W7）
- 微调（W9-10）

---

## 四、自检题库（三级，答不上就回去补）

### L1 概念题（能用自己的话讲清）

1. 为什么 chunk 要带"面包屑前缀"（`Docker Desktop › Install › Windows`）？不带会怎样？
2. 为什么代码块和表格要当作**原子块**不切开？切开后对检索有什么具体影响？
3. `chunk_overlap` 解决什么问题？overlap 太大会有什么副作用？
4. bi-encoder 和 cross-encoder 的区别是什么？为什么检索要分"召回 + 精排"两阶段，而不是直接用 cross-encoder？
5. 为什么 RRF 用**名次**而不是分数？如果直接 `0.7*向量 + 0.3*BM25` 会出什么问题？
6. 为什么 BM25 在中文 query 上完全失效？这个结论对"混合检索一定更好"这个说法意味着什么？
7. 向量已经 L2 归一化了，为什么这时"内积"和"余弦相似度"等价？

### L2 代码题（能指到具体文件/函数）

8. `app/rag/chunker.py` 里，标题栈是怎么维护的？代码块里的 `#` 为什么不会被当成标题？
9. `split_long_text` 和 `_hard_split` 分别解决什么场景？为什么需要两个？
10. `Retriever.search(mode="hybrid_rerank")` 的完整数据流是什么？每一步的 top_k 是多少？
11. `app/rag/index.py` 里 `VectorStore` 抽象出了哪几个方法？为什么要抽这一层？（提示：我们中途换过两次后端）
12. FAISS 后端里 `upsert` 为什么实现成"整体重建"？换 100 万条数据你会怎么改？

### L3 排错题（能复现 + 能修）

13. 如果检索结果全是 "Prerequisites"，你会按什么顺序排查？（提示：先看数据覆盖度，再看排序）
14. 现在把 `DOCS_MAX_FILES` 改回 400 会发生什么？为什么？
15. 你怎么证明"索引里确实有 8,788 条向量"而不是只写进去一部分？为什么 `count()` 可能骗人？
16. 为什么"数据量小时正常、一多就失败、而且不报错"这类 bug 最难查？你会加什么防护？

---

## 五、面试话术模板（把 W2 讲成一段故事）

> "我的 RAG 一开始效果很差，问'Docker 装完起不来'返回的全是 Prerequisites。
> 我按'数据 → 召回 → 精排'三层排查：
> **第一层查数据**，发现文档抓取是按字母序截断的，troubleshooting 章节整块没进来，语料里 59% 是 AI 和 Build 文档；
> **第二层查召回**，中文 query 在 BM25 上完全零命中，因为语料是英文，混合检索实际退化成纯向量；
> **第三层加精排**，用 gte-rerank 做 cross-encoder 重排，把'讲安装'和'讲装完起不来'区分开。
> 修完后同一条 query 的 Top-2 命中 `Docker Desktop fails to start`。
> 这个过程让我确认一件事：**检索系统的天花板由数据覆盖度决定，不是由模型决定。**"

这段话里有：定位方法、三层结构、量化证据、结论。比"我用了 bge-m3 + RRF + rerank" 强十倍。

---

## 六、如果只有 2 小时

1. 读 `app/rag/chunker.py` 的 `split_sections` + `pack_section`（30 min）
2. 读 `app/rag/retrieve.py` 的 `rrf_fuse` + `search`（30 min）
3. 跑 `python scripts/search.py "<你的问题>" --compare`，对比四种模式（20 min）
4. 把 L1 的 7 个问题答案写进 `docs/notes/w2.md`（40 min）
