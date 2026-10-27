"""结构化切片。

核心思想（面试要能讲清）：
  1. **按标题层级切，而不是按字符数硬切** —— 标题栈本身就是最好的语义边界
  2. **每个 chunk 带上「面包屑前缀」**（Docker Desktop › Install › Windows）
     —— 脱离上下文的孤立段落，向量化后语义会漂
  3. **代码块与表格是原子块，不切开** —— 切一半的 YAML/表格既不可读也检不准
  4. **块内按段落合并到 token 预算，块间保留重叠** —— 防止答案正好跨在切点上

参数（settings）：chunk_size=400, chunk_overlap=80
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

CJK_RE = re.compile(r"[\u4e00-\u9fff]")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
TABLE_RE = re.compile(r"^\s*\|")
BREADCRUMB_SEP = " › "


def estimate_tokens(text: str) -> int:
    """粗估 token 数：中文按字、英文按 4 字符 1 token。不依赖模型，只用于切分预算。"""
    if not text:
        return 0
    cjk = len(CJK_RE.findall(text))
    rest = max(len(text) - cjk, 0)
    return cjk + rest // 4 + 1


@dataclass
class Section:
    heading_path: str
    text: str


@dataclass
class Chunk:
    ord: int
    heading_path: str
    content: str
    n_tokens: int
    has_code: int
    id: str = ""

    def finalize(self, doc_id: str) -> "Chunk":
        self.id = hashlib.sha1(f"{doc_id}:{self.ord}".encode()).hexdigest()[:16]
        return self


def split_sections(md: str) -> list[Section]:
    """按 markdown 标题切成「标题路径 + 正文」的段。代码块内的 # 不算标题。"""
    stack: list[tuple[int, str]] = []
    sections: list[Section] = []
    buf: list[str] = []
    in_fence = False

    def flush() -> None:
        text = "\n".join(buf).strip()
        if text:
            sections.append(Section(_breadcrumb(stack), text))

    for line in md.splitlines():
        if FENCE_RE.match(line):
            in_fence = not in_fence
            buf.append(line)
            continue

        m = None if in_fence else HEADING_RE.match(line)
        if m:
            flush()
            buf = []
            level, title = len(m.group(1)), m.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
        else:
            buf.append(line)
    flush()

    if not sections:  # 没有标题的文档
        sections.append(Section("", md.strip()))
    return sections


def _breadcrumb(stack: list[tuple[int, str]]) -> str:
    return BREADCRUMB_SEP.join(title for _, title in stack)


def split_blocks(text: str) -> list[tuple[str, str]]:
    """把一段正文拆成「原子块」：代码块、表格、普通段落。"""
    blocks: list[tuple[str, str]] = []
    cur: list[str] = []
    kind = "para"
    in_fence = False

    def flush(k: str = kind) -> None:
        joined = "\n".join(cur).strip("\n")
        if joined.strip():
            blocks.append((k, joined))
        cur.clear()

    for line in text.splitlines():
        if FENCE_RE.match(line):
            if not in_fence:
                flush()
                in_fence = True
                kind = "code"
            else:
                cur.append(line)
                flush("code")
                in_fence = False
                kind = "para"
                continue
            cur.append(line)
            continue

        if in_fence:
            cur.append(line)
            continue

        if TABLE_RE.match(line):
            if kind != "table":
                flush()
                kind = "table"
            cur.append(line)
            continue
        if kind == "table":
            flush()
            kind = "para"

        if not line.strip():
            flush()
            kind = "para"
            continue
        cur.append(line)

    flush()
    return blocks


SENT_END = re.compile(r"(?<=[。！？；.!?;])\s*")


def _split_long_text(text: str, limit: int) -> list[str]:
    """把一段超长文本按句子/字符边界切成不超过 limit 的若干片。

    真实文档里经常出现"一大段没有空行"的正文，只按行切是切不开的。
    """
    pieces: list[str] = []
    cur: list[str] = []
    cur_tok = 0

    for sent in SENT_END.split(text):
        if not sent:
            continue
        st = estimate_tokens(sent)
        if st > limit:
            # 单句都超限：按字符硬切
            buf = ""
            for ch in sent:
                buf += ch
                if estimate_tokens(buf) >= limit:
                    pieces.append(buf)
                    buf = ""
            if buf:
                pieces.append(buf)
            cur, cur_tok = [], 0
            continue
        if cur and cur_tok + st > limit:
            pieces.append("".join(cur))
            cur, cur_tok = [], 0
        cur.append(sent)
        cur_tok += st
    if cur:
        pieces.append("".join(cur))
    return pieces


