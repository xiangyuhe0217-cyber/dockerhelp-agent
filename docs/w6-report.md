# W6 完成报告：记忆与 LangGraph 迁移（含复现指南）

> 回答你最初的问题：**记忆底层 = LangGraph checkpoint（会话状态） + Redis（长期事实/事件，SQLite 兜底）**。
> 本文档第二部分是逐条复现验收流程。

---

## 一、记忆的三层结构

| 层 | 存什么 | 技术 | 位置 |
|---|---|---|---|
| **会话状态** | 完整对话消息、工具调用与结果、当前执行位置 | **LangGraph `SqliteSaver` checkpoint**，按 `thread_id` | `data/checkpoints.sqlite`（checkpoints / writes 两张表） |
| **长期记忆** | 用户事实（等级/版本/团队）+ 操作事件（建单/升级） | **Redis**（默认）／SQLite `memories` 表（兜底），按 `user_id` 隔离 | Redis `127.0.0.1:26379` **db=1** |
| **Trace** | 每次运行的分步日志（思考/工具/参数/观察） | 自研 runs/steps | `data/helpdesk.db` |

### Redis 里的数据结构

```
dockerhelp:mem:{user_id}:facts      HASH    fact_key -> fact_value
dockerhelp:mem:{user_id}:facts:ts   ZSET    fact_key -> 更新时间戳（按新旧排序用）
dockerhelp:mem:{user_id}:events     LIST    LPUSH 最新在前，LTRIM 保留 100 条，TTL 90 天
```

**为什么 facts 要两个 key**：HASH 无序，而 prompt 里希望"最近记住的排在前面"，
所以用 ZSET 记时间戳做排序、HASH 存值。写入用 pipeline 保证两者一致。

**端口为什么是 26379**：Redis 跑在 Docker 里（容器 `ruoyi-ai-redis`），
宿主端口映射是 `26379 -> 6379`。直接连 6379 会失败。

**为什么用 db=1**：db0 里已经有 9 个 key（ruoyi 项目的数据），我们的记忆必须隔离，
所以固定 db=1 + `dockerhelp:` 前缀，绝不碰别人的数据。

**持久化**：该容器挂了命名卷 `ruoyi-ai_redis-data`，且 `appendonly yes` + RDB 快照，
**重启容器不会丢记忆**。数据目录 `/data`。

**连不上会怎样**：`get_memory()` 自动回退 SQLite 并打印警告，不会让整个 Agent 挂掉
（`MEMORY_FALLBACK_SQLITE=true`，可关）。

**三层分工（这是关键）**：

- checkpoint 负责"**能接着聊**"——机器恢复用，存的是完整 state
- memories 负责"**能记住你**"——跨会话的事实，存的是提炼后的结论
- runs/steps 负责"**能解释为什么**"——人读的审计日志

> 为什么不合并：checkpoint 是给机器恢复的（结构复杂、量大），memories 是给模型看的（精简、可注入），
> trace 是给人看的（可读、可回放）。三者用途不同，混在一起会互相拖累。

### 长期记忆只记两类东西

| kind | 来源 | 例子 |
|---|---|---|
| `fact` | 模型调 `remember_fact` 工具（用户陈述的事实） | `tier: standard`、`version: 4.34` |
| `event` | 写工具成功后自动落一条 | `create_ticket(...) → 已创建工单 T-xxx` |

**不记**：对话原文（checkpoint 已有）、一次性检索结果（会过期、会污染）。

---

## 二、复现指南（逐条验收）

### 0. 前置检查

```bash
cd D:\dsh_pro\dockerhelp-agent

# 1) 依赖：langgraph + redis 客户端
.venv\Scripts\python.exe -c "import langgraph, redis; print('deps ok')"

# 2) Redis 是否可连（端口 26379，不是默认的 6379）
.venv\Scripts\python.exe -c "import redis;r=redis.Redis(host='127.0.0.1',port=26379,db=1);print('redis:',r.ping())"
# 期望: redis: True

# 3) 向量索引
.venv\Scripts\python.exe -c "import sys;sys.path.insert(0,'.');from app.rag.index import get_store;print('向量:', get_store(create=False).count())"
# 期望: 向量: 8788

# 4) 确认记忆后端走的是 Redis
.venv\Scripts\python.exe -c "import sys;sys.path.insert(0,'.');from app.config import settings;print('backend:', settings.memory_backend, settings.redis_host, settings.redis_port, 'db', settings.redis_db)"
# 期望: backend: redis 127.0.0.1 26379 db 1
```

---

### 1. 长期记忆：写入与读取

