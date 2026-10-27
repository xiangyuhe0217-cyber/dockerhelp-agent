"""Windows 控制台编码修正。

Windows 下 Python 的 stdout 默认是 GBK，遇到 ✅/⚠️ 这类字符会直接
`UnicodeEncodeError` 崩掉。所有入口脚本第一件事就是调这个。
"""

from __future__ import annotations

import sys


def setup_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:
            pass
