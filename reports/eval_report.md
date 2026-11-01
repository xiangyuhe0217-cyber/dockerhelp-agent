# 检索评测报告

> 语料：934 篇文档 / 8794 chunk　可答题：60 条（另有 5 条拒答题，需生成层评测，见 W5）
> 相关性判定：召回 chunk 所属文档的路径以任一 `gold_path` 开头即算命中

## 1. 总体对比

| 配置 | hit@1 | hit@5 | hit@10 | recall@5 | recall@10 | mrr | ndcg@10 | latency_ms | n |
|---|---|---|---|---|---|---|---|---|---|
| **E1** 纯向量（百炼 embedding） | 0.333 | 0.650 | 0.800 | 0.442 | 0.572 | 0.477 | 0.541 | 191 | 60 |
| **E2** 纯关键词 BM25 | 0.100 | 0.217 | 0.333 | 0.142 | 0.225 | 0.151 | 0.190 | 21 | 60 |
| **E3** 向量+BM25+RRF(k=60) | 0.250 | 0.733 | 0.850 | 0.508 | 0.614 | 0.436 | 0.533 | 201 | 60 |
| **E4** E3+重排(候选20) | 0.233 | 0.667 | 0.867 | 0.481 | 0.647 | 0.437 | 0.557 | 751 | 60 |
| **E5** 重排候选扩到50 | 0.250 | 0.683 | 0.800 | 0.489 | 0.614 | 0.439 | 0.526 | 1091 | 60 |
| **E6** RRF k=20（更看首位） | 0.267 | 0.733 | 0.833 | 0.508 | 0.606 | 0.442 | 0.536 | 226 | 60 |
| **E7** RRF k=100（更平滑） | 0.250 | 0.717 | 0.850 | 0.500 | 0.614 | 0.437 | 0.534 | 213 | 60 |
| **E8** BM25 走查询改写 | 0.317 | 0.683 | 0.817 | 0.475 | 0.639 | 0.459 | 0.528 | 734 | 60 |
| **E9** 改写+重排(候选50) | 0.267 | 0.700 | 0.817 | 0.508 | 0.631 | 0.460 | 0.544 | 918 | 60 |
| **E10** E8 + 排除 release-notes | 0.333 | 0.767 | 0.833 | 0.533 | 0.664 | 0.491 | 0.567 | 215 | 60 |
| **E11** E1 + 排除 release-notes | 0.383 | 0.700 | 0.833 | 0.475 | 0.606 | 0.523 | 0.582 | 188 | 60 |
| **E12** E10 但排除工单案例（污染对照） | 0.383 | 0.767 | 0.900 | 0.533 | 0.683 | 0.536 | 0.609 | 657 | 60 |

## 2. 按题型拆解

**E1 纯向量（百炼 embedding）**

| 题型 | hit@1 | hit@5 | hit@10 | recall@5 | recall@10 | mrr | ndcg@10 | latency_ms | n |
|---|---|---|---|---|---|---|---|---|---|
| multi_hop | 0.370 | 0.630 | 0.778 | 0.315 | 0.420 | 0.479 | 0.513 | 172 | 27 |
| simple | 0.303 | 0.667 | 0.818 | 0.545 | 0.697 | 0.475 | 0.564 | 207 | 33 |

**E2 纯关键词 BM25**

| 题型 | hit@1 | hit@5 | hit@10 | recall@5 | recall@10 | mrr | ndcg@10 | latency_ms | n |
|---|---|---|---|---|---|---|---|---|---|
| multi_hop | 0.111 | 0.296 | 0.444 | 0.148 | 0.222 | 0.196 | 0.256 | 15 | 27 |
| simple | 0.091 | 0.151 | 0.242 | 0.136 | 0.227 | 0.114 | 0.135 | 26 | 33 |

**E3 向量+BM25+RRF(k=60)**

| 题型 | hit@1 | hit@5 | hit@10 | recall@5 | recall@10 | mrr | ndcg@10 | latency_ms | n |
|---|---|---|---|---|---|---|---|---|---|
| multi_hop | 0.259 | 0.667 | 0.778 | 0.333 | 0.438 | 0.425 | 0.503 | 209 | 27 |
| simple | 0.242 | 0.788 | 0.909 | 0.651 | 0.758 | 0.446 | 0.557 | 194 | 33 |

