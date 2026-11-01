# 失败归因：E10

> 数据来源：`reports/eval_summary.json` 的 `E10.cases`（60 题）
> 失败：**14 题**（hit@5 = 0），分类：A 语料缺失 3 / B 有语料未召回 7 / C 排太后 4

## 分类含义与对应动作

| 类 | 含义 | 该做什么 |
|---|---|---|
| **A** | gold 文档在语料里根本不存在 | 修正评测标注（标为不可答）或补语料；**不要动检索参数** |
| **B** | 文档在库里，但 Top-10 一条相关都没有 | 查切片位置、查询改写、embedding 语义鸿沟 |
| **C** | 召回到了但排 6~10 名 | 融合权重 / 重排 / 提升 gold 段的相似度 |

## 逐题明细

| ID | 类 | 名次 | 问题 | gold 路径 | 实际召回（前3） |
|---|---|---|---|---|---|
| q026 | A | - | WSL2 的 vmmem 进程吃了十几 G 内存 | manuals/desktop/features/wsl<br>manuals/desktop/use-desktop/resource-saver | cases/synthetic#0002<br>cases/synthetic#0001<br>cases/synthetic#0002 |
| q041 | A | - | 报 permission denied 连不上 do | manuals/engine/install/linux-postinstall<br>manuals/engine/daemon/troubleshoot | cases/synthetic#0002<br>manuals/engine/security/apparmor.md<br>manuals/ai/sandboxes/governance/monitor-and-enforce/sign-in-enforcement.md |
| q050 | A | - | 拉镜像报 manifest unknown | manuals/desktop/troubleshoot-and-support/troubleshoot<br>manuals/engine | manuals/docker-hub/repos/manage/hub-images/bulk-migrate.md<br>manuals/desktop/features/wasm.md<br>manuals/build/building/multi-platform.md |
| q020 | B | - | 为什么容器里的 localhost 连不到另一个容器 | manuals/engine/network/drivers/bridge | manuals/ai/sandboxes/workflows/development.md<br>guides/postgresql.md<br>guides/golang.md |
| q021 | B | - | 怎么限制容器只能访问某个网段 | manuals/engine/network/packet-filtering-firewalls<br>manuals/engine/network/firewall-iptables | manuals/faqs/platform.md<br>manuals/engine/swarm/networking.md<br>manuals/engine/network/port-publishing.md |
| q022 | B | - | WSL2 后端下端口映射不生效 | manuals/desktop/features/wsl<br>manuals/desktop/features/networking | cases/synthetic#0002<br>cases/synthetic#0001<br>cases/synthetic#0002 |
| q029 | B | - | 磁盘空间被 Docker 占满了怎么清理 | manuals/desktop/troubleshoot-and-support/troubleshoot<br>manuals/engine/daemon | manuals/desktop/troubleshoot-and-support/faqs/macfaqs.md<br>manuals/desktop/troubleshoot-and-support/faqs/linuxfaqs.md<br>manuals/engine/manage-resources/pruning.md |
| q040 | B | - | 怎么查看各个卷分别占用了多少空间 | manuals/desktop/use-desktop/volumes<br>manuals/engine/daemon | manuals/desktop/troubleshoot-and-support/faqs/linuxfaqs.md<br>manuals/ai/gordon/use-cases.md<br>manuals/desktop/troubleshoot-and-support/faqs/macfaqs.md |
| q046 | B | - | 拉镜像报 TLS handshake timeout | manuals/engine/daemon/proxy<br>manuals/desktop/settings-and-maintenance/settings | manuals/desktop/troubleshoot-and-support/troubleshoot/topics.md<br>manuals/ai/sandboxes/release-notes.md<br>cases/synthetic#0001 |
| q054 | B | - | 改了 Dockerfile 但构建出来的镜像没变化 | manuals/build/cache/invalidation<br>manuals/build/cache | get-started/docker-concepts/building-images/writing-a-dockerfile.md<br>cases/synthetic#0001<br>cases/synthetic#0001 |
| q001 | C | #8 | Docker Desktop 装完之后一直转圈停在  | manuals/desktop/troubleshoot-and-support/troubleshoot<br>manuals/desktop/troubleshoot-and-support/faqs | cases/synthetic#0002<br>cases/synthetic#0001<br>manuals/desktop/features/desktop-cli.md |
| q007 | C | #6 | 公司电脑没有管理员权限还能装 Docker Desk | manuals/desktop/setup/install/windows-permission-requirements | manuals/desktop/troubleshoot-and-support/faqs/general.md<br>manuals/desktop/setup/install/windows-install.md<br>manuals/desktop/setup/install/windows-install.md |
| q051 | C | #6 | docker compose up 说找不到 com | manuals/compose/how-tos | guides/go-prometheus-monitoring.md<br>guides/ros2.md<br>guides/postgresql.md |
| q057 | C | #6 | 怎么用 compose 一键拉起整套本地开发环境 | manuals/compose/how-tos | guides/django.md<br>manuals/compose/intro/features-uses.md<br>guides/ros2.md |

