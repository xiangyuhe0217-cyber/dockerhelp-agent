"""多线程断点续传下载 PyTorch CUDA wheel，并可直接安装。

为什么不用 pip：
  pip 是单连接、不能续传。国内下 2.4 GB 的 torch，中途断一次就从头再来，
  这就是"pip 指令一直断开"的根因。本脚本把文件切成 N 段并发下载，
  每段独立续传，断了重跑只补缺的部分。

用法：
  # 只下载
  python scripts/fetch_torch.py

  # 下载 + 安装 + 验证 CUDA
  python scripts/fetch_torch.py --install

  # 换版本/换源
  python scripts/fetch_torch.py --url <wheel_url> --threads 8
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.utils.console import setup_console  # noqa: E402

# 实测（2026-10-25，用户网络）：
#   上海交大 mirror.sjtu.edu.cn  单线程 1.0 MB/s，4 线程 4.0 MB/s  ← 默认
#   阿里云 mirrors.aliyun.com    单线程 0.5-0.75 MB/s，且并发会卡住
#   官方 download.pytorch.org    单线程 25 KB/s
DEFAULT_URL = (
    "https://mirror.sjtu.edu.cn/pytorch-wheels/cu126/"
    "torch-2.9.1%2Bcu126-cp311-cp311-win_amd64.whl"
)
# 备用源（交大挂了就换）
FALLBACK_URLS = [
    "https://mirrors.aliyun.com/pytorch-wheels/cu126/"
    "torch-2.9.1%2Bcu126-cp311-cp311-win_amd64.whl",
    "https://download.pytorch.org/whl/cu126/"
    "torch-2.9.1%2Bcu126-cp311-cp311-win_amd64.whl",
]
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def head(url: str) -> tuple[int, str]:
    req = urllib.request.Request(url, method="HEAD", headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        size = int(r.headers.get("Content-Length") or 0)
        accepts = r.headers.get("Accept-Ranges", "none")
    return size, accepts


class PartDownloader(threading.Thread):
    def __init__(self, url: str, path: Path, seg_start: int, seg_end: int, bar, lock, stop: threading.Event):
        super().__init__(daemon=True)
        self.url, self.path = url, path
        self.seg_start, self.seg_end = seg_start, seg_end
        self.bar, self.lock, self.stop = bar, lock, stop
        self.error: str | None = None

    def run(self) -> None:
        expect = self.seg_end - self.seg_start + 1
        have = self.path.stat().st_size if self.path.exists() else 0
        if have >= expect:
            with self.lock:
                self.bar.update(expect)
            return
        if have:
            with self.lock:
                self.bar.update(have)

        for attempt in range(4):
            if self.stop.is_set():
                return
            try:
                req = urllib.request.Request(
                    self.url,
                    headers={**UA, "Range": f"bytes={self.seg_start + have}-{self.seg_end}"},
                )
                with urllib.request.urlopen(req, timeout=60) as r, self.path.open("ab") as f:
                    while True:
                        chunk = r.read(262144)
                        if not chunk:
                            break
                        f.write(chunk)
                        with self.lock:
                            self.bar.update(len(chunk))
                return
            except Exception as e:  # noqa: BLE001
                self.error = f"{type(e).__name__}: {e}"
                time.sleep(2 * (attempt + 1))
                have = self.path.stat().st_size if self.path.exists() else 0
                if have >= expect:
                    return


def download(url: str, out: Path, threads: int) -> Path:
    from tqdm import tqdm  # 延迟导入，避免提前依赖

    size, accepts = head(url)
    if size <= 0:
        raise SystemExit("拿不到文件大小，检查 URL")
    name = Path(urllib.request.unquote(url)).name
    dest = out / name
    workdir = out / f"{name}.parts"
    workdir.mkdir(parents=True, exist_ok=True)

    if dest.exists() and dest.stat().st_size == size:
        print(f"[torch] 已存在完整文件：{dest}（{size / 1e9:.2f} GB）")
        return dest

    print(f"[torch] 目标：{name}")
    print(f"[torch] 大小：{size / 1e9:.2f} GB   分段：{threads}   Range 支持：{accepts}")

    if accepts.lower() != "bytes":
        threads = 1

    seg = size // threads
    ranges = []
    for i in range(threads):
        s = i * seg
        e = size - 1 if i == threads - 1 else (s + seg - 1)
        ranges.append((i, s, e))

    bar = tqdm(total=size, unit="B", unit_scale=True, desc="download")
    lock = threading.Lock()
    stop = threading.Event()
    workers = [
        PartDownloader(url, workdir / f"part{i}", s, e, bar, lock, stop) for i, s, e in ranges
    ]
    for w in workers:
        w.start()
    try:
        for w in workers:
            w.join()
    except KeyboardInterrupt:
        stop.set()
        print("\n[torch] 已中断，下次运行会从断点继续")
        raise SystemExit(130)
    bar.close()

    if any(w.error for w in workers):
        errs = {w.error for w in workers if w.error}
        raise SystemExit(f"[torch] 有分段失败，重跑本脚本即可续传。错误：{errs}")

    print("[torch] 合并分段…")
    tmp = dest.with_suffix(".tmp")
    with tmp.open("wb") as fout:
        for i, _, _ in ranges:
            part = workdir / f"part{i}"
            with part.open("rb") as fin:
                shutil.copyfileobj(fin, fout, 8 << 20)
    if tmp.stat().st_size != size:
        raise SystemExit(f"[torch] 合并后大小不符：{tmp.stat().st_size} != {size}")
    tmp.replace(dest)
    shutil.rmtree(workdir, ignore_errors=True)
    print(f"[torch] 完成：{dest}")
    return dest


def install(wheel: Path) -> None:
    py = sys.executable
    print(f"[torch] 安装到 {py}")
    cmd = [py, "-m", "pip", "install", "--no-deps", "--force-reinstall", str(wheel)]
    print("       " + " ".join(cmd))
    proc = subprocess.run(cmd)
    if proc.returncode != 0:
        raise SystemExit("[torch] 安装失败")


def verify() -> bool:
    print("[torch] 验证 CUDA…")
    code = (
        "import torch;"
        "print('torch', torch.__version__);"
        "print('cuda_available', torch.cuda.is_available());"
        "print('device', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU only')"
    )
    proc = subprocess.run([sys.executable, "-c", code])
    return proc.returncode == 0


def main() -> int:
    setup_console()
    p = argparse.ArgumentParser(description="多线程下载并安装 CUDA 版 torch")
    p.add_argument("--url", default=DEFAULT_URL)
    p.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / ".cache" / "wheels"))
    p.add_argument("--threads", type=int, default=8)
    p.add_argument("--install", action="store_true", help="下载完成后立即安装并验证")
    args = p.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    wheel = download(args.url, out, args.threads)
    if args.install:
        install(wheel)
        ok = verify()
        if ok:
            print("\n✅ CUDA 版 torch 就绪，可以跑 W2 了")
        return 0 if ok else 1

    print("\n下载完成。安装命令：")
    print(f'  "{sys.executable}" -m pip install --no-deps --force-reinstall "{wheel}"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
