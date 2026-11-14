#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""微信 4.x 图片 / 附件的只读解密。

微信把聊天里收到的图片落在 ``msg/attach/<会话hash>/<YYYY-MM>/Img/`` 下，
存成 ``.dat``，头部 15 字节是元信息，后面是「AES 段 + XOR 段」：

    offset  size  content
    0       6     07 08 56 32 08 07      ("\\x07\\x08V2\\x08\\x07")
    6       4     aes_size  (uint32 LE)
    10      4     xor_size  (uint32 LE)
    14      1     01
    15      aes_size+16   AES-128-ECB 密文（多出的 16 字节是 PKCS#7 填充块）
    ...     xor_size     XOR 段，逐字节异或 (uin & 0xFF)

密钥：
    aes_key = md5(f"{uin}{wxid}")[:16] 的 ASCII 字节
    xor_key = uin & 0xFF
    uin 来自 %APPDATA%\\Tencent\\xwechat\\{net,net_1,ilink,roam\\ilink}\\kvcomm\\key_<UIN>_*.statistic

解出来的载荷可能是：
    - JPEG / PNG / GIF / BMP / WEBP  直接可存
    - 以 ``wxgf`` 开头                HEVC 码流，需 ffmpeg 取首帧转 PNG

实测（本机 4.2.0.13）：全库 12099 个 .dat，抽样解密 6/6 通过；缩略图 ``_t.dat``
直接是 JPEG，原图多为 WXGF(HEVC)。

