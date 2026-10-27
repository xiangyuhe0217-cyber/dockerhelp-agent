# W1 收尾方案：CUDA torch 安装 + W1 学习清单

> 面向：`dockerhelp-agent` W1 阶段收尾
> 你的现状：依赖已装齐、工单已抓 530 条、Docker Desktop 已开；**唯一卡点是 torch 是 CPU 版**

---

## 一、先搞清楚：为什么你的 pip 一定会断

不是你的网络"不行"，是三个条件叠加，缺一不可：

| 条件 | 具体数值 | 后果 |
|---|---|---|
| **文件大** | `torch-2.9.1+cu126` Windows 轮子 **2.58 GB** | 传输时间长 |
| **管道慢** | 直连 `download.pytorch.org` 实测 **25 KB/s** | 2.58 GB 要 **28 小时** |
| **单连接、无续传** | pip 默认一次 TCP 连接，断了从 0 开始 | 抖动一次 = 前功尽弃 |

所以"pip 一直断开"的本质是：**你用单连接下 2.5 GB，而链路抖动的期望间隔远小于下载时长。**
解决办法不是"多重试几次"，而是**换源 + 分段 + 断点续传**。

### 国内镜像实测（2026-10-25，你的网络）

| 源 | 单线程 | 4 线程 | 备注 |
|---|---|---|---|
| **上海交大** `mirror.sjtu.edu.cn` | **1,024 KB/s** | **4,080 KB/s** | ✅ 并发线性加速，最快 |
| 阿里云 `mirrors.aliyun.com` | 447–747 KB/s | **卡死（0 字节）** | 并发会被限 |
| 官方 `download.pytorch.org` | 25 KB/s | — | 不可用 |
| 中科大 `mirrors.ustc.edu.cn` | HTTP 403 | — | 拒绝访问 |
| 清华 TUNA | HTTP 403 | — | 未镜像该路径 |
| 南京大学 | HTTP 302 | — | 跳转，未跟测 |

**结论：用交大镜像 + 多线程。** 2.58 GB @ 4 MB/s ≈ **11 分钟**。

---

## 二、三个方案

### 方案 A（推荐，已内置并已代跑）：多线程断点续传脚本

文件：`scripts/fetch_torch.py`

```powershell
cd D:\dsh_pro\dockerhelp-agent
.\.venv\Scripts\python.exe scripts\fetch_torch.py --threads 8 --install
```

它做了三件 pip 不做的事：

1. **分段**：按 8 段并发请求 `Range: bytes=a-b`，绕过单连接限速
2. **续传**：每段写独立的 `partN` 文件，断了重跑只补没下完的段
3. **装完自验**：`--install` 会直接 `pip install --no-deps --force-reinstall` 并打印 `torch.cuda.is_available()`

**中断了怎么办**：再跑一次同样的命令即可，不会从 0 开始。

**换源**：脚本内置了阿里云/官方两个备用 URL（`FALLBACK_URLS`），交大挂了就 `--url` 指过去。

> **本次实际结果**：8 线程，峰值 9.6 MB/s、均值约 5.6 MB/s，**7 分 35 秒**下完 2.58 GB；
> 安装后 `torch 2.9.1+cu126`，`cuda_available=True`，识别到 `RTX 4060 Laptop GPU 8.0 GB`，`pip check` 无冲突。

### 方案 B：用 aria2c（通用工具，也可以）

```powershell
winget install aria2.aria2
aria2c -x16 -s16 -c -d .cache\wheels -o torch.whl `
  "https://mirror.sjtu.edu.cn/pytorch-wheels/cu126/torch-2.9.1%2Bcu126-cp311-cp311-win_amd64.whl"
```

`-x16 -s16` 是 16 连接分片，`-c` 是续传。下完手动装：
`.\.venv\Scripts\python.exe -m pip install --no-deps --force-reinstall .cache\wheels\torch.whl`

### 方案 C（不装 GPU 也能开工）：先用 CPU 版跑 W2

**这是必须说清楚的一点：GPU 不是 W2 的阻塞项。**

| 环节 | CPU 代价（估算，i7/16G） | GPU 代价 | 是否阻塞 |
|---|---|---|---|
| 一次性 embedding（约 3–4 千 chunk） | 15–25 分钟 | 40–60 秒 | ❌ 一次性，可接受 |
| 单次 query embedding | 100–300 ms | 10 ms | ❌ 无所谓 |
| rerank 20 条候选 | 2–4 s | 0.1 s | ⚠️ 慢，但能忍 |
| **W4 跑 6 组实验 × 60 题** | 多花约 20–30 分钟 | — | ⚠️ 迭代变慢 |

所以：**W2/W3 用 CPU 完全能开工，GPU 只影响 W4 调参的手感。**
如果今天下不下来，正确的选择是"先跑起来"，而不是"等 GPU"。

---

## 三、装完怎么验收

```powershell
.\.venv\Scripts\python.exe -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))"
# 期望: 2.9.1+cu126 True NVIDIA GeForce RTX 4060 Laptop GPU

