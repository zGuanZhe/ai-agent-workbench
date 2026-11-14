#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""facts.py — 采集页面顶部要展示的真实环境数字（缓存 10 分钟）。

build.py 与 serve.py 共用，避免两边各算一套、数字对不上。
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = Path(__file__).resolve().parent / ".facts.json"
TTL = 600

WECHAT_ROOTS = [
    r"D:\聊天记录\xwechat_files",
    os.path.expanduser(r"~\Documents\xwechat_files"),
    os.path.expanduser(r"~\Documents\WeChat Files"),
]
WECHAT_EXES = [
    r"C:\Program Files\Tencent\Weixin\Weixin.exe",
    r"C:\Program Files (x86)\Tencent\Weixin\Weixin.exe",
    r"C:\Program Files\Tencent\WeChat\WeChat.exe",
]


def _account_dir() -> str | None:
    env = os.environ.get("WECHAT_ACCOUNT_DIR")
    if env and os.path.isdir(env):
        return env
    for root in WECHAT_ROOTS:
        if not os.path.isdir(root):
            continue
        for name in os.listdir(root):
            cand = os.path.join(root, name)
            if os.path.isdir(os.path.join(cand, "msg", "file")):
                return cand
    return None


def _tree_stats(path: str) -> tuple[int, int]:
    n = size = 0
    for dirpath, _dirs, files in os.walk(path):
        for f in files:
            try:
                size += os.path.getsize(os.path.join(dirpath, f))
                n += 1
            except OSError:
                continue
    return n, size


def _count(path: Path, suffix: str | None = None) -> int:
    if not path.is_dir():
        return 0
    if suffix is None:
        return sum(len(fs) for _dp, _dn, fs in os.walk(path))
    return sum(1 for p in path.rglob(f"*{suffix}") if p.is_file())


def _officecli_version() -> str | None:
    exe = ROOT / "bin" / "officecli.exe"
    if not exe.exists():
        return None
    try:
        out = subprocess.run(
            [str(exe), "--version"], capture_output=True, text=True, timeout=20,
            encoding="utf-8", errors="replace",
        )
        return (out.stdout or "").strip().splitlines()[0][:20] or None
    except Exception:
        return None


def _exe_version(path: str) -> str | None:
    if not os.path.exists(path):
        return None
    try:  # 本机 Python 没装 pywin32，借 PowerShell 读版本资源
        cmd = f"(Get-Item '{path}').VersionInfo.FileVersion"
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
            capture_output=True, text=True, timeout=15, encoding="utf-8", errors="replace",
        )
        v = (out.stdout or "").strip()
        return v or None
    except Exception:
        return None


def _wechat_exe() -> str | None:
    for p in WECHAT_EXES:
        if os.path.exists(p):
            return p
    return None


def collect(force: bool = False) -> dict:
    if not force and CACHE.exists():
        try:
            cached = json.loads(CACHE.read_text(encoding="utf-8"))
            if time.time() - float(cached.get("_ts", 0)) < TTL:
                cached.pop("_ts", None)
                return cached
        except Exception:
            pass

    acc = _account_dir()
    files = size = 0
    if acc:
        try:
            files, size = _tree_stats(os.path.join(acc, "msg", "file"))
        except OSError:
            pass

    ws = ROOT / "workspace"
    facts = {
        "project": ROOT.name,
        "wechat_version": _exe_version(_wechat_exe()) if _wechat_exe() else None,
        "wechat_account": acc,
        "inbox_files": files,
        "inbox_gb": round(size / 1024 ** 3, 2) if size else 0,
        "gathered": _count(ws / "01_微信收回原件"),
        "backups": _count(ws / "02_自动备份区"),
        "outputs": _count(ws / "03_汇总与成稿输出"),
        "cache_dbs": _count(ws / "07_微信只读缓存", ".db"),
        "officecli": _officecli_version(),
        "at": time.strftime("%Y-%m-%d %H:%M"),
    }
    try:
        CACHE.write_text(json.dumps({**facts, "_ts": time.time()}, ensure_ascii=False),
                         encoding="utf-8")
    except OSError:
        pass
    return facts


if __name__ == "__main__":
    print(json.dumps(collect(), ensure_ascii=False, indent=2))