所有操作只读源目录，不写微信任何文件。解密产物写到调用方指定的目录。
"""

from __future__ import annotations

import ctypes
import hashlib
import io
import json
import os
import re
import shutil
import struct
import subprocess
import tempfile
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:  # Pillow 用于校验与取尺寸；缺失时降级为魔数识别
    from PIL import Image  # type: ignore
except Exception:  # pragma: no cover
    Image = None  # type: ignore

MAGIC = b"\x07\x08V2\x08\x07"
HEADER_LEN = 15

# 常见安装位置，最后才走 PATH
_FFMPEG_HINTS = [
    r"D:\应用软件2\ffmpeg\ffmpeg\bin\ffmpeg.exe",
    r"C:\ffmpeg\bin\ffmpeg.exe",
    r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe"),
]

_IMG_EXT = {
    "JPEG": "jpg",
    "PNG": "png",
    "GIF": "gif",
    "WEBP": "webp",
    "BMP": "bmp",
    "TIFF": "tiff",
}


# --------------------------------------------------------------------------- #
# 账号 / UIN / 密钥
# --------------------------------------------------------------------------- #
def account_dir(explicit: str = "") -> Optional[str]:
    """复用 wxread 的账号目录定位，避免两套逻辑漂移。"""
    try:
        import wxread  # type: ignore

        return wxread.account_dir(explicit)
    except Exception:
        pass
    for root in (r"D:\聊天记录\xwechat_files",):
        if os.path.isdir(root):
            for name in os.listdir(root):
                cand = os.path.join(root, name)
                if os.path.isdir(os.path.join(cand, "msg")):
                    return cand
    return None


def wxid_of(acc: str) -> str:
    """账号目录名去掉尾部 4 位指纹即 wxid（wxid 本身含下划线，不能按下划线切）。"""
    name = os.path.basename(os.path.normpath(acc))
    return re.sub(r"_[0-9a-f]{4}$", "", name, flags=re.I)


_KVCOMM_SUB = (
    ("Tencent", "xwechat", "net", "kvcomm"),
    ("Tencent", "xwechat", "net_1", "kvcomm"),
    ("Tencent", "xwechat", "ilink", "kvcomm"),
    ("Tencent", "xwechat", "roam", "ilink", "kvcomm"),
)


def _appdata_roots() -> List[str]:
    """可能藏 xwechat 配置的 Roaming 目录。

    服务被计划任务/服务模式拉起时 APPDATA 可能为空，所以不能只认这一个变量，
    要按 USERPROFILE/LOCALAPPDATA 兜底，再不行就从 home 目录拼。
    """
    cands: List[str] = []
    for key in ("APPDATA", "LOCALAPPDATA"):
        v = os.environ.get(key)
        if v:
            cands.append(v)
    home = os.path.expanduser("~")
    if home and home != "~":
        cands.append(os.path.join(home, "AppData", "Roaming"))
        cands.append(os.path.join(home, "AppData", "Local"))
    seen, out = set(), []
    for c in cands:
        c = os.path.normpath(c)
        if c not in seen and os.path.isdir(c):
            seen.add(c)
            out.append(c)
    return out


def discover_uin() -> Optional[int]:
    """从 kvcomm 的 key_*.statistic 文件名里取 UIN。

    找不到返回 None（不是 0）——0 是合法整数但不可能是真 UIN，
    调用方若用 `if uin:` 判断就会把 None 和 0 混为一谈，派生出一把错密钥。

    微信 4.x 换过两代命名：
      旧: key_<UIN>_1_<ts>_<pid>_3600_input.statistic
      新: key_0_<UIN>_1_<ts>_<pid>_3600_input.statistic   ← 前面多了一段 0
    只取第一段会把新版全部漏掉（实测回归里图片能力自检从 ok 变 false）。
    """
    for appdata in _appdata_roots():
        for sub in _KVCOMM_SUB:
            base = os.path.join(appdata, *sub)
            if not os.path.isdir(base):
                continue
            try:
                names = sorted(os.listdir(base))
            except OSError:
                continue
            for fn in names:
                if not fn.startswith("key_"):
                    continue
                nums = re.findall(r"\d+", fn.split(".statistic")[0])
                if len(nums) < 2:
                    continue
                first, second = int(nums[0]), int(nums[1])
                # 第一段为 0 说明是新版格式，真正的 UIN 在第二段；
                # 否则第一段就是 UIN（新版格式第二段是固定小整数，不会误取）。
                cand = first if first > 0 else second
                if cand > 0:
                    return cand
    return None


def derive_key(uin: int, wxid: str) -> Tuple[bytes, int]:
    return hashlib.md5(f"{uin}{wxid}".encode()).hexdigest()[:16].encode(), (uin & 0xFF)


# --------------------------------------------------------------------------- #
# AES-128-ECB（Windows CNG，无需第三方库）
# --------------------------------------------------------------------------- #
_bcrypt = None


def _bcrypt_lib():
    global _bcrypt
    if _bcrypt is None:
        if os.name != "nt":
            raise RuntimeError("仅支持 Windows（CNG/bcrypt.dll）")
        _bcrypt = ctypes.WinDLL("bcrypt", use_last_error=True)
        for fn in ("BCryptOpenAlgorithmProvider", "BCryptSetProperty",
                   "BCryptGenerateSymmetricKey", "BCryptDecrypt",
                   "BCryptDestroyKey", "BCryptCloseAlgorithmProvider"):
            getattr(_bcrypt, fn).restype = ctypes.c_long
    return _bcrypt


def aes_ecb_decrypt(key: bytes, data: bytes) -> bytes:
    """AES-128-ECB 解密，不做去填充（调用方自己按 PKCS#7 处理）。"""
    if len(data) % 16:
        raise ValueError(f"AES 段长度 {len(data)} 不是 16 的整数倍")
    bc = _bcrypt_lib()
    alg = ctypes.c_void_p()
    hkey = ctypes.c_void_p()
    st = bc.BCryptOpenAlgorithmProvider(ctypes.byref(alg), "AES", None, 0)
    if st:
        raise OSError(f"BCryptOpenAlgorithmProvider 失败: 0x{st & 0xFFFFFFFF:08x}")
    try:
        mode = ctypes.create_unicode_buffer("ChainingModeECB")
        st = bc.BCryptSetProperty(alg, "ChainingMode", mode, len(mode) * 2, 0)
        if st:
            raise OSError(f"设置 ECB 模式失败: 0x{st & 0xFFFFFFFF:08x}")
        kb = ctypes.create_string_buffer(key, len(key))
        st = bc.BCryptGenerateSymmetricKey(alg, ctypes.byref(hkey), None, 0, kb, len(key), 0)
        if st:
            raise OSError(f"导入密钥失败: 0x{st & 0xFFFFFFFF:08x}")
        n = len(data)
        out = ctypes.create_string_buffer(n + 16)
        outlen = ctypes.c_ulong(0)
        src = ctypes.create_string_buffer(data, n)
        st = bc.BCryptDecrypt(hkey, src, n, None, None, 0, out, n + 16,
                              ctypes.byref(outlen), 0)
        if st:
            raise OSError(f"BCryptDecrypt 失败: 0x{st & 0xFFFFFFFF:08x}")
        return out.raw[: outlen.value]
    finally:
        if hkey:
            bc.BCryptDestroyKey(hkey)
        bc.BCryptCloseAlgorithmProvider(alg, 0)