.\.venv\Scripts\python.exe -m pip check      # 看有无依赖冲突（torch 从 2.14.1 降到 2.9.1 可能触发告警）
.\.venv\Scripts\python.exe scripts\check_env.py   # CUDA 一行应变 ✅
```

**关于版本**：你原来装的是 `2.14.1+cpu`，这次会装 `2.9.1+cu126`（国内镜像目前只到 2.9.1）。
`cu126` 与你的驱动 `566.24` 匹配；`sentence-transformers 6.x` 要求 `torch>=1.11`，向下兼容没问题。
如果 `pip check` 报 `transformers` 要更高版本 torch，两个选择：① 忽略（多数情况能跑）；② 降 transformers 到 5.0.x。

---

## 四、W1 你该学什么？（重点回答你的问题）

### 4.1 一句话结论

> **W1 不是"抓数据"，是"给后面 7 周的所有指标定上限"。**
> 但只需要学 5 件事，合计约 **2 小时**。不需要现在学 Milvus、FastAPI、Agent、Gradio。

理由很直接：面试时你能讲的"技术深度"，一半来自 W2–W4 的检索实验，
但那些实验的**天花板在 W1 就定死了**——文档是怎么解析的、chunk 怎么切的、
评测题是不是"答得出来"。这三件事你如果不懂，W4 的"召回率从 0.5 到 0.85"就讲不成因果。

### 4.2 学习清单（按 ROI 排序）

| # | 学什么 | 为什么要学 | 时长 | 自检问题（答不上就回去看） |
|---|---|---|---|---|
| 1 | **数据契约与可复现** | 面试必问"你的结果能复现吗"。你的答案就是 `docs_commit.txt` + `sha256` + 幂等 upsert | 40 min | 为什么 `documents` 用 `sha1(rel_path)` 当主键，而不是自增 id？`--reset` 和 `--force` 分别解决什么问题？ |
| 2 | **切片为什么是天花板** | JD 里"文档解析、切片"是 RAG 六步的前两步，后面全被它锁死 | 1 h 阅读 + 30 min 动手 | 挑一篇带表格的文档，说出"按 512 字符硬切会丢什么"；`heading_path` 前缀为什么能提升召回？ |
| 3 | **检索基础概念**（W2 前必须） | 不讲清这些，W4 的实验表就是一堆数字 | 2 h | 向量检索和 BM25 为什么互补？RRF 为什么用排名而不是分数相加？bi-encoder 和 cross-encoder 的区别？ |
| 4 | **数据质量怎么读** | 你要能从 `reports/data_stats.md` 读出风险 | 30 min | 为什么 `lang=en 100%` 意味着评测集必须用中文提问？中位词数只有 200 会担心什么？ |
| 5 | **工程习惯** | 断点续传、限流、幂等、运维开关——这些是"能跑起来"和"能交付"的差别 | 30 min | `fetch_issues` 的 `done` 集合有什么坑？（提示：今天就踩过） |

### 4.3 明确"W1 不用学"的

- Milvus / 向量索引（W2）
- FastAPI / SSE（W5）
- Agent 循环 / ReAct（W5）
- Gradio（W7）
- LoRA（W9-10，可选）

**提前学这些只会拖慢进度，而且脱离上下文记不住。**

### 4.4 如果只有 2 小时（速成路径）

1. 打开 `app/db/schema.sql`，只看 `documents` / `tickets` / `chunks` / `steps` / `eval_results` 五张表，理解它们串起来是什么流程（20 min）
2. 打开 `reports/data_stats.md`，逐个数字问自己"这个数字高了/低了会怎样"（15 min）
3. 读 `data/eval_draft.md` 开头 10 条题，想"如果我是模型，我需要哪段文档才能答这题"（20 min）
4. 搜两篇文章看：`RAG chunking 策略`、`RRF 融合 原理`（45 min）
5. 把上面 3 个自检问题的答案写进 `docs/notes/w1.md`（20 min）

**产出**：一份 200 字的笔记。面试时"你在 W1 学到什么"就有话说了，而不是"我抓了点数据"。

### 4.5 一个容易被忽略的加分点

`data/eval_draft.md` 里那 5 条 **unanswerable** 题（U1–U5），是全项目最值钱的设计之一。
面试官问"你怎么防止幻觉"，标准答案是"我专门构造了 5 条知识库答不了的题，
要求模型明确拒答而不是硬编，拒答准确率作为独立指标跟踪"。
这个思路现在就可以讲。

---

## 五、一个必须提前处理的问题：内存

你的 Docker 里现在跑着 **ruoyi-ai 的 7 个容器**（admin/web/backend/redis/weaviate/minio/mysql）。

```
16 GB 总内存 − Windows 与 Docker Desktop 常驻约 4 GB
             − ruoyi-ai 7 容器             约 3–4 GB
             − Milvus Standalone           约 2–3 GB
             − bge-m3 + reranker（GPU 常驻）约 1.5 GB
             ────────────────────────────────────────
             余量 ≈ 3–5 GB   ← 偏紧，但能跑
```

建议 W2 开工前二选一：

```powershell
# 选项 1：先把 ruoyi-ai 停掉（推荐，W2 期间用不到）
docker ps -q --filter "name=ruoyi-ai" | ForEach-Object { docker stop $_ }

# 选项 2：保留，但确认 Milvus 端口没被占
netstat -ano | findstr ":19530 :9091"
```

Milvus 只对外暴露 19530（gRPC）和 9091（健康检查），minio/etcd 都在容器内部，**不会和 ruoyi 的 9000/3306/6379 冲突**，这点可以放心。

> **一个已验证的坑**：`scripts/check_env.py` 里调用 `docker` 可能报
> `permission denied ... npipe:////./pipe/dockerDesktopLinuxEngine`。
> 这是**执行进程被禁止访问命名管道**导致的，不是 Docker 没起来——
> 你自己终端里 `docker ps` 正常。脚本已把这种情况降级为 ⚠️ 并给出提示。
> 同理，W2 启动 Milvus 请**在你自己的终端**执行 `docker compose up -d`。

---

## 六、下一步

1. 等下载跑完 → `check_env.py` 全绿
2. 决定是否停 ruoyi-ai
3. 进入 **W2**：切片 + bge-m3 + Milvus 建索引 + BM25 + RRF + rerank

W2 的第一件事不是写代码，而是**拿两篇文档切一遍，肉眼确认切片质量**——
这个习惯会帮你在 W4 省掉一整天的调参。