## 按类拆分

### A 语料缺失 / 标注问题（3 题）

- **q026** WSL2 的 vmmem 进程吃了十几 G 内存
  - gold: `manuals/desktop/features/wsl, manuals/desktop/use-desktop/resource-saver`｜实际: cases/synthetic#0002, cases/synthetic#0001
  - 判定依据：关键术语（WSL2/vmmem）在语料中无同现
  - 结论：大概率不可答 → 人工确认后改标（answerable=false）或补语料；不要去调检索参数
- **q041** 报 permission denied 连不上 docker.sock
  - gold: `manuals/engine/install/linux-postinstall, manuals/engine/daemon/troubleshoot`｜实际: cases/synthetic#0002, manuals/engine/security/apparmor.md
  - 判定依据：关键术语（permission/denied/docker.sock）在语料中无同现
  - 结论：大概率不可答 → 人工确认后改标（answerable=false）或补语料；不要去调检索参数
- **q050** 拉镜像报 manifest unknown
  - gold: `manuals/desktop/troubleshoot-and-support/troubleshoot, manuals/engine`｜实际: manuals/docker-hub/repos/manage/hub-images/bulk-migrate.md, manuals/desktop/features/wasm.md
  - 判定依据：关键术语（manifest/unknown）在语料中无同现
  - 结论：大概率不可答 → 人工确认后改标（answerable=false）或补语料；不要去调检索参数

### B 有语料未召回（7 题）

- **q020** 为什么容器里的 localhost 连不到另一个容器
  - gold: `manuals/engine/network/drivers/bridge`｜实际: manuals/ai/sandboxes/workflows/development.md, guides/postgresql.md
  - 判定依据：语料有相关段落但未召回
  - 结论：文档存在但未召回 → 查该文档切片是否过短/改写是否偏离
- **q021** 怎么限制容器只能访问某个网段
  - gold: `manuals/engine/network/packet-filtering-firewalls, manuals/engine/network/firewall-iptables`｜实际: manuals/faqs/platform.md, manuals/engine/swarm/networking.md
  - 判定依据：语料有相关段落但未召回
  - 结论：文档存在但未召回 → 查该文档切片是否过短/改写是否偏离
- **q022** WSL2 后端下端口映射不生效
  - gold: `manuals/desktop/features/wsl, manuals/desktop/features/networking`｜实际: cases/synthetic#0002, cases/synthetic#0001
  - 判定依据：语料有相关段落但未召回
  - 结论：文档存在但未召回 → 查该文档切片是否过短/改写是否偏离
- **q029** 磁盘空间被 Docker 占满了怎么清理
  - gold: `manuals/desktop/troubleshoot-and-support/troubleshoot, manuals/engine/daemon`｜实际: manuals/desktop/troubleshoot-and-support/faqs/macfaqs.md, manuals/desktop/troubleshoot-and-support/faqs/linuxfaqs.md
  - 判定依据：语料有相关段落但未召回
  - 结论：文档存在但未召回 → 查该文档切片是否过短/改写是否偏离
