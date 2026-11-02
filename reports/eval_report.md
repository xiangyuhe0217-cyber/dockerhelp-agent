# 检索评测报告

> 语料：934 篇文档 / 8794 chunk　可答题：59 条（另有 6 条拒答题，需生成层评测，见 W5）
> 相关性判定：召回 chunk 所属文档的路径以任一 `gold_path` 开头即算命中

## 1. 总体对比

| 配置 | hit@1 | hit@5 | hit@10 | recall@5 | recall@10 | mrr | ndcg@10 | latency_ms | n |
|---|---|---|---|---|---|---|---|---|---|
| **E1** 纯向量（百炼 embedding） | 0.339 | 0.610 | 0.797 | 0.421 | 0.565 | 0.476 | 0.535 | 193 | 59 |
| **E2** 纯关键词 BM25 | 0.068 | 0.170 | 0.305 | 0.110 | 0.203 | 0.117 | 0.163 | 19 | 59 |
| **E3** 向量+BM25+RRF(k=60) | 0.237 | 0.678 | 0.814 | 0.480 | 0.590 | 0.406 | 0.500 | 203 | 59 |
| **E4** E3+重排(候选20) | 0.237 | 0.644 | 0.831 | 0.463 | 0.624 | 0.429 | 0.541 | 709 | 59 |
| **E5** 重排候选扩到50 | 0.254 | 0.678 | 0.780 | 0.489 | 0.607 | 0.434 | 0.517 | 832 | 59 |
| **E6** RRF k=20（更看首位） | 0.254 | 0.695 | 0.814 | 0.489 | 0.590 | 0.417 | 0.507 | 188 | 59 |
| **E7** RRF k=100（更平滑） | 0.237 | 0.678 | 0.814 | 0.480 | 0.590 | 0.406 | 0.500 | 194 | 59 |
| **E8** BM25 走查询改写 | 0.322 | 0.695 | 0.831 | 0.483 | 0.633 | 0.466 | 0.540 | 650 | 59 |
| **E9** 改写+重排(候选50) | 0.271 | 0.712 | 0.814 | 0.517 | 0.633 | 0.463 | 0.545 | 828 | 59 |
| **E10** E8 + 排除 release-notes | 0.339 | 0.780 | 0.848 | 0.542 | 0.667 | 0.499 | 0.577 | 203 | 59 |
| **E11** E1 + 排除 release-notes | 0.373 | 0.695 | 0.831 | 0.472 | 0.607 | 0.516 | 0.576 | 180 | 59 |
| **E12** E10 但排除工单案例（污染对照） | 0.373 | 0.780 | 0.915 | 0.548 | 0.701 | 0.540 | 0.621 | 209 | 59 |

## 2. 按题型拆解

**E1 纯向量（百炼 embedding）**

| 题型 | hit@1 | hit@5 | hit@10 | recall@5 | recall@10 | mrr | ndcg@10 | latency_ms | n |
|---|---|---|---|---|---|---|---|---|---|
| multi_hop | 0.370 | 0.556 | 0.778 | 0.272 | 0.401 | 0.472 | 0.502 | 177 | 27 |
| simple | 0.312 | 0.656 | 0.812 | 0.547 | 0.703 | 0.480 | 0.563 | 207 | 32 |

**E2 纯关键词 BM25**

| 题型 | hit@1 | hit@5 | hit@10 | recall@5 | recall@10 | mrr | ndcg@10 | latency_ms | n |
|---|---|---|---|---|---|---|---|---|---|
| multi_hop | 0.111 | 0.222 | 0.407 | 0.111 | 0.204 | 0.169 | 0.223 | 13 | 27 |
| simple | 0.031 | 0.125 | 0.219 | 0.109 | 0.203 | 0.074 | 0.112 | 24 | 32 |

**E3 向量+BM25+RRF(k=60)**

| 题型 | hit@1 | hit@5 | hit@10 | recall@5 | recall@10 | mrr | ndcg@10 | latency_ms | n |
|---|---|---|---|---|---|---|---|---|---|
| multi_hop | 0.222 | 0.593 | 0.741 | 0.290 | 0.420 | 0.384 | 0.460 | 195 | 27 |
| simple | 0.250 | 0.750 | 0.875 | 0.641 | 0.734 | 0.424 | 0.533 | 210 | 32 |

