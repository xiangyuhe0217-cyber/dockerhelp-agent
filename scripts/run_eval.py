"""一条命令跑完评测集并出表（支持多次运行累积汇总）。

用法：
  python scripts/run_eval.py                          # 跑 E1-E4 全部并重建报告
  python scripts/run_eval.py --configs E1,E3          # 只跑部分，其余保留上次结果
  python scripts/run_eval.py --configs E4 --limit 10  # 小样本冒烟
  python scripts/run_eval.py --report-only            # 只按已有结果重出报告

结果落在 reports/eval_summary.json（累积）+ reports/eval_report.md（人读）。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.db import dao  # noqa: E402
from app.eval import metrics  # noqa: E402
from app.eval.runner import RetrievalEvaluator  # noqa: E402
from app.rag.bm25 import BM25Index, default_bm25_path  # noqa: E402
from app.rag.index import get_store  # noqa: E402
from app.rag.retrieve import Retriever  # noqa: E402
from app.utils.console import setup_console  # noqa: E402

CONFIGS = {
    # 基线（W3）
    "E1": {"mode": "vector", "desc": "纯向量（百炼 embedding）", "kw": {}},
    "E2": {"mode": "bm25", "desc": "纯关键词 BM25", "kw": {}},
    "E3": {"mode": "hybrid", "desc": "向量+BM25+RRF(k=60)", "kw": {"rrf_k": 60}},
    "E4": {"mode": "hybrid_rerank", "desc": "E3+重排(候选20)", "kw": {"rrf_k": 60, "rerank_in": 20}},
    # W4 消融
    "E5": {"mode": "hybrid_rerank", "desc": "重排候选扩到50", "kw": {"rrf_k": 60, "rerank_in": 50}},
    "E6": {"mode": "hybrid", "desc": "RRF k=20（更看首位）", "kw": {"rrf_k": 20}},
    "E7": {"mode": "hybrid", "desc": "RRF k=100（更平滑）", "kw": {"rrf_k": 100}},
    "E8": {"mode": "hybrid", "desc": "BM25 走查询改写", "kw": {"rrf_k": 60, "rewrite": True}},
    "E9": {"mode": "hybrid_rerank", "desc": "改写+重排(候选50)", "kw": {"rrf_k": 60, "rewrite": True, "rerank_in": 50}},
    # W4 第二轮：去掉"万能吸铁石"文档
    "E10": {"mode": "hybrid", "desc": "E8 + 排除 release-notes", "kw": {"rrf_k": 60, "rewrite": True, "exclude": ["manuals/desktop/release-notes", "manuals/desktop/previous-versions", "manuals/engine/release-notes"]}},
    "E11": {"mode": "vector", "desc": "E1 + 排除 release-notes", "kw": {"exclude": ["manuals/desktop/release-notes", "manuals/desktop/previous-versions", "manuals/engine/release-notes"]}},
    # W8 归因实验：工单案例（cases/*）只有 6 个 chunk 却含大量高频词，
    # 实测出现在 17/60 题的 Top-5 且全部是噪声。这里量化它的影响。
    "E12": {"mode": "hybrid", "desc": "E10 但排除工单案例（污染对照）", "kw": {"rrf_k": 60, "rewrite": True, "exclude": ["manuals/desktop/release-notes", "manuals/desktop/previous-versions", "manuals/engine/release-notes", "cases/"]}},
}
SUMMARY_PATH = lambda: settings.reports_dir / "eval_summary.json"  # noqa: E731
COLS = ["hit@1", "hit@5", "hit@10", "recall@5", "recall@10", "mrr", "ndcg@10", "latency_ms"]


def build_retriever(chunk_to_doc: dict[str, str] | None = None) -> Retriever:
    store = get_store(create=False)
    if not store.exists():
        raise SystemExit("向量库不存在，先跑 scripts/build_index.py")
    bm25_path = default_bm25_path()
    if not bm25_path.exists():
        raise SystemExit("BM25 索引不存在，先跑 scripts/build_index.py --bm25-only")
    return Retriever(store, BM25Index.load(bm25_path), chunk_to_doc=chunk_to_doc)


def load_summary() -> dict:
    p = SUMMARY_PATH()
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {}


def breakdown(rows: list[dict], key: str) -> dict[str, dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[r.get(key) or "-"].append(r)
    return {k: metrics.aggregate(v) for k, v in sorted(groups.items())}


def table(aggs: dict[str, dict], label: str = "配置", descs: dict[str, str] | None = None) -> str:
    lines = [
        f"| {label} | " + " | ".join(COLS) + " | n |",
        "|" + "---|" * (len(COLS) + 2),
    ]
    for name, agg in aggs.items():
        cells = [
            f"{agg.get(c, 0):.0f}" if c == "latency_ms" else f"{agg.get(c, 0):.3f}"
            for c in COLS
        ]
        label_cell = f"**{name}** {descs[name]}" if descs and name in descs else name
        lines.append(f"| {label_cell} | " + " | ".join(cells) + f" | {agg.get('n', 0)} |")
    return "\n".join(lines)


def write_report(
    summary: dict, n_cases: int, docs: int, chunks: int, n_refusal: int = 0
) -> Path:
    present = [k for k in CONFIGS if k in summary]
    out = [
        "# 检索评测报告",
        "",
        f"> 语料：{docs} 篇文档 / {chunks} chunk　可答题：{n_cases} 条"
        f"（另有 {n_refusal} 条拒答题，需生成层评测，见 W5）",
        "> 相关性判定：召回 chunk 所属文档的路径以任一 `gold_path` 开头即算命中",
        "",
        "## 1. 总体对比",
        "",
        table(
            {k: summary[k]["agg"] for k in present},
            descs={k: CONFIGS[k]["desc"] for k in present},
        ),
        "",
        "## 2. 按题型拆解",
        "",
    ]
    for name in present:
        rows = summary[name]["cases"]
        bd = breakdown(rows, "type")
        out.append(f"**{name} {CONFIGS[name]['desc']}**\n")
        out.append(table(bd, "题型"))
        out.append("")

    out.append("## 3. 按难度拆解")
    out.append("")
    for name in present:
        bd = breakdown(summary[name]["cases"], "difficulty")
        out.append(f"**{name}**\n")
        out.append(table(bd, "难度"))
        out.append("")

    out.append("## 4. 逐题明细（以最强配置为基准）")
    out.append("")
    best = max(present, key=lambda k: summary[k]["agg"].get("hit@5", 0)) if present else None
    if best:
        out.append(f"基准：**{best}**　✅ = Top-5 内命中\n")
        out.append("| id | 问题 | 结果 | 首个命中名次 | 命中文档 |")
        out.append("|---|---|---|---|---|")
        for row in summary[best]["cases"]:
            rank = next((i for i, r in enumerate(row["rel"], 1) if r), 0)
            path = next((p for p, r in zip(row["doc_paths"], row["rel"]) if r), "")
            mark = "✅" if row["hit@5"] else "❌"
            out.append(
                f"| {row['case_id']} | {row['question'][:30]} | {mark} | "
                f"{rank or '-'} | {path[:52]} |"
            )

    out.append("")
    out.append("## 5. 怎么读这张表")
    out.append("")
    out.append("- **hit@5**：Top-5 里至少有一条相关文档，最直观的「用户能不能看到答案」")
    out.append("- **recall@5**：多跳题需要覆盖多个板块，这个指标惩罚「只中一个」")
    out.append("- **mrr**：第一个相关结果的平均倒数名次，衡量「排得靠不靠前」")
    out.append("- **latency**：单题平均耗时（含 embedding / rerank 的网络往返）")

    p = settings.reports_dir / "eval_report.md"
    p.write_text("\n".join(out) + "\n", encoding="utf-8")
    return p


def main() -> int:
    setup_console()
    p = argparse.ArgumentParser(description="检索评测")
    p.add_argument("--configs", default="E1,E2,E3,E4")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--top-k", type=int, default=10)
    p.add_argument("--no-save", action="store_true")
    p.add_argument("--report-only", action="store_true")
    args = p.parse_args()

    conn = dao.connect(settings.db_path)
    n_answerable = dao.fetch_one(
        conn, "SELECT COUNT(*) AS c FROM eval_cases WHERE answerable = 1"
    )["c"]
    n_refusal = dao.fetch_one(
        conn, "SELECT COUNT(*) AS c FROM eval_cases WHERE answerable = 0"
    )["c"]
    summary = load_summary()

    if not args.report_only:
        cases = dao.fetch_all(
            conn,
            "SELECT * FROM eval_cases WHERE answerable = 1 ORDER BY id"
            + (f" LIMIT {args.limit}" if args.limit else ""),
        )
        if not cases:
            raise SystemExit("eval_cases 为空，先跑 scripts/build_evalset.py")
        print(f"[eval] 题目 {len(cases)} 条，top_k={args.top_k}")

        chunk_to_doc = {
            r["id"]: r["rel_path"]
            for r in dao.fetch_all(
                conn,
                "SELECT c.id AS id, d.rel_path AS rel_path "
                "FROM chunks c JOIN documents d ON c.doc_id = d.id",
            )
        }
        print(f"[eval] chunk→doc 映射 {len(chunk_to_doc):,} 条")

        retriever = build_retriever(chunk_to_doc)
        evaluator = RetrievalEvaluator(retriever, chunk_to_doc)

        names = [c.strip().upper() for c in args.configs.split(",") if c.strip()]
        unknown = [n for n in names if n not in CONFIGS]
        if unknown:
            raise SystemExit(f"未知配置：{unknown}，可选 {list(CONFIGS)}")

        for name in names:
            cfg = CONFIGS[name]
            print(f"\n=== {name} {cfg['desc']} (mode={cfg['mode']} kw={cfg['kw']}) ===")
            outcomes, agg = evaluator.run(
                cases,
                name,
                cfg["mode"],
                conn=None if args.no_save else conn,
                top_k=args.top_k,
                **cfg["kw"],
            )
            summary[name] = {"agg": agg, "cases": [o.to_dict() for o in outcomes]}
            print(
                f"  hit@5={agg['hit@5']:.3f}  recall@5={agg['recall@5']:.3f}  "
                f"mrr={agg['mrr']:.3f}  ndcg@10={agg['ndcg@10']:.3f}  p50={agg['latency_ms']:.0f}ms"
            )

        SUMMARY_PATH().parent.mkdir(parents=True, exist_ok=True)
        SUMMARY_PATH().write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    if not summary:
        raise SystemExit("还没有任何评测结果，先跑一次（去掉 --report-only）")

    md = write_report(
        summary,
        n_answerable,
        dao.count(conn, "documents"),
        dao.count(conn, "chunks"),
        n_refusal=n_refusal,
    )
    print(
        "\n"
        + table(
            {k: summary[k]["agg"] for k in CONFIGS if k in summary},
            descs={k: CONFIGS[k]["desc"] for k in CONFIGS if k in summary},
        )
    )
    print(f"\n[eval] 报告 → {md}")
    print(f"[eval] 汇总 → {SUMMARY_PATH()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
