"""抓取 GitHub Issues 作为「历史工单」。

特点（都是为了"可复现 + 不被限流卡死"）：
  - 过滤 PR（issues 接口会混进 PR，靠 pull_request 字段识别）
  - 断点续抓（已写入的 id 直接跳过）
  - 主动感知限流（X-RateLimit-Remaining 低就睡到 reset）
  - 可选抓首条 maintainer 回复当 resolution（相似工单推荐的"标准答案"）

用法：
  python scripts/fetch_issues.py --repo docker/for-win --state closed --limit 300
  python scripts/fetch_issues.py --repo moby/moby --label kind/bug --limit 200 --with-comments 150
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from tenacity import retry, stop_after_attempt, wait_exponential
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.utils.console import setup_console  # noqa: E402

VERSION_RE = re.compile(r"\b(\d+\.\d+\.\d+)\b")
VERSION_LABEL_RE = re.compile(r"(?:^|/)version/(\d+\.\d+\.\d+)")
MAX_BODY = 8000
MAX_RESOLUTION = 2000

# 这些是机器人模板话术，不是真正的解决方案
STALE_MARKERS = (
    "automatically marked as stale",
    "this issue is stale",
    "stale, locked",
    "has been automatically closed",
)


def version_from_labels(label_names: list[str]) -> str:
    """优先用 `version/4.34.2` 这类标签 —— 比正文里瞎抓的 4.1.1 靠谱得多。"""
    for name in label_names:
        m = VERSION_LABEL_RE.search(name)
        if m:
            return m.group(1)
    return ""


def make_session() -> requests.Session:
    s = requests.Session()
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "dockerhelp-agent",
    }
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"
    else:
        print(
            "[issues] ⚠ 未配置 GITHUB_TOKEN，限流 60 次/小时，"
            "抓 400 条大概率会中断。请先在 .env 填 token。"
        )
    s.headers.update(headers)
    return s


def respect_rate_limit(resp: requests.Response) -> None:
    remaining = resp.headers.get("X-RateLimit-Remaining")
    if remaining is None:
        return
    if int(remaining) <= 5:
        reset = int(resp.headers.get("X-RateLimit-Reset", "0"))
        wait = max(0, reset - int(time.time())) + 3
        print(f"\n[issues] 限流即将耗尽，休眠 {wait}s 直到重置…")
        time.sleep(min(wait, 3700))


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=30))
def get_json(session: requests.Session, url: str, params: dict | None = None) -> list | dict:
    resp = session.get(url, params=params, timeout=30)
    respect_rate_limit(resp)
    if resp.status_code == 403:
        reset = int(resp.headers.get("X-RateLimit-Reset", "0"))
        wait = max(0, reset - int(time.time())) + 3
        print(f"\n[issues] 403 限流，休眠 {wait}s…")
        time.sleep(min(wait, 3700))
        raise requests.HTTPError("rate limited")
    resp.raise_for_status()
    return resp.json()


def pick_resolution(session: requests.Session, api: str, repo: str, number: int, author: str) -> str:
    """取首条"非 OP、非 bot、非 stale 模板、有一定长度"的回复作为 resolution。"""
    try:
        comments = get_json(
            session,
            f"{api}/repos/{repo}/issues/{number}/comments",
            {"per_page": 20},
        )
    except Exception:
        return ""
    if not isinstance(comments, list):
        return ""
    for c in comments:
        user = c.get("user") or {}
        login = user.get("login", "")
        if login == author or user.get("type") == "Bot" or login.endswith("[bot]"):
            continue
        body = (c.get("body") or "").strip()
        if len(body) < 80:
            continue
        lowered = body.lower()
        if any(marker in lowered for marker in STALE_MARKERS):
            continue
        return body[:MAX_RESOLUTION]
    return ""


def load_done_ids(path: Path) -> set[str]:
    done: set[str] = set()
    if not path.exists():
        return done
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                done.add(json.loads(line)["id"])
            except Exception:
                continue
    return done


def main() -> int:
    setup_console()
    p = argparse.ArgumentParser(description="抓取 GitHub Issues 作为历史工单")
    p.add_argument("--repo", action="append", default=None, help="owner/repo，可重复")
    p.add_argument("--state", default="closed", choices=["open", "closed", "all"])
    p.add_argument("--label", default="", help="逗号分隔，如 kind/bug")
    p.add_argument("--limit", type=int, default=300)
    p.add_argument("--with-comments", type=int, default=0, help="前 N 条抓 resolution")
    p.add_argument("--sort", default="updated")
    p.add_argument(
        "--force",
        action="store_true",
        help="忽略断点续抓，重新抓取已有 id（用于修复早期抓到的不完整数据）",
    )
    args = p.parse_args()

    settings.ensure_dirs()
    repos = args.repo or settings.repo_list
    api = settings.github_api
    session = make_session()

    total_written = 0
    for repo in repos:
        owner, name = repo.split("/", 1)
        out_path = settings.issues_dir / f"{owner}_{name}.jsonl"
        done = load_done_ids(out_path)
        if args.force and out_path.exists():
            backup = out_path.with_suffix(".jsonl.bak")
            out_path.replace(backup)
            done = set()
            print(f"[issues] --force：原文件已备份为 {backup.name}，将重新抓取")
        print(f"\n[issues] {repo}  已有 {len(done)} 条，目标 {args.limit}")

        params: dict = {
            "state": args.state,
            "sort": args.sort,
            "direction": "desc",
            "per_page": 100,
        }
        if args.label:
            params["labels"] = args.label

        written = 0
        page = 1
        fetched = 0
        with out_path.open("a", encoding="utf-8") as fout, tqdm(
            total=args.limit, desc=repo, unit="issue"
        ) as bar:
            while written < args.limit:
                params["page"] = page
                try:
                    batch = get_json(session, f"{api}/repos/{repo}/issues", params)
                except Exception as e:
                    print(f"\n[issues] 第 {page} 页抓取失败：{e}，停止该仓库")
                    break
                if not isinstance(batch, list) or not batch:
                    print(f"\n[issues] 第 {page} 页为空，结束")
                    break

                for item in batch:
                    if written >= args.limit:
                        break
                    if "pull_request" in item:
                        continue  # 是 PR，不是工单

                    tid = f"{repo}#{item['number']}"
                    if tid in done:
                        continue

                    author = (item.get("user") or {}).get("login", "")
                    body = (item.get("body") or "").strip()
                    label_names = [lb.get("name", "") for lb in item.get("labels", [])]
                    resolution = ""
                    if args.with_comments and written < args.with_comments:
                        resolution = pick_resolution(
                            session, api, repo, item["number"], author
                        )

                    text_for_version = f"{item.get('title', '')}\n{body}"
                    vm = VERSION_RE.search(text_for_version)

                    row = {
                        "id": tid,
                        "source": repo,
                        "number": item["number"],
                        "title": item.get("title", "")[:500],
                        "body": body[:MAX_BODY],
                        "labels": json.dumps(label_names, ensure_ascii=False),
                        "state": item.get("state", ""),
                        "created_at": item.get("created_at", ""),
                        "closed_at": item.get("closed_at") or "",
                        "resolution": resolution,
                        "url": item.get("html_url", ""),
                        "product_version": version_from_labels(label_names)
                        or (vm.group(1) if vm else ""),
                        "is_synthetic": 0,
                        "sha256": hashlib.sha256(
                            f"{tid}{item.get('title','')}".encode()
                        ).hexdigest(),
                        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    }
                    fout.write(json.dumps(row, ensure_ascii=False) + "\n")
                    done.add(tid)
                    written += 1
                    bar.update(1)

                fetched += len(batch)
                page += 1
                time.sleep(0.3)  # 温和一点，别把 secondary limit 惹出来

        total_written += written
        print(f"[issues] {repo} 新增 {written} 条 → {out_path}")

    print(f"\n[issues] 合计新增 {total_written} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