**E4 E3+重排(候选20)**

| 题型 | hit@1 | hit@5 | hit@10 | recall@5 | recall@10 | mrr | ndcg@10 | latency_ms | n |
|---|---|---|---|---|---|---|---|---|---|
| multi_hop | 0.185 | 0.667 | 0.815 | 0.383 | 0.494 | 0.400 | 0.522 | 755 | 27 |
| simple | 0.273 | 0.667 | 0.909 | 0.561 | 0.773 | 0.467 | 0.585 | 749 | 33 |

**E5 重排候选扩到50**

| 题型 | hit@1 | hit@5 | hit@10 | recall@5 | recall@10 | mrr | ndcg@10 | latency_ms | n |
|---|---|---|---|---|---|---|---|---|---|
| multi_hop | 0.185 | 0.667 | 0.778 | 0.383 | 0.512 | 0.386 | 0.485 | 1059 | 27 |
| simple | 0.303 | 0.697 | 0.818 | 0.576 | 0.697 | 0.481 | 0.559 | 1117 | 33 |

**E6 RRF k=20（更看首位）**

| 题型 | hit@1 | hit@5 | hit@10 | recall@5 | recall@10 | mrr | ndcg@10 | latency_ms | n |
|---|---|---|---|---|---|---|---|---|---|
| multi_hop | 0.296 | 0.667 | 0.778 | 0.333 | 0.438 | 0.444 | 0.522 | 241 | 27 |
| simple | 0.242 | 0.788 | 0.879 | 0.651 | 0.742 | 0.441 | 0.547 | 213 | 33 |

**E7 RRF k=100（更平滑）**

| 题型 | hit@1 | hit@5 | hit@10 | recall@5 | recall@10 | mrr | ndcg@10 | latency_ms | n |
|---|---|---|---|---|---|---|---|---|---|
| multi_hop | 0.259 | 0.667 | 0.778 | 0.333 | 0.438 | 0.425 | 0.503 | 208 | 27 |
| simple | 0.242 | 0.758 | 0.909 | 0.636 | 0.758 | 0.447 | 0.558 | 217 | 33 |

**E8 BM25 走查询改写**

| 题型 | hit@1 | hit@5 | hit@10 | recall@5 | recall@10 | mrr | ndcg@10 | latency_ms | n |
|---|---|---|---|---|---|---|---|---|---|
| multi_hop | 0.333 | 0.704 | 0.815 | 0.352 | 0.531 | 0.467 | 0.539 | 691 | 27 |
| simple | 0.303 | 0.667 | 0.818 | 0.576 | 0.727 | 0.452 | 0.519 | 769 | 33 |

**E9 改写+重排(候选50)**

| 题型 | hit@1 | hit@5 | hit@10 | recall@5 | recall@10 | mrr | ndcg@10 | latency_ms | n |
|---|---|---|---|---|---|---|---|---|---|
| multi_hop | 0.185 | 0.704 | 0.852 | 0.389 | 0.549 | 0.413 | 0.534 | 981 | 27 |
| simple | 0.333 | 0.697 | 0.788 | 0.606 | 0.697 | 0.498 | 0.553 | 867 | 33 |

**E10 E8 + 排除 release-notes**

| 题型 | hit@1 | hit@5 | hit@10 | recall@5 | recall@10 | mrr | ndcg@10 | latency_ms | n |
|---|---|---|---|---|---|---|---|---|---|
| multi_hop | 0.333 | 0.815 | 0.815 | 0.426 | 0.568 | 0.484 | 0.560 | 215 | 27 |
| simple | 0.333 | 0.727 | 0.849 | 0.621 | 0.742 | 0.497 | 0.573 | 214 | 33 |

**E11 E1 + 排除 release-notes**

| 题型 | hit@1 | hit@5 | hit@10 | recall@5 | recall@10 | mrr | ndcg@10 | latency_ms | n |
|---|---|---|---|---|---|---|---|---|---|
| multi_hop | 0.444 | 0.741 | 0.815 | 0.370 | 0.457 | 0.552 | 0.577 | 193 | 27 |
| simple | 0.333 | 0.667 | 0.849 | 0.561 | 0.727 | 0.499 | 0.586 | 184 | 33 |

