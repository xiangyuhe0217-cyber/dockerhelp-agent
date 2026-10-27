"""查询改写（中文问题 → 英文检索词）。

为什么需要：
  BM25 是词面匹配。我们的知识库是英文，用户全用中文提问，
  实测纯中文 query 在 BM25 上**零命中**（W3 已验证：E2 hit@5 只有 0.217，
  且集中在带英文错误码的 hard 题上）。

  所以把中文 query 先改写成英文关键词，再喂 BM25 —— 让"混合检索"这一路
  在中文场景下也能真正贡献召回，而不是形同虚设。

实现：百炼 qwen（OpenAI 兼容端点），要求模型只输出检索关键词、不要解释。
带本地缓存，评测时同一问题不会重复调用。
"""

from __future__ import annotations

import re

from app.config import settings

PROMPT = """你是 Docker 技术支持领域的资深工程师。把用户的中文问题改写成用于英文文档检索的关键词。

要求：
1. 只输出关键词短语，用空格分隔，不要编号、不要解释、不要标点
2. 保留原文里的错误码、命令、版本号、专有名词（如 0x80370102、wsl --update、4.34.2）
3. 优先使用 Docker 官方文档的术语（例如：
   - "起不来" → "fails to start"
   - "连不上" → "cannot connect"
   - "卡住" → "stuck hanging"
   - "占内存" → "high memory usage"
   - "清理空间" → "prune disk space"
   - "挂载" → "bind mount"
   - "端口映射" → "port mapping publish"
   - "权限" → "permission denied"
   - "开机自启" → "start on boot restart policy")
4. 最多 12 个词

用户问题：{q}
关键词："""


class QueryRewriter:
    name = "bailian:qwen-rewrite"

    def __init__(self, model: str | None = None):
        from openai import OpenAI  # noqa: PLC0415

        if not settings.bailian_api_key:
            raise RuntimeError("未配置 BAILIAN_API_KEY，无法做查询改写")
        self.model = model or settings.rewrite_model
        self.client = OpenAI(
            api_key=settings.bailian_api_key,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        self._cache: dict[str, str] = {}
        self.calls = 0

    def _clean(self, text: str) -> str:
        text = text.strip().splitlines()[0] if text.strip() else ""
        text = re.sub(r"^[Kk]eywords?[:：]\s*", "", text)
        text = re.sub(r"[^\w\s\-\u4e00-\u9fff]", " ", text)
        return " ".join(text.split())[:300]

    def rewrite(self, query: str) -> str:
        if query in self._cache:
            return self._cache[query]

        # 查询里已经有足够的英文 token（错误码/命令）时，原句直接可用，
        # 不必再花一次调用（也避免改写把精确串弄丢）
        ascii_tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9_.\-]{2,}", query)
        if len(ascii_tokens) >= 3:
            self._cache[query] = query
            return query

        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": PROMPT.format(q=query)}],
                temperature=0,
                max_tokens=64,
            )
            out = self._clean(resp.choices[0].message.content or "")
            self.calls += 1
        except Exception:
            out = ""

        if not out:
            out = query  # 失败就退回原句，绝不让实验因为一次网络抖动中断
        self._cache[query] = out
        return out
