"""微信 4.x SQLCipher 密钥提取与 DPAPI 缓存（集成自 SmileGrassSky620/wechat-local-reader，MIT）。

算法来源与验证：
  https://github.com/SmileGrassSky620/wechat-local-reader  docs/key-extraction.md
  - 4.0.x   ：内存中直接是 ASCII `x'<64hex enc_key><32hex salt>'`
  - 4.1.11+ ：字符串被 XOR 掩码，需经 Config.Cipher 对象链路解出（本机 4.2.0.13 实测命中）
  - DPAPI   ：通过页 1 HMAC 校验的密钥使用当前 Windows 用户态 CryptProtectData 加密落盘，
              下次读取直接毫秒级命中并逐库校验页头 HMAC，无需重扫进程内存。

本文件仅用标准库，不做任何源库写操作，不联网。
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import hashlib
import hmac as hmac_mod
import json
import os
import re
import struct
import sys
import time

PAGE_SZ = 4096
KEY_SZ = 32
SALT_SZ = 16
IV_SZ = 16
HMAC_SZ = 64
RESERVE_SZ = 80
IV_OFFSET = PAGE_SZ - RESERVE_SZ  # 4016

DPAPI_ENTROPY = b"work-super-agent-wxkey-v1"
CRYPTPROTECT_UI_FORBIDDEN = 0x1

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)

PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
MEM_COMMIT = 0x1000
TH32CS_SNAPPROCESS = 0x00000002
MAX_PATH_W = 260
READABLE = {0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80}
HEX_RE = re.compile(rb"x'([0-9a-fA-F]{64,192})'")

# ---- 微信 4.1.11+ 路径：Config.Cipher 对象（blob 被 XOR 掩码，解码后为 x'<key><salt>'）----
CONFIG_NEEDLE = b"com.Tencent.WCDB.Config.Cipher"
CONFIG_MASK = bytes.fromhex(
    "d2c7442458020000004889442450488b"
    "450048844c2448488944254048584c24"
)
CONFIG_BLOB_MAX = 1024
CONFIG_LITERAL_RE = re.compile(rb"[xX]'([0-9a-fA-F]{64,192})'")
MAX_ADDR = 0x0000_8000_0000_0000
CHUNK = 2 * 1024 * 1024


class DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wt.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


crypt32.CryptProtectData.argtypes = [
    ctypes.POINTER(DATA_BLOB),
    wt.LPCWSTR,
    ctypes.POINTER(DATA_BLOB),
    ctypes.c_void_p,
    ctypes.c_void_p,
    wt.DWORD,
    ctypes.POINTER(DATA_BLOB),
]
crypt32.CryptProtectData.restype = wt.BOOL

crypt32.CryptUnprotectData.argtypes = [
    ctypes.POINTER(DATA_BLOB),
    ctypes.POINTER(wt.LPWSTR),
    ctypes.POINTER(DATA_BLOB),
    ctypes.c_void_p,
    ctypes.c_void_p,
    wt.DWORD,
    ctypes.POINTER(DATA_BLOB),
]
crypt32.CryptUnprotectData.restype = wt.BOOL

kernel32.LocalFree.argtypes = [ctypes.c_void_p]
kernel32.LocalFree.restype = ctypes.c_void_p


def _make_blob(data: bytes) -> tuple[DATA_BLOB, ctypes.Array]:
    buf = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    return DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte))), buf


def dpapi_encrypt(raw: bytes, entropy: bytes = DPAPI_ENTROPY) -> bytes:
    """用当前 Windows 登录用户 DPAPI 加密字节串。"""
    in_blob, _in_buf = _make_blob(raw)
    ent_blob, _ent_buf = _make_blob(entropy)
    out_blob = DATA_BLOB()
    ok = crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        "work-wxkey",
        ctypes.byref(ent_blob),
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out_blob),
    )
    if not ok:
        raise OSError(f"CryptProtectData failed: {ctypes.get_last_error()}")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        if out_blob.pbData:
            kernel32.LocalFree(ctypes.cast(out_blob.pbData, ctypes.c_void_p))


def dpapi_decrypt(cipher: bytes, entropy: bytes = DPAPI_ENTROPY) -> bytes | None:
    """解密当前 Windows 用户的 DPAPI 密文；失败返回 None。"""
    if not cipher:
        return None
    in_blob, _in_buf = _make_blob(cipher)
    ent_blob, _ent_buf = _make_blob(entropy)
    out_blob = DATA_BLOB()
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        ctypes.byref(ent_blob),
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out_blob),
    )
    if not ok:
        return None
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        if out_blob.pbData:
            kernel32.LocalFree(ctypes.cast(out_blob.pbData, ctypes.c_void_p))


class MBI(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_uint64),
        ("AllocationBase", ctypes.c_uint64),
        ("AllocationProtect", wt.DWORD),
        ("_pad1", wt.DWORD),
        ("RegionSize", ctypes.c_uint64),
        ("State", wt.DWORD),
        ("Protect", wt.DWORD),
        ("Type", wt.DWORD),
        ("_pad2", wt.DWORD),
    ]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("cntUsage", wt.DWORD),
        ("th32ProcessID", wt.DWORD),
        ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", wt.DWORD),
        ("cntThreads", wt.DWORD),
        ("th32ParentProcessID", wt.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wt.DWORD),
        ("szExeFile", ctypes.c_wchar * MAX_PATH_W),
    ]


def find_pids(image_name: str = "Weixin.exe"):
    """返回 [(pid, threads)]，按线程数（约等于活跃度）降序。"""
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == -1 or snap is None:
        return []
    out = []
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
    while ok:
        if entry.szExeFile.lower() == image_name.lower():
            out.append((int(entry.th32ProcessID), int(entry.cntThreads)))
        ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
    kernel32.CloseHandle(snap)
    out.sort(key=lambda x: x[1], reverse=True)
    return out


def enumerate_regions(handle):
    regions = []
    addr = 0
    mbi = MBI()
    mbi_size = ctypes.sizeof(mbi)
    while addr < 0x7FFFFFFFFFFF:
        got = kernel32.VirtualQueryEx(
            handle, ctypes.c_uint64(addr), ctypes.byref(mbi), mbi_size
        )
        if not got:
            break
        if (
            mbi.State == MEM_COMMIT
            and mbi.Protect in READABLE
            and 0 < mbi.RegionSize <= 512 * 1024 * 1024
        ):
            regions.append((int(mbi.BaseAddress), int(mbi.RegionSize)))
        nxt = int(mbi.BaseAddress) + int(mbi.RegionSize)
        if nxt <= addr:
            break
        addr = nxt
    return regions


def read_mem(handle, addr, size):
    buf = ctypes.create_string_buffer(size)
    read = ctypes.c_size_t(0)
    if kernel32.ReadProcessMemory(
        handle, ctypes.c_uint64(addr), buf, size, ctypes.byref(read)
    ):
        return buf.raw[: read.value]
    return None


def mac_key_of(enc_key: bytes, salt: bytes) -> bytes:
    mac_salt = bytes(b ^ 0x3A for b in salt)
    return hashlib.pbkdf2_hmac("sha512", enc_key, mac_salt, 2, dklen=KEY_SZ)


def verify_key(enc_key: bytes, page1: bytes) -> bool:
    """用第 1 页的 HMAC 验证候选密钥。"""
    if len(page1) < PAGE_SZ or len(enc_key) != KEY_SZ:
        return False
    salt = page1[:SALT_SZ]
    hmac_data = page1[SALT_SZ : IV_OFFSET + IV_SZ]
    stored = page1[PAGE_SZ - HMAC_SZ :]
    h = hmac_mod.new(mac_key_of(enc_key, salt), hmac_data, hashlib.sha512)
    h.update(struct.pack("<I", 1))
    return hmac_mod.compare_digest(h.digest(), stored)


def collect_dbs(db_dir: str):
    dbs = []
    for root, _dirs, files in os.walk(db_dir):
        for name in files:
            if not name.endswith(".db"):
                continue
            path = os.path.join(root, name)
            try:
                if os.path.getsize(path) < PAGE_SZ:
                    continue
                with open(path, "rb") as fh:
                    page1 = fh.read(PAGE_SZ)
            except OSError:
                continue
            rel = os.path.relpath(path, db_dir).replace(os.sep, "/")
            dbs.append({"rel": rel, "path": path, "page1": page1, "salt": page1[:SALT_SZ].hex()})
    dbs.sort(key=lambda d: d["rel"])
    return dbs


def load_dpapi_cache(cache_file: str, dbs: list[dict]) -> dict[str, dict]:
    """从 DPAPI 缓存加载密钥，并逐个对当前数据库的 page1 做 HMAC 校验。"""
    if not cache_file or not os.path.isfile(cache_file):
        return {}
    try:
        with open(cache_file, "rb") as fh:
            raw = dpapi_decrypt(fh.read())
        if not raw:
            return {}
        payload = json.loads(raw.decode("utf-8"))
    except Exception:  # noqa: BLE001
        return {}

    by_rel = payload.get("keys") or {}
    by_salt = payload.get("salts") or {}
    verified: dict[str, dict] = {}
    for d in dbs:
        rel, salt = d["rel"], d["salt"]
        cand = ""
        info = by_rel.get(rel)
        if isinstance(info, dict) and info.get("salt") == salt:
            cand = str(info.get("enc_key") or "")
        if not cand and salt in by_salt:
            cand = str(by_salt.get(salt) or "")
        if len(cand) == 64:
            try:
                if verify_key(bytes.fromhex(cand), d["page1"]):
                    verified[rel] = {"enc_key": cand, "salt": salt}
            except ValueError:
                continue
    return verified


def save_dpapi_cache(cache_file: str, keys: dict[str, dict], account: str = "") -> bool:
    """将已验证的密钥用 DPAPI 加密写入 cache_file。"""
    if not cache_file or not keys:
        return False
    try:
        os.makedirs(os.path.dirname(cache_file) or ".", exist_ok=True)
        salts = {
            v["salt"]: v["enc_key"]
            for v in keys.values()
            if isinstance(v, dict) and v.get("salt") and v.get("enc_key")
        }
        doc = {
            "version": 1,
            "account": account,
            "updated_at": int(time.time()),
            "keys": keys,
            "salts": salts,
        }
        blob = dpapi_encrypt(json.dumps(doc, ensure_ascii=False).encode("utf-8"))
        tmp = cache_file + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(blob)
        os.replace(tmp, cache_file)
        return True
    except Exception:  # noqa: BLE001
        return False


def _u64(buf, off):
    if off < 0 or off + 8 > len(buf):
        return 0
    return struct.unpack_from("<Q", buf, off)[0]


def _xor_repeat(data, mask):
    return bytes(v ^ mask[i % len(mask)] for i, v in enumerate(data))


def _iter_chunks(regions, read, chunk=CHUNK, overlap=0):
    """按块读内存，块间可重叠，避免跨块的结构被截断。"""
    for base, size in regions:
        off = 0
        tail = b""
        tail_base = base
        while off < size:
            take = min(chunk, size - off)
            data = read(base + off, take) or b""
            data_base = tail_base if tail else base + off
            merged = tail + data
            if merged:
                yield data_base, merged
                if overlap:
                    tail = merged[-overlap:]
                    tail_base = data_base + max(0, len(merged) - len(tail))
                else:
                    tail = b""
                    tail_base = base + off + take
            else:
                tail = b""
                tail_base = base + off + take
            off += take


def config_cipher_scan(pid, dbs):
    """微信 4.1.11+：从 Config.Cipher 对象解出 x'<enc_key><salt>'（XOR 掩码后的 blob）。

    返回 ({salt_hex: enc_key_hex}, stats)。这是 4.1+ 的主路径。
    """
    handle = kernel32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not handle:
        return {}, {"error": "open-process-failed"}
    try:
        regions = enumerate_regions(handle)

        def read(addr, size):
            return read_mem(handle, addr, size)

        needle_addrs = set()
        for base, data in _iter_chunks(regions, read, overlap=len(CONFIG_NEEDLE) - 1):
            pos = data.find(CONFIG_NEEDLE)
            while pos >= 0:
                needle_addrs.add(base + pos)
                pos = data.find(CONFIG_NEEDLE, pos + 1)

        stats = {"needle": len(needle_addrs), "nodes": 0, "blobs": 0, "candidates": 0}
        if not needle_addrs:
            return {}, stats

        pairs = [struct.pack("<Q", a) + struct.pack("<Q", len(CONFIG_NEEDLE)) for a in needle_addrs]
        found = {}
        for base, data in _iter_chunks(regions, read, overlap=0x80):
            for pat in pairs:
                pos = data.find(pat)
                while pos >= 0:
                    qaddr = base + pos
                    node = read(qaddr - 0x10, 0x50)
                    if node and len(node) >= 0x40 and _u64(node, 0x10) in needle_addrs                             and _u64(node, 0x18) == len(CONFIG_NEEDLE):
                        stats["nodes"] += 1
                        cfg = _u64(node, 0x28)
                        if 0x10000 <= cfg < MAX_ADDR:
                            obj = read(cfg + 0x88, 0x28)
                            if obj and len(obj) >= 0x18:
                                dptr, dlen = _u64(obj, 0x8), _u64(obj, 0x10)
                                if 0 < dlen <= CONFIG_BLOB_MAX and 0x10000 <= dptr < MAX_ADDR:
                                    blob = read(dptr, int(dlen))
                                    if blob and len(blob) == dlen:
                                        stats["blobs"] += 1
                                        decoded = _xor_repeat(blob, CONFIG_MASK)
                                        for m in CONFIG_LITERAL_RE.finditer(decoded):
                                            run = m.group(1).decode("ascii").lower()
                                            stats["candidates"] += 1
                                            if len(run) >= 96:
                                                salt_hex = run[64:96]
                                                enc_hex = run[:64]
                                                found[salt_hex] = enc_hex
                    pos = data.find(pat, pos + 1)
        # 对候选密钥做第 1 页 HMAC 校验，防止伪命中污染缓存
        salt_to_page1 = {d["salt"]: d["page1"] for d in dbs}
        verified_found = {}
        for salt_hex, enc_hex in found.items():
            p1 = salt_to_page1.get(salt_hex)
            if p1 is None or verify_key(bytes.fromhex(enc_hex), p1):
                verified_found[salt_hex] = enc_hex
        stats["matched"] = len(verified_found)
        return verified_found, stats
    finally:
        kernel32.CloseHandle(handle)


def scan(pid, dbs, chunk=8 * 1024 * 1024):
    handle = kernel32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not handle:
        return None, "open-process-failed", {}
    try:
        salt_to_dbs = {}
        for d in dbs:
            salt_to_dbs.setdefault(d["salt"], []).append(d)

        regions = enumerate_regions(handle)
        found = {}
        stats = {
            "regions": len(regions),
            "readable_mb": round(sum(s for _b, s in regions) / 1024 / 1024),
            "candidates": 0,
            "seconds": 0.0,
        }
        t0 = time.time()
        pending = set(salt_to_dbs)

        for base, size in regions:
            if not pending:
                break
            overlap = 320
            off = 0
            while off < size:
                take = min(chunk, size - off)
                data = read_mem(handle, base + off, take)
                if data:
                    for m in HEX_RE.finditer(data):
                        stats["candidates"] += 1
                        hex_str = m.group(1).decode("ascii")
                        n = len(hex_str)
                        if n == 96:
                            enc_hex, salt_hex = hex_str[:64], hex_str[64:]
                            if salt_hex not in pending:
                                continue
                            enc_key = bytes.fromhex(enc_hex)
                            if verify_key(enc_key, salt_to_dbs[salt_hex][0]["page1"]):
                                found[salt_hex] = enc_hex
                                pending.discard(salt_hex)
                        elif n == 64:
                            enc_key = bytes.fromhex(hex_str)
                            for salt_hex in list(pending):
                                if verify_key(enc_key, salt_to_dbs[salt_hex][0]["page1"]):
                                    found[salt_hex] = hex_str
                                    pending.discard(salt_hex)
                                    break
                        else:
                            enc_hex, salt_hex = hex_str[:64], hex_str[-32:]
                            if salt_hex not in pending:
                                continue
                            if verify_key(bytes.fromhex(enc_hex), salt_to_dbs[salt_hex][0]["page1"]):
                                found[salt_hex] = enc_hex
                                pending.discard(salt_hex)
                if take < chunk:
                    break
                off += take - overlap

        stats["seconds"] = round(time.time() - t0, 2)
        return found, None, stats
    finally:
        kernel32.CloseHandle(handle)


def extract_for_dir(
    db_dir: str,
    cache_file: str = "",
    force_scan: bool = False,
    image: str = "Weixin.exe",
    required_rels: list[str] | None = None,
) -> dict:
    """提取或从 DPAPI 缓存加载指定 db_storage 目录的密钥。"""
    db_dir = os.path.abspath(db_dir)
    if not os.path.isdir(db_dir):
        return {"ok": False, "code": "no-db-dir", "message": f"目录不存在: {db_dir}"}

    dbs = collect_dbs(db_dir)
    if not dbs:
        return {"ok": False, "code": "no-db", "message": "没有找到 .db 文件"}

    account = os.path.basename(os.path.dirname(db_dir))
    existing_rels = {d["rel"] for d in dbs}
    need_set = (set(required_rels) & existing_rels) if required_rels else existing_rels

    cached_keys: dict[str, dict] = {}
    if cache_file and not force_scan:
        cached_keys = load_dpapi_cache(cache_file, dbs)
        if cached_keys and need_set.issubset(cached_keys.keys()):
            missing = [d["rel"] for d in dbs if d["rel"] not in cached_keys]
            return {
                "ok": True,
                "mode": "dpapi-cache",
                "cached": True,
                "pid": None,
                "account": account,
                "keys": cached_keys,
                "missing": missing,
                "stats": {"cached_keys": len(cached_keys)},
            }

    pids = find_pids(image)
    if not pids:
        if cached_keys:
            missing = [d["rel"] for d in dbs if d["rel"] not in cached_keys]
            return {
                "ok": True,
                "mode": "dpapi-cache-offline",
                "cached": True,
                "pid": None,
                "account": account,
                "keys": cached_keys,
                "missing": missing,
                "stats": {"cached_keys": len(cached_keys), "weixin_running": False},
            }
        return {
            "ok": False,
            "code": "weixin-not-running",
            "message": "Weixin.exe 未运行且无可用 DPAPI 密钥缓存。请先打开并登录微信。",
        }

    # 第一优先：微信 4.1.11+ 的 Config.Cipher 对象（XOR 掩码）
    for pid, _threads in pids:
        try:
            found, cstats = config_cipher_scan(pid, dbs)
        except Exception:  # noqa: BLE001
            continue
        if found:
            keys = dict(cached_keys)
            for d in dbs:
                if d["salt"] in found:
                    keys[d["rel"]] = {"enc_key": found[d["salt"]], "salt": d["salt"]}
            if keys:
                if cache_file:
                    save_dpapi_cache(cache_file, keys, account=account)
                missing = [d["rel"] for d in dbs if d["rel"] not in keys]
                return {
                    "ok": True,
                    "mode": "config-cipher",
                    "cached": False,
                    "pid": pid,
                    "account": account,
                    "keys": keys,
                    "missing": missing,
                    "stats": cstats,
                }

    # 回退：微信 4.0.x 明文扫描
    last_err = None
    for pid, _threads in pids:
        found, err, stats = scan(pid, dbs)
        if err:
            last_err = err
            continue
        if found:
            keys = dict(cached_keys)
            for d in dbs:
                if d["salt"] in found:
                    keys[d["rel"]] = {"enc_key": found[d["salt"]], "salt": d["salt"]}
            if cache_file:
                save_dpapi_cache(cache_file, keys, account=account)
            missing = [d["rel"] for d in dbs if d["rel"] not in keys]
            return {
                "ok": True,
                "mode": "legacy-scan",
                "cached": False,
                "pid": pid,
                "account": account,
                "keys": keys,
                "missing": missing,
                "stats": stats,
            }

    if cached_keys:
        missing = [d["rel"] for d in dbs if d["rel"] not in cached_keys]
        return {
            "ok": True,
            "mode": "dpapi-cache-fallback",
            "cached": True,
            "pid": None,
            "account": account,
            "keys": cached_keys,
            "missing": missing,
            "stats": {"cached_keys": len(cached_keys)},
        }

    return {
        "ok": False,
        "code": last_err or "no-key",
        "message": "在微信进程中没找到匹配的数据库密钥。常见原因：微信刚启动还没解锁数据库、"
                   "或当前登录的账号与所选数据目录不一致。可以试着在微信里点开几个聊天再重试。",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db-dir", required=True)
    ap.add_argument("--image", default="Weixin.exe")
    ap.add_argument("--cache-file", default="")
    ap.add_argument("--force-scan", action="store_true")
    args = ap.parse_args()

    res = extract_for_dir(
        args.db_dir,
        cache_file=args.cache_file,
        force_scan=args.force_scan,
        image=args.image,
    )
    print(json.dumps(res, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - 兜底，保证输出可解析
        print(json.dumps({"ok": False, "code": "crash", "message": f"{type(exc).__name__}: {exc}"},
                         ensure_ascii=False))
        sys.exit(0)
