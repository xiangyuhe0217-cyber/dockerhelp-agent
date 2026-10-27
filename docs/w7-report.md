# W7 完成报告：Vue 3 前端 + Trace 侧栏（含复现指南）

> 用 **Vue 3 + Vite** 替换原计划的 Gradio。后端 FastAPI 提供 JSON 接口，前端单页应用。
> 关键设计：**Trace 侧栏轮询数据库，而不是维护 SSE 连接**——因为每一步本来就落库了。

---

## 一、做了什么

```
┌──────────────────────────────────────────────────┐
│  Vue 3 单页（web/src）                            │
│  ├─ 左：对话区（消息流 + 输入框）                   │
│  ├─ 右：Trace 侧栏 / 记忆面板（可切换）             │
│  └─ 弹窗：写操作人工确认                            │
└───────────────┬──────────────────────────────────┘
                │ fetch + 轮询（400ms）
┌───────────────▼──────────────────────────────────┐
│  FastAPI（app/api/server.py）                     │
│  POST /api/chat        启动一次 run，立即返回 run_id │
│  GET  /api/chat/{id}   runs+steps → 前端渲染         │
│  POST /api/resume      人工确认后恢复                │
│  GET  /api/memory/{u}  长期记忆（Redis）             │
│  GET  /api/runs        历史运行列表                  │
└───────────────┬──────────────────────────────────┘
                │
        LangGraph Agent（W6）+ Redis 记忆 + FAISS 检索
```

### 为什么不用 SSE

`TraceRecorder` 本来就是**每步落库**的，所以 `steps` 表天然是一条事件流。
前端按 `run_id` 每 400ms 拉一次就能看到"逐步长出来"，不需要再维护 SSE 连接 + 内存队列。
**少一套状态，就少一类 bug**（断线重连、事件乱序、跨线程队列）。

代价是多几次 HTTP 轮询——单机演示完全可以忽略。

### 界面构成

| 区域 | 内容 |
|---|---|
| 顶栏 | 标题、chunks 数、记忆后端、用户/会话切换（存 localStorage） |
| 左：对话区 | 消息流、发送框（Ctrl+Enter）、错误提示 |
| 右：Trace | 每一步的 `#序号 类型 工具 耗时 token` + 思考 + 工具参数 + 观察结果 |
| 右：记忆 | 事实（HASH）、最近操作（LIST）、清空按钮 |
| 弹窗 | 写操作确认：工具名 + 完整参数 + 允许/拒绝 |

步骤按类型着色：`tool` 蓝、`answer` 绿、`guard`/`confirm` 黄。

---

## 二、复现指南

### 0. 前置

```bash
cd D:\dsh_pro\dockerhelp-agent

# 后端依赖（FastAPI + uvicorn 已在 requirements 里）
.venv\Scripts\python.exe -c "import fastapi, uvicorn; print('ok')"

# 前端依赖（已装过就跳过）
cd web
npm install --registry=https://registry.npmmirror.com
cd ..
```

---

### 1. 确认前端已构建

```bash
ls web\dist
# 期望看到：index.html  assets\
```

**没有就构建一次**：

```bash
cd web
npm run build
# 期望输出：
#   dist/index.html                  0.41 kB
#   dist/assets/index-xxxx.css       3.72 kB
#   dist/assets/index-xxxx.js       73.98 kB │ gzip: 29.52 kB
#   ✓ built in ~400ms
cd ..
```

---

### 2. 启动服务

```bash
.venv\Scripts\python.exe scripts\serve.py --port 8000
```

**预期输出**：

```
[serve] http://127.0.0.1:8000
INFO:     Uvicorn running on http://127.0.0.1:8000
```

> 用 `--reload` 可以在改后端时自动重载（前端不用重跑服务）。

---

### 3. 打开界面

浏览器打开 **http://127.0.0.1:8000**

**预期看到**：
- 顶栏显示 `8788 chunks · 记忆 redis`
- 左侧空白对话区（有一条示例问题提示）
- 右侧「执行轨迹 / 记忆」两个标签

---

### 4. 试四类操作（验收重点）

#### ① 纯知识问答（看 Trace 逐步出现）

输入：`Docker 装完一直卡在 Starting 怎么办`，回车。

**预期**：
- 右侧 Trace 先出现 `#1 决策 路由判定：rag`
- 然后 `#2 工具 search_knowledge` + 检索结果
- 最后 `#3 回答`
- 左侧出现带出处的回答，下方灰字显示 `route=rag · 3 步 · 1456 tokens`

#### ② 工具编排

输入：`premium 客户的 P1 工单 SLA 多久响应`

**预期**：Trace 里出现 `check_sla`，回答里的数字是 `1 小时 / 8 小时`（与库一致）。

#### ③ 写操作 → 弹确认框

输入：`直接建单，不要再问我：标题「测试工单」，描述「验证确认链路」，优先级 P2，客户 cust-003。`

**预期**：
- 弹出对话框「⚠ 写操作需要确认」，显示 `create_ticket` 和完整参数
- 点**拒绝** → 对话里出现"未创建"，**数据库里查不到**
- 再发一次点**允许** → 建单成功，可去「记忆」标签看到一条 event

验证没建单：

```bash
.venv\Scripts\python.exe -c "import sqlite3;c=sqlite3.connect('data/helpdesk.db');print(c.execute(\"select count(*) from tickets where title like '%测试工单%'\").fetchone())"
```

#### ④ 记忆面板

