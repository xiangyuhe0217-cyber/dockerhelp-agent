# 评测集草稿（60 条）

> W1 产出。目的**不是**现在就标 `gold_doc_ids`，而是先验证：**中文提问 → 英文文档**，哪些问题问得出来、哪些是知识库答不了的。
> W2 抓完索引后，把「预期目录」换成真实 `doc_id`，再补 `gold_points`。

**构成**：40 条 simple（单跳） + 15 条 multi_hop（需 2 篇以上） + 5 条 unanswerable（考拒答/转工具）

**标注规则**
- `simple`：答案能在单一章节里找到，通常一段话够
- `multi_hop`：需要跨章节拼（如"端口发布 + 防火墙"、"缓存 + 多阶段构建"）
- `unanswerable`：文档里**没有**答案（公司内部信息 / 实时状态），正确行为是**拒答或转工具**，不是硬编
- `预期目录` 是**粗粒度**的（`manuals/xxx` 级别），W2 再细到文件

---

## 一、安装与启动失败

| # | 问题 | 类型 | 难度 | 预期目录 | 判分要点 |
|---|---|---|---|---|---|
| 1 | Docker Desktop 装完之后一直卡在 Starting，转圈很久没反应 | simple | easy | manuals/desktop | 需重启 / 重置 / 查看日志 |
| 2 | Windows 上装 Docker Desktop 一定要开 WSL2 吗 | simple | easy | manuals/desktop | WSL2 与 Hyper-V 两种后端 |
| 3 | 安装时提示要开启虚拟化，在哪里打开 | simple | easy | manuals/desktop | BIOS/UEFI 虚拟化 + Windows 功能 |
| 4 | Mac 上打开提示"已损坏，无法打开"怎么办 | simple | medium | manuals/desktop | 安全性与隐私 / 命令行绕过 |
| 5 | 升级 Docker Desktop 之后容器和镜像都不见了 | multi_hop | hard | manuals/desktop | 升级路径 + 数据位置 + 恢复 |
| 6 | 卸载 Docker Desktop 会把镜像和容器一起删掉吗 | simple | easy | manuals/desktop | 保留 / 删除数据两种选项 |
| 7 | 能不能装在 Windows Server 上 | simple | medium | manuals/desktop | 支持矩阵 |
| 8 | 公司电脑没有管理员权限，还能装吗 | simple | medium | manuals/desktop | 权限要求 |
| 9 | 启动报 WSL kernel version too low 怎么处理 | simple | medium | manuals/desktop | wsl --update |
| 10 | Docker Desktop 支持哪些 Windows 版本 | simple | easy | manuals/desktop | 版本要求 |

## 二、网络与端口

| # | 问题 | 类型 | 难度 | 预期目录 | 判分要点 |
|---|---|---|---|---|---|
| 11 | 容器里怎么访问宿主机上跑的服务 | simple | medium | manuals/network | host.docker.internal / 网关 IP |
| 12 | 宿主机访问不到容器里监听的 8080 端口 | multi_hop | hard | manuals/network | -p 发布 + 监听地址 + 防火墙 |
| 13 | 两个容器之间怎么互相访问 | simple | easy | manuals/network | 自定义 bridge + 服务名 |
| 14 | 容器重启之后 IP 地址会变吗 | simple | medium | manuals/network | 动态分配 / 静态 IP |
| 15 | 怎么让容器走公司的 HTTP 代理拉镜像 | multi_hop | hard | manuals/desktop + manuals/network | 客户端代理 vs daemon 代理 |
| 16 | 端口被占用了，怎么查是哪个进程 | simple | easy | manuals/desktop | 端口排查 |
| 17 | bridge 网络和 host 网络有什么区别 | simple | medium | manuals/network | 驱动对比 |
| 18 | 为什么容器里的 localhost 连不上另一个容器 | simple | medium | manuals/network | 每个容器独立网络命名空间 |
| 19 | 怎么限制容器只能访问某个网段 | simple | hard | manuals/network | 自定义网络 / 防火墙 |
| 20 | WSL2 后端下端口映射不生效 | multi_hop | hard | manuals/desktop + manuals/network | WSL 集成 + netsh 端口转发 |

