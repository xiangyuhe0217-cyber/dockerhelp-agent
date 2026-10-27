"""下载 SentenceTransformer 模型（默认从魔搭 ModelScope，多线程 + 断点续传）。

为什么不用 huggingface_hub：
  实测本机到 huggingface.co / hf-mirror.com 只有 25–31 KB/s（2.27 GB 要 20+ 小时），
  魔搭 8 线程能到 ~580 KB/s（约 1 小时）。

用法：
  python scripts/fetch_model.py                          # BAAI/bge-m3 → models/bge-m3
  python scripts/fetch_model.py --repo BAAI/bge-reranker-v2-m3
  python scripts/fetch_model.py --threads 8 --print-only  # 只打印将要下载的文件
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.utils.console import setup_console  # noqa: E402

MS_BASE = "https://modelscope.cn/api/v1/models/{repo}/repo?Revision=master&FilePath={path}"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


def resolve_cdn(url: str) -> str:
    """ModelScope 会 302 到 CDN 直链。

    **关键**：api 端点只有 ~23 KB/s，而 302 之后的 cdn-lfs 直链能到 7+ MB/s。
    所以必须先解析跳转，再对直链做分段下载。
    """
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, headers=UA)
    try:
        with opener.open(req, timeout=30) as r:
            return url  # 没有跳转
    except urllib.error.HTTPError as e:
        loc = e.headers.get("Location")
        if e.code in (301, 302, 303, 307, 308) and loc:
            return loc
        raise

# 只取 dense 编码必需的文件；colbert_linear.pt / sparse_linear.pt 是稀疏/多向量模式用的，跳过
REQUIRED_FILES = [
    "config.json",
    "config_sentence_transformers.json",
    "modules.json",
    "sentence_bert_config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "sentencepiece.bpe.model",
    "1_Pooling/config.json",
    "pytorch_model.bin",
]


def head(url: str) -> int:
    req = urllib.request.Request(url, method="HEAD", headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return int(r.headers.get("Content-Length") or 0)


def download_file(url: str, dest: Path, threads: int) -> None:
    size = head(url)
    if size <= 0:
        raise RuntimeError(f"拿不到大小: {url}")
    if dest.exists() and dest.stat().st_size == size:
        print(f"  ✓ {dest.name} 已存在（{size / 1e6:.1f}MB）")
        return

    if size < 4 * 1024 * 1024 or threads <= 1:
        # 小文件直接单线程
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        have = tmp.stat().st_size if tmp.exists() else 0
        headers = {**UA}
        if have:
            headers["Range"] = f"bytes={have}-"
        with urllib.request.urlopen(
            urllib.request.Request(url, headers=headers), timeout=120
        ) as r, tmp.open("ab" if have else "wb") as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
        tmp.replace(dest)
        print(f"  ✓ {dest.name}（{size / 1e6:.1f}MB）")
        return

    # 大文件分段并发
    dest.parent.mkdir(parents=True, exist_ok=True)
    workdir = dest.parent / f".{dest.name}.parts"
    workdir.mkdir(exist_ok=True)

    # 分段清单：线程数一变，分段边界就变了，旧分段文件会错位 —— 必须重建
    manifest = workdir / "_manifest.json"
    meta = {"size": size, "threads": threads}
    if manifest.exists():
        try:
            old = json.loads(manifest.read_text(encoding="utf-8"))
        except Exception:
            old = {}
        if old.get("size") != size or old.get("threads") != threads:
            print(f"  ⚠ 分段参数变化（{old} → {meta}），清空旧分段重下")
            shutil.rmtree(workdir, ignore_errors=True)
            workdir.mkdir(exist_ok=True)
    manifest.write_text(json.dumps(meta), encoding="utf-8")

    seg = size // threads
    ranges = [
        (i, i * seg, size - 1 if i == threads - 1 else (i + 1) * seg - 1)
        for i in range(threads)
    ]
    done_bytes = [0] * threads
    lock = threading.Lock()
    errors: list[str] = []

    def worker(idx: int, start: int, end: int) -> None:
        path = workdir / f"part{idx}"
        expect = end - start + 1
        have = path.stat().st_size if path.exists() else 0
        if have >= expect:
            done_bytes[idx] = expect
            return
        for attempt in range(4):
            try:
                req = urllib.request.Request(
                    url, headers={**UA, "Range": f"bytes={start + have}-{end}"}
                )
                with urllib.request.urlopen(req, timeout=90) as r, path.open("ab") as f:
                    while True:
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                        with lock:
                            done_bytes[idx] += len(chunk)
                return
            except Exception as e:  # noqa: BLE001
                errors.append(f"{type(e).__name__}: {e}")
                time.sleep(2 * (attempt + 1))
                have = path.stat().st_size if path.exists() else 0

    ts = [threading.Thread(target=worker, args=r, daemon=True) for r in ranges]
    for t in ts:
        t.start()

    t0 = time.time()
    while any(t.is_alive() for t in ts):
        time.sleep(2)
        got = sum(done_bytes)
        speed = got / max(time.time() - t0, 0.01) / 1024
        pct = got / size * 100
        print(
            f"\r  {dest.name}: {got / 1e6:6.1f}/{size / 1e6:.1f}MB "
            f"({pct:5.1f}%) {speed:6.0f} KB/s",
            end="",
            flush=True,
        )
    print()

    if any(not (workdir / f"part{i}").exists() for i in range(threads)):
        raise RuntimeError(f"分段缺失，重跑即可续传。错误：{errors[:3]}")

    tmp = dest.with_suffix(dest.suffix + ".tmp")
    with tmp.open("wb") as fout:
        for i in range(threads):
            with (workdir / f"part{i}").open("rb") as fin:
                while True:
                    buf = fin.read(8 << 20)
                    if not buf:
                        break
                    fout.write(buf)
    if tmp.stat().st_size != size:
        raise RuntimeError(f"合并大小不符 {tmp.stat().st_size} != {size}")
    tmp.replace(dest)
    for i in range(threads):
        (workdir / f"part{i}").unlink(missing_ok=True)
    workdir.rmdir()
    print(f"  ✓ {dest.name}（{size / 1e6:.1f}MB）")


def main() -> int:
    setup_console()
    p = argparse.ArgumentParser(description="下载 SentenceTransformer 模型")
    p.add_argument("--repo", default="BAAI/bge-m3")
    p.add_argument("--out", default="", help="默认 models/<repo 名>")
    p.add_argument("--threads", type=int, default=8)
    p.add_argument("--files", nargs="*", default=None, help="覆盖默认文件列表")
    p.add_argument("--print-only", action="store_true")
    args = p.parse_args()

    root = Path(__file__).resolve().parent.parent
    out = Path(args.out) if args.out else root / "models" / args.repo.split("/")[-1]
    files = args.files or REQUIRED_FILES

    print(f"[model] {args.repo} → {out}")
    print(f"[model] 共 {len(files)} 个文件，{args.threads} 线程/大文件")
    if args.print_only:
        for f in files:
            print("  ", f)
        return 0

    out.mkdir(parents=True, exist_ok=True)
    failed: list[str] = []
    for i, f in enumerate(files, 1):
        api_url = MS_BASE.format(repo=args.repo, path=urllib.parse.quote(f, safe=""))
        print(f"[{i}/{len(files)}] {f}")
        try:
            url = resolve_cdn(api_url)
            download_file(url, out / f, args.threads)
        except urllib.error.HTTPError as e:
            print(f"  ✗ HTTP {e.code}（该文件可能不存在，跳过）")
            failed.append(f)
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ {type(e).__name__}: {e}")
            failed.append(f)

    if failed:
        print(f"\n[model] 失败 {len(failed)} 个：{failed}")
        print("[model] 直接重跑本命令即可续传")
        return 1
    print(f"\n[model] 完成：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