def _unpad(buf: bytes) -> bytes:
    if not buf:
        return buf
    n = buf[-1]
    if 1 <= n <= 16 and buf[-n:] == bytes([n]) * n:
        return buf[:-n]
    return buf


def decrypt_dat(raw: bytes, aes_key: bytes, xor_key: int) -> bytes:
    """V2 .dat → 图片载荷。头部不合法或截断会抛 ValueError。"""
    if len(raw) < HEADER_LEN + 16:
        raise ValueError("文件过短")
    sig, aes_size, xor_size = struct.unpack("<6sLLx", raw[:HEADER_LEN])
    if sig != MAGIC:
        raise ValueError(f"不是 V2 .dat（头 {sig!r}）")
    aes_len = aes_size + 16
    if HEADER_LEN + aes_len > len(raw):
        raise ValueError(f"头部声明加密段 {aes_len}B，超过文件实际长度 {len(raw)}B")
    body = _unpad(aes_ecb_decrypt(aes_key, raw[HEADER_LEN: HEADER_LEN + aes_len]))
    if xor_size <= 0:
        return body
    tail = raw[-xor_size:]
    table = bytes(b ^ xor_key for b in range(256))
    return body + tail.translate(table)


# --------------------------------------------------------------------------- #
# 图片载荷 → 可落盘格式
# --------------------------------------------------------------------------- #
def sniff(payload: bytes) -> str:
    if payload[:3] == b"\xff\xd8\xff":
        return "JPEG"
    if payload[:4] == b"\x89PNG":
        return "PNG"
    if payload[:4] == b"GIF8":
        return "GIF"
    if payload[:2] == b"BM":
        return "BMP"
    if payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return "WEBP"
    if payload[:4] == b"wxgf":
        return "WXGF"
    if payload[:4] in (b"\x00\x00\x00\x01", b"\x00\x00\x01"):
        return "HEVC"
    return ""


def find_ffmpeg() -> Optional[str]:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    for p in _FFMPEG_HINTS:
        if p and os.path.isfile(p):
            return p
    return None


def _hevc_start(payload: bytes) -> int:
    """找第一个 Annex-B 起始码；没有就返回 0。"""
    i = 0
    n = len(payload)
    while i < n - 3:
        if payload[i: i + 4] == b"\x00\x00\x00\x01":
            return i
        if payload[i: i + 3] == b"\x00\x00\x01":
            return i
        i += 1
    return 0