def _hard_split(text: str, limit: int) -> list[str]:
    """单个超长块（超大表格/代码/长段落）切分成若干片，尽量保留行完整性。"""
    pieces: list[str] = []
    cur: list[str] = []
    cur_tok = 0
    for line in text.splitlines():
        t = estimate_tokens(line)
        if t > limit:  # 单行就超限，交给字符级切分
            if cur:
                pieces.append("\n".join(cur))
                cur, cur_tok = [], 0
            pieces.extend(_split_long_text(line, limit))
            continue
        if cur and cur_tok + t > limit:
            pieces.append("\n".join(cur))
            cur, cur_tok = [], 0
        cur.append(line)
        cur_tok += t
    if cur:
        pieces.append("\n".join(cur))
    return pieces


def pack_section(section: Section, limit: int, overlap: int) -> list[Chunk]:
    """把一个 section 打包成若干 chunk。"""
    blocks = split_blocks(section.text)
    if not blocks:
        return []

    chunks: list[Chunk] = []
    cur: list[str] = []
    cur_tok = 0

    def emit() -> None:
        nonlocal cur, cur_tok
        if not cur:
            return
        body = "\n\n".join(cur).strip()
        if not body:
            cur, cur_tok = [], 0
            return
        content = f"{section.heading_path}\n\n{body}" if section.heading_path else body
        chunks.append(
            Chunk(
                ord=len(chunks),
                heading_path=section.heading_path,
                content=content,
                n_tokens=estimate_tokens(content),
                has_code=int("```" in body or "~~~" in body),
            )
        )
        # 重叠：保留尾部若干块
        tail: list[str] = []
        tail_tok = 0
        for block in reversed(cur):
            bt = estimate_tokens(block)
            if tail_tok + bt > overlap:
                break
            tail.insert(0, block)
            tail_tok += bt
        cur, cur_tok = list(tail), tail_tok

    for kind, block in blocks:
        bt = estimate_tokens(block)

        if bt > limit:  # 单块超限：先收尾，再独立硬切
            emit()
            if cur:  # emit 留下了重叠尾巴，避免重复计入
                cur, cur_tok = [], 0
            for piece in _hard_split(block, limit):
                body = piece.strip()
                if not body:
                    continue
                content = (
                    f"{section.heading_path}\n\n{body}" if section.heading_path else body
                )
                chunks.append(
                    Chunk(
                        ord=len(chunks),
                        heading_path=section.heading_path,
                        content=content,
                        n_tokens=estimate_tokens(content),
                        has_code=int(kind == "code"),
                    )
                )
            continue

        if cur and cur_tok + bt > limit:
            emit()

        cur.append(block)
        cur_tok += bt

    # 收尾
    if cur:
        body = "\n\n".join(cur).strip()
        if body and (len(chunks) == 0 or estimate_tokens(body) >= 1):
            content = f"{section.heading_path}\n\n{body}" if section.heading_path else body
            chunks.append(
                Chunk(
                    ord=len(chunks),
                    heading_path=section.heading_path,
                    content=content,
                    n_tokens=estimate_tokens(content),
                    has_code=int("```" in body or "~~~" in body),
                )
            )
    return chunks


@dataclass
class ChunkResult:
    chunks: list[Chunk] = field(default_factory=list)
    sections: int = 0
    dropped_small: int = 0


def chunk_document(
    md: str,
    doc_id: str,
    limit: int = 400,
    overlap: int = 80,
    min_tokens: int = 80,
) -> ChunkResult:
    sections = split_sections(md)
    result = ChunkResult(sections=len(sections))

    raw: list[Chunk] = []
    for sec in sections:
        raw.extend(pack_section(sec, limit, overlap))

    # 过短的尾部碎片丢掉（通常只是"下一篇 / 相关链接"）。
    # 但**整篇都很短**时不能丢：工单案例这类文档可能整篇才 100~200 token，
    # 按 min_tokens 一刀切会把整篇丢光（实测结单后 0 chunk、根本检索不到）。
    total_tokens = sum(c.n_tokens for c in raw)
    allow_drop = len(raw) > 1 and total_tokens >= min_tokens * 3

    kept: list[Chunk] = []
    for c in raw:
        if allow_drop and c.n_tokens < min_tokens:
            result.dropped_small += 1
            continue
        kept.append(c)

    for i, c in enumerate(kept):
        c.ord = i
        c.finalize(doc_id)
    result.chunks = kept
    return result
