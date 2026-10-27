"""极简 Markdown front-matter 解析。

只做一件事：把 `---\\n<yaml>\\n---\\n<body>` 拆成 (meta, body)。

为什么不用 python-frontmatter：本项目对它的需求仅此一项，
自己写 20 行可以少一个依赖，也少一个"pip 装不上就全盘卡住"的风险点。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

FM_RE = re.compile(r"^-{3}[ \t]*\r?\n(.*?)\r?\n-{3}[ \t]*\r?\n", re.S)


def _fallback_parse(block: str) -> dict[str, Any]:
    """没有 PyYAML 时，只解析 `key: value` 与 `- item` 列表（Docker 文档足够用）。"""
    meta: dict[str, Any] = {}
    key: str | None = None
    for raw in block.splitlines():
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if line.lstrip().startswith("- ") and key:
            meta.setdefault(key, [])
            if not isinstance(meta[key], list):
                meta[key] = [meta[key]] if meta[key] else []
            meta[key].append(line.lstrip()[2:].strip().strip("'\""))
            continue
        if ":" in line:
            k, v = line.split(":", 1)
            key = k.strip()
            v = v.strip().strip("'\"")
            meta[key] = v if v else None
    return meta


def parse_front_matter(text: str) -> tuple[dict[str, Any], str]:
    m = FM_RE.match(text)
    if not m:
        return {}, text
    block, body = m.group(1), text[m.end() :]

    try:
        import yaml  # noqa: PLC0415

        data = yaml.safe_load(block) or {}
        if isinstance(data, dict):
            return data, body
    except Exception:
        pass
    return _fallback_parse(block), body


def read_markdown(path: Path) -> tuple[dict[str, Any], str]:
    return parse_front_matter(path.read_text(encoding="utf-8", errors="replace"))
