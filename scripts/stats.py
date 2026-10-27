"""数据体检：生成 reports/data_stats.md。

W1 的验收物之一。它回答三个问题：
  1. 知识库到底有多少内容、覆盖哪些板块？
  2. 工单里有多少条带 resolution（能当"标准答案"用）？
  3. 数据够不够支撑 60 条评测集？

用法：python scripts/stats.py
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.db import dao  # noqa: E402
from app.utils.console import setup_console  # noqa: E402


def md_table(headers: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def main() -> int:
    setup_console()
    if not settings.db_path.exists():
        raise SystemExit(f"找不到 {settings.db_path}，请先运行 build_db.py")

    conn = dao.connect(settings.db_path)
    docs = dao.fetch_all(
        conn,
        "SELECT section, lang, word_count, has_code, has_table, title FROM documents",
    )
    tickets = dao.fetch_all(
        conn,
        "SELECT source, labels, resolution, body, created_at, is_synthetic FROM tickets",
    )
    counts = {t: dao.count(conn, t) for t in ("documents", "tickets", "customers", "sla", "chunks", "eval_cases")}

    # ---------- 文档 ----------
    words = [d["word_count"] for d in docs] or [0]
    section_counter = Counter(d["section"] or "(root)" for d in docs)
    lang_counter = Counter(d["lang"] or "unknown" for d in docs)
    code_ratio = sum(d["has_code"] for d in docs) / max(len(docs), 1)
    table_ratio = sum(d["has_table"] for d in docs) / max(len(docs), 1)

    # ---------- 工单 ----------
    real = [t for t in tickets if not t["is_synthetic"]]
    with_res = [t for t in real if t["resolution"]]
    label_counter: Counter[str] = Counter()
    for t in real:
        try:
            for lb in json.loads(t["labels"] or "[]"):
                label_counter[lb] += 1
        except Exception:
            continue
    body_lens = [len(t["body"] or "") for t in real] or [0]

    lines: list[str] = []
    lines.append("# 数据体检报告\n")
    lines.append(f"> 生成时间：{dao.get_meta(conn, 'built_at')}")
    lines.append(f"> 文档 commit：`{dao.get_meta(conn, 'docs_commit')}`\n")

    lines.append("## 1. 规模总览\n")
    lines.append(
        md_table(
            ["表", "行数"],
            [[k, f"{v:,}"] for k, v in counts.items()],
        )
    )

    lines.append("\n## 2. 知识库文档\n")
    lines.append(
        md_table(
            ["指标", "值"],
            [
                ["文档数", f"{len(docs):,}"],
                ["总词数", f"{sum(words):,}"],
                ["平均词数", f"{statistics.mean(words):,.0f}"],
                ["中位词数", f"{statistics.median(words):,.0f}"],
                ["最长文档", f"{max(words):,}"],
                ["含代码块比例", f"{code_ratio:.1%}"],
                ["含表格比例", f"{table_ratio:.1%}"],
            ],
        )
    )
    lines.append("\n**板块分布**\n")
    lines.append(md_table(["section", "篇数"], [[k, v] for k, v in section_counter.most_common()]))
    lines.append("\n**语言分布**（跨语言检索的前提）\n")
    lines.append(md_table(["lang", "篇数"], [[k, v] for k, v in lang_counter.most_common()]))

    lines.append("\n## 3. 历史工单\n")
    lines.append(
        md_table(
            ["指标", "值"],
            [
                ["真实工单数", f"{len(real):,}"],
                ["其中带 resolution", f"{len(with_res):,}（{len(with_res) / max(len(real), 1):.1%}）"],
                ["平均正文长度", f"{statistics.mean(body_lens):,.0f} 字符"],
                ["中位正文长度", f"{statistics.median(body_lens):,.0f} 字符"],
            ],
        )
    )
    src_counter = Counter(t["source"] for t in real)
    lines.append("\n**来源分布**\n")
    lines.append(md_table(["source", "条数"], [[k, v] for k, v in src_counter.most_common()]))
    lines.append("\n**高频标签 Top 12**\n")
    lines.append(
        md_table(
            ["label", "次数"],
            [[k, v] for k, v in label_counter.most_common(12)],
        )
    )

    lines.append("\n## 4. W1 结论与下一步\n")
    checks = [
        ("文档 ≥ 200 篇", len(docs) >= 200),
        ("工单 ≥ 400 条", len(real) >= 400),
        ("带 resolution 的工单 ≥ 100 条", len(with_res) >= 100),
        ("含表格文档 ≥ 20%", table_ratio >= 0.2),
        ("含代码块文档 ≥ 30%", code_ratio >= 0.3),
    ]
    for name, ok in checks:
        lines.append(f"- {'✅' if ok else '❌'} {name}")
    lines.append(
        "\n> 跨语言提示：若 `lang=en` 占绝对多数而 `lang=zh` 极少，"
        "说明知识库是纯英文，评测集必须全部用中文提问才能验证 bge-m3 的跨语言能力。"
    )

    out = settings.reports_dir / "data_stats.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[stats] 已生成 {out}")
    print("\n".join(lines[-8:]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