```bash
# 给用户 demo1 记两条事实
.venv\Scripts\python.exe scripts\agent_demo.py --user demo1 --remember tier=premium
.venv\Scripts\python.exe scripts\agent_demo.py --user demo1 --remember version=4.34

# 查看该用户的记忆
.venv\Scripts\python.exe scripts\agent_demo.py --user demo1 --memory
```

**预期输出**：

```
【关于用户 demo1 的记忆】（来自历史会话，可直接使用）
已知事实：
- version: 4.34
- tier: premium
```

**验收点**：`--user` 不同，`--memory` 内容不同（用户隔离）。

```bash
# 换一个用户，应该看不到上面两条
.venv\Scripts\python.exe scripts\agent_demo.py --user demo2 --memory
# 期望: 用户 demo2 目前没有记忆
```

**直接去 Redis 里看**（最硬的证据）：

```bash
# 列出我们写入的 key（注意用 -n 1 选 db1）
docker exec ruoyi-ai-redis redis-cli -n 1 keys "dockerhelp:*"

# 看某个用户的事实
docker exec ruoyi-ai-redis redis-cli -n 1 hgetall "dockerhelp:mem:demo1:facts"
# 期望: tier / premium …

# 确认 events 是 LIST 且有过期时间
docker exec ruoyi-ai-redis redis-cli -n 1 type "dockerhelp:mem:demo1:events"
docker exec ruoyi-ai-redis redis-cli -n 1 ttl  "dockerhelp:mem:demo1:events"
# 期望: list / 一个正数秒数（90 天）
```

> ⚠️ **db0 是 ruoyi 项目的数据（9 个 key），不要动**。我们固定用 db1 + `dockerhelp:` 前缀。

---

### 2. 会话记忆：多轮对话 + 跨轮回忆

```bash
# --clear 会同时清空记忆和该会话的 checkpoint，保证从干净状态开始
.venv\Scripts\python.exe scripts\demo_multiturn.py --clear --user u_demo --thread t_demo
```

**三轮提问**（脚本内置）：
1. `我是 standard 客户，我们团队用的是 Docker 4.34。`
2. `我的 P1 工单 SLA 要求多久响应？`
3. `刚才我说我们用的是哪个版本？`

**我实测的输出（节选）**：

```
[第 1 轮]
   - plan
   - plan(remember_fact,remember_fact)
   - tool(remember_fact)      ← 记住 tier=standard
   - tool(remember_fact)      ← 记住 version=4.34
助手：已记录：客户等级 standard，产品版本 Docker Desktop 4.34

[第 2 轮]
   - tool(check_sla)          ← 用了记忆里的 standard
助手：standard 客户 P1 工单：首次响应 4 小时内，解决时限 48 小时内。

[第 3 轮]
助手：**你刚才说的是 Docker 4.34。**
```

**验收点**：
- 第 1 轮出现了 `remember_fact` 两次（模型主动记事实）
- 第 2 轮**没有问**你是什么等级，直接用 standard 查 SLA
- 第 3 轮能回忆起第 1 轮说过的版本

---

### 3. checkpoint：证明它真的持久化

```bash
# 查某个会话的快照数量
.venv\Scripts\python.exe scripts\agent_demo.py --user u_demo --thread t_demo --history
```

**预期**：`线程 u_demo:t_demo 共 N 个 checkpoint 快照`（3 轮对话约 18 个）。

再进一步，直接看库：

```bash
.venv\Scripts\python.exe -c "import sqlite3;c=sqlite3.connect('data/checkpoints.sqlite');print(c.execute('select count(*) from checkpoints').fetchone())"
```

**验收点**：数字 > 0，说明每次节点执行都落了快照。

---

### 4. interrupt 人工确认：暂停 → 拒绝 → 恢复

这条最容易验错，用脚本自动跑：

```bash
.venv\Scripts\python.exe -x -c "print()" >nul 2>&1
```

手工版：直接跑下面这条命令，**不要加 `--yes`**：

```bash
.venv\Scripts\python.exe scripts\agent_demo.py "直接建单，不要再问我任何问题：标题「确认测试」，描述「验证链路」，优先级 P2，客户 cust-002。"
```

**预期**：程序打印

```
  ⚠ 写操作待确认：create_ticket
    参数：{"title": "确认测试", "description": "验证链路", "priority": "P2", "customer_id": "cust-002"}
    执行吗？[y/N]
```

- 输入 `n` → 回答里会说"工单未创建"，库里也查不到
- 再跑一次输入 `y` → 建单成功

**实测结果**（脚本化验证）：

| 操作 | 结果 |
|---|---|
| 首次调用 | `status=need_confirm`，interrupt 里带完整参数 |
| 拒绝（resume=False） | 回答了"未创建"，**库里工单数 = 0** |
| 同意（resume=True） | 建单成功，**库里工单数 = 1** |