## 三、资源与性能

| # | 问题 | 类型 | 难度 | 预期目录 | 判分要点 |
|---|---|---|---|---|---|
| 21 | 容器内存占用太高把电脑卡死了怎么办 | simple | medium | manuals/desktop | 资源限制 + 虚拟机内存设置 |
| 22 | 怎么给容器限制只能用几个 CPU 核 | simple | medium | manuals/engine | --cpus / --cpuset |
| 23 | Docker Desktop 默认给虚拟机分多少内存，在哪改 | simple | easy | manuals/desktop | Settings → Resources |
| 24 | WSL2 的 vmmem 进程吃了十几个 G 内存 | multi_hop | hard | manuals/desktop + manuals/engine | 内存回收 + .wslconfig |
| 25 | 镜像构建特别慢，有什么加速办法 | multi_hop | medium | manuals/build | BuildKit + 缓存 + 顺序优化 |
| 26 | 容器里的 Java 应用被 OOM kill 了，怎么定位 | simple | hard | manuals/engine | 退出码 137 + dmesg |
| 27 | 磁盘被 Docker 占满了，怎么清理 | simple | easy | manuals/engine | system prune 系列 |
| 28 | 怎么实时看容器的 CPU 和内存占用 | simple | easy | manuals/engine | docker stats |
| 29 | 怎么限制容器的 swap 使用 | simple | hard | manuals/engine | --memory-swap |
| 30 | 同时跑多个容器，资源怎么分配比较合理 | simple | medium | manuals/engine | 限制总和不超过宿主机 |

## 四、存储与卷

| # | 问题 | 类型 | 难度 | 预期目录 | 判分要点 |
|---|---|---|---|---|---|
| 31 | 容器里的数据到底存在宿主机哪个目录 | simple | medium | manuals/volumes | /var/lib/docker/volumes |
| 32 | 怎么备份一个数据卷 | simple | medium | manuals/volumes | 临时容器 tar 打包 |
| 33 | bind mount 和 named volume 有什么区别 | simple | medium | manuals/volumes | 管理方式 / 适用场景 |
| 34 | 删除容器之后里面的数据还在吗 | simple | easy | manuals/volumes | 卷独立于容器生命周期 |
| 35 | 怎么把宿主机的目录挂进容器 | simple | easy | manuals/volumes | -v / --mount |
| 36 | 挂载进去的目录权限不对，容器里写不进去 | multi_hop | hard | manuals/volumes | uid/gid + 用户映射 |
| 37 | 多台机器之间能共享同一个卷吗 | simple | medium | manuals/volumes | 卷驱动 |
| 38 | tmpfs 挂载适合什么场景 | simple | medium | manuals/volumes | 内存盘 / 非持久 |
| 39 | 镜像层和容器层是怎么叠加的 | simple | hard | manuals/engine | overlay2 / 写时复制 |
| 40 | 怎么查看各个卷占用了多少空间 | simple | easy | manuals/engine | system df -v |

## 五、权限与报错

| # | 问题 | 类型 | 难度 | 预期目录 | 判分要点 |
|---|---|---|---|---|---|
| 41 | 报 permission denied 连不上 docker.sock | simple | medium | manuals/engine | socket 权限 / 用户组 |
| 42 | 怎么让普通用户不用 sudo 就能跑 docker | simple | easy | manuals/engine | 加入 docker 组 + 重登 |
| 43 | Dockerfile 里怎么用非 root 用户跑应用 | simple | medium | manuals/build | USER 指令 |
| 44 | 报 Cannot connect to the Docker daemon，怎么一步步排查 | multi_hop | hard | manuals/engine + manuals/desktop | 服务状态 → 上下文 → 权限 |
| 45 | Windows 上敲 docker 提示命令找不到 | simple | easy | manuals/desktop | PATH / 安装完整性 |
| 46 | 拉镜像报 TLS handshake timeout | multi_hop | hard | manuals/engine + manuals/desktop | 代理 / 镜像加速 / DNS |
| 47 | 报 no space left on device 但磁盘明明还有空间 | multi_hop | hard | manuals/engine | inode / overlay 层 |
| 48 | 容器一启动就退出，看不到任何日志 | simple | medium | manuals/engine | logs / 前台进程 / 退出码 |
| 49 | 报 port is already allocated | simple | easy | manuals/engine | 已占用端口 |
| 50 | 拉镜像报 manifest unknown | simple | medium | manuals/engine | tag 不存在 / 架构不符 |

