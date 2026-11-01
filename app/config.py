"""集中配置。

所有脚本与服务只从这里读配置，禁止在业务代码里散落 os.getenv。
读取优先级：显式传入的参数 > 环境变量 > `.env` 文件 > 这里的默认值。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


def _redirect_temp_dir() -> Path:
    """把临时目录重定向到项目内。

    背景（踩过的坑）：在本机某些受限环境下，Python 进程访问系统 TEMP
    （C:\\Users\\<user>\\AppData\\Local\\Temp）会被拒绝，导致
      - sqlite3 大事务落临时文件时抛 `unable to open database file`
      - tempfile.gettempdir() 静默退回到当前工作目录
    表现为"几千条数据写入时随机失败"，极难定位。
    统一指到 data/tmp 后彻底消失。
    """
    tmp = BASE_DIR / "data" / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    os.environ["SQLITE_TMPDIR"] = str(tmp)
    os.environ["TMPDIR"] = str(tmp)
    os.environ["TMP"] = str(tmp)
    os.environ["TEMP"] = str(tmp)
    tempfile.tempdir = str(tmp)
    return tmp


TEMP_DIR = _redirect_temp_dir()


class Settings(BaseSettings):
    """项目级配置，字段名与 .env 中的大写变量一一对应（大小写不敏感）。"""

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------- 路径 ----------
    data_dir: Path = BASE_DIR / "data"
    db_path: Path = BASE_DIR / "data" / "helpdesk.db"
    cache_dir: Path = BASE_DIR / ".cache"
    reports_dir: Path = BASE_DIR / "reports"

    # ---------- 数据源 ----------
    docs_repo: str = "https://github.com/docker/docs.git"
    docs_commit: str = ""
    docs_max_files: int = 1200
    docs_skip_dirs: str = "partials,includes,_partials,vendor"
    issues_repos: str = "docker/for-win,moby/moby"

    # ---------- GitHub ----------
    github_token: str = ""
    github_api: str = "https://api.github.com"

    # ---------- Agent ----------
    agent_provider: str = "auto"  # auto | deepseek | bailian
    agent_model: str = "qwen-plus"
    agent_max_steps: int = 8
    agent_max_tokens: int = 1500
    agent_obs_limit: int = 1200  # 单个工具结果进上下文的长度上限
    agent_token_budget: int = 30000  # 单次运行累计 token 上限，超了强制收口
    agent_temperature: float = 0.0
    agent_memory_turns: int = 6
    agent_memory_token_budget: int = 3000

    # ---------- 模型 ----------
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    bailian_api_key: str = ""
    embed_model: str = "BAAI/bge-m3"
    rerank_model: str = "BAAI/bge-reranker-v2-m3"

    # ---------- 向量库 ----------
    # 默认 FAISS：零服务依赖、索引文件可校验（Milvus 镜像拉不到；Chroma 在 3441 行静默丢写）
    vector_backend: str = "faiss"  # faiss（默认） | chroma | milvus
    milvus_uri: str = "http://127.0.0.1:19530"
    milvus_token: str = ""
    milvus_collection: str = "helpdesk_chunks"
    milvus_metric: str = "COSINE"
    milvus_index: str = "HNSW"
    chroma_collection: str = "helpdesk_chunks"
    embed_dim: int = 1024

    # ---------- 向量化 ----------
    # 默认百炼 API：不用本地 GPU/模型下载，换环境即可跑
    embed_provider: str = "bailian"  # bailian（默认） | local
    embed_batch_size: int = 32
    embed_max_seq: int = 1024
    use_cuda: bool = True
    bailian_dim: int = 1024

    # ---------- 检索参数 ----------
    chunk_size: int = 400
    chunk_overlap: int = 80
    retrieve_topk: int = 50
    rerank_topk: int = 20
    final_topk: int = 5
    rrf_k: int = 60
    # 双语 BM25：改写后的英文查官方英文文档 + 原始中文查中文案例（cases/*）。
    # 实测开启后 E10：hit@5 0.783→0.767（略降），recall@5 0.525→0.542，
    # 但中文工单案例从"进不了 Top10"变成 Top-1 —— 案例闭环依赖它，所以默认开。
    bm25_bilingual: bool = True

    # 文档黑名单：release-notes / 历史版本这类页面把每个功能都提过一遍，
    # 对几乎任何 query 都有表面相关性（"万能吸铁石"），实测会压掉真正的答案。
    # W4 实测：仅排除它们，hit@5 从 0.733 → 0.783。
    exclude_paths: str = (
        "manuals/desktop/release-notes,"
        "manuals/desktop/previous-versions,"
        "manuals/engine/release-notes"
    )

    # ---------- 重排后端 ----------
    rerank_provider: str = "bailian"  # bailian（gte-rerank API）| local（bge-reranker）
    bailian_rerank_model: str = "gte-rerank-v2"
    bailian_rerank_url: str = (
        "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
    )

    # ---------- 查询改写 ----------
    rewrite_model: str = "qwen-plus"  # 中文问题 → 英文检索词（供 BM25 用）

    # ---------- 长期记忆 ----------
    memory_backend: str = "redis"  # redis | sqlite
    redis_host: str = "127.0.0.1"
    redis_port: int = 26379  # 本机 Redis 来自 Docker 端口映射（容器 6379 → 宿主 26379）
    redis_db: int = 1  # 独立 db，不碰其他项目的数据
    redis_password: str = ""
    redis_prefix: str = "dockerhelp:mem"
    memory_fallback_sqlite: bool = True

    # ---------- 网页搜索兜底 ----------
    web_fallback: bool = True  # 知识库没命中时，自动去网上搜一次
    web_topk: int = 5
    web_timeout: float = 12.0
    tavily_api_key: str = ""  # 有就用正经 API，没有用 Bing HTML

    # ---------- 校验 ----------
    @field_validator("docs_commit", "github_token", "milvus_token", mode="before")
    @classmethod
    def _strip(cls, v: object) -> object:
        """允许 .env 里写空值或带空格的值。"""
        return v.strip() if isinstance(v, str) else v

    # ---------- 派生属性 ----------
    @property
    def docs_dir(self) -> Path:
        return self.data_dir / "docs"

    @property
    def chroma_dir(self) -> Path:
        return self.data_dir / "chroma"

    @property
    def issues_dir(self) -> Path:
        return self.data_dir / "issues"

    @property
    def docs_index_path(self) -> Path:
        return self.data_dir / "docs_index.jsonl"

    @property
    def docs_repo_cache(self) -> Path:
        return self.cache_dir / "docker-docs"

    @property
    def skip_dirs(self) -> list[str]:
        return [s.strip() for s in self.docs_skip_dirs.split(",") if s.strip()]

    @property
    def repo_list(self) -> list[str]:
        return [s.strip() for s in self.issues_repos.split(",") if s.strip()]

    @property
    def exclude_prefixes(self) -> list[str]:
        return [s.strip() for s in self.exclude_paths.split(",") if s.strip()]

    @property
    def embed_model_path(self) -> Path:
        return BASE_DIR / "models" / "bge-m3"

    @property
    def rerank_model_path(self) -> Path:
        return BASE_DIR / "models" / "bge-reranker-v2-m3"

    def ensure_dirs(self) -> None:
        for p in (
            self.data_dir,
            self.docs_dir,
            self.issues_dir,
            self.cache_dir,
            self.reports_dir,
        ):
            p.mkdir(parents=True, exist_ok=True)


settings = Settings()