**E12 E10 但排除工单案例（污染对照）**

| 题型 | hit@1 | hit@5 | hit@10 | recall@5 | recall@10 | mrr | ndcg@10 | latency_ms | n |
|---|---|---|---|---|---|---|---|---|---|
| multi_hop | 0.370 | 0.815 | 0.926 | 0.426 | 0.593 | 0.534 | 0.622 | 612 | 27 |
| simple | 0.394 | 0.727 | 0.879 | 0.621 | 0.758 | 0.538 | 0.598 | 695 | 33 |

## 3. 按难度拆解

**E1**

| 难度 | hit@1 | hit@5 | hit@10 | recall@5 | recall@10 | mrr | ndcg@10 | latency_ms | n |
|---|---|---|---|---|---|---|---|---|---|
| easy | 0.316 | 0.684 | 0.895 | 0.579 | 0.763 | 0.496 | 0.588 | 227 | 19 |
| hard | 0.308 | 0.615 | 0.769 | 0.346 | 0.462 | 0.463 | 0.516 | 170 | 13 |
| medium | 0.357 | 0.643 | 0.750 | 0.393 | 0.494 | 0.471 | 0.520 | 177 | 28 |

**E2**

| 难度 | hit@1 | hit@5 | hit@10 | recall@5 | recall@10 | mrr | ndcg@10 | latency_ms | n |
|---|---|---|---|---|---|---|---|---|---|
| easy | 0.000 | 0.105 | 0.316 | 0.105 | 0.289 | 0.047 | 0.115 | 34 | 19 |
| hard | 0.231 | 0.462 | 0.538 | 0.269 | 0.308 | 0.319 | 0.360 | 17 | 13 |
| medium | 0.107 | 0.179 | 0.250 | 0.107 | 0.143 | 0.143 | 0.161 | 14 | 28 |

**E3**

| 难度 | hit@1 | hit@5 | hit@10 | recall@5 | recall@10 | mrr | ndcg@10 | latency_ms | n |
|---|---|---|---|---|---|---|---|---|---|
| easy | 0.210 | 0.842 | 0.947 | 0.711 | 0.816 | 0.447 | 0.563 | 195 | 19 |
| hard | 0.231 | 0.538 | 0.769 | 0.308 | 0.462 | 0.393 | 0.489 | 224 | 13 |
| medium | 0.286 | 0.750 | 0.821 | 0.464 | 0.548 | 0.449 | 0.533 | 194 | 28 |

**E4**

| 难度 | hit@1 | hit@5 | hit@10 | recall@5 | recall@10 | mrr | ndcg@10 | latency_ms | n |
|---|---|---|---|---|---|---|---|---|---|
| easy | 0.263 | 0.579 | 0.895 | 0.500 | 0.763 | 0.454 | 0.562 | 773 | 19 |
| hard | 0.154 | 0.692 | 0.769 | 0.423 | 0.500 | 0.377 | 0.507 | 749 | 13 |
| medium | 0.250 | 0.714 | 0.893 | 0.494 | 0.637 | 0.454 | 0.576 | 738 | 28 |

**E5**

| 难度 | hit@1 | hit@5 | hit@10 | recall@5 | recall@10 | mrr | ndcg@10 | latency_ms | n |
|---|---|---|---|---|---|---|---|---|---|
| easy | 0.263 | 0.632 | 0.789 | 0.500 | 0.684 | 0.436 | 0.515 | 1118 | 19 |
| hard | 0.154 | 0.615 | 0.769 | 0.385 | 0.538 | 0.353 | 0.477 | 1066 | 13 |
| medium | 0.286 | 0.750 | 0.821 | 0.530 | 0.601 | 0.480 | 0.556 | 1084 | 28 |

**E6**

