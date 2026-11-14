#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wckey.py — 从企业微信进程内存里取数据库密钥。

企业微信不把密钥写进配置文件，它只在 WXWork.exe 的内存里。取法两种：

1. **x'...' 特征**（快）：打开库时 SQL 里会带 ``x'<32 位 hex>'`` 形式的 key，
   字符串残留在内存里就直接正则捞出来。
2. **cipher 结构体特征**（稳）：wxSQLite3 的 codec 对象开头是
   ``flags=1|2``、``flags2=1|2|4096``，密钥固定落在结构体 +8 处。
   本机实测策略 1 一条都没命中，策略 2 命中 32 处、扫出 6 万多个候选。

难点不在解密而在「怎么在一堆 16 字节里认出哪一个是钥匙」。这里靠页 1 反证：
企业微信页 1 的 [16:24] 是明文头片段，解密后必须逐字节还原。所以每个候选
只要解 **一个 AES 块**（16 字节）就能判定，比整页解密快两个数量级 ——
6 万个候选从 40 秒压到 1 秒内。

密钥只落在 ``workspace/06_运行状态/wecom_key.json``（本机、不进版本库）。
取不到就如实报错，不猜、不建假 key。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import hashlib
import json
import os
import re
import struct
import subprocess
import time
from typing import Any, Dict, List, Optional

from . import wcrypto

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
STATE_DIR = os.path.join(_ROOT, "workspace", "06_运行状态")
KEY_FILE = os.path.join(STATE_DIR, "wecom_key.json")

# 企业微信 5.x 的 codec 结构体特征（实测 5.0.12.6018）
_PAGE_SIZE_FLAGS = (1, 2, 4096, 8192, 16384)

_MEM_COMMIT = 0x1000
_READABLE = {0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80}
_PROT_READ = 0x0010
_PROC_QUERY = 0x0400

_k32 = ctypes.windll.kernel32


class _MBI(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_uint64), ("AllocationBase", ctypes.c_uint64),
        ("AllocationProtect", wt.DWORD), ("_pad1", wt.DWORD),
        ("RegionSize", ctypes.c_uint64), ("State", wt.DWORD),
        ("Protect", wt.DWORD), ("Type", wt.DWORD), ("_pad2", wt.DWORD),
    ]


# --------------------------------------------------------------------------- #
# 快速判定
# --------------------------------------------------------------------------- #
def _quick_verify(raw_key: bytes, page1: bytes) -> bool:
    """只解第一个 AES 块就判断是不是这把钥匙。

    页 1 解密后的头 8 字节必须等于 [16:24] 的明文片段。CBC 下第一个明文块
    = D(C1) XOR IV，而 C1 由 [8:16] 与 [24:32] 拼成（中间那段是明文，
    解密前要把 [8:16] 挪到 [16:24]）。一次块解密 ≈ 5 微秒，
    足够把「6 万个候选」压到亚秒级。
    """
    if len(raw_key) != 16 or len(page1) < 32 or not wcrypto.has_header_fragment(page1):
        return False
    frag = bytes(page1[16:24])
    block = bytes(page1[8:16]) + bytes(page1[24:32])
    try:
        pt = wcrypto._aes_cbc_decrypt(wcrypto.page_key(raw_key, 1),
                                      wcrypto.initial_vector(1), block)
    except Exception:
        return False
    return pt[:8] == frag


# --------------------------------------------------------------------------- #
# 进程与内存
# --------------------------------------------------------------------------- #
def wxwork_pids() -> List[Dict[str, Any]]:
    """列出 WXWork.exe 进程，内存占用大的排前面（主进程就在前面）。"""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq WXWork.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    rows = []
    for line in out.strip().splitlines():
        parts = line.strip().strip('"').split('","')
        if len(parts) < 5:
            continue
        try:
            pid = int(parts[1])
            mem = int(parts[4].replace(",", "").replace(" K", "").strip() or 0)
        except ValueError:
            continue
        rows.append({"pid": pid, "mem_kb": mem})
    rows.sort(key=lambda r: -r["mem_kb"])
    return rows


def _regions(handle: int) -> List[tuple]:
    out, addr, mbi = [], 0, _MBI()
    while addr < 0x7FFFFFFFFFFF:
        if _k32.VirtualQueryEx(handle, ctypes.c_uint64(addr),
                               ctypes.byref(mbi), ctypes.sizeof(mbi)) == 0:
            break
        if (mbi.State == _MEM_COMMIT and mbi.Protect in _READABLE
                and 0 < mbi.RegionSize < 500 * 1024 * 1024):
            out.append((mbi.BaseAddress, mbi.RegionSize))
        nxt = mbi.BaseAddress + mbi.RegionSize
        if nxt <= addr:
            break
        addr = nxt
    return out


def _read(handle: int, addr: int, size: int) -> Optional[bytes]:
    buf = ctypes.create_string_buffer(size)
    got = ctypes.c_size_t(0)
    if _k32.ReadProcessMemory(handle, ctypes.c_uint64(addr), buf, size, ctypes.byref(got)):
        return buf.raw[: got.value]
    return None


