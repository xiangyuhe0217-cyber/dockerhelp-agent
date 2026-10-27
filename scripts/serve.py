"""启动 Web 服务。

用法：
  python scripts/serve.py                # 127.0.0.1:8000
  python scripts/serve.py --port 8080
  python scripts/serve.py --reload       # 开发模式（改后端自动重载）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn  # noqa: E402

from app.utils.console import setup_console  # noqa: E402


def main() -> int:
    setup_console()
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--reload", action="store_true")
    args = p.parse_args()

    dist = Path(__file__).resolve().parent.parent / "web" / "dist"
    if not dist.exists():
        print("[serve] ⚠ 没找到 web/dist，前端还没构建：")
        print("        cd web && pnpm install && pnpm build")
        print("        （先只跑 API 也可以，/api/* 照常可用）")

    print(f"[serve] http://{args.host}:{args.port}")
    uvicorn.run(
        "app.api.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
