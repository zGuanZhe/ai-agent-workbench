"""微信 4.x SQLCipher 数据库解密与 WAL 实时增量补丁（只读源库）。

页面格式（微信 4.x，page=4096, reserve=80）：
  p == 1 : [salt 16][ciphertext 4000][IV 16][HMAC 64]
  p >  1 : [ciphertext 4016][IV 16][HMAC 64]
  AES-256-CBC 无 padding
  mac_key = PBKDF2-SHA512(enc_key, salt ^ 0x3a, 2 轮, 32B)
  HMAC-SHA512(ciphertext || IV || pageNo-LittleEndian32) == 页尾 64 字节

WAL 帧格式（SQLCipher 4 + SQLite WAL）：
  WAL Header: 32 字节，[16:24] 为当前序列 (wal_salt1, wal_salt2)
  WAL Frame : 24 字节帧头 + 4096 字节加密页
    fh[0:4]   = pgno (big-endian uint32)
    fh[4:8]   = db_size_after_commit (big-endian uint32, >0 表示事务提交帧)
    fh[8:16]  = (frame_salt1, frame_salt2)
  仅当帧的 salt 与 WAL 头一致、且加密页通过 HMAC-SHA512 校验时，才归入候选帧；
  按最近一次提交帧（db_size > 0）截断并按帧序覆写目标缓存库的 (pgno - 1) * 4096 偏移。
"""
from __future__ import annotations

import hashlib
import hmac as hmac_mod
import os
import struct

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

PAGE_SZ = 4096
KEY_SZ = 32
SALT_SZ = 16
IV_SZ = 16
HMAC_SZ = 64
RESERVE_SZ = 80
IV_OFFSET = PAGE_SZ - RESERVE_SZ          # 4016
SQLITE_HEADER = b"SQLite format 3\x00"
WAL_HEADER_SZ = 32
WAL_FRAME_HEADER_SZ = 24
WAL_FRAME_SZ = WAL_FRAME_HEADER_SZ + PAGE_SZ


def mac_key_of(enc_key: bytes, salt: bytes) -> bytes:
    mac_salt = bytes(b ^ 0x3A for b in salt)
    return hashlib.pbkdf2_hmac("sha512", enc_key, mac_salt, 2, dklen=KEY_SZ)


def verify_page_hmac(enc_key: bytes, salt: bytes, page: bytes, page_no: int, mac_key: bytes | None = None) -> bool:
    """校验任意页（p==1 或 p>1，含 WAL 帧内加密页）的 SQLCipher 4 HMAC-SHA512。"""
    if len(page) < PAGE_SZ or len(enc_key) != KEY_SZ or page_no < 1:
        return False
    mk = mac_key if mac_key is not None else mac_key_of(enc_key, salt)
    data = page[SALT_SZ: IV_OFFSET + IV_SZ] if page_no == 1 else page[: IV_OFFSET + IV_SZ]
    stored = page[PAGE_SZ - HMAC_SZ:]
    h = hmac_mod.new(mk, data, hashlib.sha512)
    h.update(struct.pack("<I", page_no))
    return hmac_mod.compare_digest(h.digest(), stored)


def verify_key(enc_key: bytes, page1: bytes) -> bool:
    if len(page1) < PAGE_SZ or len(enc_key) != KEY_SZ:
        return False
    salt = page1[:SALT_SZ]
    return verify_page_hmac(enc_key, salt, page1, 1)


def _decrypt_page(enc_key: bytes, page: bytes, page_no: int) -> bytes:
    """返回 4096 字节明文页（明文页尾的 reserve 区补零，首部 journal_mode 归一为 1/1）。"""
    if page_no == 1:
        ct = page[SALT_SZ: IV_OFFSET]
        iv = page[IV_OFFSET: IV_OFFSET + IV_SZ]
        body = Cipher(algorithms.AES(enc_key), modes.CBC(iv)).decryptor()
        plain = bytearray(body.update(ct) + body.finalize())
        # 缓存库已合并 WAL，将 SQLite 头 offset 18..20 (plain[2:4]) 设为 1,1 避免只读端寻找 -wal/-shm
        plain[2:4] = b"\x01\x01"
        return SQLITE_HEADER + bytes(plain) + b"\x00" * RESERVE_SZ
    ct = page[:IV_OFFSET]
    iv = page[IV_OFFSET: IV_OFFSET + IV_SZ]
    body = Cipher(algorithms.AES(enc_key), modes.CBC(iv)).decryptor()
    plain = body.update(ct) + body.finalize()
    return plain + b"\x00" * RESERVE_SZ


def has_wal(db_path: str) -> bool:
    wal = db_path + "-wal"
    return os.path.exists(wal) and os.path.getsize(wal) > WAL_HEADER_SZ


