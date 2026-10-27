# W8 运行手册：从零重建整个项目

> 目标：一台干净机器，**15 分钟**跑出可演示状态。
> 每一步都写了"预期看到什么"，不符合就说明这步没过。

---

## 0. 前置

| 项 | 要求 | 检查 |
|---|---|---|
| Python | 3.11+ | `python --version` |
| Node | 20+（只用于构建前端） | `node --version` |
| 网络 | 能访问阿里云 pip 镜像、npm 镜像 | — |
| Key | 百炼 API Key（模型 + embedding + rerank 都走它） | — |
| Redis | 可选（没有就自动回退 SQLite） | — |

---

## 1. 装依赖（约 3 分钟）

```bash
cd dockerhelp-agent
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
```

**预期**：最后打印 `Successfully installed ...`，无 ERROR。

> 如果卡在 torch：本项目默认**不装 torch**（embedding 走百炼 API）。
> 只有想跑本地 bge-m3 才需要 `scripts/fetch_torch.py`（见 W1 文档）。

---

## 2. 配置（1 分钟）

```bash
copy .env.example .env
```

编辑 `.env`，至少填：

```ini
BAILIAN_API_KEY=sk-xxxxxxxx
```

Redis 可选（有就填，没有会自动回退 SQLite）：

```ini
MEMORY_BACKEND=redis
REDIS_HOST=127.0.0.1
REDIS_PORT=26379     # 注意：本机 Docker 映射端口是 26379，不是 6379
REDIS_DB=1
```

**验证**：

```bash
.venv\Scripts\python.exe scripts\check_env.py
```

---

## 3. 抓数据（约 5 分钟）

### 3.1 Docker 官方文档

```bash
.venv\Scripts\python.exe scripts\fetch_docs.py --max-files 1200
```

**预期**：`data/docs/` 下约 932 个 `.md`，并打印 commit sha。

> `--max-files` 一定要给够。默认 400 会按字母序截断，导致 `manuals/compose` 之后的目录全丢（踩过）。

### 3.2 GitHub Issues

```bash
.venv\Scripts\python.exe scripts\fetch_issues.py
```

**预期**：`data/issues/*.jsonl`，日志里显示抓取条数与跳过条数。
断点续抓：中途断了直接重跑，不会重复拉。

---

## 4. 建库 + 切片 + 索引（约 4 分钟）

```bash
# 4.1 装配 SQLite（documents / tickets / customers / sla）
.venv\Scripts\python.exe scripts\build_db.py --reset

# 4.2 结构感知切片
.venv\Scripts\python.exe scripts\build_chunks.py --reset

# 4.3 双索引：向量（FAISS）+ BM25
.venv\Scripts\python.exe scripts\build_index.py --reset
```

**预期**：

```
documents: 932   chunks: 8788   tickets: 530
FAISS: 8788 vectors (dim=1024)
BM25: 8788 docs, vocab=...
```

> 如果 chunks 数量明显偏少（比如 3000），多半是 `--max-files` 太小；
> 如果是 0，检查 `DOCS_MAX_FILES` 与 `data/docs/` 是否为空。

---

## 5. 评测集 + 评测（约 3 分钟）

```bash
# 5.1 生成 65 条评测题（gold 文档路径会校验，匹配不上直接失败）
.venv\Scripts\python.exe scripts\build_evalset.py

# 5.2 跑关键配置
.venv\Scripts\python.exe scripts\run_eval.py --configs E1,E3,E8,E10 --top-k 10
```

**预期**：控制台打印每个配置的 `hit@5 / recall@5 / mrr / ndcg@10`，并写 `reports/eval_summary.json`。

最终配置（E10）应在 **hit@5 ≈ 0.76~0.78**。差太远说明数据或索引没建对。

---

## 6. 前端（约 2 分钟）

```bash
cd web
npm install --registry=https://registry.npmmirror.com
npm run build
cd ..
```

**预期**：`web/dist/` 出现 `index.html` + `assets/`。

> 用 npm 而不是 pnpm：pnpm 10+ 会拦截 esbuild 的构建脚本（`ERR_PNPM_IGNORED_BUILDS`）。

---

## 7. 起服务

```bash
.venv\Scripts\python.exe scripts\serve.py --port 8000
```

**预期**：

```
[serve] http://127.0.0.1:8000
INFO:     Uvicorn running on http://127.0.0.1:8000
```

浏览器打开 → 能对话即成功。

---

## 8. 验收（1 分钟）

```bash
# 一键自检（18 项）
.venv\Scripts\python.exe scripts\selfcheck.py

# 单测（35 项）
.venv\Scripts\python.exe tests\test_w1.py
.venv\Scripts\python.exe tests\test_rag.py
.venv\Scripts\python.exe tests\test_store.py
.venv\Scripts\python.exe tests\test_agent_tools.py
.venv\Scripts\python.exe tests\test_memory.py

# API 端到端（9 项，需服务在跑）
.venv\Scripts\python.exe scripts\test_api.py
```

**全部绿**才算交付就绪。

---

## 常见故障

| 现象 | 原因 | 处理 |
|---|---|---|
| `pip install` 卡住 | 走了默认源 | 加 `-i https://mirrors.aliyun.com/pypi/simple/` |
| `SQLite ... unable to open database file` | 系统 TEMP 对 Python 不可达 | `config.py` 已自动重定向到 `data/tmp`；确认该目录可写 |
| 检索结果为空 | 索引没建 / 维度不匹配 | 重跑 `build_index.py --reset`；检查 embedding 维度=1024 |
| 前端 404 | `web/dist` 不存在 | `npm run build` 后**重启服务**（静态挂载在导入时判定） |
| Redis 连不上 | 端口写成 6379 | Docker 映射端口是 **26379**；或把 `MEMORY_BACKEND=sqlite` |
| 回答里出现「知识库没有」 | 语料没覆盖 | 正常，会触发网页搜索降级（看 Trace 第 3 步） |
| 工单结单后检索不到 | 旧版本 `min_tokens` 把短文档全丢了 | 已修复；确认 `chunk_document` 里是 `allow_drop` 逻辑 |

---

## 目录速查

```
data/
├─ docs/              抓下来的 markdown（932 篇）
├─ issues/            抓下来的 Issues
├─ helpdesk.db        SQLite 主库
├─ faiss/             向量索引（index.faiss + meta.jsonl）
├─ bm25.pkl           BM25 索引
├─ checkpoints.sqlite LangGraph 会话快照
└─ eval_cases.jsonl   65 条评测题
```
