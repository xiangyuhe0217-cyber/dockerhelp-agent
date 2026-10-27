"""BM25 关键词检索。

为什么要自己做 BM25 而不是只靠向量：
  - 错误码（0x80370102）、命令（wsl --update）、版本号（4.34.2）这类**精确串**，
    向量检索天生不擅长，BM25 几乎一找一个准
  - 中文 query 检英文文档时，专有名词是唯一可靠的锚点
  - 它还是 E2 基线，以及 E3/E4 做 RRF 融合的另一路召回

分词策略：中英混合语料必须双分词 —— 中文用 jieba，英文/数字按 \\w+。
"""

from __future__ import annotations

import pickle
import re
from pathlib import Path
from typing import Any, Iterable

import jieba
from rank_bm25 import BM25Okapi

CJK_RE = re.compile(r"[\u4e00-\u9fff]")
TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_\-\.]*")
SPLIT_RE = re.compile(r"([\u4e00-\u9fff]+)")

# jieba 首次加载会打印日志，关掉
jieba.setLogLevel(20)


def tokenize(text: str) -> list[str]:
    """中英混合分词：中文走 jieba，英文/数字走正则，统一小写。"""
    tokens: list[str] = []
    for piece in SPLIT_RE.split(text.lower()):
        if not piece:
            continue
        if CJK_RE.match(piece[0]):
            tokens.extend(w for w in jieba.cut(piece, cut_all=False) if w.strip())
        else:
            tokens.extend(TOKEN_RE.findall(piece))
    return [t for t in tokens if len(t) > 0]


class BM25Index:
    def __init__(self, ids: list[str], corpus_tokens: list[list[str]]):
        if not ids:
            raise ValueError("BM25 语料为空")
        self.ids = ids
        self.corpus_tokens = corpus_tokens
        self.bm25 = BM25Okapi(corpus_tokens)
        self._pos = {cid: i for i, cid in enumerate(ids)}

    @classmethod
    def build(cls, rows: Iterable[dict[str, Any]]) -> "BM25Index":
        ids: list[str] = []
        toks: list[list[str]] = []
        for r in rows:
            ids.append(r["id"])
            toks.append(tokenize(r["content"]))
        return cls(ids, toks)

    def search(self, query: str, top_k: int = 50) -> list[tuple[str, float]]:
        q = tokenize(query)
        if not q:
            return []
        scores = self.bm25.get_scores(q)
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
        return [(self.ids[i], float(scores[i])) for i in ranked if scores[i] > 0]

    # ---------- 持久化 ----------
    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as f:
            pickle.dump(
                {"ids": self.ids, "corpus_tokens": self.corpus_tokens}, f, protocol=4
            )

    @classmethod
    def load(cls, path: Path) -> "BM25Index":
        with path.open("rb") as f:
            d = pickle.load(f)
        return cls(d["ids"], d["corpus_tokens"])


def default_bm25_path() -> Path:
    from app.config import settings  # 延迟导入避免循环

    return settings.data_dir / "bm25.pkl"