- **q040** 怎么查看各个卷分别占用了多少空间
  - gold: `manuals/desktop/use-desktop/volumes, manuals/engine/daemon`｜实际: manuals/desktop/troubleshoot-and-support/faqs/linuxfaqs.md, manuals/ai/gordon/use-cases.md
  - 判定依据：语料有相关段落但未召回
  - 结论：文档存在但未召回 → 查该文档切片是否过短/改写是否偏离
- **q046** 拉镜像报 TLS handshake timeout
  - gold: `manuals/engine/daemon/proxy, manuals/desktop/settings-and-maintenance/settings`｜实际: manuals/desktop/troubleshoot-and-support/troubleshoot/topics.md, manuals/ai/sandboxes/release-notes.md
  - 判定依据：语料有相关段落但未召回
  - 结论：文档存在但未召回 → 查该文档切片是否过短/改写是否偏离
- **q054** 改了 Dockerfile 但构建出来的镜像没变化
  - gold: `manuals/build/cache/invalidation, manuals/build/cache`｜实际: get-started/docker-concepts/building-images/writing-a-dockerfile.md, cases/synthetic#0001
  - 判定依据：语料有相关段落但未召回
  - 结论：文档存在但未召回 → 查该文档切片是否过短/改写是否偏离

### C 召回了但排太后（4 题）

- **q001** Docker Desktop 装完之后一直转圈停在 Starting 起不来
  - gold: `manuals/desktop/troubleshoot-and-support/troubleshoot, manuals/desktop/troubleshoot-and-support/faqs`｜实际: cases/synthetic#0002, cases/synthetic#0001
  - 判定依据：第 8 名命中
  - 结论：第 8 名命中 → 融合/重排可救
- **q007** 公司电脑没有管理员权限还能装 Docker Desktop 吗
  - gold: `manuals/desktop/setup/install/windows-permission-requirements`｜实际: manuals/desktop/troubleshoot-and-support/faqs/general.md, manuals/desktop/setup/install/windows-install.md
  - 判定依据：第 6 名命中
  - 结论：第 6 名命中 → 融合/重排可救
- **q051** docker compose up 说找不到 compose 文件
  - gold: `manuals/compose/how-tos`｜实际: guides/go-prometheus-monitoring.md, guides/ros2.md
  - 判定依据：第 6 名命中
  - 结论：第 6 名命中 → 融合/重排可救
- **q057** 怎么用 compose 一键拉起整套本地开发环境
  - gold: `manuals/compose/how-tos`｜实际: guides/django.md, manuals/compose/intro/features-uses.md
  - 判定依据：第 6 名命中
  - 结论：第 6 名命中 → 融合/重排可救

## 案例库污染度（W7 闭环的副作用）

工单案例（`cases/*`）只有很短的内容，却含「容器/启动/根因/解决方案」这类高频词，
可能像 release-notes 一样变成「万能吸铁石」。这里量化它出现在 Top-5 的频率：

- 案例文档出现在 Top-5 的题：**17 / 60**
- 其中**位置无关（噪声）**的题：**17**（q001, q002, q003, q005, q008, q009, q012, q017, q022, q025, q026, q027, q041, q046, q048, q054, q055）

> 结论：案例只在「命中它的那一刻」有用，其余时候是噪声。
> 建议改为**补充召回**：主检索先不出案例，仅当主结果为空或用户明确提到历史工单时再召回。

## 优先级建议

1. **先处理 A 类（3 题）**：成本最低，改标注即可，且能立刻修正指标口径。
2. **再处理 C 类（4 题）**：重排在这类题上才可能有效（候选里已有正确答案）。
3. **最后处理 B 类（7 题）**：需要动切片/改写，代价最大。
