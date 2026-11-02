"""备用推送通道：走 GitHub Git Data API（本机 git 的 SSH/HTTPS 被网络拦截时用）。

为什么会需要它：
    本机环境里 `git push` 可能报 "Connection reset by ... port 22" 或 TLS 握手超时，
    但 Python 的 HTTPS 是通的。这个脚本用 API 把**当前 HEAD 的完整文件树**重建到远端，
    因此结果与本地提交逐字节一致（不会出现换行符差异）。

安全约束：
    · 远端 HEAD 若是本地不认识的提交，必须显式 --force，避免误覆盖别人的提交；
    · 远端 tree 与本地 tree 相同时直接退出，不做无意义的空提交。

用法：
    python scripts/push_via_api.py            # 常规推送
    python scripts/push_via_api.py --force    # 远端有未知提交时强制覆盖（慎用）
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8"
    ).stdout.strip()


def git_bytes(*args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True).stdout


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser()
    p.add_argument("--force", action="store_true")
    args = p.parse_args()

    remote_url = git("remote", "get-url", "origin")
    if "github.com" not in remote_url:
        raise SystemExit(f"origin 不是 GitHub 仓库：{remote_url}")
    slug = remote_url.split("github.com")[-1].lstrip(":/").removesuffix(".git")
    owner, repo = slug.split("/")[:2]

    token = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True).stdout.strip()
    if not token:
        raise SystemExit("拿不到 gh token（先 gh auth login）")

    api_base = f"https://api.github.com/repos/{owner}/{repo}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "dockerhelp-agent",
        "Content-Type": "application/json",
    }

    def api(method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(api_base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            print(f"HTTP {e.code} {method} {path}: {e.read()[:200]}")
            raise

    local_sha = git("rev-parse", "HEAD")
    local_tree = git("rev-parse", "HEAD^{tree}")
    print(f"本地 HEAD {local_sha[:8]} tree {local_tree[:12]}")

    remote = api("GET", "/commits/main")
    remote_sha, remote_tree = remote["sha"], remote["commit"]["tree"]["sha"]
    print(f"远端 HEAD {remote_sha[:8]} tree {remote_tree[:12]}")

    if local_tree == remote_tree:
        print("✅ 远端已与本地一致，无需推送")
        return 0

    known = subprocess.run(
        ["git", "cat-file", "-e", f"{remote_sha}^{{commit}}"], cwd=ROOT, capture_output=True
    ).returncode == 0
    if not known and not args.force:
        print("⚠ 远端 HEAD 不在本地历史里（可能是上一次 API 推送产生的提交）。")
        print("  确认远端内容是你的、可以覆盖后，加 --force 重跑。")
        return 2

    # 用 `ls-tree -z` 枚举（NUL 分隔，中文路径不会被转义）
    records = [r for r in git_bytes("ls-tree", "-r", "-z", "HEAD").split(b"\x00") if r]
    print(f"重建 {len(records)} 个文件…")
    entries = []
    for rec in records:
        meta, path = rec.split(b"\t", 1)
        mode, otype, sha = meta.decode().split()
        if otype != "blob":
            continue
        content = git_bytes("cat-file", "blob", sha)
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            print("  跳过二进制:", path)
            continue
        blob = api("POST", "/git/blobs", {"content": text, "encoding": "utf-8"})
        entries.append(
            {"path": path.decode("utf-8"), "mode": mode, "type": "blob", "sha": blob["sha"]}
        )

    tree = api("POST", "/git/trees", {"tree": entries})
    same = tree["sha"] == local_tree
    print(f"新 tree {tree['sha'][:12]} | 与本地一致: {same}")
    if not same:
        print("⚠ tree 仍不一致，可能有文件被跳过（二进制/换行），已中止")
        return 3

    message = git("log", "-1", "--format=%B") or f"sync {local_sha[:8]}"
    commit = api(
        "POST", "/git/commits", {"message": message, "tree": tree["sha"], "parents": [remote_sha]}
    )
    api("PATCH", "/git/refs/heads/main", {"sha": commit["sha"], "force": False})
    print(f"✅ 已推送 → https://github.com/{owner}/{repo} ({commit['sha'][:8]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
