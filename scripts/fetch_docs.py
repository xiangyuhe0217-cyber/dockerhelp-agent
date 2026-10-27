"""抓取 docker/docs 的 markdown 源。

为什么不用 git clone：
  1. 少一个系统依赖（没有 git 的机器也能跑）；
  2. 可以锁定到具体 commit，天然可复现；
  3. 只下一次 tarball，比 sparse clone 快，也不会被 git 代理配置卡住。

输出：
  data/docs/**.md            原始 markdown（自包含，build_db 不依赖网络）
  data/docs_index.jsonl      每个文件一行元数据
  data/docs_commit.txt       本次锁定的 commit

用法：
  python scripts/fetch_docs.py
  python scripts/fetch_docs.py --max-files 300 --commit <sha>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import tarfile
import sys
import urllib.error
import urllib.request
from pathlib import Path

import requests
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.utils.console import setup_console  # noqa: E402
from app.utils.md import read_markdown  # noqa: E402

DOCS_BASE = "https://docs.docker.com"
GH_BLOB = "https://github.com/docker/docs/blob"
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
WORD_RE = re.compile(r"[A-Za-z0-9_]+")


# --------------------------------------------------------------------------- #
# 下载与解包
# --------------------------------------------------------------------------- #
def repo_slug(repo_url: str) -> str:
    """https://github.com/docker/docs.git -> docker/docs"""
    s = repo_url.strip().rstrip("/")
    if s.endswith(".git"):
        s = s[:-4]
    parts = s.split("/")
    return f"{parts[-2]}/{parts[-1]}"


def derive_sha(root_dir_name: str) -> str:
    """从 tarball 顶层目录名取 sha（形如 `docs-<sha>`）。

    注意：只有按 sha 下载时目录名才带 sha；按分支下载拿到的是 `docs-main`。
    所以真正可靠的来源是 resolve_ref_sha（Atom 源），这里只作兜底。
    """
    m = re.search(r"-([0-9a-f]{7,40})$", root_dir_name)
    return m.group(1) if m else ""


def resolve_ref_sha(slug: str, ref: str) -> str:
    """用 GitHub 的 Atom 提交源解析分支最新 commit。

    为什么不用 REST API：未认证时 60 次/小时，抓个文档就耗尽；
    Atom 源没有这个限制，且同样能拿到 40 位 sha。
    """
    url = f"https://github.com/{slug}/commits/{ref}.atom"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "dockerhelp-agent"})
        with urllib.request.urlopen(req, timeout=30) as r:
            text = r.read().decode("utf-8", "replace")
    except Exception as e:
        print(f"[docs] ⚠ 无法解析 {ref} 的 sha（{type(e).__name__}），退回分支名记录")
        return ""
    m = re.search(r"Grit::Commit/([0-9a-f]{40})", text)
    return m.group(1) if m else ""


