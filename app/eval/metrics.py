"""检索评测指标。

只做**检索层**指标（W3 范围）。生成层（忠实度/答案正确率）要等 W5 接入 LLM，
那时再用 `gold_points` 做判分。

相关性定义：一条召回结果，只要它的文档路径以任一 `gold_path` 开头，就算相关。
这样标注成本低、且不会因为 chunk 边界不同而误判。
"""

from __future__ import annotations

import math
from typing import Sequence


def is_relevant(doc_path: str, gold_paths: Sequence[str]) -> bool:
    return any(doc_path.startswith(gp) for gp in gold_paths)


def relevance_vector(doc_paths: Sequence[str], gold_paths: Sequence[str]) -> list[int]:
    return [1 if is_relevant(p, gold_paths) else 0 for p in doc_paths]


def hit_at_k(rel: Sequence[int], k: int) -> float:
    """Top-k 里是否至少有一条相关（HR@k）。"""
    return 1.0 if any(rel[:k]) else 0.0


def recall_at_k(rel: Sequence[int], gold_paths: Sequence[str], doc_paths: Sequence[str], k: int) -> float:
    """文档级覆盖率：gold 前缀里有多少个在 Top-k 中被覆盖到。

    比 HR@k 更严格：多跳题需要同时命中两三个不同板块才算满。
    """
    if not gold_paths:
        return 0.0
    covered = set()
    for p in doc_paths[:k]:
        for gp in gold_paths:
            if p.startswith(gp):
                covered.add(gp)
    return len(covered) / len(gold_paths)


def mrr(rel: Sequence[int]) -> float:
    for i, r in enumerate(rel, start=1):
        if r:
            return 1.0 / i
    return 0.0


def ndcg_at_k(rel: Sequence[int], k: int) -> float:
    gains = rel[:k]
    if not gains:
        return 0.0
    dcg = sum(g / math.log2(i + 2) for i, g in enumerate(gains))
    ideal = sorted(rel, reverse=True)[:k]
    idcg = sum(g / math.log2(i + 2) for i, g in enumerate(ideal))
    return dcg / idcg if idcg else 0.0


def aggregate(per_case: Sequence[dict]) -> dict:
    """对一批 case 的指标取平均。"""
    if not per_case:
        return {}
    keys = ["hit@1", "hit@5", "hit@10", "recall@5", "recall@10", "mrr", "ndcg@10", "latency_ms"]
    out: dict[str, float] = {}
    for k in keys:
        vals = [c[k] for c in per_case if k in c]
        out[k] = round(sum(vals) / len(vals), 4) if vals else 0.0
    out["n"] = len(per_case)
    return out
