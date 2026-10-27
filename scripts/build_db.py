"""把 data/ 下的原始数据装配进 helpdesk.db。

幂等：重复执行不会产生重复行（documents 用 sha1(rel_path) 主键，tickets 用 repo#number）。
可复现：documents 的量取决于 docs_index.jsonl，tickets 的量取决于 issues/*.jsonl。

用法：
  python scripts/build_db.py
  python scripts/build_db.py --no-synthetic   # 不生成合成业务数据
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.db import dao  # noqa: E402
from app.utils.console import setup_console  # noqa: E402


def load_docs() -> list[dict]:
    """从 docs_index.jsonl + data/docs/ 组装 documents 行。"""
    if not settings.docs_index_path.exists():
        raise FileNotFoundError(
            f"缺少 {settings.docs_index_path}，请先运行 `python scripts/fetch_docs.py`"
        )

    rows: list[dict] = []
    missing = 0
    with settings.docs_index_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            meta = json.loads(line)
            content_file = settings.docs_dir / meta["rel_path"]
            if not content_file.exists():
                missing += 1
                continue
            rows.append(
                {
                    "id": meta["id"],
                    "rel_path": meta["rel_path"],
                    "url": meta.get("url"),
                    "title": meta.get("title"),
                    "description": meta.get("description"),
                    "section": meta.get("section"),
                    "content_md": content_file.read_text(encoding="utf-8"),
                    "word_count": meta.get("word_count", 0),
                    "has_code": meta.get("has_code", 0),
                    "has_table": meta.get("has_table", 0),
                    "lang": meta.get("lang"),
                    "sha256": meta.get("sha256"),
                    "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }
            )
    if missing:
        print(f"[build] ⚠ {missing} 篇在索引里但文件缺失，已跳过")
    return rows


def load_tickets() -> list[dict]:
    rows: list[dict] = []
    if not settings.issues_dir.exists():
        return rows
    for path in sorted(settings.issues_dir.glob("*.jsonl")):
        before = len(rows)
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        print(f"[build] {path.name}: {len(rows) - before} 条")
    return rows


# --------------------------------------------------------------------------- #
# 合成业务数据（固定 seed，保证每次生成一致）
# --------------------------------------------------------------------------- #
TIERS = ["basic", "standard", "premium"]
SLA_ROWS = [
    ("sla-basic-p1", "basic", "P1", 8, 72),
    ("sla-basic-p2", "basic", "P2", 24, 120),
    ("sla-standard-p1", "standard", "P1", 4, 48),
    ("sla-standard-p2", "standard", "P2", 12, 72),
    ("sla-premium-p1", "premium", "P1", 1, 8),
]
SYNTH_TITLES = [
    "DeskTop 启动卡在 Starting the Docker Engine",
    "WSL2 backend 启动报 0x80370102",
    "拉取镜像超时 TLS handshake timeout",
    "端口 8080 已被占用，容器起不来",
    "磁盘空间不足导致镜像无法构建",
    "宿主机访问不到容器内服务",
    "docker compose up 报 volume 权限错误",
    "内存占用过高导致笔记本卡死",
    "在代理环境下无法登录 Docker Hub",
    "卸载重装后容器数据丢失",
]


def build_synthetic(n: int = 30, seed: int = 42) -> tuple[list[dict], list[dict], list[dict]]:
    rng = random.Random(seed)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    customers = [
        {
            "id": f"cust-{i:03d}",
            "name": f"某制造企业 {chr(65 + i % 26)}{i}",
            "tier": rng.choice(TIERS),
            "contract_level": rng.choice(["年度", "季度", "按次"]),
            "note": "合成数据，仅用于工具演示",
        }
        for i in range(1, 21)
    ]

    tickets: list[dict] = []
    for i in range(1, n + 1):
        title = rng.choice(SYNTH_TITLES)
        tier = rng.choice(TIERS)
        priority = rng.choice(["P1", "P2", "P3"])
        tickets.append(
            {
                "id": f"synthetic#{i:04d}",
                "source": "synthetic",
                "number": i,
                "title": title,
                "body": f"（合成工单）客户环境：Windows 11 + Docker Desktop 4.{rng.randint(28, 40)}。"
                f"优先级 {priority}，客户等级 {tier}。现象：{title}。",
                "labels": json.dumps(["synthetic", priority.lower()], ensure_ascii=False),
                "state": "closed",
                "created_at": now,
                "closed_at": now,
                "resolution": "（合成）已按知识库步骤排查并解决。",
                "url": "",
                "product_version": f"4.{rng.randint(28, 40)}.0",
                "is_synthetic": 1,
                "sha256": f"synth{i:04d}",
                "fetched_at": now,
            }
        )

    sla = [
        {
            "id": sid,
            "tier": tier,
            "priority": pri,
            "first_response_h": fr,
            "resolution_h": rs,
        }
        for sid, tier, pri, fr, rs in SLA_ROWS
    ]
    return customers, sla, tickets


def main() -> int:
    setup_console()
    p = argparse.ArgumentParser(description="装配 helpdesk.db")
    p.add_argument("--no-synthetic", action="store_true")
    p.add_argument(
        "--reset",
        action="store_true",
        help="先清空 documents/tickets/customers/sla 再重建（源文件删了但库里还有旧行时用）",
    )
    args = p.parse_args()

    settings.ensure_dirs()
    docs = load_docs()
    tickets = load_tickets()

    customers: list[dict] = []
    sla: list[dict] = []
    synthetic: list[dict] = []
    if not args.no_synthetic:
        customers, sla, synthetic = build_synthetic()
        tickets.extend(synthetic)

    with dao.session(settings.db_path) as conn:
        dao.init_schema(conn)

        if args.reset:
            # 顺序重要：chunks 有外键，先删
            for table in ("chunks", "documents", "tickets", "customers", "sla"):
                n = dao.count(conn, table)
                conn.execute(f"DELETE FROM {table}")
                if n:
                    print(f"[build] --reset: 清空 {table}（{n} 行）")

        n_docs = dao.upsert_many(conn, "documents", docs)
        n_tickets = dao.upsert_many(conn, "tickets", tickets) if tickets else 0
        n_cust = dao.upsert_many(conn, "customers", customers) if customers else 0
        n_sla = dao.upsert_many(conn, "sla", sla) if sla else 0

        commit_file = settings.data_dir / "docs_commit.txt"
        if commit_file.exists():
            dao.set_meta(conn, "docs_commit", commit_file.read_text(encoding="utf-8").strip())
        dao.set_meta(conn, "built_at", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        dao.set_meta(conn, "docs_count", str(dao.count(conn, "documents")))
        dao.set_meta(conn, "tickets_count", str(dao.count(conn, "tickets")))

    print("\n[build] 完成")
    print(f"  documents : {n_docs:>5}   (库内合计 {dao.count(dao.connect(settings.db_path), 'documents')})")
    print(f"  tickets   : {n_tickets:>5}   其中合成 {len(synthetic)}")
    print(f"  customers : {n_cust:>5}")
    print(f"  sla       : {n_sla:>5}")
    print(f"  db        : {settings.db_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