def download_tarball(session: requests.Session, slug: str, ref: str, dest: Path) -> Path:
    """用 urllib 流式下载。

    实测在某些网络环境下 requests 的 stream=True 会卡在首包，
    而 urllib 正常；这里以"能下下来"为准。
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 1024 * 1024:
        print(f"[docs] 复用已下载的 tarball（{dest.stat().st_size / 1e6:.1f} MB）")
        return dest

    url = f"https://codeload.github.com/{slug}/tar.gz/{ref}"
    tmp = dest.with_suffix(".part")

    # ---- 断点续传：这网络环境经常几十 KB/s，一次下完不现实 ----
    start = tmp.stat().st_size if tmp.exists() else 0
    headers = {"User-Agent": "dockerhelp-agent"}
    if start:
        headers["Range"] = f"bytes={start}-"
        print(f"[docs] 续传：已有 {start / 1e6:.1f} MB")
    else:
        print(f"[docs] 下载 {url}")

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            # 服务器不支持 Range 时会返回 200，必须从头写
            mode = "ab" if (start and resp.status == 206) else "wb"
            if mode == "wb":
                start = 0
            total = int(resp.headers.get("Content-Length") or 0) + start
            with tmp.open(mode) as f, tqdm(
                total=total, initial=start, unit="B", unit_scale=True, desc="download"
            ) as bar:
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
                    bar.update(len(chunk))
                    f.flush()
    except urllib.error.HTTPError as e:
        if e.code == 416 and start:
            print("[docs] 已完成（服务器返回 416），直接使用本地文件")
        else:
            tmp.unlink(missing_ok=True)
            err = requests.HTTPError(f"HTTP {e.code} for {url}")
            err.response = type("R", (), {"status_code": e.code})()  # type: ignore[assignment]
            raise err from e
    tmp.replace(dest)
    return dest


def safe_extract(tar_path: Path, dest_dir: Path) -> tuple[Path, str]:
    """解包并去掉仓库名那层目录，返回 (根目录, 顶层目录名)。

    手工做路径校验：拒绝绝对路径与 `..`（tarfile 的 filter='data' 要 3.12+）。
    """
    if dest_dir.exists():
        shutil.rmtree(dest_dir, ignore_errors=True)
    dest_dir.mkdir(parents=True, exist_ok=True)

    root_name: str | None = None
    with tarfile.open(tar_path, "r:gz") as tf:
        members = []
        for m in tf.getmembers():
            parts = Path(m.name).parts
            if not parts:
                continue
            if root_name is None:
                root_name = parts[0]
            if parts[0] != root_name:
                continue
            rel = Path(*parts[1:]) if len(parts) > 1 else Path()
            if rel == Path() or any(p == ".." for p in parts) or Path(m.name).is_absolute():
                continue
            m.name = str(rel)
            members.append(m)
        tf.extractall(dest_dir, members=members)  # noqa: S202

    return dest_dir, (root_name or "")


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #
def detect_lang(text: str) -> str:
    if not text:
        return "en"
    cjk = len(CJK_RE.findall(text))
    ratio = cjk / len(text)
    if ratio > 0.15:
        return "zh"
    if ratio < 0.01:
        return "en"
    return "mixed"


def count_words(text: str) -> int:
    return len(WORD_RE.findall(text)) + len(CJK_RE.findall(text))


def resolve_url(fm: dict, rel_from_root: str, commit: str) -> str:
    """优先用 front-matter 的 aliases 拼出 docs.docker.com 真实链接。"""
    aliases = fm.get("aliases") or []
    if isinstance(aliases, str):
        aliases = [aliases]
    if aliases:
        path = str(aliases[0]).strip()
        if path and path.startswith("/"):
            return DOCS_BASE + path.rstrip("/") + "/"
    return f"{GH_BLOB}/{commit}/{rel_from_root}"


def main() -> int:
    setup_console()
    p = argparse.ArgumentParser(description="抓取 docker/docs 的 markdown")
    p.add_argument("--max-files", type=int, default=settings.docs_max_files)
    p.add_argument("--commit", default=settings.docs_commit, help="锁定 commit / 分支")
    p.add_argument("--repo", default=settings.docs_repo)
    args = p.parse_args()

    settings.ensure_dirs()
    session = requests.Session()
    session.headers.update({"User-Agent": "dockerhelp-agent"})
    if settings.github_token:
        session.headers["Authorization"] = f"Bearer {settings.github_token}"

    slug = repo_slug(args.repo)
    slug_key = slug.replace("/", "_")

    # 1) 先把 ref 解析成真实 sha（用于记录与可复现）
    ref_in = args.commit or "main"
    if re.fullmatch(r"[0-9a-f]{40}", ref_in):
        sha = ref_in
    else:
        sha = resolve_ref_sha(slug, ref_in)
        print(f"[docs] {ref_in} -> {sha or '(未取到 sha)'}")

    # 2) 下载：显式给了 sha 就按 sha 下（严格锁定）；否则按分支下（省一次全量重下）
    targets = [ref_in] if sha and ref_in == sha else [f"refs/heads/{ref_in}"]
    if not args.commit:
        targets.append("refs/heads/master")

    tarball: Path | None = None
    for target in targets:
        candidate = settings.cache_dir / f"{slug_key}-{target.replace('/', '_')}.tar.gz"
        try:
            download_tarball(session, slug, target, candidate)
            tarball = candidate
            break
        except requests.HTTPError as e:
            status = getattr(e.response, "status_code", None)
            print(f"[docs] target={target} 失败（HTTP {status}），尝试下一个")

    if tarball is None:
        raise SystemExit("[docs] 无法下载仓库 tarball，请检查 --commit / --repo")

    repo_root, root_name = safe_extract(tarball, settings.cache_dir / f"{tarball.name[:-7]}_src")
    commit = derive_sha(root_name) or sha or ref_in
    print(f"[docs] {slug} @ {commit}")

    content_root = repo_root / "content"
    if not content_root.is_dir():
        print("[docs] ⚠ 未找到 content/，退回仓库根目录")
        content_root = repo_root

    md_files = sorted(content_root.rglob("*.md"))
    print(f"[docs] 仓库内 markdown 共 {len(md_files)} 个，开始解析")

    index_rows: list[dict] = []
    written = 0
    skipped_short = 0
    skipped_dir = 0

    for path in tqdm(md_files, desc="parse", unit="file"):
        rel_parts = path.relative_to(content_root).parts
        if any(part in settings.skip_dirs for part in rel_parts):
            skipped_dir += 1
            continue

        try:
            fm, raw_body = read_markdown(path)
        except Exception as e:
            print(f"\n[docs] ⚠ 解析失败 {path.name}: {e}")
            continue

        body = raw_body.strip()
        if count_words(body) < 50:
            skipped_short += 1
            continue

        rel_path = "/".join(rel_parts)
        doc_id = hashlib.sha1(rel_path.encode("utf-8")).hexdigest()[:16]
        rel_from_root = path.relative_to(repo_root).as_posix()

        row = {
            "id": doc_id,
            "rel_path": rel_path,
            "url": resolve_url(fm, rel_from_root, commit),
            "title": str(fm.get("title") or path.stem).strip(),
            "description": str(fm.get("description") or "").strip(),
            "section": rel_parts[0] if len(rel_parts) > 1 else "",
            "word_count": count_words(body),
            "has_code": int("```" in body),
            "has_table": int(bool(re.search(r"\n\s*\|?[\s:-]*\|[\s:-]*\|", body))),
            "lang": detect_lang(body),
            "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "commit": commit,
        }

        out_path = settings.docs_dir / rel_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(raw_body, encoding="utf-8")

        index_rows.append(row)
        written += 1
        if written >= args.max_files:
            break

    with settings.docs_index_path.open("w", encoding="utf-8") as f:
        for row in index_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    (settings.data_dir / "docs_commit.txt").write_text(commit, encoding="utf-8")

    total_words = sum(r["word_count"] for r in index_rows)
    print(
        f"\n[docs] 完成：写入 {written} 篇，跳过(过短) {skipped_short}，"
        f"跳过(目录) {skipped_dir}，总词数 {total_words:,}"
    )
    print(f"[docs] commit = {commit}")
    print(f"[docs] 索引   = {settings.docs_index_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
