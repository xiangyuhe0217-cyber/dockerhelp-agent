"""网页搜索兜底：当知识库没有答案时，用外部搜索补一次。

为什么需要：
  知识库只覆盖 Docker 官方文档 + 历史工单。用户问"我们公司 VPN 怎么配"、
  或者问一个官方文档没写的边缘问题，纯 RAG 只能答"没找到"——体验很差。
  这时候去网上搜一次，把结果作为**带来源标注**的证据交给模型，比直接拒答更有用。

后端选择（实测）
  · DuckDuckGo / SearXNG：本机网络超时，不可用
  · Bing HTML：可用（无需 key）
  留了 `TAVILY_API_KEY` 扩展位：以后接正经 API 只需实现一个分支。

边界：
  · 结果**必须标注为"网络搜索"**，不能和知识库内容混为一谈（否则用户会以为是官方依据）
  · 只在知识库没命中时触发（默认策略），避免每次都多花 2 秒
"""

from __future__ import annotations

import html as htmllib
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from app.config import settings

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

TAG_RE = re.compile(r"<[^>]+>")
# 360 搜索：<li class="res-list"> 里带 data-mdurl（真实地址）
BLOCK_360 = re.compile(r'<li class="res-list".*?</li>', re.S)
TITLE_360 = re.compile(r'<h3[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
MDURL_360 = re.compile(r'data-mdurl="([^"]+)"')
SUMMARY_360 = re.compile(r"data-result-summary[^>]*>(.*?)</span>", re.S)
# Bing：<li class="b_algo"> 里是普通链接
BLOCK_BING = re.compile(r'<li class="b_algo".*?</li>', re.S)
TITLE_BING = re.compile(r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
SNIPPET_BING = re.compile(r"<p[^>]*>(.*?)</p>", re.S)


@dataclass
class WebResult:
    title: str
    url: str
    snippet: str = ""


@dataclass
class WebSearchResponse:
    query: str
    results: list[WebResult] = field(default_factory=list)
    backend: str = ""
    error: str = ""


_cache: dict[str, WebSearchResponse] = {}


def _clean(text: str) -> str:
    return " ".join(htmllib.unescape(TAG_RE.sub(" ", text)).split())


def _search_360(query: str, top_k: int, timeout: float) -> WebSearchResponse:
    """360 搜索：中文查询相关性最好，结果块里带 data-mdurl（真实地址）。"""
    url = "https://www.so.com/s?q=" + urllib.parse.quote(query)
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        html = r.read(500_000).decode("utf-8", "replace")

    resp = WebSearchResponse(query=query, backend="360")
    for block in BLOCK_360.findall(html):
        m = TITLE_360.search(block)
        if not m:
            continue
        title = _clean(m.group(2))
        # 优先 data-mdurl（真实站点），退而用 href（可能是 so.com 跳转链接）
        real = MDURL_360.search(block)
        link = htmllib.unescape(real.group(1)) if real else htmllib.unescape(m.group(1))
        snip = SUMMARY_360.search(block)
        snippet = _clean(snip.group(1)) if snip else ""
        if not title or not link.startswith("http"):
            continue
        # 过滤 360 的翻译/直达卡片/跳转壳，它们不是真正的内容页
        if any(x in link for x in ("fanyi.so.com", "ai.so.com", "so.com/link", "www.so.com/link")):
            continue
        resp.results.append(WebResult(title=title, url=link, snippet=snippet[:300]))
        if len(resp.results) >= top_k:
            break
    return resp


def _search_bing(query: str, top_k: int, timeout: float) -> WebSearchResponse:
    """Bing 兜底：英文查询还行，中文容易被泛化结果带偏。"""
    url = "https://cn.bing.com/search?q=" + urllib.parse.quote(query) + "&setlang=zh-CN"
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        html = r.read(500_000).decode("utf-8", "replace")

    resp = WebSearchResponse(query=query, backend="bing")
    for block in BLOCK_BING.findall(html):
        m = TITLE_BING.search(block)
        if not m:
            continue
        link = htmllib.unescape(m.group(1))
        title = _clean(m.group(2))
        snip = SNIPPET_BING.search(block)
        snippet = _clean(snip.group(1)) if snip else ""
        if not title or not link.startswith("http"):
            continue
        if "bing.com" in link and "/ck/a" in link:
            continue
        resp.results.append(WebResult(title=title, url=link, snippet=snippet[:300]))
        if len(resp.results) >= top_k:
            break
    return resp


def search_web(query: str, top_k: int = 5, timeout: float = 12.0, use_cache: bool = True) -> WebSearchResponse:
    key = f"{query}|{top_k}"
    if use_cache and key in _cache:
        return _cache[key]

    t0 = time.time()
    if settings.tavily_api_key:
        resp = _search_tavily(query, top_k, timeout)  # 预留
    else:
        # 先 360（中文相关性最好），没结果再退 Bing
        resp = WebSearchResponse(query=query, backend="")
        for backend, fn in (("360", _search_360), ("bing", _search_bing)):
            try:
                resp = fn(query, top_k, timeout)
            except Exception as e:  # noqa: BLE001
                resp = WebSearchResponse(query=query, backend=backend, error=f"{type(e).__name__}: {e}")
            if resp.results:
                break

    if resp.backend:
        resp.backend = f"{resp.backend} ({time.time() - t0:.1f}s)"
    if use_cache:
        _cache[key] = resp
    return resp


def _search_tavily(query: str, top_k: int, timeout: float) -> WebSearchResponse:  # pragma: no cover
    """预留：配了 TAVILY_API_KEY 就用正经搜索 API。"""
    import json as _json

    payload = _json.dumps(
        {"api_key": settings.tavily_api_key, "query": query, "max_results": top_k}
    ).encode()
    req = urllib.request.Request(
        "https://api.tavily.com/search",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = _json.loads(r.read())
    resp = WebSearchResponse(query=query, backend="tavily")
    for item in data.get("results", [])[:top_k]:
        resp.results.append(
            WebResult(title=item.get("title", ""), url=item.get("url", ""),
                      snippet=(item.get("content") or "")[:300])
        )
    return resp


def format_results(resp: WebSearchResponse) -> str:
    """转成给模型看的文本。必须显式标注"非知识库"。"""
    if resp.error:
        return f"（网页搜索失败：{resp.error}）"
    if not resp.results:
        return "（网页搜索没有返回结果）"
    lines = [
        f"【网络搜索结果】（来源：{resp.backend}，非内部知识库，引用时请注明这是网上资料）"
    ]
    for i, r in enumerate(resp.results, 1):
        lines.append(f"[W{i}] {r.title}\n    链接: {r.url}\n    摘要: {r.snippet}")
    return "\n".join(lines)
