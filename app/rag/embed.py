"""向量化：本地 bge-m3（默认）或阿里百炼 API（对照实验 E5）。

两个实现同一接口，切换只改 .env 的 EMBED_PROVIDER，不改业务代码。
这也是 E5 对照组能"一行配置切换"的原因。
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Sequence

import numpy as np

from app.config import settings


class Embedder(ABC):
    name: str = "base"
    dim: int = 0

    @abstractmethod
    def encode(self, texts: Sequence[str], batch_size: int = 32) -> np.ndarray:
        """返回 (n, dim) 的 float32 数组，**已 L2 归一化**（配合 COSINE/IP 检索）。"""


def _l2_normalize(mat: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (mat / norms).astype(np.float32)


class LocalBGEEmbedder(Embedder):
    """本地 bge-m3（多语言，1024 维）。

    中文 query 检英文文档依赖它 —— bge-small-zh 这类中文专用模型在这里会明显掉点。
    """

    def __init__(self, model_path: str | Path | None = None, device: str | None = None):
        from sentence_transformers import SentenceTransformer  # 延迟导入，避免拖慢无关命令

        path = Path(model_path or settings.embed_model_path)
        if not path.exists():
            raise FileNotFoundError(
                f"本地模型不存在：{path}\n先跑：python scripts/fetch_model.py --repo BAAI/bge-m3"
            )
        self.name = f"bge-m3(local:{path.name})"
        self.device = device or ("cuda" if settings.use_cuda else "cpu")
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
        self.model = SentenceTransformer(str(path), device=self.device)
        self.model.max_seq_length = settings.embed_max_seq
        self.dim = int(self.model.get_sentence_embedding_dimension())
        self.batch_size = settings.embed_batch_size

    def encode(self, texts: Sequence[str], batch_size: int = 0) -> np.ndarray:
        vecs = self.model.encode(
            list(texts),
            batch_size=batch_size or self.batch_size,
            normalize_embeddings=False,  # 自己归一化，保证所有实现一致
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        return _l2_normalize(np.asarray(vecs))


class BailianEmbedder(Embedder):
    """阿里百炼 text-embedding-v3（对照组 E5）。

    用 OpenAI 兼容端点，省一个 SDK。
    """

    def __init__(self) -> None:
        from openai import OpenAI  # noqa: PLC0415

        if not settings.bailian_api_key:
            raise RuntimeError("未配置 BAILIAN_API_KEY")
        self.name = "bailian:text-embedding-v3"
        self.dim = settings.bailian_dim
        self.client = OpenAI(
            api_key=settings.bailian_api_key,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        self.model = "text-embedding-v3"

    def encode(self, texts: Sequence[str], batch_size: int = 10) -> np.ndarray:
        out: list[list[float]] = []
        bs = batch_size or 10
        items = list(texts)
        for i in range(0, len(items), bs):  # 百炼单次最多 10 条
            resp = self.client.embeddings.create(
                model=self.model,
                input=items[i : i + bs],
                dimensions=self.dim,
                encoding_format="float",
            )
            out.extend(d.embedding for d in resp.data)
        return _l2_normalize(np.asarray(out, dtype=np.float32))


def get_embedder(provider: str | None = None) -> Embedder:
    provider = (provider or settings.embed_provider).lower()
    if provider in {"local", "bge", "bge-m3"}:
        return LocalBGEEmbedder()
    if provider in {"bailian", "dashscope", "api"}:
        return BailianEmbedder()
    raise ValueError(f"未知的 EMBED_PROVIDER: {provider}")
