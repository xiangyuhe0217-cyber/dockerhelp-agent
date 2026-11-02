"""用评测集校准「检索置信度」阈值。

思路：
    可答题的 Top-1 向量相似度应该偏高；"知识库根本答不了"的题（answerable=false）
    应该偏低。用这两组的分布差，选一个能把它们分开的门槛，
    低于门槛就触发降级（先查工单案例，再不行才走网页搜索）。

为什么用向量余弦而不是 RRF 分：
    RRF 分只反映名次（Σ1/(k+rank)），量级只有 1e-2，而且"第一名"永远是那个值，
    无法表达"这个第一到底像不像"。余弦相似度是绝对量，可跨查询比较。

用法：python scripts/calibrate_confidence.py
"""

from __future__ import annotations

import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.db import dao  # noqa: E402
from app.rag.bm25 import BM25Index, default_bm25_path  # noqa: E402
from app.rag.index import get_store  # noqa: E402
from app.rag.retrieve import Retriever  # noqa: E402
from app.utils.console import setup_console  # noqa: E402


def main() -> int:
    setup_console()
    conn = dao.connect(settings.db_path)
    rows = dao.fetch_all(
        conn, "SELECT id, question, answerable FROM eval_cases ORDER BY answerable DESC, id"
    )
    chunk_to_doc = {
        r["cid"]: r["path"]
        for r in dao.fetch_all(
            conn,
            "SELECT c.id AS cid, d.rel_path AS path FROM chunks c JOIN documents d ON d.id = c.doc_id",
        )
    }
    r = Retriever(
        get_store(create=False),
        BM25Index.load(default_bm25_path()),
        chunk_to_doc=chunk_to_doc,
    )

    exclude = list(settings.exclude_prefixes) + [settings.case_docs_prefix]
    groups: dict[int, list[tuple[str, float]]] = {1: [], 0: []}
    rr_groups: dict[int, list[tuple[str, float]]] = {1: [], 0: []}
    spread_groups: dict[int, list[tuple[str, float]]] = {1: [], 0: []}
    reranker = None
    try:
        from app.rag.retrieve import get_reranker  # noqa: PLC0415

        reranker = get_reranker()
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 重排不可用，跳过第二个信号：{e}")

    for row in rows:
        hits = r.search(
            row["question"], mode="hybrid", top_k=5, rewrite=True, exclude=exclude
        )
        score = hits[0].vec_score if hits else 0.0
        groups[int(row["answerable"])].append((row["id"], round(score, 3)))
        # 相对信号：Top-1 与 Top-5 均值的差（"第一名是否明显胜出"）
        vecs = [h.vec_score for h in hits if h.vec_score > 0]
        if len(vecs) >= 3:
            spread = vecs[0] - (sum(vecs[1:5]) / max(1, len(vecs[1:5])))
            spread_groups[int(row["answerable"])].append((row["id"], round(spread, 4)))
        if reranker and hits:
            try:
                top = reranker.rerank(row["question"], hits[:5], top_k=1)
                rr_groups[int(row["answerable"])].append(
                    (row["id"], round(float(top[0].score), 3))
                )
            except Exception:  # noqa: BLE001
                pass

    for label, key in (("可答（answerable=1）", 1), ("不可答（answerable=0）", 0)):
        vals = [s for _, s in groups[key]]
        if not vals:
            continue
        print(f"\n{label}  n={len(vals)}")
        print(f"  min={min(vals):.3f}  p25={statistics.quantiles(vals, n=4)[0]:.3f}  "
              f"median={statistics.median(vals):.3f}  max={max(vals):.3f}")
        print("  最低 5 个:", sorted(groups[key], key=lambda x: x[1])[:5])

    print("\n阈值扫描（低于阈值视为低置信 → 触发降级）：")
    print(f"{'阈值':>6} {'可答误触发':>10} {'不可答命中':>10} {'准确率':>8}")
    ans = [s for _, s in groups[1]]
    unans = [s for _, s in groups[0]]
    best = None
    for th in [0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70]:
        tp = sum(1 for s in unans if s < th)  # 不可答被正确判为低置信
        fp = sum(1 for s in ans if s < th)    # 可答被误判
        acc = (tp + (len(ans) - fp)) / (len(ans) + len(unans))
        if best is None or acc > best[1]:
            best = (th, acc)
        print(f"{th:>6.2f} {fp:>10} {tp:>10} {acc:>8.1%}")
    print(f"\n向量余弦建议阈值：{best[0]:.2f}（准确率 {best[1]:.1%}）")

    # ---------- 第二个信号：重排分 ----------
    if reranker and rr_groups[1] and rr_groups[0]:
        print("\n=== 重排分（gte-rerank）===")
        for label, key in (("可答", 1), ("不可答", 0)):
            vals = [s for _, s in rr_groups[key]]
            print(f"{label}  n={len(vals)}  min={min(vals):.3f}  median={statistics.median(vals):.3f}  max={max(vals):.3f}")
            print("  最低 5 个:", sorted(rr_groups[key], key=lambda x: x[1])[:5])

        a2 = [s for _, s in rr_groups[1]]
        u2 = [s for _, s in rr_groups[0]]
        print(f"\n{'阈值':>6} {'可答误触发':>10} {'不可答命中':>10} {'准确率':>8}")
        best2 = None
        for th in [0.05, 0.10, 0.15, 0.20, 0.30, 0.40, 0.50]:
            tp = sum(1 for s in u2 if s < th)
            fp = sum(1 for s in a2 if s < th)
            acc = (tp + (len(a2) - fp)) / (len(a2) + len(u2))
            if best2 is None or acc > best2[1]:
                best2 = (th, acc)
            print(f"{th:>6.2f} {fp:>10} {tp:>10} {acc:>8.1%}")
        print(f"\n重排分建议阈值：{best2[0]:.2f}（准确率 {best2[1]:.1%}）")

    # ---------- 第三个信号：Top-1 相对 Top-5 的差距（尖锐度）----------
    if spread_groups[1] and spread_groups[0]:
        print("\n=== Top-1 与 Top-5 均值之差（尖锐度，越大越'有明确赢家'）===")
        for label, key in (("可答", 1), ("不可答", 0)):
            vals = [s for _, s in spread_groups[key]]
            print(f"{label}  n={len(vals)}  min={min(vals):+.4f}  median={statistics.median(vals):+.4f}  max={max(vals):+.4f}")
            print("  最低 5 个:", sorted(spread_groups[key], key=lambda x: x[1])[:5])

        a3 = [s for _, s in spread_groups[1]]
        u3 = [s for _, s in spread_groups[0]]
        print(f"\n{'阈值':>8} {'可答误触发':>10} {'不可答命中':>10} {'准确率':>8}")
        best3 = None
        for th in [0.00, 0.01, 0.02, 0.03, 0.05, 0.08]:
            tp = sum(1 for s in u3 if s < th)
            fp = sum(1 for s in a3 if s < th)
            acc = (tp + (len(a3) - fp)) / (len(a3) + len(u3))
            if best3 is None or acc > best3[1]:
                best3 = (th, acc)
            print(f"{th:>8.3f} {fp:>10} {tp:>10} {acc:>8.1%}")
        print(f"\n尖锐度建议阈值：{best3[0]:.3f}（准确率 {best3[1]:.1%}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