**E4 E3+重排(候选20)**

| 题型 | hit@1 | hit@5 | hit@10 | recall@5 | recall@10 | mrr | ndcg@10 | latency_ms | n |
|---|---|---|---|---|---|---|---|---|---|
| multi_hop | 0.185 | 0.667 | 0.741 | 0.364 | 0.438 | 0.377 | 0.485 | 693 | 27 |
| simple | 0.281 | 0.625 | 0.906 | 0.547 | 0.781 | 0.473 | 0.589 | 723 | 32 |

**E5 重排候选扩到50**

| 题型 | hit@1 | hit@5 | hit@10 | recall@5 | recall@10 | mrr | ndcg@10 | latency_ms | n |
|---|---|---|---|---|---|---|---|---|---|
| multi_hop | 0.185 | 0.667 | 0.741 | 0.383 | 0.494 | 0.368 | 0.463 | 836 | 27 |
| simple | 0.312 | 0.688 | 0.812 | 0.578 | 0.703 | 0.490 | 0.562 | 828 | 32 |

**E6 RRF k=20（更看首位）**

| 题型 | hit@1 | hit@5 | hit@10 | recall@5 | recall@10 | mrr | ndcg@10 | latency_ms | n |
|---|---|---|---|---|---|---|---|---|---|
| multi_hop | 0.259 | 0.630 | 0.778 | 0.309 | 0.438 | 0.406 | 0.484 | 181 | 27 |
| simple | 0.250 | 0.750 | 0.844 | 0.641 | 0.719 | 0.427 | 0.527 | 195 | 32 |

**E7 RRF k=100（更平滑）**

| 题型 | hit@1 | hit@5 | hit@10 | recall@5 | recall@10 | mrr | ndcg@10 | latency_ms | n |
|---|---|---|---|---|---|---|---|---|---|
| multi_hop | 0.222 | 0.593 | 0.741 | 0.290 | 0.420 | 0.384 | 0.460 | 188 | 27 |
| simple | 0.250 | 0.750 | 0.875 | 0.641 | 0.734 | 0.424 | 0.533 | 200 | 32 |

**E8 BM25 走查询改写**

| 题型 | hit@1 | hit@5 | hit@10 | recall@5 | recall@10 | mrr | ndcg@10 | latency_ms | n |
|---|---|---|---|---|---|---|---|---|---|
| multi_hop | 0.333 | 0.704 | 0.815 | 0.352 | 0.512 | 0.467 | 0.539 | 614 | 27 |
| simple | 0.312 | 0.688 | 0.844 | 0.594 | 0.734 | 0.466 | 0.541 | 681 | 32 |

**E9 改写+重排(候选50)**

| 题型 | hit@1 | hit@5 | hit@10 | recall@5 | recall@10 | mrr | ndcg@10 | latency_ms | n |
|---|---|---|---|---|---|---|---|---|---|
| multi_hop | 0.185 | 0.704 | 0.852 | 0.389 | 0.549 | 0.407 | 0.529 | 830 | 27 |
| simple | 0.344 | 0.719 | 0.781 | 0.625 | 0.703 | 0.510 | 0.558 | 826 | 32 |

**E10 E8 + 排除 release-notes**

| 题型 | hit@1 | hit@5 | hit@10 | recall@5 | recall@10 | mrr | ndcg@10 | latency_ms | n |
|---|---|---|---|---|---|---|---|---|---|
| multi_hop | 0.333 | 0.815 | 0.815 | 0.426 | 0.549 | 0.484 | 0.560 | 209 | 27 |
| simple | 0.344 | 0.750 | 0.875 | 0.641 | 0.766 | 0.512 | 0.591 | 198 | 32 |

**E11 E1 + 排除 release-notes**

| 题型 | hit@1 | hit@5 | hit@10 | recall@5 | recall@10 | mrr | ndcg@10 | latency_ms | n |
|---|---|---|---|---|---|---|---|---|---|
| multi_hop | 0.407 | 0.704 | 0.815 | 0.346 | 0.457 | 0.523 | 0.558 | 178 | 27 |
| simple | 0.344 | 0.688 | 0.844 | 0.578 | 0.734 | 0.510 | 0.591 | 182 | 32 |

