#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wcread.py — 读企业微信本地会话、消息、联系人、文件。

数据链路：
    Data/*.db（wxSQLite3 加密）
      → wckey.get_key()  从 WXWork.exe 内存取 16 字节钥匙
      → wcrypto           按页解成明文副本，落到 workspace/07_企业微信缓存/
      → 本模块            ATTACH 跨库查询 + protobuf 解正文

为什么必须落一份明文副本：企业微信的库不能用 sqlcipher 打开（它没有 HMAC、
reserve 区，页结构是 wxSQLite3 自己的一套），逐页解出来的字节流本身就是
一份可用的 SQLite 文件。副本放在 workspace 下，源库一个字节都不动。

会话 ID 自带类型前缀，直接当「是群还是单聊」用，不用再猜：
    R:<数字>        群聊
    S:<数字>_<数字> 单聊（两个数字是双方 id）
    M:<数字>        微信联系人
    O:<数字>        应用 / 公众号
    Y:<数字>        系统会话
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import sqlite3
import time
from typing import Any, Dict, Iterable, List, Optional

from . import wckey, wcrypto

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
CACHE_REL = os.path.join("workspace", "07_企业微信缓存")

# 只需要这四个库就能回答「谁在哪个群说了什么、收了什么文件」。
# 路径相对账号目录下的 Data/，缓存里也按同样的相对层级落盘。
NEED_DBS = ("message.db", "session.db", "user.db", "file.db")

_CONV_PREFIX = {
    "R": "群聊", "S": "单聊", "M": "微信联系人", "O": "应用", "Y": "系统",
}

# content_type → 人话。企业微信这套编号和个人微信不通用，这里是按本机
# 实测样本逐个核过的，认不出来的保留原始编号，别硬猜成语义。
_TYPES = {
    0: "文本", 2: "文本", 13: "文档/链接", 14: "表情", 15: "文件", 20: "文件",
    29: "表情包", 31: "图文链接", 35: "应用消息", 38: "系统通知", 78: "小程序",
    503: "会议", 515: "会议", 1011: "图片", 1002: "图片", 1006: "文件",
    1027: "投票", 1043: "红包", 1052: "日程",
}
_TEXT_TYPES = {0, 2}
_FILE_TYPES = {15, 20, 1006}
_MEDIA_TYPES = {1011, 1002, 14, 29}

_FILE_EXT_RE = re.compile(
    r"\.(docx?|xlsx?|pptx?|pdf|txt|csv|zip|rar|7z|jpg|jpeg|png|gif|wps|et|dps)$", re.I)


# --------------------------------------------------------------------------- #
# 数据目录
# --------------------------------------------------------------------------- #
def _looks_like_account(path: str) -> bool:
    return os.path.isdir(os.path.join(path, "Data")) and (
        os.path.isfile(os.path.join(path, "Data", "message.db"))
        or os.path.isfile(os.path.join(path, "Data", "session.db")))


def account_dir(explicit: str = "") -> Optional[str]:
    """定位企业微信账号目录（形如 .../WXWork/<account_id>）。

    企业微信允许用户改存储位置，所以不写死 Documents：环境变量优先，
    其次从进程安装路径反推，最后扫几个常见落点。
    """
    if explicit and _looks_like_account(explicit):
        return explicit.rstrip("\\/")
    if explicit:
        inner = os.path.join(explicit, "Data")
        if os.path.isdir(inner):
            return explicit.rstrip("\\/")
    env = os.environ.get("WECOM_ACCOUNT_DIR", "")
    if env and _looks_like_account(env):
        return env.rstrip("\\/")

    roots = [
        r"D:\聊天记录\WXWork",
        os.path.join(os.path.expanduser("~"), "Documents", "WXWork"),
        os.path.join(os.environ.get("USERPROFILE", ""), "Documents", "WXWork"),
        r"C:\WXWork",
        r"D:\WXWork",
        r"E:\WXWork",
    ]
    best, best_mtime = None, -1.0
    for root in roots:
        if not os.path.isdir(root):
            continue
        for name in os.listdir(root):
            cand = os.path.join(root, name)
            if not _looks_like_account(cand):
                continue
            try:
                mt = os.path.getmtime(os.path.join(cand, "Data", "message.db"))
            except OSError:
                mt = 0.0
            if mt > best_mtime:
                best, best_mtime = cand, mt
    return best


def data_dir(acc: str) -> str:
    return os.path.join(acc, "Data")


def cache_dir() -> str:
    return os.path.join(_ROOT, CACHE_REL)


def cache_db_dir() -> str:
    """缓存里放库的位置：与源库同构（缓存/Data/xxx.db），_conn 按这个层级找。"""
    return os.path.join(cache_dir(), "Data")


# --------------------------------------------------------------------------- #
# 解密缓存
# --------------------------------------------------------------------------- #
def ensure_cache(acc: str, force: bool = False,
                 need: Iterable[str] = NEED_DBS) -> Dict[str, Any]:
    """保证缓存里的明文库跟源库一样新。

    用 mtime 比对做增量：只有源库更新过（企业微信又收发了消息）才重解，
    平时读消息不会每次都把 20 个库解一遍。
    """
    src = data_dir(acc)
    out = cache_db_dir()
    need = [n.replace("/", os.sep) for n in need]
    stale = []
    missing_src = []
    for rel in need:
        s = os.path.join(src, rel)
        d = os.path.join(out, rel)
        if not os.path.isfile(s):
            missing_src.append(rel)
            continue
        if force or not os.path.isfile(d) or os.path.getmtime(s) > os.path.getmtime(d):
            stale.append(rel)
    if not stale:
        return {"ok": True, "refreshed": [], "cache_dir": out, "missing": missing_src}

    got = wckey.get_key(src, force=force)
    if not got.get("ok"):
        return {"ok": False, "error": got.get("error", "取密钥失败"),
                "hint": got.get("hint"), "cache_dir": out}

    res = wcrypto.decrypt_many(src, out, bytes.fromhex(got["key"]), only=stale)
    # 源库刚写过时 mtime 会小于等于缓存，下一轮又会判成 stale；把缓存时间对齐源库，
    # 让「解过一次就不再重复解」这件事成立。
    for rel in stale:
        s = os.path.join(src, rel)
        d = os.path.join(out, rel)
        if os.path.isfile(d) and os.path.isfile(s):
            try:
                st = os.stat(s)
                os.utime(d, (st.st_atime, st.st_mtime))
            except OSError:
                pass
    refreshed = [k for k, v in res["detail"].items() if v.get("ok") and "skipped" not in v]
    return {
        "ok": res["ok"],
        "refreshed": refreshed,
        "failed": res["failed"],
        "key_from": got.get("from"),
        "cache_dir": out,
        "wal_pending": any(v.get("wal_pending") for v in res["detail"].values()),
    }


def _conn(need: Iterable[str] = NEED_DBS) -> sqlite3.Connection:
    """打开缓存里的 message.db，并把其余库 ATTACH 成别名，方便一条 SQL 联查。

    注意：这里只挂已经解密好的副本，不碰源库，也不加写锁。
    """
    base = os.path.join(cache_dir(), "Data", "message.db")
    if not os.path.isfile(base):
        raise FileNotFoundError("企业微信缓存缺失，先调 ensure_cache")
    con = sqlite3.connect(f"file:{base}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.text_factory = lambda b: b.decode("utf-8", "replace")
    for alias, name in (("s", "session.db"), ("u", "user.db"), ("f", "file.db")):
        path = os.path.join(cache_dir(), "Data", name)
        if os.path.isfile(path):
            try:
                con.execute(f"ATTACH DATABASE 'file:{path}?mode=ro' AS {alias}")
            except sqlite3.Error:
                pass
    return con


# --------------------------------------------------------------------------- #
# protobuf 解析
# --------------------------------------------------------------------------- #
def _varint(buf: bytes, i: int) -> tuple:
    val = shift = 0
    while i < len(buf):
        byte = buf[i]
        i += 1
        val |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return val, i
        shift += 7
        if shift > 63:
            break
    raise ValueError("varint 越界")


def _iter_texts(buf: bytes, depth: int = 0, acc: Optional[List[str]] = None) -> List[str]:
    """把所有层级的干净文本都收上来（找文件名用，文件名常常比正文藏得深）。"""
    if acc is None:
        acc = []
    if depth > 6 or not buf:
        return acc
    for _, wire, val in _pb_fields(buf):
        if wire != 2 or not isinstance(val, (bytes, bytearray)):
            continue
        text = _clean_text(bytes(val))
        if text is not None and len(text) >= 2:
            acc.append(text)
        _iter_texts(bytes(val), depth + 1, acc)
    return acc


def _looks_binary(text: str) -> bool:
    """base64 / 长 hex / 无空格随机串：这些是 ID、hash、密钥片段，不是人话。

    下划线和连字符也算进「单 token」的字符集，否则 ``vote_1688854300000000_12345678_1``
    这种投票 id 会被当成正文——它比卡片标题长，一取最长就把标题挤掉了。
    """
    t = text.strip()
    if re.fullmatch(r"wx[0-9a-f]{16}", t):  # 小程序 appid，18 位，长度门槛拦不住它
        return True
    if len(t) < 20:
        return False
    if re.fullmatch(r"[0-9a-fA-F]+", t):
        return True
    if re.fullmatch(r"[A-Za-z0-9+/=_\-]+", t) and not re.search(r"[\u4e00-\u9fa5]", t):
        return True
    return False


def _looks_url(text: str) -> bool:
    """整段就是一个链接。卡片消息里第一个字段往往是链接、第二个是图标，
    都不是标题——不排掉的话正文会变成一串 CDN 地址。"""
    return bool(re.fullmatch(r"(?:https?|ftp)://\S+", text.strip(), re.I))


def _body(content: Optional[bytes], ctype: int) -> str:
    """按消息类型把 protobuf 正文收成一句话。"""
    if not content:
        return ""
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, (bytes, bytearray)):
        return ""

    parts = [p for p in _text_parts(content) if not _looks_binary(p)]

    if ctype in _FILE_TYPES:
        for p in parts + [x for x in _iter_texts(content) if not _looks_binary(x)]:
            if not _FILE_EXT_RE.search(p.strip()):
                continue
            # 有些客户端把 multipart 头一起存进了正文：「附件一.docx; filename=附件一.docx」
            name = re.split(r";\s*(?:filename|name|size)=", p.strip(), maxsplit=1)[0].strip()
            name = name.split("?")[0].strip()
            return os.path.basename(name)
        return "[文件]"

    if ctype in _MEDIA_TYPES:
        for p in parts:
            if re.search(r"[\u4e00-\u9fa5]{2,}", p):
                return f"[{_TYPES.get(ctype, '媒体')}] " + re.sub(r"\s+", " ", p).strip()[:40]
        return f"[{_TYPES.get(ctype, '媒体')}]"

    def _human(p: str) -> bool:
        if _looks_url(p) or _looks_binary(p):
            return False
        if p.strip().endswith("@app"):  # 小程序卡片的 appid，长得像名字而已
            return False
        if re.match(r"^[\w\-./]+\.html(\?|$)", p.strip()):  # 小程序页面路径
            return False
        # 会议卡片的正文是参会人 id 列表（16 位纯数字），比标题还长。
        # 文本消息里的手机号/金额是正常内容，所以这条只对卡片类生效。
        return not (ctype not in _TEXT_TYPES and re.fullmatch(r"\d{8,}", p.strip()))

    best = ""
    if ctype in _TEXT_TYPES:
        best = max(parts, key=len, default="")
    else:
        # 卡片类（文档 / 图文链接 / 接龙 / 投票 / 会议）的字段顺序基本一致：
        # 链接 → 图标 → 标题 → 摘要。取「最长」会把长摘要或图标地址当正文，
        # 所以这里取第一个像人话的字段，也就是标题。
        for p in parts:
            if _human(p):
                best = re.sub(r"\s+", " ", p).strip()
                break
    if not best:  # 结构化没取到就退一步，扫全层级
        for p in _iter_texts(content):
            if _human(p) and len(p) > len(best):
                best = p
    if not best:
        # 纯链接消息（分享一个文档/网页）：留着链接也比留空有用。
        # 只认 URL，别把 appid / 内部 id 这类「唯一的候选」当正文。
        for p in parts:
            if _looks_url(p) and len(p) > len(best):
                best = p
    if not best:
        return "" if ctype in _TEXT_TYPES else f"[{_TYPES.get(ctype, f'类型{ctype}')}]"
    return re.sub(r"[ \t]+", " ", best).strip()


def _pb_fields(buf: bytes) -> List[tuple]:
    """把一段 protobuf 切成 ``(字段号, wire_type, 值)``。

    只做「按 wire format 走一遍」，不假设任何字段号 —— 企业微信没有公开
    .proto，且不同版本字段号会挪位，写死字段号等于给自己挖坑。
    """
    out: List[tuple] = []
    i, n = 0, len(buf)
    while i < n:
        try:
            key, i = _varint(buf, i)
        except ValueError:
            break
        fno, wire = key >> 3, key & 7
        try:
            if wire == 0:
                val, i = _varint(buf, i)
            elif wire == 2:
                ln, i = _varint(buf, i)
                if ln < 0 or i + ln > n:
                    break
                val, i = buf[i: i + ln], i + ln
            elif wire == 5:
                val, i = buf[i: i + 4], i + 4
            elif wire == 1:
                val, i = buf[i: i + 8], i + 8
            else:
                break
        except ValueError:
            break
        out.append((fno, wire, val))
    return out


def _clean_text(chunk: bytes) -> Optional[str]:
    """能不能把这块字节直接当正文。判据刻意保守：只收「人真的写出来的字」。"""
    try:
        text = chunk.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if not text or text[0] in "\n\r\t ":
        return None
    if any(ord(ch) < 32 and ch not in "\n\t" for ch in text):
        return None
    return text


def _text_parts(buf: bytes, depth: int = 0) -> List[str]:
    """按层级把正文段落取出来。

    文本消息的正文稳定落在 ``field1.len → field2.len → field1.len``，
    但引用、合并转发这类会带多段。这里逐段下钻，只收「解码后是干净文本」
    的那一层，避免把长度字节（一个 ascii 字符）粘到正文前面 ——
    「c普通话的考试时间…」「1 概率论课教室…」就是这么来的。
    """
    if depth > 3 or not buf:
        return []
    out: List[str] = []
    for _, wire, val in _pb_fields(buf):
        if wire != 2 or not isinstance(val, (bytes, bytearray)):
            continue
        text = _clean_text(val)
        if text is not None and len(text) >= 2:
            out.append(text)
            continue
        out.extend(_text_parts(val, depth + 1))
    return out




# --------------------------------------------------------------------------- #
# 查询
# --------------------------------------------------------------------------- #
def _fmt(ts: Any) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(ts)))
    except (ValueError, OSError, OverflowError):
        return ""


def _parse_time(text: str, end: bool = False) -> int:
    """把 ISO 日期或中文相对时间（今天/昨天/本周/本月/近N天/11月等）转为时间戳；end=True 取当天末尾。"""
    text = (text or "").strip()
    if not text:
        return 0
    if text.isdigit() and len(text) >= 10:
        return int(text)
    from datetime import datetime, timedelta
    now = datetime.now()
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    def _day(offset: int) -> int:
        d = day0 - timedelta(days=offset)
        if end:
            d = d.replace(hour=23, minute=59, second=59)
        return int(d.timestamp())
    rel_days = {"今天": 0, "今日": 0, "昨天": 1, "昨日": 1, "前天": 2, "大前天": 3}
    if text in rel_days:
        return _day(rel_days[text])
    m = re.fullmatch(r"(?:近|最近|过去)\s*(\d+)\s*(?:天|日)", text)
    if m:
        return _day(0) if end else _day(max(1, int(m.group(1))))
    m = re.fullmatch(r"(?:近|最近|过去)\s*(\d+)\s*(?:小时|h)", text, re.I)
    if m:
        return int((now - timedelta(hours=0 if end else max(1, int(m.group(1))))).timestamp())
    if text in ("本周", "这周", "本星期", "这星期"):
        sd = day0 - timedelta(days=now.weekday())
        return int((sd + timedelta(days=6)).replace(hour=23, minute=59, second=59).timestamp()) if end else int(sd.timestamp())
    if text in ("上周", "上星期"):
        sd = day0 - timedelta(days=now.weekday() + 7)
        return int((sd + timedelta(days=6)).replace(hour=23, minute=59, second=59).timestamp()) if end else int(sd.timestamp())
    if text in ("本月", "这个月"):
        sd = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if end:
            nxt = (sd + timedelta(days=32)).replace(day=1)
            return int((nxt - timedelta(seconds=1)).timestamp())
        return int(sd.timestamp())
    if text in ("上月", "上个月"):
        first = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        pe = first - timedelta(seconds=1)
        sd = pe.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return int(pe.timestamp()) if end else int(sd.timestamp())
    if text in ("今年", "本年"):
        sd = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        return int(now.replace(hour=23, minute=59, second=59).timestamp()) if end else int(sd.timestamp())
    m_ym = re.fullmatch(r"(?:(\d{4})\s*[-/年]\s*)?(\d{1,2})\s*月(?:份)?", text) or re.fullmatch(r"(\d{4})-(\d{1,2})", text)
    if m_ym:
        yr = int(m_ym.group(1)) if m_ym.group(1) else now.year
        mo = int(m_ym.group(2))
        if 1 <= mo <= 12:
            sd = datetime(yr, mo, 1, 0, 0, 0)
            if end:
                nxt = (sd + timedelta(days=32)).replace(day=1)
                return int((nxt - timedelta(seconds=1)).timestamp())
            return int(sd.timestamp())
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d",
                "%Y年%m月%d日", "%m-%d", "%m月%d日"):
        try:
            dt = datetime.strptime(text, fmt)
            if fmt in ("%m-%d", "%m月%d日"):
                dt = dt.replace(year=now.year)
            if end and "H" not in fmt:
                dt = dt.replace(hour=23, minute=59, second=59)
            return int(dt.timestamp())
        except ValueError:
            continue
    return 0


def kind_of(conv_id: str) -> str:
    return _CONV_PREFIX.get((conv_id or "")[:1].upper(), "未知")


def sessions(limit: int = 30, only_unread: bool = False) -> List[Dict[str, Any]]:
    con = _conn()
    try:
        unread = {}
        try:
            for r in con.execute("SELECT id, count FROM unread_conversation_table"):
                unread[r["id"]] = r["count"] or 0
        except sqlite3.Error:
            pass
        sql = ("SELECT id, name, last_message_time, last_message_id, is_marked "
               "FROM s.conversation_table "
               "WHERE COALESCE(name,'') <> '' ORDER BY last_message_time DESC")
        rows = con.execute(sql).fetchall()
        out = []
        for r in rows:
            cid = r["id"] or ""
            n = unread.get(cid, 0)
            if only_unread and n <= 0:
                continue
            out.append({
                "id": cid,
                "name": r["name"],
                "kind": kind_of(cid),
                "last_time": _fmt(r["last_message_time"]),
                "last_ts": int(r["last_message_time"] or 0),
                "unread": n,
                "marked": bool(r["is_marked"]),
            })
            if len(out) >= max(1, limit):
                break
        return out
    finally:
        con.close()


def resolve_chat(chat: str) -> Optional[Dict[str, Any]]:
    """把「群名 / 群名片段 / R:123」统一解析成一个会话。"""
    chat = (chat or "").strip()
    if not chat:
        return None
    con = _conn()
    try:
        row = con.execute("SELECT id, name FROM s.conversation_table WHERE id = ?", (chat,)).fetchone()
        if row:
            return {"id": row["id"], "name": row["name"] or row["id"]}
        rows = con.execute(
            "SELECT id, name FROM s.conversation_table "
            "WHERE COALESCE(name,'') LIKE ? ORDER BY last_message_time DESC",
            (f"%{chat}%",)).fetchall()
        if not rows:
            return None
        exact = [r for r in rows if r["name"] == chat]
        pick = exact[0] if exact else rows[0]
        return {"id": pick["id"], "name": pick["name"] or pick["id"],
                "candidates": [{"id": r["id"], "name": r["name"]} for r in rows[:5]]}
    finally:
        con.close()


def _sender_names(con: sqlite3.Connection, ids: Iterable[int]) -> Dict[int, str]:
    ids = [int(i) for i in ids if i]
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    out: Dict[int, str] = {}
    try:
        for r in con.execute(f"SELECT id, name FROM u.user_table WHERE id IN ({marks})", ids):
            if r["name"]:
                out[int(r["id"])] = r["name"]
    except sqlite3.Error:
        pass
    return out


def messages(chat: str, limit: int = 50, since: str = "", until: str = "") -> Dict[str, Any]:
    """读某个会话的消息。``chat`` 支持群名、群名片段、会话 ID。"""
    target = resolve_chat(chat)
    if not target:
        return {"ok": False, "error": f"找不到会话: {chat}",
                "hint": "先用 work_wecom_sessions 看有哪些会话，或用 id（如 R:233536007534344）"}
    con = _conn()
    try:
        where = ["conversation_id = ?"]
        args: List[Any] = [target["id"]]
        t0, t1 = _parse_time(since), _parse_time(until, end=True)
        if t0:
            where.append("send_time >= ?")
            args.append(t0)
        if t1:
            where.append("send_time <= ?")
            args.append(t1)
        sql = (f"SELECT send_time, sender_id, content_type, content FROM message_table "
               f"WHERE {' AND '.join(where)} ORDER BY send_time DESC LIMIT ?")
        args.append(max(1, limit))
        rows = con.execute(sql, args).fetchall()
        names = _sender_names(con, [r["sender_id"] for r in rows])
        items = []
        for r in reversed(rows):
            ctype = int(r["content_type"] or 0)
            items.append({
                "time": _fmt(r["send_time"]),
                "ts": int(r["send_time"] or 0),
                "sender": names.get(int(r["sender_id"] or 0), str(r["sender_id"])),
                "type": _TYPES.get(ctype, f"类型{ctype}"),
                "text": _body(r["content"], ctype),
            })
        return {"ok": True, "chat": target["name"], "id": target["id"],
                "kind": kind_of(target["id"]), "count": len(items),
                "candidates": target.get("candidates"), "messages": items}
    finally:
        con.close()


def search(keyword: str, chat: str = "", limit: int = 30,
           since: str = "", until: str = "") -> Dict[str, Any]:
    """按关键词搜消息正文。

    正文是 protobuf，SQL 的 LIKE 搜不到 —— 只能把候选窗口拉出来在内存里匹配。
    所以先用时间窗和会话把范围压小，再逐条解正文。
    """
    kw = (keyword or "").strip()
    if not kw:
        return {"ok": False, "error": "keyword 不能为空"}
    con = _conn()
    try:
        where = ["content_type IN (0,2,13,15,20,31,35,38,1027)"]  # 有正文可搜的类型
        args: List[Any] = []
        if chat:
            target = resolve_chat(chat)
            if not target:
                return {"ok": False, "error": f"找不到会话: {chat}"}
            where.append("conversation_id = ?")
            args.append(target["id"])
        t0, t1 = _parse_time(since), _parse_time(until, end=True)
        if t0:
            where.append("send_time >= ?")
            args.append(t0)
        if t1:
            where.append("send_time <= ?")
            args.append(t1)
        sql = (f"SELECT conversation_id, send_time, sender_id, content_type, content "
               f"FROM message_table WHERE {' AND '.join(where)} ORDER BY send_time DESC")
        hits = []
        names_cache: Dict[int, str] = {}
        conv_names = {r["id"]: r["name"] for r in
                      con.execute("SELECT id, name FROM s.conversation_table")}
        for r in con.execute(sql, args):
            body = _body(r["content"], int(r["content_type"] or 0))
            if kw.lower() not in body.lower():
                continue
            sid = int(r["sender_id"] or 0)
            if sid not in names_cache:
                names_cache.update(_sender_names(con, [sid]))
            hits.append({
                "chat": conv_names.get(r["conversation_id"], r["conversation_id"]),
                "conversation_id": r["conversation_id"],
                "time": _fmt(r["send_time"]),
                "sender": names_cache.get(sid, str(sid)),
                "type": _TYPES.get(int(r["content_type"] or 0), ""),
                "text": body,
            })
            if len(hits) >= max(1, limit):
                break
        return {"ok": True, "keyword": kw, "count": len(hits), "hits": hits}
    finally:
        con.close()


def contacts(keyword: str = "", limit: int = 30) -> Dict[str, Any]:
    con = _conn()
    try:
        cols = {d[1] for d in con.execute("PRAGMA u.table_info(user_table)")}
        pick = [c for c in ("id", "name", "english_name", "mobile", "email", "position", "corp_id")
                if c in cols]
        sql = f"SELECT {', '.join(pick)} FROM u.user_table"
        args: List[Any] = []
        if keyword:
            conds = [f"COALESCE({c},'') LIKE ?" for c in pick if c != "id"]
            sql += " WHERE " + " OR ".join(conds)
            args = [f"%{keyword}%"] * len(conds)
        sql += " LIMIT ?"
        args.append(max(1, limit))
        rows = [dict(r) for r in con.execute(sql, args)]
        return {"ok": True, "count": len(rows), "contacts": rows}
    finally:
        con.close()


def files(since: str = "", until: str = "", exts: Optional[Iterable[str]] = None,
          limit: int = 200) -> Dict[str, Any]:
    """列出企业微信收到的文件。

    文件名不在 file.db 里（file_table4.name 实测是空的），它藏在 message.db
    的文件类消息正文中；file.db 只补体积和 md5。所以这里以消息为主表，
    用 message_id 左连 file_table4，再按「Cache/File/<年-月>/文件名」找本地路径。
    """
    con = _conn()
    try:
        acc = account_dir()
        base = os.path.join(acc, "Cache", "File") if acc else ""
        where = ["content_type IN (15,20,1006)"]
        args: List[Any] = []
        t0, t1 = _parse_time(since), _parse_time(until, end=True)
        if t0:
            where.append("send_time >= ?")
            args.append(t0)
        if t1:
            where.append("send_time <= ?")
            args.append(t1)
        sql = (f"SELECT message_id, conversation_id, send_time, sender_id, content_type, content "
               f"FROM message_table WHERE {' AND '.join(where)} ORDER BY send_time DESC LIMIT ?")
        args.append(max(50, limit * 3))  # 多取一些，过滤扩展名后再截断

        conv_names = {r["id"]: r["name"] for r in
                      con.execute("SELECT id, name FROM s.conversation_table")}
        want = {e.lower().lstrip(".") for e in (exts or []) if e}
        out: List[Dict[str, Any]] = []
        names_cache: Dict[int, str] = {}
        for r in con.execute(sql, args):
            name = _body(r["content"], int(r["content_type"] or 0)).strip()
            if not name or not _FILE_EXT_RE.search(name):
                continue
            ext = name.rsplit(".", 1)[-1].lower()
            if want and ext not in want:
                continue
            sid = int(r["sender_id"] or 0)
            if sid not in names_cache:
                names_cache.update(_sender_names(con, [sid]))
            month = time.strftime("%Y-%m", time.localtime(int(r["send_time"] or 0)))
            local = os.path.join(base, month, name) if base else ""
            size = md5 = None
            try:
                fr = con.execute(
                    "SELECT size, md5 FROM f.file_table4 WHERE message_id = ? LIMIT 1",
                    (str(r["message_id"]),)).fetchone()
                if fr:
                    size, md5 = fr["size"], fr["md5"]
            except sqlite3.Error:
                pass
            out.append({
                "name": name,
                "chat": conv_names.get(r["conversation_id"], r["conversation_id"]),
                "time": _fmt(r["send_time"]),
                "sender": names_cache.get(sid, str(sid)),
                "ext": ext,
                "size_kb": round(int(size) / 1024, 1) if size else None,
                "md5": md5,
                "path": local if local and os.path.isfile(local) else "",
                "on_disk": bool(local) and os.path.isfile(local),
            })
            if len(out) >= max(1, limit):
                break
        return {"ok": True, "count": len(out), "files": out,
                "files_root": base, "note": "path 为空表示客户端已清缓存或文件还没下载到本地"}
    finally:
        con.close()


_BAD_NAME = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def safe_name(name: str, limit: int = 120) -> str:
    """把会话名/人名/文件名拼成合法文件名的片段。"""
    out = _BAD_NAME.sub("_", name or "").strip(" .")
    if len(out) > limit:
        stem, dot, ext = out.rpartition(".")
        out = f"{stem[:limit - len(ext) - 1]}.{ext}" if dot and len(ext) <= 8 else out[:limit]
    return out or "未命名"


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def harvest(dest_dir: str, since: str = "", until: str = "", chats: Optional[Iterable[str]] = None,
            exts: Optional[Iterable[str]] = None, prefix: str = "{chat}",
            dedupe: bool = True, dry_run: bool = True, limit: int = 200) -> Dict[str, Any]:
    """把时间窗内的企业微信文件收进工作区。

    企业微信和企业微信里的文件不一定已经在本地 —— 云端文件要用户点开才下载，
    所以「消息里有、盘上没有」是常态。这里如实分成三类报：
    已复制 / 本地缺失（没下载） / 内容重复跳过，而不是假装全都收到了。
    """
    got = files(since=since, until=until, exts=exts, limit=limit)
    items = got.get("files", [])
    if chats:
        want = [c.lower() for c in chats if c]
        items = [x for x in items if any(w in (x.get("chat") or "").lower() for w in want)]

    plan, missing, copied, dup = [], [], [], []
    seen = {}
    if dedupe and os.path.isdir(dest_dir):
        for name in os.listdir(dest_dir):
            p = os.path.join(dest_dir, name)
            if os.path.isfile(p):
                try:
                    seen[_sha256(p)] = name
                except OSError:
                    pass

    for x in items:
        if not x.get("on_disk") or not x.get("path"):
            missing.append({"name": x["name"], "chat": x["chat"], "time": x["time"]})
            continue
        piece = prefix or ""
        target = safe_name(x["name"])
        if piece:
            fill = {"chat": x["chat"], "sender": x["sender"], "month": x["time"][:7],
                    "ext": x["ext"], "name": x["name"]}
            try:
                head = piece.format(**fill)
            except (KeyError, IndexError):
                head = x["chat"]
            if head:
                target = safe_name(f"{head}_{target}")
        dst = os.path.join(dest_dir, target)
        plan.append({"src": x["path"], "dst": dst, "name": x["name"], "chat": x["chat"]})
        if dry_run:
            continue

        sha = _sha256(x["path"]) if dedupe else ""
        if dedupe and sha in seen:
            dup.append({"name": x["name"], "same_as": seen[sha]})
            continue
        os.makedirs(dest_dir, exist_ok=True)
        base, ext = os.path.splitext(dst)
        i = 2
        while os.path.exists(dst):
            dst = f"{base}({i}){ext}"
            i += 1
        try:
            shutil.copy2(x["path"], dst)
            try:  # 原件常带只读，档案里留可写，后续合并/排版才不被拒
                os.chmod(dst, 0o644)
            except OSError:
                pass
        except OSError as exc:
            missing.append({"name": x["name"], "error": str(exc)[:120]})
            continue
        if sha:
            seen[sha] = os.path.basename(dst)
        copied.append({"name": x["name"], "dst": dst, "chat": x["chat"], "time": x["time"]})

    return {
        "ok": True,
        "dry_run": dry_run,
        "dest_dir": dest_dir,
        "matched": len(items),
        "planned": len(plan),
        "plan": plan if dry_run else [],
        "copied": copied,
        "skipped_duplicate": dup,
        "not_downloaded": missing,
        "note": ("企业微信的云端文件要客户端点开才会落到本地；"
                 "not_downloaded 里的就是「消息里有、本机没下载」的部分"),
    }


def probe() -> Dict[str, Any]:
    """体检：装没装、数据在哪、几个库、密钥能不能取、要不要重解。"""
    acc = account_dir()
    info: Dict[str, Any] = {
        "ok": False,
        "running": bool(wckey.wxwork_pids()),
        "account_dir": acc,
        "processes": wckey.wxwork_pids()[:3],
    }
    if not acc:
        info["error"] = "没找到企业微信数据目录"
        info["hint"] = ("企业微信默认落在 用户目录\\Documents\\WXWork\\<账号>\\Data；"
                        "如果改过存储位置，把那个目录传给 account_dir")
        return info
    src = data_dir(acc)
    dbs = []
    encrypted = plain = 0
    for name in sorted(os.listdir(src)):
        if not name.endswith(".db"):
            continue
        path = os.path.join(src, name)
        if os.path.getsize(path) < wcrypto.PAGE:
            continue
        is_plain = wcrypto.is_plain_sqlite(path)
        plain += 1 if is_plain else 0
        encrypted += 0 if is_plain else 1
        dbs.append({"name": name, "size_kb": round(os.path.getsize(path) / 1024, 1),
                    "plain": is_plain})
    info.update({
        "data_dir": src,
        "db_total": len(dbs),
        "db_encrypted": encrypted,
        "db_plain": plain,
        "db_sample": dbs[:8],
        "cached_key": bool(wckey.load_cached(src)),
        "cache_dir": cache_dir(),
        "files_dir": os.path.join(acc, "Cache", "File"),
    })
    info["ok"] = encrypted > 0 or plain > 0
    if encrypted and not info["running"]:
        info["hint"] = "企业微信没开：密钥只在它的内存里，读加密库前要先打开并登录"
    return info
