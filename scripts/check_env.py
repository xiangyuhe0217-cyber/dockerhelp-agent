"""环境自检：一条命令看清"还差什么"。

检查项：
  Python 版本 / 关键依赖 / CUDA 可用性 / 显卡型号与显存 /
  Docker 守护进程 / Milvus 连通性 / 数据盘空间

用法：python scripts/check_env.py
"""

from __future__ import annotations

import importlib.util
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.utils.console import setup_console  # noqa: E402

OK, BAD, WARN = "✅", "❌", "⚠️"
rows: list[tuple[str, str, str]] = []


def add(name: str, status: str, detail: str) -> None:
    rows.append((name, status, detail))


# --------------------------------------------------------------------------- #
def check_python() -> None:
    v = sys.version_info
    if v >= (3, 11):
        add("Python", OK, f"{v.major}.{v.minor}.{v.micro}  ({sys.executable})")
    else:
        add("Python", BAD, f"{v.major}.{v.minor} 过低，需要 >= 3.11")


def check_deps() -> None:
    deps = {
        "fastapi": "API 层",
        "pydantic_settings": "配置",
        "requests": "抓取",
        "yaml": "front-matter 解析",
        "rank_bm25": "BM25",
        "jieba": "中文分词",
        "pymilvus": "Milvus 客户端",
        "sentence_transformers": "向量模型",
        "torch": "深度学习运行时",
        "openai": "LLM 调用",
    }
    missing = [f"{k}({v})" for k, v in deps.items() if importlib.util.find_spec(k) is None]
    if not missing:
        add("依赖", OK, f"{len(deps)} 个关键包齐全")
    else:
        add("依赖", BAD, "缺失: " + ", ".join(missing) + "  → pip install -r requirements.txt")


def check_cuda() -> None:
    if importlib.util.find_spec("torch") is None:
        add("CUDA", WARN, "torch 未安装，跳过（先装 CUDA 版 torch，见 README）")
        return
    try:
        import torch  # noqa: PLC0415

        if torch.cuda.is_available():
            name = torch.cuda.get_device_name(0)
            total = torch.cuda.get_device_properties(0).total_memory / 1024**3
            add("CUDA", OK, f"{name}  {total:.1f} GB  (torch {torch.__version__})")
        else:
            add(
                "CUDA",
                BAD,
                "torch 是 CPU 版！bge-m3 会慢 10 倍且容易误判为模型问题。"
                "重装: pip install torch --index-url https://download.pytorch.org/whl/cu124",
            )
    except Exception as e:  # pragma: no cover
        add("CUDA", BAD, f"torch 导入失败: {e}")


def check_docker() -> None:
    exe = shutil.which("docker")
    if not exe:
        add("Docker", BAD, "未找到 docker 命令")
        return
    import subprocess

    try:
        proc = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=40,  # 首次调用 Docker Desktop 可能要 20s+
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode == 0 and proc.stdout.strip():
            running = subprocess.run(
                ["docker", "ps", "-q"],
                capture_output=True,
                text=True,
                timeout=20,
                encoding="utf-8",
                errors="replace",
            )
            n = len([x for x in running.stdout.split() if x])
            add("Docker", OK, f"daemon 在线 (server {proc.stdout.strip()})，运行中容器 {n} 个")
        elif "permission denied" in (proc.stderr or "").lower() and "npipe" in (proc.stderr or "").lower():
            add(
                "Docker",
                WARN,
                "当前进程被限制访问 Docker 命名管道（沙箱常见），"
                "请在你自己的终端里执行 `docker ps` 确认；正常终端下不受影响",
            )
        else:
            add("Docker", BAD, "客户端在，但守护进程没起来 → 请启动 Docker Desktop")
    except Exception as e:
        add("Docker", BAD, f"docker info 失败: {e}")


def check_milvus() -> None:
    if importlib.util.find_spec("pymilvus") is None:
        add("Milvus", WARN, "pymilvus 未安装，跳过")
        return
    try:
        from pymilvus import MilvusClient  # noqa: PLC0415

        client = MilvusClient(
            uri=settings.milvus_uri,
            token=settings.milvus_token or None,
            timeout=5,
        )
        cols = client.list_collections()
        add("Milvus", OK, f"{settings.milvus_uri} 可达，已有 collection: {cols or '无'}")
        client.close()
    except Exception as e:
        add(
            "Milvus",
            WARN,
            f"{settings.milvus_uri} 连不上（W1 不影响；W2 前需启动: "
            f"docker compose -f docker/milvus-compose.yml up -d）原因: {type(e).__name__}",
        )


def check_disk() -> None:
    settings.ensure_dirs()
    try:
        usage = shutil.disk_usage(settings.data_dir)
        free_gb = usage.free / 1024**3
        status = OK if free_gb >= 10 else WARN
        add("磁盘", status, f"{settings.data_dir} 所在盘剩余 {free_gb:.1f} GB（建议 ≥ 10 GB）")
    except Exception as e:
        add("磁盘", WARN, f"无法获取: {e}")


def check_sqlite() -> None:
    try:
        conn = sqlite3.connect(":memory:")
        ver = conn.execute("select sqlite_version()").fetchone()[0]
        conn.close()
        add("SQLite", OK, f"v{ver}")
    except Exception as e:
        add("SQLite", BAD, str(e))


def check_env_file() -> None:
    env = Path(__file__).resolve().parent.parent / ".env"
    if not env.exists():
        add(".env", WARN, "不存在，请复制 .env.example 为 .env 并填 GITHUB_TOKEN")
    elif not settings.github_token:
        add(".env", WARN, "存在但 GITHUB_TOKEN 为空 → 60 次/小时限流，抓不完 400 条")
    else:
        add(".env", OK, "已配置，GITHUB_TOKEN 已填")


def main() -> int:
    setup_console()
    check_python()
    check_deps()
    check_cuda()
    check_docker()
    check_milvus()
    check_sqlite()
    check_disk()
    check_env_file()

    width = max(len(r[0]) for r in rows)
    print("\n" + "=" * 72)
    print(" dockerhelp-agent 环境自检")
    print("=" * 72)
    for name, status, detail in rows:
        print(f" {status}  {name.ljust(width)}  {detail}")
    print("=" * 72)

    hard_fail = [r for r in rows if r[1] == BAD and r[0] in {"Python", "依赖", "CUDA", "SQLite"}]
    if hard_fail:
        print("\n必须先修：" + ", ".join(r[0] for r in hard_fail))
        return 1
    print("\nW1 可以开工。Milvus 是 ⚠️ 也没关系——W1 不碰向量库。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
