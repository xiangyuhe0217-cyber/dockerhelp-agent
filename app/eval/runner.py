"""检索评测执行器。

一次 run = 一个配置（E1/E2/E3/E4）× 全部可答题目。
结果既进数据库（eval_results 表，可追溯），也汇总成指标表。
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Sequence

from app.db import dao
from app.eval import metrics


@dataclass
class CaseOutcome:
    case_id: str
    question: str
    topic: str
    type: str
    difficulty: str = ""
    doc_paths: list[str] = field(default_factory=list)
    chunk_ids: list[str] = field(default_factory=list)
    rel: list[int] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "question": self.question,
            "topic": self.topic,
            "type": self.type,
            "difficulty": self.difficulty,
            "doc_paths": self.doc_paths,
            "rel": self.rel,
            **self.scores,
        }


class RetrievalEvaluator:
    def __init__(self, retriever, chunk_to_doc: dict[str, str]):
        self.retriever = retriever
        self.chunk_to_doc = chunk_to_doc

    # ---------- 单个 case ----------
    def evaluate_case(
        self, case: dict, mode: str, top_k: int = 10, **search_kwargs
    ) -> CaseOutcome:
        gold_paths = json.loads(case.get("gold_paths") or "[]")
        out = CaseOutcome(
            case_id=case["id"],
            question=case["question"],
            topic=case.get("topic", ""),
            type=case.get("type", ""),
            difficulty=case.get("difficulty", "") or "-",
        )

        t0 = time.time()
        hits = self.retriever.search(
            case["question"], mode=mode, top_k=top_k, **search_kwargs
        )
        latency = (time.time() - t0) * 1000

        out.chunk_ids = [h.id for h in hits]
        out.doc_paths = [self.chunk_to_doc.get(h.id, "") for h in hits]
        out.rel = metrics.relevance_vector(out.doc_paths, gold_paths)

        out.scores = {
            "hit@1": metrics.hit_at_k(out.rel, 1),
            "hit@5": metrics.hit_at_k(out.rel, 5),
            "hit@10": metrics.hit_at_k(out.rel, 10),
            "recall@5": metrics.recall_at_k(out.rel, gold_paths, out.doc_paths, 5),
            "recall@10": metrics.recall_at_k(out.rel, gold_paths, out.doc_paths, 10),
            "mrr": metrics.mrr(out.rel),
            "ndcg@10": metrics.ndcg_at_k(out.rel, 10),
            "latency_ms": round(latency, 1),
        }
        return out

    # ---------- 整批 ----------
    def run(
        self,
        cases: Sequence[dict],
        config: str,
        mode: str,
        conn=None,
        top_k: int = 10,
        **search_kwargs,
    ) -> tuple[list[CaseOutcome], dict]:
        run_id = f"{config}-{uuid.uuid4().hex[:8]}"
        outcomes: list[CaseOutcome] = []

        for i, case in enumerate(cases, 1):
            o = self.evaluate_case(case, mode, top_k, **search_kwargs)
            outcomes.append(o)
            flag = "✓" if o.scores["hit@5"] else "✗"
            print(
                f"\r  {config} [{i:>2}/{len(cases)}] {flag} "
                f"mrr={o.scores['mrr']:.2f} {o.question[:38]:<40}",
                end="",
                flush=True,
            )

            if conn is not None:
                dao.upsert_many(
                    conn,
                    "eval_results",
                    [
                        {
                            "id": f"{run_id}-{case['id']}",
                            "run_id": run_id,
                            "case_id": case["id"],
                            "config": config,
                            "retrieved_ids": json.dumps(o.chunk_ids, ensure_ascii=False),
                            "answer": "",
                            "recall5": o.scores["recall@5"],
                            "recall10": o.scores["recall@10"],
                            "mrr": o.scores["mrr"],
                            "ndcg10": o.scores["ndcg@10"],
                            "faithfulness": None,
                            "correct": None,
                            "latency_ms": int(o.scores["latency_ms"]),
                            "tokens": None,
                        }
                    ],
                )
        print()
        agg = metrics.aggregate([o.to_dict() for o in outcomes])
        return outcomes, agg