| 难度 | hit@1 | hit@5 | hit@10 | recall@5 | recall@10 | mrr | ndcg@10 | latency_ms | n |
|---|---|---|---|---|---|---|---|---|---|
| easy | 0.210 | 0.842 | 0.895 | 0.711 | 0.763 | 0.440 | 0.548 | 210 | 19 |
| hard | 0.231 | 0.538 | 0.769 | 0.308 | 0.462 | 0.393 | 0.496 | 247 | 13 |
| medium | 0.321 | 0.750 | 0.821 | 0.464 | 0.566 | 0.467 | 0.546 | 227 | 28 |

**E7**

| 难度 | hit@1 | hit@5 | hit@10 | recall@5 | recall@10 | mrr | ndcg@10 | latency_ms | n |
|---|---|---|---|---|---|---|---|---|---|
| easy | 0.210 | 0.789 | 0.947 | 0.684 | 0.816 | 0.450 | 0.564 | 202 | 19 |
| hard | 0.231 | 0.538 | 0.769 | 0.308 | 0.462 | 0.393 | 0.489 | 203 | 13 |
| medium | 0.286 | 0.750 | 0.821 | 0.464 | 0.548 | 0.449 | 0.533 | 224 | 28 |

**E8**

| 难度 | hit@1 | hit@5 | hit@10 | recall@5 | recall@10 | mrr | ndcg@10 | latency_ms | n |
|---|---|---|---|---|---|---|---|---|---|
| easy | 0.210 | 0.632 | 0.842 | 0.579 | 0.763 | 0.406 | 0.502 | 729 | 19 |
| hard | 0.308 | 0.692 | 0.692 | 0.385 | 0.500 | 0.422 | 0.480 | 631 | 13 |
| medium | 0.393 | 0.714 | 0.857 | 0.446 | 0.619 | 0.511 | 0.567 | 785 | 28 |

**E9**

| 难度 | hit@1 | hit@5 | hit@10 | recall@5 | recall@10 | mrr | ndcg@10 | latency_ms | n |
|---|---|---|---|---|---|---|---|---|---|
| easy | 0.316 | 0.737 | 0.789 | 0.632 | 0.737 | 0.495 | 0.537 | 878 | 19 |
| hard | 0.154 | 0.615 | 0.769 | 0.346 | 0.538 | 0.374 | 0.493 | 1060 | 13 |
| medium | 0.286 | 0.714 | 0.857 | 0.500 | 0.601 | 0.476 | 0.573 | 879 | 28 |

**E10**

| 难度 | hit@1 | hit@5 | hit@10 | recall@5 | recall@10 | mrr | ndcg@10 | latency_ms | n |
|---|---|---|---|---|---|---|---|---|---|
| easy | 0.263 | 0.789 | 0.895 | 0.684 | 0.763 | 0.475 | 0.586 | 217 | 19 |
| hard | 0.308 | 0.692 | 0.692 | 0.423 | 0.538 | 0.423 | 0.495 | 206 | 13 |
| medium | 0.393 | 0.786 | 0.857 | 0.482 | 0.655 | 0.533 | 0.587 | 217 | 28 |

**E11**

| 难度 | hit@1 | hit@5 | hit@10 | recall@5 | recall@10 | mrr | ndcg@10 | latency_ms | n |
|---|---|---|---|---|---|---|---|---|---|
| easy | 0.368 | 0.737 | 0.895 | 0.605 | 0.789 | 0.539 | 0.617 | 184 | 19 |
| hard | 0.462 | 0.692 | 0.846 | 0.385 | 0.500 | 0.603 | 0.646 | 197 | 13 |
| medium | 0.357 | 0.679 | 0.786 | 0.429 | 0.530 | 0.475 | 0.529 | 187 | 28 |

**E12**

| 难度 | hit@1 | hit@5 | hit@10 | recall@5 | recall@10 | mrr | ndcg@10 | latency_ms | n |
|---|---|---|---|---|---|---|---|---|---|
| easy | 0.316 | 0.789 | 0.895 | 0.684 | 0.763 | 0.502 | 0.592 | 689 | 19 |
| hard | 0.308 | 0.692 | 0.846 | 0.423 | 0.615 | 0.455 | 0.557 | 552 | 13 |
| medium | 0.464 | 0.786 | 0.929 | 0.482 | 0.661 | 0.598 | 0.644 | 685 | 28 |

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
| q050 | 拉镜像报 manifest unknown | ❌ | - |  |
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