先在输入里说：`我是 premium 客户，我们用的是 Docker 4.34。`
然后切到「记忆」标签。

**预期**：事实里出现 `tier = premium`、`version = Docker 4.34`
（这些是 Redis 里的，`docker exec ruoyi-ai-redis redis-cli -n 1 hgetall "dockerhelp:mem:web:facts"` 也能看到）

---

### 5. 无浏览器的自动验收

```bash
.venv\Scripts\python.exe scripts\test_api.py
```

**我实测结果（9 项全过）**：

```
✅ 健康检查
   chunks=8788 记忆=redis redis=127.0.0.1:26379/db1
✅ 知识问答有回答
✅ 知识问答走 rag
✅ 步骤已落库
   步数=3 tokens=1456
✅ 工具编排调用 check_sla
   工具调用: ['check_sla']
✅ 写操作停在确认
   待确认: create_ticket({"title": "API 验收工单", ...})
✅ 拒绝后未建单
✅ 拒绝后有回答
✅ 记忆接口可读
   记忆后端=redis facts=0 events=0

9 通过 / 0 失败
```

---

### 6. 开发模式（改前端时用）

```bash
# 终端 1：后端
.venv\Scripts\python.exe scripts\serve.py --port 8000

# 终端 2：前端热更新
cd web
npm run dev
# 打开 http://127.0.0.1:5173（vite 会把 /api 代理到 8000）
```

---

## 三、验收清单

| # | 检查项 | 方法 | 通过标准 |
|---|---|---|---|
| 1 | 依赖 | `import fastapi, uvicorn` | 无报错 |
| 2 | 前端产物 | `ls web\dist` | 有 index.html + assets |
| 3 | 服务启动 | 第 2 步 | 打印 URL |
| 4 | 页面可开 | 浏览器 | 顶栏显示 8788 chunks |
| 5 | 知识问答 | 第 4 步① | Trace 出现 3 步、回答带出处 |
| 6 | 工具编排 | 第 4 步② | Trace 有 check_sla |
| 7 | 写确认-拒绝 | 第 4 步③ | 库里 0 条 |
| 8 | 写确认-允许 | 第 4 步③ | 建单成功 + 记忆多一条 event |
| 9 | API 自动化 | 第 5 步 | 9/9 |
| 10 | 记忆面板 | 第 4 步④ | 显示 tier/version |

---

## 四、踩的坑

| # | 问题 | 现象 | 修法 |
|---|---|---|---|
| 1 | **pnpm 12 拦截 esbuild 构建脚本** | `ERR_PNPM_IGNORED_BUILDS` | 试了 package.json 的 `pnpm` 字段（已废弃）、`pnpm-workspace.yaml`（仍报错），最后改用 **npm**（35 包 / 7 秒装完） |
| 2 | **静态挂载时机** | 先起服务再 build，`/` 会 404 | `web/dist` 在**导入时**判断存在与否，所以构建完要**重启服务** |
| 3 | **`win32` 下 CSP 与轮询** | 已用 400ms 轮询 + 超时 180s 兜底，避免一直转圈 | — |

第 1 个值得记：**工具链的安全策略会变**。pnpm 10+ 把 `onlyBuiltDependencies` 从 package.json 迁到 workspace 配置，
而 pnpm 12 又要求交互式 `approve-builds`。与其跟它纠缠，不如换个工具——npm 默认不拦。

---

## 五、已知限制

| 限制 | 说明 | 影响 |
|---|---|---|
| 单进程、串行执行 | 后端用一把全局锁，一次只跑一个 Agent | 演示够用；并发需要改成队列或每请求一个 Agent |
| 轮询而非流式 | 400ms 一次，token 级流式没有 | 观感略钝，但 trace 是逐步骤出现的 |
| 没有鉴权 | 谁打开都能用 | 本地演示可以；上线要加 |
| 没有会话列表 | 只能靠改「会话」输入框切换 | W8 可加历史会话选择 |
| UI 未做响应式 | 窄屏右侧会被挤 | 桌面演示够用 |

> **诚实说明**：界面效果我无法截图验证（没有浏览器工具）。
> 但**接口链路 9/9 实测通过、静态资源 200 且大小正确、Vue 构建无报错**，
> 剩下的是纯渲染层，请打开 URL 看一眼；若有样式/交互问题告诉我具体现象，我来改。

---

## 六、W7 学习清单（约 3 小时）

| 主题 | 时长 | 关键点 |
|---|---|---|
| 前后端分离的基本形态 | 1h | 静态资源托管、CORS、开发代理 |
| 为什么轮询而不是 SSE | 0.5h | 复用"每步落库"的事实，减少状态 |
| 前端状态管理 | 1h | 单一数据源（messages/steps）、轮询更新、确认弹窗 |
| 构建产物与部署 | 0.5h | vite build 输出、FastAPI 静态挂载时机 |

### 自检题

1. 为什么 Trace 侧栏用轮询而不是 SSE？如果改用 SSE，你需要多维护哪些状态？
2. 前端为什么把 `user/thread` 存 localStorage，而不是每次请求都问用户？
3. 写操作确认放在**前端弹窗**和放在**后端 interrupt** 有什么区别？（我们现在是后者驱动前者）
4. `web/dist` 不存在时 `/` 会 404，但 `/api/*` 仍然可用——这是怎么做到的？
5. 如果要支持"多用户同时用"，现在的全局锁会有什么问题？怎么改？