# --------------------------------------------------------------------------- #
# 扫描
# --------------------------------------------------------------------------- #
def _scan_pid(pid: int, page1: bytes, deadline: float,
              verbose: bool = False) -> Dict[str, Any]:
    """扫一个进程。返回命中的 key 与统计，不做缓存判断。"""
    stat = {"pid": pid, "regions": 0, "bytes": 0, "candidates": 0, "hits": [], "error": None}
    handle = _k32.OpenProcess(_PROT_READ | _PROC_QUERY, False, pid)
    if not handle:
        # 权限不够时通常是「以管理员身份运行」才能读；这里只记原因，不抛
        stat["error"] = "OpenProcess 失败（可能权限不足，试试以管理员身份运行）"
        return stat
    hex_re = re.compile(rb"x'([0-9a-fA-F]{32,192})'")
    try:
        regs = _regions(handle)
        stat["regions"] = len(regs)
        for base, size in regs:
            if time.time() > deadline:
                stat["error"] = "超时"
                break
            data = _read(handle, base, size)
            if not data:
                continue
            stat["bytes"] += len(data)

            # 策略 1：内存里残留的 x'...' 形式的 key
            for m in hex_re.finditer(data):
                s = m.group(1).decode("ascii", "ignore")
                for cand in {s[:32], s[:64][:32]}:
                    if len(cand) != 32:
                        continue
                    stat["candidates"] += 1
                    try:
                        key = bytes.fromhex(cand)
                    except ValueError:
                        continue
                    if _quick_verify(key, page1):
                        stat["hits"].append(cand)

            # 策略 2：codec 结构体（flags, flags2, key 固定在同一偏移）
            n = len(data)
            if n < 0x40:
                continue
            for off in range(0, n - 0x40, 4):
                f0, f4 = struct.unpack_from("<II", data, off)
                if f0 not in (1, 2) or f4 not in _PAGE_SIZE_FLAGS:
                    continue
                key = data[off + 8: off + 24]
                if key == b"\x00" * 16 or len(set(key)) < 6:
                    continue
                stat["candidates"] += 1
                if _quick_verify(key, page1):
                    stat["hits"].append(key.hex())
    finally:
        _k32.CloseHandle(handle)
    stat["hits"] = sorted(set(stat["hits"]))
    return stat


def scan(page1: bytes, timeout: int = 120, verbose: bool = False) -> Dict[str, Any]:
    """按内存占用顺序扫 WXWork.exe 进程，命中即停。"""
    pids = wxwork_pids()
    if not pids:
        return {"ok": False, "error": "WXWork.exe 没在运行：密钥只在内存里，请先打开企业微信并登录",
                "pids": []}
    deadline = time.time() + max(10, timeout)
    stats = []
    for item in pids:
        st = _scan_pid(item["pid"], page1, deadline, verbose)
        stats.append(st)
        if st["hits"]:
            # 完整解一页复核，避免「快校验撞了个巧合」
            for h in st["hits"]:
                if wcrypto.verify_key(bytes.fromhex(h), page1):
                    return {"ok": True, "key": h, "pid": item["pid"], "stats": stats,
                            "candidates": sum(s["candidates"] for s in stats)}
            stats[-1]["hits"] = []
        if time.time() > deadline:
            break
    return {"ok": False, "error": "扫完了没找到能解开当前库的 key",
            "hint": "企业微信版本升级后密钥位置可能变了；也可能扫到的是别的账号的进程",
            "stats": stats, "candidates": sum(s["candidates"] for s in stats)}


# --------------------------------------------------------------------------- #
# 缓存
# --------------------------------------------------------------------------- #
def _fingerprint(db_dir: str) -> str:
    """用 message.db 的页 1 前 64 字节做指纹：换了账号或重建库都会变。"""
    for name in ("message.db", "session.db", "user.db"):
        p = os.path.join(db_dir, name)
        if os.path.isfile(p):
            try:
                with open(p, "rb") as f:
                    return hashlib.md5(f.read(64)).hexdigest()
            except OSError:
                continue
    return ""


def load_cached(db_dir: str) -> Optional[str]:
    """缓存里的 key 还能解开当前库才返回，否则当作无效。"""
    try:
        data = json.loads(open(KEY_FILE, encoding="utf-8").read())
    except (OSError, ValueError):
        return None
    if data.get("db_dir") != db_dir or data.get("fingerprint") != _fingerprint(db_dir):
        return None
    key = str(data.get("key") or "")
    if len(key) != 32:
        return None
    page1 = wcrypto.read_page1(os.path.join(db_dir, "message.db"))
    if not page1 or not wcrypto.verify_key(bytes.fromhex(key), page1):
        return None
    return key


def save_cached(db_dir: str, key: str, extra: Optional[dict] = None) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    payload = {
        "key": key,
        "db_dir": db_dir,
        "fingerprint": _fingerprint(db_dir),
        "extracted_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "note": "企业微信数据库密钥，本机缓存；企业微信升级或换账号后会自动失效重取",
    }
    if extra:
        payload.update(extra)
    tmp = KEY_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, KEY_FILE)
    try:  # 只有本用户可读，别让同机其他账号顺手拿走
        os.chmod(KEY_FILE, 0o600)
    except OSError:
        pass


def get_key(db_dir: str, force: bool = False, timeout: int = 120) -> Dict[str, Any]:
    """拿一把能解开 db_dir 的钥匙：先看缓存，不行再扫内存。"""
    if not force:
        cached = load_cached(db_dir)
        if cached:
            return {"ok": True, "key": cached, "from": "cache"}
    page1 = wcrypto.read_page1(os.path.join(db_dir, "message.db"))
    if not page1:
        return {"ok": False, "error": f"找不到 {os.path.join(db_dir, 'message.db')}"}
    if page1[:16] == wcrypto.SQLITE_HDR:
        return {"ok": False, "error": "这个库是明文的，不需要密钥"}
    t0 = time.time()
    res = scan(page1, timeout=timeout)
    res["elapsed"] = round(time.time() - t0, 1)
    res["from"] = "scan"
    if res.get("ok"):
        save_cached(db_dir, res["key"], {"pid": res.get("pid")})
    return res
