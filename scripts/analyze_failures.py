"""失败归因：把评测里没命中的题分类，产出可执行的改进清单。

为什么要单独做：
    "hit@5 = 0.767" 只是一个数字，它说不清下一步该改什么。
    把失败拆成「语料没有 / 有但没召回 / 召回了但排太后」三类之后，
    每一类对应完全不同的动作（补语料 / 改切片·改写 / 调融合·重排）。

判定规则（对指定配置，默认 E10）：
    rank = 该题 Top-10 里第一个相关结果的名次
      · rank 存在且 > 5        → C 排序不足（差一点，重排/融合能救）
      · rank 不存在，但 gold 文档在库里 → B 有语料未召回（切片/改写/embedding 问题）
      · rank 不存在，且 gold 文档不在库里 → A 语料缺失或标注问题

用法：
    python scripts/analyze_failures.py               # 默认 E10
    python scripts/analyze_failures.py --config E1
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.db import dao  # noqa: E402
from app.utils.console import setup_console  # noqa: E402


def first_rel_rank(rel: list[int]) -> int | None:
    for i, r in enumerate(rel, 1):
        if r:
            return i
    return None


ASCII_TERM = __import__("re").compile(r"[A-Za-z][A-Za-z0-9_.\-]{2,}")


def corpus_contains_all(conn, terms: list[str]) -> bool:
    """语料里是否存在**同时包含**这些词的文档（用于判断"这题到底答不答得了"）。"""
    if not terms:
        return True
    sql = "SELECT COUNT(*) AS c FROM chunks WHERE " + " AND ".join(
        ["LOWER(content) LIKE ?"] * len(terms)
    )
    params = [f"%{t.lower()}%" for t in terms]
    return conn.execute(sql, params).fetchone()[0] > 0


def main() -> int:
    setup_console()
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="E10")
    args = p.parse_args()

    summary_path = settings.reports_dir / "eval_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if args.config not in summary:
        raise SystemExit(f"{summary_path} 里没有配置 {args.config}，先跑 run_eval.py")

    cases = summary[args.config]["cases"]
    conn = dao.connect(settings.db_path)
    doc_paths_all = [r["rel_path"] for r in dao.fetch_all(conn, "SELECT rel_path FROM documents")]
    gold_meta = {
        r["id"]: r for r in dao.fetch_all(conn, "SELECT id, gold_paths, question, type, topic FROM eval_cases")
    }

    rows = []
    for c in cases:
        if c.get("hit@5"):
            continue
        golds = json.loads(gold_meta.get(c["case_id"], {}).get("gold_paths") or "[]")
        rank = first_rel_rank(c.get("rel") or [])
        # gold 文档是否真的在语料里（用前缀匹配）
        gold_in_corpus = [
            g for g in golds if any(p.startswith(g) for p in doc_paths_all)
        ]
        # 语料里是否同时出现题目里的英文关键词（TLS/handshake/vmmem 这类）
        terms = [t for t in ASCII_TERM.findall(c.get("question", ""))][:3]
        has_terms = corpus_contains_all(conn, terms)
        if rank is not None and rank > 5:
            kind = "C"
            reason = f"第 {rank} 名命中"
        elif not gold_in_corpus:
            kind = "A"
            reason = "gold 文档不在语料中"
        elif not has_terms:
            kind = "A"
            reason = f"关键术语（{'/'.join(terms)}）在语料中无同现"
        else:
            kind = "B"
            reason = "语料有相关段落但未召回"
        rows.append(
            {
                "id": c["case_id"],
                "question": c.get("question", ""),
                "type": c.get("type", ""),
                "topic": c.get("topic", ""),
                "kind": kind,
                "reason": reason,
                "rank": rank,
                "hit@10": c.get("hit@10", 0),
                "golds": golds,
                "gold_in_corpus": gold_in_corpus,
                "terms": terms,
                "has_terms": has_terms,
                "retrieved": [p for p in (c.get("doc_paths") or []) if p][:5],
            }
        )

    order = {"A": 0, "B": 1, "C": 2}
    rows.sort(key=lambda r: (order[r["kind"]], r["id"]))
    counts = {k: sum(1 for r in rows if r["kind"] == k) for k in "ABC"}

    out = [
        f"# 失败归因：{args.config}",
        "",
        f"> 数据来源：`reports/eval_summary.json` 的 `{args.config}.cases`（{len(cases)} 题）",
        f"> 失败：**{len(rows)} 题**（hit@5 = 0），分类：A 语料缺失 {counts['A']} / "
        f"B 有语料未召回 {counts['B']} / C 排太后 {counts['C']}",
        "",
        "## 分类含义与对应动作",
        "",
        "| 类 | 含义 | 该做什么 |",
        "|---|---|---|",
        "| **A** | gold 文档在语料里根本不存在 | 修正评测标注（标为不可答）或补语料；**不要动检索参数** |",
        "| **B** | 文档在库里，但 Top-10 一条相关都没有 | 查切片位置、查询改写、embedding 语义鸿沟 |",
        "| **C** | 召回到了但排 6~10 名 | 融合权重 / 重排 / 提升 gold 段的相似度 |",
        "",
        "## 逐题明细",
        "",
        "| ID | 类 | 名次 | 问题 | gold 路径 | 实际召回（前3） |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        golds = "<br>".join(r["golds"]) or "-"
        got = "<br>".join(r["retrieved"][:3]) or "（空）"
        rank = f"#{r['rank']}" if r["rank"] else "-"
        out.append(
            f"| {r['id']} | {r['kind']} | {rank} | {r['question'][:26]} | {golds} | {got} |"
        )

    out += ["", "## 按类拆分", ""]
    for k, title in (("A", "A 语料缺失 / 标注问题"), ("B", "B 有语料未召回"), ("C", "C 召回了但排太后")):
        sub = [r for r in rows if r["kind"] == k]
        out.append(f"### {title}（{len(sub)} 题）")
        out.append("")
        for r in sub:
            out.append(f"- **{r['id']}** {r['question']}")
            out.append(f"  - gold: `{', '.join(r['golds']) or '-'}`｜实际: {', '.join(r['retrieved'][:2]) or '空'}")
            if k == "A":
                out.append(f"  - 判定依据：{r['reason']}")
                out.append("  - 结论：大概率不可答 → 人工确认后改标（answerable=false）或补语料；不要去调检索参数")
            elif k == "B":
                out.append(f"  - 判定依据：{r['reason']}")
                out.append("  - 结论：文档存在但未召回 → 查该文档切片是否过短/改写是否偏离")
            else:
                out.append(f"  - 判定依据：{r['reason']}")
                out.append(f"  - 结论：第 {r['rank']} 名命中 → 融合/重排可救")
        out.append("")

    out += [
        "## 案例库污染度（W7 闭环的副作用）",
        "",
        "工单案例（`cases/*`）只有很短的内容，却含「容器/启动/根因/解决方案」这类高频词，",
        "可能像 release-notes 一样变成「万能吸铁石」。这里量化它出现在 Top-5 的频率：",
        "",
    ]
    n_case_hit = 0
    n_pollute = 0
    pollute_ids: list[str] = []
    for c in cases:
        paths = [p for p in (c.get("doc_paths") or []) if p]
        rel = c.get("rel") or []
        hit_case = False
        for i, p in enumerate(paths[:5]):
            if p.startswith("cases/"):
                hit_case = True
                if i < len(rel) and not rel[i]:
                    n_pollute += 1
                    pollute_ids.append(c["case_id"])
                    break
        if hit_case:
            n_case_hit += 1
    out += [
        f"- 案例文档出现在 Top-5 的题：**{n_case_hit} / {len(cases)}**",
        f"- 其中**位置无关（噪声）**的题：**{n_pollute}**"
        + (f"（{', '.join(sorted(set(pollute_ids))) }）" if pollute_ids else ""),
        "",
    ]
    if n_pollute:
        out += [
            "> 结论：案例只在「命中它的那一刻」有用，其余时候是噪声。",
            "> 建议改为**补充召回**：主检索先不出案例，仅当主结果为空或用户明确提到历史工单时再召回。",
            "",
        ]

    out += [
        "## 优先级建议",
        "",
        f"1. **先处理 A 类（{counts['A']} 题）**：成本最低，改标注即可，且能立刻修正指标口径。",
        f"2. **再处理 C 类（{counts['C']} 题）**：重排在这类题上才可能有效（候选里已有正确答案）。",
        f"3. **最后处理 B 类（{counts['B']} 题）**：需要动切片/改写，代价最大。",
        "",
    ]

    path = settings.reports_dir / "failure_analysis.md"
    path.write_text("\n".join(out), encoding="utf-8")
    print(f"[analyze] 失败 {len(rows)} 题 → A={counts['A']} B={counts['B']} C={counts['C']}")
    try:
        shown = path.resolve().relative_to(ROOT)
    except ValueError:
        shown = path
    print(f"[analyze] 报告 → {shown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