def patch_wal(
    wal_path: str,
    dst: str,
    enc_key_hex: str,
    salt_hex: str = "",
    src_db: str = "",
) -> dict:
    """将源库的 -wal 实时增量帧解密并原位覆写到已解密的缓存库 dst。

    只读打开 wal_path（及可选 src_db 以读取 16B salt），按 WAL 头的 (salt1, salt2)
    与每帧 HMAC-SHA512 双重校验筛选当前有效序列，并在最后一个提交帧处应用。
    """
    if not os.path.exists(wal_path):
        return {"ok": True, "patched_pages": 0, "committed_frames": 0, "wal_exists": False}
    wal_size = os.path.getsize(wal_path)
    if wal_size <= WAL_HEADER_SZ or not os.path.exists(dst):
        return {"ok": True, "patched_pages": 0, "committed_frames": 0, "wal_size": wal_size}

    enc_key = bytes.fromhex(enc_key_hex)
    salt = bytes.fromhex(salt_hex) if salt_hex else b""
    if len(salt) != SALT_SZ and src_db and os.path.exists(src_db):
        with open(src_db, "rb") as sf:
            salt = sf.read(SALT_SZ)
    mac_key = mac_key_of(enc_key, salt) if len(salt) == SALT_SZ else None

    pending_frames: list[tuple[int, bytes, int]] = []
    committed_idx = -1

    with open(wal_path, "rb") as wf:
        wal_hdr = wf.read(WAL_HEADER_SZ)
        if len(wal_hdr) < WAL_HEADER_SZ:
            return {"ok": True, "patched_pages": 0, "committed_frames": 0}
        wal_salt1, wal_salt2 = struct.unpack(">II", wal_hdr[16:24])

        while wf.tell() + WAL_FRAME_SZ <= wal_size:
            fh = wf.read(WAL_FRAME_HEADER_SZ)
            if len(fh) < WAL_FRAME_HEADER_SZ:
                break
            ep = wf.read(PAGE_SZ)
            if len(ep) < PAGE_SZ:
                break
            pgno, db_sz, fs1, fs2 = struct.unpack(">IIII", fh[:16])
            if (fs1, fs2) != (wal_salt1, wal_salt2):
                # SQLite WAL 当前序列帧从头连续排列；遇到旧序列残留帧即可停止
                break
            if pgno < 1 or pgno > 1_000_000:
                break
            if mac_key is not None and not verify_page_hmac(enc_key, salt, ep, pgno, mac_key=mac_key):
                break
            pending_frames.append((pgno, ep, db_sz))
            if db_sz > 0:
                committed_idx = len(pending_frames)

    # 若存在显式提交标记（db_sz > 0），仅应用到最后一个已提交事务帧；
    # 若全部匹配帧均为同序列且无未完成尾帧，兼容无 db_sz 标记场景。
    active_frames = pending_frames[:committed_idx] if committed_idx >= 0 else pending_frames
    if not active_frames:
        return {
            "ok": True,
            "patched_pages": 0,
            "committed_frames": 0,
            "wal_size": wal_size,
        }

    # 同一页号多次写入时，仅需解密并写入最后一次出现的帧
    last_frame_by_page: dict[int, bytes] = {}
    for pgno, ep, _db_sz in active_frames:
        last_frame_by_page[pgno] = ep

    with open(dst, "r+b") as df:
        for pgno in sorted(last_frame_by_page):
            dec = _decrypt_page(enc_key, last_frame_by_page[pgno], pgno)
            df.seek((pgno - 1) * PAGE_SZ)
            df.write(dec)
        df.seek(0, os.SEEK_END)
        total_pages = df.tell() // PAGE_SZ
        if total_pages >= 1:
            df.seek(18)
            df.write(b"\x01\x01")
            df.seek(28)
            df.write(struct.pack(">I", total_pages))

    return {
        "ok": True,
        "patched_pages": len(last_frame_by_page),
        "committed_frames": len(active_frames),
        "wal_size": wal_size,
    }


def decrypt_db(src: str, dst: str, enc_key_hex: str, salt_hex: str = "", verify: bool = True, apply_wal: bool = True) -> dict:
    """解密单个库到 dst，并在存在 -wal 时自动打上实时增量补丁。"""
    enc_key = bytes.fromhex(enc_key_hex)
    total = os.path.getsize(src)
    pages = total // PAGE_SZ
    if pages == 0:
        return {"ok": False, "error": "文件小于一页"}

    with open(src, "rb") as fh:
        page1 = fh.read(PAGE_SZ)
    salt = page1[:SALT_SZ]
    verified = verify_key(enc_key, page1)
    if verify and not verified:
        return {"ok": False, "error": "密钥校验失败（HMAC 不匹配）", "verified": False}

    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    tmp = dst + ".part"
    with open(src, "rb") as fin, open(tmp, "wb") as fout:
        for i in range(1, pages + 1):
            page = fin.read(PAGE_SZ)
            if len(page) < PAGE_SZ:
                break
            fout.write(_decrypt_page(enc_key, page, i))

    wal_info = {"patched_pages": 0, "committed_frames": 0}
    wal_path = src + "-wal"
    if apply_wal and os.path.exists(wal_path) and os.path.getsize(wal_path) > WAL_HEADER_SZ:
        wal_info = patch_wal(
            wal_path,
            tmp,
            enc_key_hex,
            salt_hex=salt_hex or salt.hex(),
            src_db=src,
        )

    os.replace(tmp, dst)
    return {
        "ok": True,
        "verified": verified,
        "pages": pages,
        "wal_patched_pages": wal_info.get("patched_pages", 0),
        "wal_committed_frames": wal_info.get("committed_frames", 0),
        "size": os.path.getsize(dst),
        "src": src,
        "dst": dst,
    }


def decrypt_all(db_dir: str, out_dir: str, keys: dict, only: list[str] | None = None) -> dict:
    """按 keys（{rel: {enc_key, salt}}）批量解密。只读源文件。"""
    results = {}
    for rel, info in sorted(keys.items()):
        if only and rel not in only:
            continue
        src = os.path.join(db_dir, rel.replace("/", os.sep))
        if not os.path.exists(src):
            results[rel] = {"ok": False, "error": "源文件不存在"}
            continue
        dst = os.path.join(out_dir, rel.replace("/", os.sep))
        try:
            results[rel] = decrypt_db(src, dst, info["enc_key"], salt_hex=info.get("salt", ""))
        except Exception as exc:  # noqa: BLE001
            results[rel] = {"ok": False, "error": str(exc)}
    ok = [k for k, v in results.items() if v.get("ok")]
    return {
        "ok": bool(ok),
        "decrypted": len(ok),
        "total": len(results),
        "out_dir": out_dir,
        "detail": results,
    }
