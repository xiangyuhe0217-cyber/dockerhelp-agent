"""一键自检：交付前跑这个，确认环境、依赖、数据、索引、记忆都是好的。

用法：python scripts/selfcheck.py
退出码 0 = 全部就绪；1 = 有硬性问题（⚠️ 警告不算）
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.utils.console import setup_console  # noqa: E402

OK, WARN, FAIL = "✅", "⚠️ ", "❌"
results: list[tuple[str, str, str]] = []


def add(level: str, name: str, detail: str = "") -> None:
    results.append((level, name, detail))
    print(f"{level} {name}" + (f"  — {detail}" if detail else ""))


def main() -> int:
    setup_console()
    print("=" * 70)
    print("dockerhelp-agent 自检")
    print("=" * 70)

    # 1. Python
    v = sys.version_info
    add(OK if v >= (3, 11) else FAIL, f"Python {v.major}.{v.minor}.{v.micro}", "需要 >= 3.11")

    # 2. 依赖
    deps = {
        "langgraph": "LangGraph",
        "langgraph.checkpoint.sqlite": "checkpoint-sqlite",
        "faiss": "FAISS",
        "jieba": "jieba",
        "fastapi": "FastAPI",
        "uvicorn": "uvicorn",
        "redis": "redis",
        "openai": "openai sdk",
    }
    for mod, label in deps.items():
        try:
            __import__(mod)
            add(OK, f"依赖 {label}")
        except Exception as e:  # noqa: BLE001
            add(FAIL, f"依赖 {label}", f"{type(e).__name__}: {e}")

    # 3. 配置
    from app.config import settings  # noqa: PLC0415

    if settings.bailian_api_key:
        add(OK, "BAILIAN_API_KEY 已配置", f"模型={settings.agent_model}")
    else:
        add(FAIL, "BAILIAN_API_KEY 缺失", "复制 .env.example 为 .env 并填写")

    # 4. 数据与索引
    db = Path(settings.db_path)
    if db.exists():
        from app.db import dao  # noqa: PLC0415

        conn = dao.connect(db)
        docs = dao.count(conn, "documents")
        chunks = dao.count(conn, "chunks")
        tickets = dao.count(conn, "tickets")
        cases = conn.execute(
            "SELECT COUNT(*) FROM documents WHERE section='cases'"
        ).fetchone()[0]
        add(OK, "数据库", f"{db.name}：{docs} 文档 / {chunks} chunk / {tickets} 工单 / {cases} 案例")
        if chunks < 1000:
            add(WARN, "chunk 数量偏少", "可能没跑 build_chunks.py")
    else:
        add(FAIL, "数据库缺失", "跑 scripts/build_db.py && build_chunks.py")

    for name, path, hint in [
        ("FAISS 索引", Path(settings.data_dir) / "faiss", "scripts/build_index.py"),
        ("BM25 索引", Path(settings.data_dir) / "bm25.pkl", "scripts/build_index.py"),
        ("checkpoint", Path(settings.data_dir) / "checkpoints.sqlite", "首次对话自动创建"),
    ]:
        p = Path(path)
        add(OK if p.exists() else WARN, name, "存在" if p.exists() else f"缺失，跑 {hint}")

    # 5. 记忆后端
    from app.agent.memory import get_memory  # noqa: PLC0415

    store = get_memory("selfcheck")
    if store.backend == "redis":
        add(OK, "长期记忆 = Redis", f"{settings.redis_host}:{settings.redis_port}/db{settings.redis_db}")
    else:
        add(WARN, "长期记忆回退到 SQLite", f"Redis {settings.redis_host}:{settings.redis_port} 不可用")

    # 6. 检索能不能真的返回结果
    try:
        from app.agent.tools import execute  # noqa: PLC0415

        obs, _ = execute("search_knowledge", {"query": "Docker 容器端口冲突", "top_k": 3})
        hit = "出处:" in obs
        add(OK if hit else FAIL, "检索冒烟测试", obs.splitlines()[0][:60])
    except Exception as e:  # noqa: BLE001
        add(FAIL, "检索冒烟测试", f"{type(e).__name__}: {e}")

    # 7. 前端产物
    dist = ROOT / "web" / "dist" / "index.html"
    add(OK if dist.exists() else WARN, "前端构建产物", "存在" if dist.exists() else "缺 web/dist，跑 cd web && npm run build")

    # 8. 评测集
    eval_set = Path(settings.data_dir) / "eval_cases.jsonl"
    if eval_set.exists():
        n = sum(1 for _ in eval_set.open(encoding="utf-8"))
        add(OK, "评测集", f"{n} 条")
    else:
        add(WARN, "评测集缺失", "跑 scripts/build_evalset.py")

    # 汇总
    fails = [r for r in results if r[0] == FAIL]
    warns = [r for r in results if r[0] == WARN]
    print("=" * 70)
    print(f"通过 {len(results) - len(fails) - len(warns)} / 警告 {len(warns)} / 失败 {len(fails)}")
    if fails:
        print("\n必须解决：")
        for _, name, detail in fails:
            print(f"  · {name} {detail}")
    print("=" * 70)
    if not fails:
        print("🎉 环境就绪，可以交付/演示")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