def wxgf_to_png(payload: bytes, ffmpeg: str) -> bytes:
    """WXGF 里的 HEVC 码流取首帧转 PNG。"""
    off = _hevc_start(payload)
    data = payload[off:]
    tmpdir = tempfile.mkdtemp(prefix="wxgf_")
    inp = os.path.join(tmpdir, "a.hevc")
    out = os.path.join(tmpdir, "a.png")
    try:
        with open(inp, "wb") as f:
            f.write(data)
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
             "-f", "hevc", "-i", inp, "-frames:v", "1", out],
            capture_output=True, timeout=120,
        )
        if proc.returncode != 0 or not os.path.exists(out):
            raise RuntimeError(
                "ffmpeg 转换失败: " + proc.stderr.decode("utf-8", "replace")[:200]
            )
        with open(out, "rb") as f:
            return f.read()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def to_image(payload: bytes) -> Dict[str, Any]:
    """把解出的载荷规整成可保存的图片，返回 {data, ext, width, height, kind}。"""
    kind = sniff(payload)
    if kind in ("WXGF", "HEVC"):
        ff = find_ffmpeg()
        if not ff:
            raise RuntimeError("原图是 WXGF/HEVC 编码，需要 ffmpeg 才能转换（本机未找到）")
        payload = wxgf_to_png(payload, ff)
        kind = sniff(payload) or "PNG"
    if kind == "JPEG":
        end = payload.rfind(b"\xff\xd9")
        if end > 0:  # 源文件尾部可能有 padding
            payload = payload[: end + 2]
    ext = _IMG_EXT.get(kind, "png")
    width = height = 0
    if not kind:
        # 密钥不对时解出来的是随机字节，报「图片损坏」会让人往文件损坏方向查，
        # 把前 8 字节打出来，一眼能区分「密钥错」和「格式不认识」。
        head = payload[:8].hex(" ")
        raise ValueError(f"解密结果不是图片格式（头 {head}），多为 UIN/wxid 与密钥不匹配")
    if Image is not None:
        im = Image.open(io.BytesIO(payload))
        try:
            im.load()
            width, height = im.size
        finally:
            im.close()
    return {"data": payload, "ext": ext, "width": width, "height": height, "kind": kind}


# --------------------------------------------------------------------------- #
# .dat 索引与查找
# --------------------------------------------------------------------------- #
def _cache_dir(acc: str) -> str:
    try:
        import wxread  # type: ignore

        base = wxread.cache_dir()
    except Exception:
        base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "workspace", "07_微信只读缓存")
    d = os.path.join(base, "media")
    os.makedirs(d, exist_ok=True)
    return d


def _index_file(acc: str) -> str:
    return os.path.join(_cache_dir(acc), "dat_index.json")


def dat_index(acc: str, refresh: bool = False, ttl: int = 600) -> Dict[str, str]:
    """{文件名主干(小写) → .dat 绝对路径}。

    遍历一次 12k 个文件约 1 秒，缓存 10 分钟。
    """
    path = _index_file(acc)
    if not refresh and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if time.time() - cached.get("_at", 0) < ttl:
                return cached.get("map", {})
        except Exception:
            pass
    root = os.path.join(acc, "msg", "attach")
    mapping: Dict[str, str] = {}
    if os.path.isdir(root):
        for dirpath, _dirs, files in os.walk(root):
            for fn in files:
                low = fn.lower()
                if not low.endswith(".dat"):
                    continue
                stem = low[:-4]
                # 原图优先于缩略图/高清图，避免同名覆盖
                if stem.endswith("_t") or stem.endswith("_h"):
                    mapping.setdefault(stem, os.path.join(dirpath, fn))
                else:
                    mapping[stem] = os.path.join(dirpath, fn)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"_at": time.time(), "count": len(mapping), "map": mapping},
                      f, ensure_ascii=False)
    except Exception:
        pass
    return mapping


_HEX32 = re.compile(rb"[0-9a-fA-F]{32}")


def image_ref(packed: Any) -> str:
    """从 packed_info_data（protobuf）里取图片文件名主干。"""
    if not packed:
        return ""
    if isinstance(packed, str):
        packed = packed.encode("utf-8", "ignore")
    hits = _HEX32.findall(bytes(packed))
    return hits[-1].decode().lower() if hits else ""


def locate_dat(acc: str, ref: str, prefer: str = "orig") -> Optional[str]:
    """按名字主干找 .dat；prefer=thumb 时优先缩略图（快且不用 ffmpeg）。"""
    if not ref:
        return None
    idx = dat_index(acc)
    if prefer == "thumb":
        for suffix in ("_t", "_h", ""):
            p = idx.get(ref + suffix)
            if p:
                return p
        return None
    p = idx.get(ref)
    if p:
        return p
    for suffix in ("_h", "_t"):
        p = idx.get(ref + suffix)
        if p:
            return p
    return None