**E12 E10 但排除工单案例（污染对照）**

| 题型 | hit@1 | hit@5 | hit@10 | recall@5 | recall@10 | mrr | ndcg@10 | latency_ms | n |
|---|---|---|---|---|---|---|---|---|---|
| multi_hop | 0.370 | 0.815 | 0.926 | 0.438 | 0.605 | 0.540 | 0.629 | 211 | 27 |
| simple | 0.375 | 0.750 | 0.906 | 0.641 | 0.781 | 0.539 | 0.614 | 208 | 32 |

## 3. 按难度拆解

**E1**

| 难度 | hit@1 | hit@5 | hit@10 | recall@5 | recall@10 | mrr | ndcg@10 | latency_ms | n |
|---|---|---|---|---|---|---|---|---|---|
| easy | 0.316 | 0.684 | 0.895 | 0.579 | 0.763 | 0.496 | 0.588 | 229 | 19 |
| hard | 0.308 | 0.538 | 0.769 | 0.308 | 0.423 | 0.457 | 0.502 | 181 | 13 |
| medium | 0.370 | 0.593 | 0.741 | 0.364 | 0.494 | 0.472 | 0.514 | 173 | 27 |

**E2**

| 难度 | hit@1 | hit@5 | hit@10 | recall@5 | recall@10 | mrr | ndcg@10 | latency_ms | n |
|---|---|---|---|---|---|---|---|---|---|
| easy | 0.000 | 0.053 | 0.263 | 0.053 | 0.237 | 0.036 | 0.093 | 31 | 19 |
| hard | 0.154 | 0.385 | 0.462 | 0.231 | 0.269 | 0.252 | 0.297 | 13 | 13 |
| medium | 0.074 | 0.148 | 0.259 | 0.093 | 0.148 | 0.110 | 0.147 | 12 | 27 |

**E3**

| 难度 | hit@1 | hit@5 | hit@10 | recall@5 | recall@10 | mrr | ndcg@10 | latency_ms | n |
|---|---|---|---|---|---|---|---|---|---|
| easy | 0.210 | 0.842 | 0.947 | 0.711 | 0.816 | 0.434 | 0.555 | 202 | 19 |
| hard | 0.231 | 0.462 | 0.692 | 0.269 | 0.423 | 0.361 | 0.441 | 188 | 13 |
| medium | 0.259 | 0.667 | 0.778 | 0.420 | 0.512 | 0.407 | 0.489 | 211 | 27 |

**E4**

| 难度 | hit@1 | hit@5 | hit@10 | recall@5 | recall@10 | mrr | ndcg@10 | latency_ms | n |
|---|---|---|---|---|---|---|---|---|---|
| easy | 0.263 | 0.579 | 0.842 | 0.500 | 0.737 | 0.449 | 0.548 | 732 | 19 |
| hard | 0.154 | 0.692 | 0.769 | 0.385 | 0.462 | 0.353 | 0.489 | 702 | 13 |
| medium | 0.259 | 0.667 | 0.852 | 0.475 | 0.624 | 0.452 | 0.562 | 696 | 27 |

**E5**

| 难度 | hit@1 | hit@5 | hit@10 | recall@5 | recall@10 | mrr | ndcg@10 | latency_ms | n |
|---|---|---|---|---|---|---|---|---|---|
| easy | 0.263 | 0.632 | 0.789 | 0.500 | 0.684 | 0.436 | 0.515 | 807 | 19 |
| hard | 0.154 | 0.615 | 0.692 | 0.385 | 0.500 | 0.322 | 0.439 | 845 | 13 |
| medium | 0.296 | 0.741 | 0.815 | 0.531 | 0.605 | 0.488 | 0.555 | 843 | 27 |

**E6**

| 难度 | hit@1 | hit@5 | hit@10 | recall@5 | recall@10 | mrr | ndcg@10 | latency_ms | n |
|---|---|---|---|---|---|---|---|---|---|
| easy | 0.210 | 0.842 | 0.895 | 0.711 | 0.763 | 0.435 | 0.542 | 195 | 19 |
| hard | 0.231 | 0.538 | 0.769 | 0.308 | 0.462 | 0.367 | 0.463 | 182 | 13 |
| medium | 0.296 | 0.667 | 0.778 | 0.420 | 0.531 | 0.429 | 0.504 | 186 | 27 |

