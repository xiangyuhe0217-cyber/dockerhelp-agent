# 失败归因的人工复核（自动分类只是初筛）

> 背景：`scripts/analyze_failures.py` 会按规则给失败题分类，但**规则判不了"语料里到底有没有答案"**。
> 这份文档记录 3 道"疑似语料缺失"的题，逐题查证后的结论。
> 结论直接影响标注：只有真正答不了的题才应该标 `answerable=false`，否则是掩盖召回问题。

---

## 复核结论一览

| 题号 | 问题 | 自动初筛 | **人工复核** | 依据 |
|---|---|---|---|---|
| q026 | WSL2 的 vmmem 进程吃了十几 G 内存 | A（术语无同现） | **B 召回问题** | `manuals/desktop/features/wsl/best-practices.md` L11 提到 `vmmem.exe`，L25「Limit CPU and memory usage」直接讲怎么限制 |
| q041 | 报 permission denied 连不上 docker.sock | A（术语无同现） | **B 召回问题** | `manuals/engine/install/linux-postinstall.md` L39 `sudo groupadd docker`、L124 `groupadd --force docker`，就是"把用户加进 docker 组"的标准解法 |
| q050 | 拉镜像报 manifest unknown | A（术语无同现） | **A 语料缺失** | 全库搜 `manifest unknown` / `no matching manifest` / `tag does not exist` **均 0 命中**；相关文档只讲 manifest 列表，不讲这个报错 |

**净结果**：3 道里只有 1 道是真缺语料，另外 2 道是我的检索没召回——**如果不复核就改标注，等于把检索的锅甩给数据**。

---

## 为什么自动规则会误判

初筛用的是「题目里的英文术语是否在同一个 chunk 里同现」：

| 题 | 题目用词 | 文档用词 | 结果 |
|---|---|---|---|
| q026 | `WSL2` + `vmmem` | `WSL` + `vmmem.exe` | 同现失败 → 误判缺失 |
| q041 | `docker.sock` | `daemon socket` / `docker group` | 同现失败 → 误判缺失 |

改成「逐词看是否**出现过**」后误判消失，但反过来把 q050 放过了（因为 `manifest` 这个词本身存在）。

**所以自动分类只能做三分类里的 B/C 拆分**（B=没进 Top10，C=排 6~10），
"A 到底缺不缺语料"必须人工查证。现在脚本输出的 `A=0` 是"没有术语完全零出现的题"，
不等于"没有不可答题"。

---

## 标注修正

**q050 → `answerable=false`**（`data/eval_cases.jsonl` 与 `eval_cases` 表同步）

理由：该报错的成因（tag 不存在 / 架构不匹配）在语料里没有任何依据，
继续算作"应答题"只会让指标失真；它更适合进"拒答题"组，用来检验系统会不会老实说"不知道"。

**q026 / q041 保持 `answerable=true`**，它们是检索问题，已列入 B 类待办。

---

## 影响

| 口径 | 可答题 | 不可答 | E10 hit@5 |
|---|---|---|---|
| 修正前 | 60 | 5 | 0.767 |
| **修正后** | **59** | **6** | **0.780** |

注意：这个提升（+1.3pp）**来自分母修正**，不是检索变好了。
两类数字都要能解释，否则就是"调指标"而不是"改系统"。

---

## 结论：剩下 9 道 B 类怎么办

按性价比排序：

1. **q026 / q041**（本可答）：文档里明明有答案却没召回。
   查一下这两篇文档的 chunk 是否被 `allow_drop` 过滤、或标题路径太模糊导致向量不显著。
2. **q020 / q021**（网络类）：gold 指向 `engine/network/...`，但召回的是 `ai/sandboxes` 和 `guides/`。
   可能需要给 `manuals/engine` 加一点权重，或修查询改写。
3. **其余**：逐题看 `doc_paths` 与 `gold_paths` 的差距，大概率是"题目里的口语说法"和"文档术语"对不上。