## 六、Compose 与构建

| # | 问题 | 类型 | 难度 | 预期目录 | 判分要点 |
|---|---|---|---|---|---|
| 51 | docker compose up 说找不到 compose 文件 | simple | easy | manuals/compose | 文件名 / -f 参数 |
| 52 | compose 里怎么传环境变量给容器 | simple | easy | manuals/compose | environment / env_file |
| 53 | 怎么保证数据库先起来，应用后起来 | simple | medium | manuals/compose | depends_on + healthcheck |
| 54 | 改了 Dockerfile 但构建出来的镜像没变化 | multi_hop | medium | manuals/build | 层缓存 / --no-cache |
| 55 | 怎么把镜像体积做小 | multi_hop | medium | manuals/build | 多阶段 / 基础镜像 / 清理 |
| 56 | compose 里把宿主目录挂进服务怎么写 | simple | easy | manuals/compose | volumes 语法 |
| 57 | 怎么用 compose 一键拉起整套本地开发环境 | simple | medium | manuals/compose | 多服务编排 |
| 58 | build 时 COPY 报文件不存在 | simple | easy | manuals/build | .dockerignore / 上下文 |
| 59 | 多阶段构建怎么只保留编译产物 | simple | medium | manuals/build | FROM ... AS builder |
| 60 | compose 里能直接用服务名当地址访问吗 | simple | medium | manuals/compose | 默认网络 + DNS |

---

## 七、Unanswerable（5 条，考"拒答 + 转工具"）

这 5 条**故意**问知识库里没有的东西。正确行为是：明确说"文档里没有"，并（可选地）调用工具查工单库。

| # | 问题 | 类型 | 正确行为 |
|---|---|---|---|
| U1 | 我们公司内网 VPN 怎么配才能访问内部镜像仓库 | unanswerable | 拒答 + 说明不覆盖企业内部信息 |
| U2 | 这个月我们团队拉镜像花了多少钱 | unanswerable | 拒答（无计费数据） |
| U3 | 我们 K8s 集群里哪台节点磁盘快满了 | unanswerable | 拒答（知识库只有 Docker 文档） |
| U4 | 同事小王电脑上现在有几个容器在跑 | unanswerable | 拒答（无远端状态） |
| U5 | 我们采购 Docker Business 能打几折 | unanswerable | 拒答（无商务信息） |

---

## 八、W2 待办：把草稿变成 `eval_cases`

1. 索引建好后，跑一次"每条问题 → 检索 Top5"，人工看命中：
   - 命中且相关 → 记下真实 `doc_id` 进 `gold_doc_ids`
   - 命中但无关 → 改题（说明表述与文档用词差太远）
   - 完全没命中 → 优先怀疑是**问题写法**，其次才是知识库缺内容
2. 每条补 `gold_points`（3 个左右要点），供"答案正确率"人工判分
3. 全部用中文提问、英文文档 —— **这是 bge-m3 跨语言能力的主战场，不能改成英文提问**
4. 写入 `eval_cases` 表，`type` / `difficulty` / `topic` 三个维度都要填，后续才能出分维度统计

> 提醒：如果 W2 发现"安装类"问题大多命中不到 `manuals/desktop`，
> 说明文档结构和你预期不同，**先修检索与切片，不要改题去迁就**。