**E7**

| 难度 | hit@1 | hit@5 | hit@10 | recall@5 | recall@10 | mrr | ndcg@10 | latency_ms | n |
|---|---|---|---|---|---|---|---|---|---|
| easy | 0.210 | 0.842 | 0.947 | 0.711 | 0.816 | 0.434 | 0.555 | 202 | 19 |
| hard | 0.231 | 0.462 | 0.692 | 0.269 | 0.423 | 0.361 | 0.441 | 180 | 13 |
| medium | 0.259 | 0.667 | 0.778 | 0.420 | 0.512 | 0.407 | 0.488 | 196 | 27 |

**E8**

| 难度 | hit@1 | hit@5 | hit@10 | recall@5 | recall@10 | mrr | ndcg@10 | latency_ms | n |
|---|---|---|---|---|---|---|---|---|---|
| easy | 0.210 | 0.632 | 0.842 | 0.579 | 0.737 | 0.406 | 0.512 | 646 | 19 |
| hard | 0.308 | 0.692 | 0.692 | 0.385 | 0.500 | 0.422 | 0.480 | 584 | 13 |
| medium | 0.407 | 0.741 | 0.889 | 0.463 | 0.624 | 0.530 | 0.589 | 685 | 27 |

**E9**

| 难度 | hit@1 | hit@5 | hit@10 | recall@5 | recall@10 | mrr | ndcg@10 | latency_ms | n |
|---|---|---|---|---|---|---|---|---|---|
| easy | 0.316 | 0.737 | 0.789 | 0.632 | 0.737 | 0.495 | 0.537 | 840 | 19 |
| hard | 0.154 | 0.615 | 0.769 | 0.346 | 0.538 | 0.374 | 0.493 | 789 | 13 |
| medium | 0.296 | 0.741 | 0.852 | 0.518 | 0.605 | 0.483 | 0.575 | 837 | 27 |

**E10**

| 难度 | hit@1 | hit@5 | hit@10 | recall@5 | recall@10 | mrr | ndcg@10 | latency_ms | n |
|---|---|---|---|---|---|---|---|---|---|
| easy | 0.263 | 0.789 | 0.895 | 0.684 | 0.763 | 0.475 | 0.586 | 200 | 19 |
| hard | 0.308 | 0.692 | 0.692 | 0.423 | 0.538 | 0.423 | 0.495 | 200 | 13 |
| medium | 0.407 | 0.815 | 0.889 | 0.500 | 0.660 | 0.553 | 0.610 | 206 | 27 |

**E11**

| 难度 | hit@1 | hit@5 | hit@10 | recall@5 | recall@10 | mrr | ndcg@10 | latency_ms | n |
|---|---|---|---|---|---|---|---|---|---|
| easy | 0.368 | 0.737 | 0.895 | 0.605 | 0.789 | 0.539 | 0.615 | 182 | 19 |
| hard | 0.385 | 0.692 | 0.846 | 0.385 | 0.500 | 0.551 | 0.598 | 178 | 13 |
| medium | 0.370 | 0.667 | 0.778 | 0.420 | 0.531 | 0.483 | 0.537 | 180 | 27 |

**E12**

| 难度 | hit@1 | hit@5 | hit@10 | recall@5 | recall@10 | mrr | ndcg@10 | latency_ms | n |
|---|---|---|---|---|---|---|---|---|---|
| easy | 0.316 | 0.789 | 0.895 | 0.684 | 0.763 | 0.510 | 0.605 | 205 | 19 |
| hard | 0.308 | 0.692 | 0.846 | 0.423 | 0.615 | 0.455 | 0.557 | 198 | 13 |
| medium | 0.444 | 0.815 | 0.963 | 0.512 | 0.698 | 0.602 | 0.663 | 217 | 27 |

## 4. 逐题明细（以最强配置为基准）

基准：**E10**　✅ = Top-5 内命中