# --------------------------------------------------------------------------- #
# 对外：能力探测 + 单个导出
# --------------------------------------------------------------------------- #
def capability(acc: Optional[str] = None, refresh_index: bool = False) -> Dict[str, Any]:
    """媒体通道是否就绪，供 probe / status 展示。"""
    acc = acc or account_dir()
    out: Dict[str, Any] = {
        "ok": False,
        "account": acc,
        "wxid": wxid_of(acc) if acc else None,
        "uin": None,
        "aes_key_ready": False,
        "ffmpeg": find_ffmpeg(),
        "dat_total": 0,
        "notes": [],
    }
    if not acc:
        out["notes"].append("找不到微信账号目录")
        return out
    uin = discover_uin()
    out["uin"] = uin
    if uin is None:
        out["notes"].append("未找到 kvcomm/key_<UIN>_*.statistic，图片密钥无法派生")
    else:
        wxid = wxid_of(acc)
        aes_key, xor_key = derive_key(uin, wxid)
        sample = None
        try:
            idx = dat_index(acc, refresh=refresh_index)
            out["dat_total"] = len({k[:-2] if k.endswith(("_t", "_h")) else k for k in idx})
            # 取一个有原图的样本验证密钥
            for stem, p in idx.items():
                if not stem.endswith(("_t", "_h")):
                    sample = p
                    break
        except Exception as exc:
            out["notes"].append(f"建立 .dat 索引失败：{exc}")
        if sample:
            try:
                with open(sample, "rb") as f:
                    raw = f.read()
                payload = decrypt_dat(raw, aes_key, xor_key)
                out["aes_key_ready"] = True
                out["sample"] = {
                    "path": sample,
                    "kind": sniff(payload),
                    "payload_bytes": len(payload),
                }
            except Exception as exc:
                out["notes"].append(f"密钥校验失败（{os.path.basename(sample)}）：{exc}")
        else:
            out["notes"].append("attach 目录里没有 .dat 样本")
    out["ok"] = bool(out["aes_key_ready"])
    if out["ok"] and not out["ffmpeg"]:
        out["notes"].append("未找到 ffmpeg：缩略图可解，WXGF 原图无法转换")
    return out


def export_one(
    acc: str,
    ref: str,
    dest_dir: str,
    prefer: str = "orig",
    aes_key: Optional[bytes] = None,
    xor_key: Optional[int] = None,
) -> Dict[str, Any]:
    """解密一张图片并落盘，返回 {ok, path, kind, width, height, bytes, source}。"""
    src = locate_dat(acc, ref, prefer=prefer)
    if not src:
        return {"ok": False, "ref": ref, "error": "找不到对应的 .dat"}
    if aes_key is None or xor_key is None:
        uin = discover_uin()
        if uin is None:
            return {"ok": False, "ref": ref, "error": "无法派生图片密钥（缺 UIN）"}
        aes_key, xor_key = derive_key(uin, wxid_of(acc))
    try:
        with open(src, "rb") as f:
            raw = f.read()
        payload = decrypt_dat(raw, aes_key, xor_key)
        img = to_image(payload)
    except Exception as exc:
        # 原图失败时退回缩略图，图片能看比格式完整更重要
        if prefer != "thumb":
            thumb = locate_dat(acc, ref, prefer="thumb")
            if thumb and thumb != src:
                try:
                    with open(thumb, "rb") as f:
                        raw = f.read()
                    img = to_image(decrypt_dat(raw, aes_key, xor_key))
                    src = thumb
                except Exception:
                    return {"ok": False, "ref": ref, "error": str(exc), "source": src}
            else:
                return {"ok": False, "ref": ref, "error": str(exc), "source": src}
        else:
            return {"ok": False, "ref": ref, "error": str(exc), "source": src}

    os.makedirs(dest_dir, exist_ok=True)
    name = f"{ref}.{img['ext']}"
    dst = os.path.join(dest_dir, name)
    with open(dst, "wb") as f:
        f.write(img["data"])
    return {
        "ok": True,
        "ref": ref,
        "path": dst,
        "name": name,
        "kind": img["kind"],
        "width": img["width"],
        "height": img["height"],
        "bytes": len(img["data"]),
        "source": src,
        "from_thumb": src.lower().endswith(("_t.dat", "_h.dat")),
    }
