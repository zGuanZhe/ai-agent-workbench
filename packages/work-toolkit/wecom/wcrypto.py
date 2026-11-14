#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wcrypto.py — 企业微信 wxSQLite3 加密库的按页解密。

企业微信 PC 端（4.x / 5.x）把 Data 下的 .db 用 **wxSQLite3** 加密，
不是个人微信那套 SQLCipher：

    加密算法   AES-128-CBC，按页独立加密
    页大小     4096 字节（库头 [16:18] 明文写着，实测 0x1000）
    页密钥     MD5(raw_key || page_no 小端 4 字节 || b"sAlT")
    页 IV      MD5(LCG 连续迭代 4 次的 16 字节结果)
    HMAC       没有；reserve 区也没有，所以密文长度就等于页大小

因此不能拿 sqlcipher 或 pysqlcipher3 打开，只能自己按页解出一份明文副本。

第 1 页是最容易搞错的地方：它不整页都是密文。
    [0:8]    原库头的前 8 字节（"SQLite f"）
    [8:16]   密文的一部分，解密前要先挪到 [16:24]
    [16:24]  明文，正好是 SQLite 头的后半段（含页大小、保留区等）
    [16:]    密文，4080 字节，AES-CBC 解密后覆盖回 [16:]
解密成功后 [16:24] 应当与挪动前的明文片段逐字节相同 —— 这就是免费的密钥校验：
key 不对时这里必然对不上，不会解出一堆垃圾还当成成功。
"""

from __future__ import annotations

import hashlib
import os
import struct
from typing import Iterable, Optional

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

PAGE = 4096
SALT = b"sAlT"
SQLITE_HDR = b"SQLite format 3\x00"
# 判断「SQLite 页 1 长得对」时看的 b-tree 页类型：内部表/叶子表/内部索引/叶子索引
_BTREE_TYPES = (0x02, 0x05, 0x0A, 0x0D)


# --------------------------------------------------------------------------- #
# 参数派生
# --------------------------------------------------------------------------- #
def _modmult(a: int, b: int, c: int, m: int, s: int) -> int:
    """复刻 sqlite3mc 里的乘加取模。整数溢出行为必须和 C 版本逐位一致。"""
    q = s // a
    s = b * (s - a * q) - c * q
    if s < 0:
        s += m
    return s


def initial_vector(page_no: int) -> bytes:
    """每页 IV：以 page_no+1 为种子做 4 轮 LCG，取 16 字节做 MD5。"""
    z = page_no + 1
    buf = bytearray(16)
    for idx in range(4):
        z = _modmult(52774, 40692, 3791, 2147483399, z)
        buf[idx * 4: idx * 4 + 4] = struct.pack("<I", z & 0xFFFFFFFF)
    return hashlib.md5(bytes(buf)).digest()


def page_key(raw_key: bytes, page_no: int) -> bytes:
    """每页 AES-128 密钥：MD5(raw_key || page_no(小端 4B) || "sAlT")。"""
    if len(raw_key) != 16:
        raise ValueError("企业微信 raw_key 必须是 16 字节")
    return hashlib.md5(raw_key + struct.pack("<I", page_no) + SALT).digest()


def _aes_cbc_decrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    return dec.update(data) + dec.finalize()


# --------------------------------------------------------------------------- #
# 页级判定与解密
# --------------------------------------------------------------------------- #
def is_plain_sqlite(path: str) -> bool:
    """明文库（企业微信里也有一部分，例如 avatar_store_v3.db）不用解。"""
    try:
        with open(path, "rb") as f:
            return f.read(16) == SQLITE_HDR
    except OSError:
        return False


def has_header_fragment(page: bytes) -> bool:
    """页 1 的 [16:24] 是明文头片段：页大小合法且保留区标记为 0x40/0x20/0x20。

    实测企业微信的库头是 ``1000020200402020``：
    0x1000 页大小 + 0x00 写版本 + 0x02 读版本 + ... + 0x40 保留区 + 0x20 0x20。
    """
    if len(page) < 24:
        return False
    frag = page[16:24]
    size = (frag[0] << 8) | frag[1]
    if size == 1:  # SQLite 用 1 表示 65536
        size = 65536
    return (512 <= size <= 65536 and (size & (size - 1)) == 0
            and frag[5] == 0x40 and frag[6] == 0x20 and frag[7] == 0x20)


def decrypt_page(raw_key: bytes, page: bytes, page_no: int) -> bytes:
    """解一页。第 1 页走「挪动 8 字节 + 校验明文片段」的特殊流程。"""
    if len(page) != PAGE:
        raise ValueError(f"页长度必须是 {PAGE}，收到 {len(page)}")
    data = bytearray(page)
    if page_no == 1 and bytes(data[:16]) != SQLITE_HDR and has_header_fragment(data):
        frag = bytes(data[16:24])
        data[16:24] = data[8:16]
        data[16:] = _aes_cbc_decrypt(page_key(raw_key, 1), initial_vector(1), bytes(data[16:]))
        if bytes(data[16:24]) != frag:
            raise ValueError("密钥校验失败：解出的库头片段与原明文不一致")
        data[:16] = SQLITE_HDR
        return bytes(data)
    return _aes_cbc_decrypt(page_key(raw_key, page_no), initial_vector(page_no), bytes(data))


def verify_key(raw_key: bytes, page1: bytes) -> bool:
    """用页 1 验一把钥匙，不落盘。

    只要求解得出 SQLite 头还不够（CBC 解错也可能碰巧对上 16 字节魔数），
    再加一条「第 100 字节是合法 b-tree 页类型」，把误判压到可以忽略。
    """
    if len(raw_key) != 16 or len(page1) < PAGE:
        return False
    try:
        head = decrypt_page(raw_key, page1[:PAGE], 1)
    except (ValueError, KeyError):
        return False
    return head[:16] == SQLITE_HDR and head[100] in _BTREE_TYPES


def read_page1(path: str) -> Optional[bytes]:
    try:
        with open(path, "rb") as f:
            return f.read(PAGE)
    except OSError:
        return None


# --------------------------------------------------------------------------- #
# 整库解密
# --------------------------------------------------------------------------- #
def decrypt_file(src: str, dst: str, raw_key: bytes) -> dict:
    """把一个库按页解成明文副本。返回页数与体积，不返回密钥。"""
    size = os.path.getsize(src)
    pages = (size + PAGE - 1) // PAGE
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    tmp = dst + ".tmp"
    with open(src, "rb") as fin, open(tmp, "wb") as fout:
        for page_no in range(1, pages + 1):
            page = fin.read(PAGE)
            if not page:
                break
            if len(page) < PAGE:
                page += b"\x00" * (PAGE - len(page))
            fout.write(decrypt_page(raw_key, page, page_no))
    os.replace(tmp, dst)
    return {"ok": True, "pages": pages, "size_kb": round(os.path.getsize(dst) / 1024, 1)}


def wal_pending(src: str) -> bool:
    """源库旁边有非空 -wal，说明还有没落主库的最新消息。

    我们解的是主库文件，WAL 里的增量读不到 —— 与其悄悄少几条，不如把这件事
    报给调用方，让它自己决定要不要提醒「最新的消息可能还没跟上」。
    """
    for suffix in ("-wal",):
        p = src + suffix
        try:
            if os.path.isfile(p) and os.path.getsize(p) > 32:
                return True
        except OSError:
            pass
    return False


def decrypt_many(db_dir: str, out_dir: str, raw_key: bytes,
                 only: Optional[Iterable[str]] = None) -> dict:
    """批量解密一个目录下的 .db（跳过明文库、跳过 wal/shm 附属文件）。

    ``only`` 给相对路径白名单时可只解需要的那几个，重复调用就不会每次都
    把 20 个库全解一遍。
    """
    want = {str(x).replace("/", os.sep) for x in only} if only else None
    detail: dict[str, dict] = {}
    for root, dirs, files in os.walk(db_dir):
        dirs[:] = [d for d in dirs if d not in ("-journal", "Temp")]
        for name in files:
            if not name.endswith(".db"):
                continue
            src = os.path.join(root, name)
            rel = os.path.relpath(src, db_dir)
            if want is not None and rel not in want:
                continue
            if os.path.getsize(src) < PAGE:
                continue
            if is_plain_sqlite(src):
                detail[rel] = {"ok": True, "skipped": "已经明文"}
                continue
            dst = os.path.join(out_dir, rel)
            try:
                info = decrypt_file(src, dst, raw_key)
                info["wal_pending"] = wal_pending(src)
                detail[rel] = info
            except Exception as exc:  # 一个库坏了不该带崩整批
                detail[rel] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:200]}
    failed = {k: v for k, v in detail.items() if not v.get("ok")}
    return {"ok": not failed, "count": len(detail), "failed": failed, "detail": detail}