| id | 问题 | 结果 | 首个命中名次 | 命中文档 |
|---|---|---|---|---|
| q001 | Docker Desktop 装完之后一直转圈停在 Star | ❌ | 8 | manuals/desktop/troubleshoot-and-support/troubleshoo |
| q002 | Windows 上装 Docker Desktop 一定要开 | ✅ | 5 | manuals/desktop/setup/install/windows-install.md |
| q003 | 安装时提示要在 BIOS 里开启虚拟化，具体在哪开 | ✅ | 4 | manuals/desktop/setup/install/windows-install.md |
| q004 | Mac 上打开提示 Docker.app 已损坏无法打开怎么 | ✅ | 4 | manuals/desktop/setup/install/mac-install.md |
| q005 | 升级 Docker Desktop 之后容器和镜像全都不见了 | ✅ | 4 | manuals/desktop/settings-and-maintenance/backup-and- |
| q006 | 卸载 Docker Desktop 会不会把镜像和容器一起删 | ✅ | 2 | manuals/desktop/uninstall.md |
| q007 | 公司电脑没有管理员权限还能装 Docker Desktop  | ❌ | 6 | manuals/desktop/setup/install/windows-permission-req |
| q008 | 启动报 WSL kernel version too low | ✅ | 2 | manuals/desktop/features/wsl/best-practices.md |
| q009 | Docker Desktop 支持哪些 Windows 版本 | ✅ | 1 | manuals/desktop/setup/install/windows-install.md |
| q010 | 安装过程中失败，有哪些常见原因 | ✅ | 2 | manuals/desktop/troubleshoot-and-support/troubleshoo |
| q011 | 怎么彻底重装 Docker Desktop 才能干净 | ✅ | 1 | manuals/desktop/uninstall.md |
| q012 | Docker Desktop 启动失败，去哪里看日志 | ✅ | 5 | manuals/desktop/troubleshoot-and-support/troubleshoo |
| q013 | 容器里怎么访问宿主机上跑的服务 | ✅ | 1 | manuals/engine/network/drivers/host.md |
| q014 | 宿主机访问不到容器里监听的 8080 端口 | ✅ | 4 | manuals/engine/network/port-publishing.md |
| q015 | 两个容器之间怎么互相访问 | ✅ | 1 | manuals/engine/network/drivers/bridge.md |
| q016 | 容器重启之后 IP 地址会变吗 | ✅ | 5 | manuals/engine/network/drivers/bridge.md |
| q017 | 怎么让容器走公司的 HTTP 代理拉镜像 | ✅ | 4 | manuals/desktop/settings-and-maintenance/settings.md |
| q018 | 端口被占用了，怎么查是哪个进程在用 | ✅ | 1 | manuals/desktop/troubleshoot-and-support/troubleshoo |
| q019 | bridge 网络和 host 网络有什么区别，该选哪个 | ✅ | 1 | manuals/engine/network/drivers/bridge.md |
| q020 | 为什么容器里的 localhost 连不到另一个容器 | ❌ | - |  |
| q021 | 怎么限制容器只能访问某个网段 | ❌ | - |  |
| q022 | WSL2 后端下端口映射不生效 | ❌ | - |  |
| q023 | 容器内存占用太高把电脑卡死了怎么办 | ✅ | 3 | manuals/engine/containers/resource_constraints.md |
| q024 | 怎么给容器限制只能用两个 CPU 核 | ✅ | 1 | manuals/engine/containers/resource_constraints.md |
| q025 | Docker Desktop 默认给虚拟机分多少内存，在哪改 | ✅ | 2 | manuals/desktop/settings-and-maintenance/settings.md |
| q026 | WSL2 的 vmmem 进程吃了十几 G 内存 | ❌ | - |  |
| q027 | 镜像构建特别慢，有什么加速办法 | ✅ | 1 | manuals/build/cache/optimize.md |
| q028 | 容器里的 Java 应用被 OOM kill 了怎么定位 | ✅ | 1 | manuals/engine/containers/resource_constraints.md |
| q029 | 磁盘空间被 Docker 占满了怎么清理 | ❌ | - |  |
| q030 | 怎么实时查看容器的 CPU 和内存占用 | ✅ | 4 | manuals/engine/containers/runmetrics.md |
| q031 | 怎么限制容器能用的 swap 大小 | ✅ | 1 | manuals/engine/containers/resource_constraints.md |
| q032 | 容器里的数据到底存在宿主机哪个目录 | ✅ | 3 | manuals/engine/storage/drivers/device-mapper-driver. |
| q033 | 怎么备份一个数据卷 | ✅ | 1 | manuals/engine/storage/volumes.md |
| q034 | bind mount 和 named volume 有什么区 | ✅ | 2 | manuals/engine/storage/bind-mounts.md |
| q035 | 删除容器之后里面的数据还在吗 | ✅ | 3 | manuals/engine/storage/volumes.md |
| q036 | 怎么把宿主机的目录挂载到容器里 | ✅ | 2 | manuals/engine/storage/bind-mounts.md |
| q037 | 挂载进去的目录权限不对，容器里写不进去 | ✅ | 4 | manuals/desktop/troubleshoot-and-support/troubleshoo |
| q038 | 多台机器之间能共享同一个数据卷吗 | ✅ | 1 | manuals/engine/storage/volumes.md |
| q039 | 镜像层和容器层是怎么叠加起来的 | ✅ | 1 | manuals/engine/storage/drivers/overlayfs-driver.md |
| q040 | 怎么查看各个卷分别占用了多少空间 | ❌ | - |  |
| q041 | 报 permission denied 连不上 docker | ❌ | - |  |
| q042 | 怎么让普通用户不用 sudo 就能跑 docker 命令 | ✅ | 1 | manuals/engine/install/linux-postinstall.md |
| q043 | Dockerfile 里怎么用非 root 用户运行应用 | ✅ | 1 | manuals/build/building/best-practices.md |
| q044 | 报 Cannot connect to the Docker | ✅ | 1 | manuals/engine/daemon/troubleshoot.md |
| q045 | Windows 上敲 docker 提示命令找不到 | ✅ | 4 | manuals/desktop/setup/install/windows-install.md |
| q046 | 拉镜像报 TLS handshake timeout | ❌ | - |  |
| q047 | 报 no space left on device 但磁盘明 | ✅ | 2 | manuals/engine/storage/drivers/device-mapper-driver. |
| q048 | 容器一启动就退出，看不到任何日志 | ✅ | 3 | manuals/engine/containers/start-containers-automatic |
| q049 | 报 port is already allocated 怎么 | ✅ | 5 | get-started/docker-concepts/running-containers/publi |
| q051 | docker compose up 说找不到 compose | ❌ | 6 | manuals/compose/how-tos/oci-artifact.md |
| q052 | compose 里怎么把环境变量传给容器 | ✅ | 1 | manuals/compose/how-tos/environment-variables/variab |
| q053 | 怎么保证数据库先起来应用后起来 | ✅ | 1 | manuals/compose/how-tos/startup-order.md |
| q054 | 改了 Dockerfile 但构建出来的镜像没变化 | ❌ | - |  |
| q055 | 怎么把镜像体积做小一点 | ✅ | 5 | get-started/docker-concepts/building-images/multi-st |
| q056 | compose 里把宿主目录挂进服务要怎么写 | ✅ | 2 | manuals/compose/how-tos/networking.md |
| q057 | 怎么用 compose 一键拉起整套本地开发环境 | ❌ | 6 | manuals/compose/how-tos/production.md |
| q058 | build 的时候 COPY 报文件不存在 | ✅ | 2 | manuals/build/concepts/context.md |
| q059 | 多阶段构建怎么只保留编译产物 | ✅ | 1 | manuals/build/building/multi-stage.md |
| q060 | compose 里能直接用服务名当地址访问吗 | ✅ | 1 | manuals/compose/how-tos/networking.md |

## 5. 怎么读这张表

- **hit@5**：Top-5 里至少有一条相关文档，最直观的「用户能不能看到答案」
- **recall@5**：多跳题需要覆盖多个板块，这个指标惩罚「只中一个」
- **mrr**：第一个相关结果的平均倒数名次，衡量「排得靠不靠前」
- **latency**：单题平均耗时（含 embedding / rerank 的网络往返）