**验收点**：拒绝后库里必须是 0 条——这证明 interrupt 真的拦在了副作用之前。

---

### 5. 回归：8 个剧本仍然可用

```bash
.venv\Scripts\python.exe scripts\reset_demo_ticket.py
.venv\Scripts\python.exe scripts\agent_demo.py --scenarios data\scenarios.jsonl --yes --no-steps --user u_demo --thread s
```

**实测**：

```
id   route  steps  tokens      ms  status
s1   rag        3    1658    5939  done
s2   agent      4    3063    2136  done
s3   agent      5    3834    4774  done
s4   agent      5    4109    4629  done
s5   agent      6    4945    4452  done
s6   agent      6    4145    6907  done
s7   rag        3    1558    5899  done
s8   rag        3    1317    4081  done
```

> 注意：每个剧本用 `--thread s-<id>` 独立会话。**共用一个 thread 会让场景之间串历史**（我踩过这个坑，见第四节）。

---

### 6. 单测

```bash
.venv\Scripts\python.exe tests\test_w1.py            # 6/6
.venv\Scripts\python.exe tests\test_rag.py           # 12/12
.venv\Scripts\python.exe tests\test_store.py         # 3/3
.venv\Scripts\python.exe tests\test_agent_tools.py   # 9/9
.venv\Scripts\python.exe tests\test_memory.py        # 7/7  ← W6 新增（含 Redis 专项 2 项）
```

**合计 37 项全绿**。

其中 `test_memory.py` 的 7 项里，有两项是专门验 Redis 的：
- `test_backend_is_redis_by_default`：默认后端必须是 redis（连不上会暴露）
- `test_redis_key_layout`：key 前缀、HASH/LIST 类型、events 的 TTL 都符合预期

---

## 三、验收清单

| # | 检查项 | 命令 | 通过标准 |
|---|---|---|---|
| 1 | 依赖 | `import langgraph, redis` | 无报错 |
| 2 | Redis 可连 | 第 0 步 | `ping=True`（端口 26379） |
| 3 | 长期记忆写入/读取 | 第 1 步 | `--memory` 显示两条事实 |
| 4 | 用户隔离 | 第 1 步 demo2 | 看不到 demo1 的记忆 |
| 5 | **Redis 里真的存在** | 第 1 步 `redis-cli hgetall` | 能读到 `tier=premium` |
| 6 | 多轮回忆 | 第 2 步 | 第 3 轮答出 4.34 |
| 7 | checkpoint 落盘 | 第 3 步 | 快照数 > 0 |
| 8 | 会话隔离 | 第 5 步 | 每个剧本独立 thread |
| 9 | interrupt 拒绝 | 第 4 步 | 库里 0 条 |
| 10 | interrupt 同意 | 第 4 步 | 库里 1 条 |
| 11 | 全量单测 | 第 6 步 | 37/37 |

---

## 四、迁移中踩的五个坑（都修了）

| # | 问题 | 现象 | 修法 |
|---|---|---|---|
| 1 | **消息对象协议不通用** | `422 missing field role` | 加 `to_openai_messages()`，把 LangChain 消息转 OpenAI dict |
| 2 | **checkpoint 会跨测试污染** | 单测里假 LLM 一上来就看到旧消息 | 每个测试用独立 `thread_id` |
| 3 | **批量剧本互相串会话** | 8 个剧本共用一个 thread，后面的能看到前面的历史 | 每个剧本 `thread=场景id` |
| 4 | **中文输入产生代理字符** | 管道输入的中文写库报 `surrogates not allowed`，整个 run 崩 | `safe_text()` 清洗所有入库文本 |
| 5 | **Redis 端口不是 6379** | 直连 6379 超时 | 容器把 6379 映射成宿主 **26379**，且用 db1 避开 ruoyi 的数据 |

第 4 个值得单独说：它不是 LangGraph 的问题，而是**外部输入永远不可信**。
现在问题、思考、观察、答案在入库前都过一遍 `safe_text()`。

第 5 个值得单独说：**"本机没起 Redis"不等于"没有 Redis"**。
先用 `Test-NetConnection` 探端口、再 `docker ps` 看端口映射，才发现它在 26379。
如果直接按默认 6379 硬编码，会得出"环境没有 Redis"的错误结论。

---

## 五、设计取舍（面试可讲）

**① 为什么不用现成的记忆框架（Mem0/Zep）**
依赖重、黑盒，且我们这个环境已经被 chromadb 的依赖链坑过一次（把 tokenizers 降到 0.20.3，
transformers 直接不可用）。存"事实 + 事件"这种结构化信息，一张表 + 两个方法就够了。

