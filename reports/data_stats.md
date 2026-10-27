# 数据体检报告

> 生成时间：2026-10-26T10:16:49+00:00
> 文档 commit：`1d503f8b7c2b2008da6cbc08bb5be163666c84bf`

## 1. 规模总览

| 表 | 行数 |
|---|---|
| documents | 932 |
| tickets | 530 |
| customers | 20 |
| sla | 5 |
| chunks | 8,788 |
| eval_cases | 0 |

## 2. 知识库文档

| 指标 | 值 |
|---|---|
| 文档数 | 932 |
| 总词数 | 1,133,060 |
| 平均词数 | 1,216 |
| 中位词数 | 704 |
| 最长文档 | 54,325 |
| 含代码块比例 | 67.4% |
| 含表格比例 | 25.2% |

**板块分布**

| section | 篇数 |
|---|---|
| manuals | 797 |
| guides | 71 |
| reference | 42 |
| get-started | 21 |
| (root) | 1 |

**语言分布**（跨语言检索的前提）

| lang | 篇数 |
|---|---|
| en | 932 |

## 3. 历史工单

| 指标 | 值 |
|---|---|
| 真实工单数 | 500 |
| 其中带 resolution | 360（72.0%） |
| 平均正文长度 | 3,653 字符 |
| 中位正文长度 | 3,716 字符 |

**来源分布**

| source | 条数 |
|---|---|
| docker/for-win | 300 |
| moby/moby | 200 |

**高频标签 Top 12**

| label | 次数 |
|---|---|
| status/triage | 156 |
| kind/bug | 111 |
| needs-triage | 74 |
| area/WSL2 | 62 |
| status/0-triage | 37 |
| area/testing | 32 |
| flaky | 27 |
| area/startup | 25 |
| area/networking | 19 |
| status/acknowledged | 18 |
| area/installer | 17 |
| version/4.38.0 | 17 |

## 4. W1 结论与下一步

- ✅ 文档 ≥ 200 篇
- ✅ 工单 ≥ 400 条
- ✅ 带 resolution 的工单 ≥ 100 条
- ✅ 含表格文档 ≥ 20%
- ✅ 含代码块文档 ≥ 30%

> 跨语言提示：若 `lang=en` 占绝对多数而 `lang=zh` 极少，说明知识库是纯英文，评测集必须全部用中文提问才能验证 bge-m3 的跨语言能力。