**② 为什么 checkpoint 和 memories 分开**
- checkpoint 存**完整 state**（含所有历史消息），按 `thread_id`，机器恢复用；
- memories 存**提炼后的事实**，按 `user_id`，跨会话注入 prompt 用。

如果只用 checkpoint：换个会话就"忘了你"；如果只用 memories：没法续接当前对话。

**③ 为什么记忆注入 system prompt 而不是 messages**
塞进 messages 会破坏 `assistant(tool_calls)` 与 `tool` 消息的配对结构，
轻则模型困惑，重则 API 报错。system prompt 是唯一安全的注入点。

**④ interrupt 为什么必须"一次只执行一个工具"**
interrupt 恢复时整个节点会从头重跑。如果节点里一次执行 5 个工具，
重跑就会重复 5 次副作用。所以 `tools` 节点设计成"取一个 → 执行 → 返回"，
循环由图的边来驱动。

**⑤ 长期记忆为什么放 Redis 而不是继续用 SQLite**

| 维度 | SQLite | Redis |
|---|---|---|
| 读写延迟 | ~1ms（本地文件锁） | ~0.2ms |
| 多进程并发写 | 要处理 `database is locked` | 天然安全 |
| 过期/淘汰 | 要自己写清理任务 | `EXPIRE` 一行搞定 |
| 跨机器共享 | 不行 | 天然支持（以后上多实例） |

代价是多一个运行时依赖。所以保留了 SQLite 兜底 + 一次性迁移脚本，
`MEMORY_BACKEND=sqlite` 一行就能切回去，不会把项目锁死在 Redis 上。

---

## 六、已知问题

| 问题 | 影响 | 计划 |
|---|---|---|
| 记忆块只在会话首轮注入 | 会话中途新增的事实，本轮看不到（下一轮才有） | 可在每轮 router 重新生成，代价是 system prompt 长度变化 |
| 事实没有 TTL | 事实永久保留（事件有 90 天 TTL） | 可按 fact 类型分别设 TTL |
| checkpoint 表会持续增长 | 长期使用后 sqlite 变大 | 加定期清理脚本（保留最近 N 个 thread） |
| `remember_fact` 依赖模型自觉调用 | 用户说了事实但模型没记 | 可加规则抽取兜底（正则匹配"我是 X 客户"） |
| Redis 与 ruoyi 项目共用实例 | 若对方 flushdb 会连累我们 | 已用 db1 隔离；更稳妥是单独起一个 Redis 容器 |

---

## 七、W6 学习清单（约 3.5 小时）

| 主题 | 时长 | 关键点 |
|---|---|---|
| LangGraph 的 State/Node/Edge | 1h | 状态是"每步合并的 dict"，add_messages 决定合并策略 |
| checkpoint 机制 | 1h | thread_id、快照、time travel、为什么能 resume |
| interrupt 与幂等 | 1h | 恢复时节点重跑 → 副作用必须幂等或"一次一个" |
| 记忆分层 | 0.5h | 会话状态 / 长期事实 / 审计日志 三者为何不能合并 |

### 自检题

1. 为什么 `tools` 节点要设计成"一次只执行一个工具"？如果一次执行多个，interrupt 恢复会出什么问题？
2. checkpoint 和长期记忆都存"历史"，为什么不合并成一份？
3. 记忆块为什么注入 system prompt，而不是塞进 messages？
4. 用户说"我是 premium 客户"，但模型没调 `remember_fact`，下次它会记得吗？你怎么兜底？
5. 批量跑 8 个剧本时共用一个 `thread_id` 会发生什么？为什么？
6. 如果要把记忆做成"能遗忘"，你会怎么设计（数据结构 + 触发条件）？
7. 事实用 HASH + ZSET 两个 key 存，为什么不能只用一个？只用 HASH 会丢什么能力？

---

## 附：SQLite → Redis 迁移记录

历史记忆（W6 早期写进 SQLite 的 9 条）已一次性迁到 Redis：

```bash
# 先看会迁什么（不写）
.venv\Scripts\python.exe scripts\migrate_memory_to_redis.py --dry-run

# 正式迁移（幂等，可重复跑）
.venv\Scripts\python.exe scripts\migrate_memory_to_redis.py
```

实测输出：

```
[migrate] SQLite 里共 9 条记忆 → Redis 127.0.0.1:26379 db=1
  u_demo: 2 条事实 / 3 条事件
  u_demo2: 2 条事实 / 0 条事件
  u_intr: 0 条事实 / 1 条事件
  demo1: 1 条事实 / 0 条事件
[migrate] 完成，写入 9 条
```

SQLite 表**没有删除**（保留作为兜底与审计）。要切回 SQLite：
`.env` 里把 `MEMORY_BACKEND=redis` 改成 `sqlite` 即可，代码无需改动。
