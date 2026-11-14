"""微信 4.x 只读查询（会话 / 消息 / 全文搜索）。

链路：
    wxkey.py   取 23 个库的 SQLCipher 密钥（4.1.11+ 走 Config.Cipher + XOR 掩码）
    wxdecrypt  逐页解密到缓存目录（源文件只读，绝不改动）
    wxread     解析会话表、通讯录、按会话的消息表

已知结构（本机微信 4.2.0.13 实测）：
    session/session.db   SessionTable(username, summary, unread_count, sort_timestamp)
    contact/contact.db   contact(username, remark, nick_name) / chat_room / chatroom_member
    message/message_N.db 每会话一张表 Msg_<md5(username)>，
                         列 local_id/local_type/real_sender_id/create_time/message_content/compress_content
    Name2Id(rowid, user_name) 把 real_sender_id 还原成 wxid
    群消息正文以 "wxid_xxx:\\n内容" 开头，可直接取发送者
    local_type 低 32 位是类型，高 32 位是子类型；WCDB_CT_message_content==4 表示正文被 zstd 压缩
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
from typing import Any, Iterable

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import wxdecrypt  # noqa: E402
import wxkey  # noqa: E402

CACHE_REL = os.path.join("workspace", "07_微信只读缓存")
NEED_DBS = [
    "session/session.db",
    "contact/contact.db",
    "message/message_0.db",
    "message/message_1.db",
]

_MSG_TYPES = {
    1: "文本", 3: "图片", 34: "语音", 42: "名片", 43: "视频", 47: "表情",
    48: "位置", 49: "应用消息", 50: "通话", 10000: "系统消息", 10002: "系统消息",
    49 << 32 | 1: "文件",  # 类型 49 子类型 1
}


# --------------------------------------------------------------------------- #
# 定位与缓存
# --------------------------------------------------------------------------- #
def _project_root() -> str:
    return os.path.dirname(HERE)


def cache_dir() -> str:
    return os.path.join(_project_root(), CACHE_REL)


def dpapi_cache_path() -> str:
    return os.path.join(cache_dir(), ".keys.dpapi")


def sync_meta_path() -> str:
    return os.path.join(cache_dir(), ".sync_meta.json")


def _load_sync_meta() -> dict[str, dict]:
    p = sync_meta_path()
    if not os.path.isfile(p):
        return {}
    try:
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _save_sync_meta(meta: dict[str, dict]) -> None:
    p = sync_meta_path()
    try:
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
    except Exception:  # noqa: BLE001
        pass


def _file_sig(path: str) -> tuple[float, int]:
    try:
        st = os.stat(path)
        return (round(st.st_mtime, 3), int(st.st_size))
    except OSError:
        return (0.0, 0)


def account_dir(explicit: str = "") -> str | None:
    if explicit and os.path.isdir(explicit):
        return explicit
    env = os.environ.get("WECHAT_ACCOUNT_DIR")
    if env and os.path.isdir(env):
        return env
    for root in (r"D:\聊天记录\xwechat_files",
                 os.path.expanduser(r"~\Documents\xwechat_files"),
                 os.path.expanduser(r"~\Documents\WeChat Files")):
        if not os.path.isdir(root):
            continue
        for name in sorted(os.listdir(root)):
            cand = os.path.join(root, name)
            if os.path.isdir(os.path.join(cand, "db_storage", "message")):
                return cand
    return None


def extract_keys(
    acc: str,
    force_scan: bool = False,
    required_rels: Iterable[str] | None = NEED_DBS,
) -> dict:
    db_dir = os.path.join(acc, "db_storage")
    data = wxkey.extract_for_dir(
        db_dir,
        cache_file=dpapi_cache_path(),
        force_scan=force_scan,
        required_rels=list(required_rels) if required_rels is not None else None,
    )
    if not data.get("ok"):
        raise RuntimeError(f"取密钥失败: {data.get('message') or data}")
    return data


def ensure_cache(acc: str, force: bool = False, need: Iterable[str] = NEED_DBS) -> dict:
    """保证缓存里有可用的解密库，追踪 (db_mtime, wal_mtime) 实现轻量 WAL 实时补丁。"""
    db_dir = os.path.join(acc, "db_storage")
    out_dir = cache_dir()
    need = list(need)
    meta = _load_sync_meta()

    full_stale: list[str] = []
    wal_stale: list[str] = []
    current_sigs: dict[str, dict[str, float | int]] = {}

    for rel in need:
        src = os.path.join(db_dir, rel.replace("/", os.sep))
        dst = os.path.join(out_dir, rel.replace("/", os.sep))
        if not os.path.exists(src):
            continue
        db_mt, db_sz = _file_sig(src)
        wal_mt, wal_sz = _file_sig(src + "-wal")
        current_sigs[rel] = {
            "db_mtime": db_mt,
            "db_size": db_sz,
            "wal_mtime": wal_mt,
            "wal_size": wal_sz,
        }
        prev = meta.get(rel) or {}
        if force or not os.path.exists(dst):
            full_stale.append(rel)
        elif prev.get("db_mtime") != db_mt or prev.get("db_size") != db_sz:
            full_stale.append(rel)
        elif prev.get("wal_mtime") != wal_mt or prev.get("wal_size") != wal_sz:
            wal_stale.append(rel)

    if not full_stale and not wal_stale:
        return {
            "ok": True,
            "refreshed": [],
            "wal_patched": [],
            "cache_dir": out_dir,
            "key_cached": os.path.isfile(dpapi_cache_path()),
        }

    key_res = extract_keys(acc, force_scan=False, required_rels=full_stale + wal_stale)
    keys = key_res["keys"]
    missing = [r for r in (full_stale + wal_stale) if r not in keys]

    refreshed: list[str] = []
    wal_patched: list[str] = []
    failed: dict[str, Any] = {}

    if full_stale:
        to_decrypt = [r for r in full_stale if r in keys]
        res = wxdecrypt.decrypt_all(db_dir, out_dir, keys, only=to_decrypt)
        for r in to_decrypt:
            d = res["detail"].get(r, {})
            if d.get("ok"):
                refreshed.append(r)
                meta[r] = current_sigs[r]
                if d.get("wal_patched_pages", 0) > 0:
                    wal_patched.append(r)
            else:
                failed[r] = d

    for r in wal_stale:
        if r not in keys:
            continue
        src = os.path.join(db_dir, r.replace("/", os.sep))
        dst = os.path.join(out_dir, r.replace("/", os.sep))
        info = keys[r]
        try:
            wres = wxdecrypt.patch_wal(
                src + "-wal",
                dst,
                info["enc_key"],
                salt_hex=info.get("salt", ""),
                src_db=src,
            )
            if wres.get("ok"):
                wal_patched.append(r)
                meta[r] = current_sigs[r]
            else:
                failed[r] = wres
        except Exception as exc:  # noqa: BLE001
            failed[r] = {"ok": False, "error": str(exc)}

    _save_sync_meta(meta)
    return {
        "ok": not failed and (bool(refreshed) or bool(wal_patched) or not (full_stale or wal_stale)),
        "refreshed": refreshed,
        "wal_patched": wal_patched,
        "failed": failed,
        "no_key": missing,
        "cache_dir": out_dir,
        "key_count": len(keys),
        "key_mode": key_res.get("mode", ""),
        "key_cached": bool(key_res.get("cached")),
    }


def _conn(rel: str) -> sqlite3.Connection:
    path = os.path.join(cache_dir(), rel.replace("/", os.sep))
    if not os.path.exists(path):
        raise FileNotFoundError(f"缓存缺失: {path}（先执行 ensure_cache）")
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


# --------------------------------------------------------------------------- #
# 名称解析
# --------------------------------------------------------------------------- #
def _name_map() -> dict[str, str]:
    m: dict[str, str] = {}
    con = _conn("contact/contact.db")
    try:
        for r in con.execute("select username, remark, nick_name, alias from contact"):
            u = r["username"]
            if not u:
                continue
            m[u] = (r["remark"] or r["nick_name"] or r["alias"] or u)
    finally:
        con.close()
    return m


def display(username: str, names: dict[str, str] | None = None) -> str:
    if not username:
        return ""
    if username.endswith("@chatroom"):
        return (names or {}).get(username, username)
    return (names or {}).get(username, username)


# --------------------------------------------------------------------------- #
# 会话
# --------------------------------------------------------------------------- #
def sessions(limit: int = 30, only_unread: bool = False, names: dict | None = None) -> list[dict]:
    names = names if names is not None else _name_map()
    con = _conn("session/session.db")
    try:
        sql = ("select username, type, unread_count, summary, last_timestamp, sort_timestamp, is_hidden "
               "from SessionTable")
        if only_unread:
            sql += " where unread_count > 0"
        sql += " order by sort_timestamp desc limit ?"
        rows = con.execute(sql, (max(1, int(limit)),)).fetchall()
    finally:
        con.close()
    out = []
    for r in rows:
        u = r["username"]
        if u == "@placeholder_foldgroup" or u.startswith("gh_"):
            continue
        ts = r["sort_timestamp"] or r["last_timestamp"] or 0
        out.append({
            "username": u,
            "name": display(u, names),
            "is_group": u.endswith("@chatroom"),
            "unread": r["unread_count"] or 0,
            "summary": (r["summary"] or "").replace("\n", " ")[:80],
            "last_time": _fmt(ts),
            "last_ts": ts,
        })
    return out


# --------------------------------------------------------------------------- #
# 消息
# --------------------------------------------------------------------------- #
def _msg_tables(con: sqlite3.Connection) -> dict[str, str]:
    """{Msg_<md5>: 表名}"""
    tabs = [r[0] for r in con.execute(
        "select name from sqlite_master where type='table' and name like 'Msg_%'")]
    return {t[4:]: t for t in tabs}


def _sender_map(con: sqlite3.Connection) -> dict[int, str]:
    try:
        return {r[0]: r[1] for r in con.execute("select rowid, user_name from Name2Id")}
    except sqlite3.Error:
        return {}


def _zstd(raw: bytes) -> str | None:
    try:
        import zstandard

        return zstandard.ZstdDecompressor().decompress(raw, max_output_size=16 << 20).decode(
            "utf-8", "replace")
    except Exception:
        return None


def _decode(row: sqlite3.Row, cols: Iterable[str]) -> str:
    """取正文。

    实测微信 4.2：WCDB_CT_message_content==4 时，被 zstd 压缩的原始字节就放在
    message_content 列本身（BLOB），compress_content 常为空；两个位置都要试。
    """
    cols = set(cols)
    raw = row["message_content"] if "message_content" in cols else None
    ct = row["WCDB_CT_message_content"] if "WCDB_CT_message_content" in cols else 0

    if isinstance(raw, (bytes, bytearray)):
        raw = bytes(raw)
        if ct == 4 or raw[:4] == b"\x28\xb5\x2f\xfd":
            text = _zstd(raw)
            if text is not None:
                return text
        return raw.decode("utf-8", "replace")

    if ct == 4:
        blob = row["compress_content"] if "compress_content" in cols else None
        if isinstance(blob, (bytes, bytearray)) and blob:
            text = _zstd(bytes(blob))
            if text is not None:
                return text
    return raw or ""


_SENDER_PREFIX = re.compile(r"^([a-zA-Z0-9_@.\-]{5,80}):\r?\n")


def _split_group_sender(text: str) -> tuple[str, str]:
    """群聊正文首行是 `发送者id:\\n`，需要剥离。

    实测 4.2 的 id 形态不止 `wxid_xxx` / `xxx@chatroom`，还包括
    `25984982989620620@openim`，因此不能只匹配 @chatroom。
    为避免误伤普通文本（如「备注：」），要求 token 里必须含 `_` 或 `@`。
    """
    m = _SENDER_PREFIX.match(text)
    if m and ("@" in m.group(1) or m.group(1).startswith("wxid_")):
        return m.group(1), text[m.end():]
    return "", text


MAX_TEXT = 600

_MEDIA_HEX = re.compile(rb"[0-9a-fA-F]{32}")


def _media_ref(row: sqlite3.Row, cols: Iterable[str]) -> str:
    """图片/视频的文件名主干。

    微信 4.x 把图片文件名放在 ``packed_info_data`` 的 protobuf 里（末尾 32 位 hex），
    而 XML 的 ``md5`` 属性是原图的另一套指纹，跟 ``msg/attach`` 下的文件名对不上——
    实测 6 个样本全部 miss，所以只能认 packed_info_data。
    """
    if "packed_info_data" not in set(cols):
        return ""
    raw = row["packed_info_data"]
    if not raw:
        return ""
    if isinstance(raw, str):
        raw = raw.encode("utf-8", "ignore")
    hits = _MEDIA_HEX.findall(bytes(raw))
    return hits[-1].decode().lower() if hits else ""


def _clip(text: str, n: int) -> str:
    """超长截断时补省略号，别让人以为原文就断在那儿。"""
    text = text or ""
    return text if len(text) <= n else text[:n].rstrip() + "…"


def _xml_val(xml_text: str, tag: str) -> str:
    if not xml_text:
        return ""
    m = re.search(rf"<{tag}>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{tag}>", xml_text, re.S | re.I)
    if not m:
        return ""
    val = m.group(1).strip()
    return (
        val.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#x0A;", "\n")
        .replace("&#x20;", " ")
        .strip()
    )


def _xml_attr(xml_text: str, tag: str, attr: str) -> str:
    if not xml_text:
        return ""
    m = re.search(rf"<{tag}\b[^>]*\b{attr}=[\"']([^\"']*)[\"']", xml_text, re.S | re.I)
    return m.group(1).strip() if m else ""


def extract_rich_info(mtype: int, body: str, sub_type: int = 0) -> dict[str, Any]:
    """结构化提取富消息（链接、文件、引用回复、小程序、群公告、接龙、视频号等）元数据。"""
    body = body or ""
    if mtype != 49:
        if mtype == 1:
            urls = re.findall(r"https?://[^\s<>\"'）)】]+", body)
            if urls:
                return {"kind": "link", "title": _clip(body, 80), "url": urls[0], "urls": urls}
            if body.lstrip().startswith("#接龙"):
                return {"kind": "solitaire", "title": body.splitlines()[0].strip(), "content": body}
        return {"kind": _MSG_TYPES.get(mtype, f"type_{mtype}")}

    app_type_str = _xml_val(body, "type")
    sub = int(app_type_str) if app_type_str.isdigit() else (sub_type or 0)
    title = _xml_val(body, "title")
    des = _xml_val(body, "des") or _xml_val(body, "descr")
    url = _xml_val(body, "url")
    src_name = _xml_val(body, "sourcedisplayname") or _xml_val(body, "appname")

    if sub == 57:  # 引用回复
        ref_block = _xml_val(body, "refermsg")
        ref_user = _xml_val(ref_block, "displayname") or _xml_val(ref_block, "chatusr")
        ref_type_s = _xml_val(ref_block, "type")
        ref_type = int(ref_type_s) if ref_type_s.isdigit() else 1
        ref_content = _xml_val(ref_block, "content")
        if ref_type == 3 or "<img" in ref_content:
            ref_preview = "[图片]"
        elif ref_type == 49:
            ref_preview = _xml_val(ref_content, "title") or "[应用消息]"
        elif ref_type == 34:
            ref_preview = "[语音]"
        elif ref_type == 43:
            ref_preview = "[视频]"
        else:
            _, ref_body = _split_group_sender(ref_content)
            ref_preview = _clip(re.sub(r"\s+", " ", ref_body).strip(), 50)
        return {
            "kind": "quote",
            "sub_type": 57,
            "reply_text": title,
            "ref_sender": ref_user,
            "ref_preview": ref_preview,
        }
    if sub in (6, 74) or re.search(r"\.(?:docx?|xlsx?|pptx?|pdf|zip|rar|7z|csv|txt|wps|et|dps|md|html?)$", title, re.I):
        ext = _xml_val(body, "fileext")
        totallen = _xml_val(body, "totallen")
        return {
            "kind": "file",
            "sub_type": sub,
            "filename": title,
            "ext": ext or (os.path.splitext(title)[1].lstrip(".").lower() if "." in title else ""),
            "size": int(totallen) if totallen.isdigit() else 0,
        }
    if sub == 87 or "<announcement>" in body or "<textannouncement>" in body:
        ann = _xml_val(body, "textannouncement") or _xml_val(body, "datadesc") or des or title
        return {"kind": "announcement", "sub_type": 87, "content": ann}
    if sub == 53 or title.lstrip().startswith("#接龙"):
        return {"kind": "solitaire", "sub_type": 53, "title": title, "des": des}
    if sub in (33, 36):
        return {"kind": "miniprogram", "sub_type": sub, "title": title, "source": src_name or des, "url": url}
    if sub == 19:
        return {"kind": "chat_record", "sub_type": 19, "title": title, "des": des}
    if sub == 51 or "<finderFeed>" in body:
        ff = _xml_val(body, "finderFeed")
        nick = _xml_val(ff, "nickname")
        fdesc = _xml_val(ff, "desc")
        return {"kind": "finder", "sub_type": 51, "nickname": nick, "desc": fdesc}
    if sub in (4, 5) or (url and not url.startswith("https://support.weixin.qq.com")):
        return {"kind": "link", "sub_type": sub, "title": title, "des": des, "url": url, "source": src_name}
    if sub == 62:
        return {"kind": "pat", "sub_type": 62, "title": title}
    if sub in (2000, 2001):
        return {"kind": "transfer" if sub == 2000 else "redpacket", "sub_type": sub, "title": title, "des": des}
    return {"kind": "app", "sub_type": sub, "title": title, "des": des, "url": url}


def _summarize(mtype: int, body: str, sub_type: int = 0) -> str:
    """把非文本消息压成一句高信息量可读摘要，避免丢失引用、链接 URL、群公告和文件名。"""
    if mtype == 1:
        body = body or ""
        if len(body) > MAX_TEXT:
            return body[:MAX_TEXT] + f"…（共 {len(body)} 字，已截断）"
        return body
    if mtype == 34:
        m = re.search(r'voicelength="(\d+)"', body or "")
        extra = f" {max(1, int(m.group(1)) // 1000)}s" if m else ""
        return f"[语音{extra}]"
    if mtype == 48:
        poi = _xml_attr(body, "location", "poiname")
        label = _xml_attr(body, "location", "label")
        loc = " · ".join(x for x in (poi, label) if x and x != "位置")
        return f"[位置] {loc}" if loc else "[位置]"
    if mtype == 42:
        nick = _xml_attr(body, "msg", "nickname")
        alias = _xml_attr(body, "msg", "alias") or _xml_attr(body, "msg", "username")
        return f"[名片] {nick} ({alias})" if nick else "[名片]"
    simple = {3: "[图片]", 43: "[视频]", 47: "[表情]", 50: "[通话]"}
    if mtype in simple:
        if mtype == 50:
            vmsg = _xml_val(body, "msg")
            return f"[通话] {vmsg}" if vmsg else "[通话]"
        return simple[mtype]
    if mtype == 49:
        info = extract_rich_info(mtype, body, sub_type)
        kind = info.get("kind")
        if kind == "quote":
            ref_u = info.get("ref_sender") or "群友"
            ref_p = info.get("ref_preview") or ""
            rep = info.get("reply_text") or ""
            return f"[回复 {ref_u}: {ref_p}] {rep}" if ref_p else f"[回复 {ref_u}] {rep}"
        if kind == "file":
            sz = info.get("size") or 0
            sz_s = f" ({round(sz / 1024, 1)}KB)" if sz > 0 else ""
            return f"[文件] {info.get('filename', '')}{sz_s}"
        if kind == "announcement":
            return f"[群公告] {_clip(info.get('content', ''), 240)}"
        if kind == "solitaire":
            return f"[群接龙] {_clip(info.get('title', ''), 240)}"
        if kind == "miniprogram":
            src = info.get("source") or "小程序"
            return f"[小程序·{src}] {info.get('title', '')}"
        if kind == "chat_record":
            des_s = re.sub(r"\s+", " ", info.get("des") or "").strip()
            return f"[转发聊天记录] {info.get('title', '')}：{_clip(des_s, 140)}" if des_s else f"[转发聊天记录] {info.get('title', '')}"
        if kind == "finder":
            nick = info.get("nickname") or "视频号"
            return f"[视频号·{nick}] {_clip(info.get('desc', ''), 120)}"
        if kind == "pat":
            return f"[拍一拍] {info.get('title', '')}"
        if kind in ("transfer", "redpacket"):
            label = "转账" if kind == "transfer" else "红包"
            bits = [x for x in (info.get("title"), info.get("des")) if x]
            return f"[{label}] " + " | ".join(bits)[:120]
        if kind == "link":
            t = info.get("title") or ""
            d = re.sub(r"\s+", " ", info.get("des") or "").strip()
            u = info.get("url") or ""
            head = " — ".join(x for x in (t, _clip(d, 80)) if x)
            return f"[链接] {head} ({u})" if u else f"[链接] {head}"
        bits = [x for x in (info.get("title"), info.get("des")) if x]
        return "[链接/应用] " + " | ".join(bits)[:160] if bits else "[应用消息]"
    if mtype in (10000, 10002):
        raw = body or ""
        inner = ""
        for tag in ("content", "replacemsg", "template"):
            m = re.search(rf"<{tag}>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{tag}>", raw, re.S)
            if m and m.group(1).strip():
                inner = m.group(1).strip()
                break
        if inner and "$" in inner:
            for lm in re.finditer(r'<link\s+name="([^"]+)"[^>]*>(.*?)</link>', raw, re.S):
                var_name = lm.group(1)
                lbody = lm.group(2)
                nicks = re.findall(r"<(?:nickname|name)>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</(?:nickname|name)>", lbody, re.S)
                if nicks:
                    inner = inner.replace(f"${var_name}$", "、".join(n.strip() for n in nicks if n.strip()))
        text = re.sub(r"<[^>]+>", "", inner if inner else raw).strip()
        return f"[系统] {text[:140]}" if text else "[系统消息]"
    return (body or "")[:160]


def _fmt(ts: float | int | None) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""


_REL_DAYS = {"今天": 0, "今日": 0, "昨天": 1, "昨日": 1, "前天": 2, "大前天": 3}


def _parse_time(text: str, end: bool = False) -> int:
    """把时间说法转成时间戳；认不出来返回 0（= 不加这个过滤条件）。

    支持：今天/昨天/前天/本周/上周/本月/上月/今年/近N天/最近N天/近N小时/近N周/11月/2026-11 等。
    """
    text = (text or "").strip()
    if not text:
        return 0
    if text.isdigit() and len(text) >= 10:
        return int(text)

    now = datetime.now()
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_day = (23, 59, 59)

    def day(offset: int) -> int:
        d = day0 - timedelta(days=offset)
        if end:
            d = d.replace(hour=end_of_day[0], minute=end_of_day[1], second=end_of_day[2])
        return int(d.timestamp())

    if text in _REL_DAYS:
        return day(_REL_DAYS[text])

    m = re.fullmatch(r"(?:近|最近|过去)\s*(\d+)\s*(?:天|日)", text)
    if m:
        n = max(1, int(m.group(1)))
        return day(0) if end else day(n)
    m = re.fullmatch(r"(?:近|最近|过去)\s*(\d+)\s*(?:小时|h)", text, re.I)
    if m:
        n = max(1, int(m.group(1)))
        return int((now - timedelta(hours=0 if end else n)).timestamp())
    m = re.fullmatch(r"(?:近|最近|过去)\s*(\d+)\s*(?:周|星期)", text)
    if m:
        n = max(1, int(m.group(1))) * 7
        return day(0) if end else day(n)
    m = re.fullmatch(r"(?:近|最近|过去)\s*(\d+)\s*个?月", text)
    if m:
        n = max(1, int(m.group(1))) * 30
        return day(0) if end else day(n)
    if text in ("本周", "这周", "本星期", "这星期"):
        start_d = day0 - timedelta(days=now.weekday())
        if end:
            return int((start_d + timedelta(days=6)).replace(
                hour=23, minute=59, second=59).timestamp())
        return int(start_d.timestamp())
    if text in ("上周", "上星期"):
        start_d = day0 - timedelta(days=now.weekday() + 7)
        if end:
            return int((start_d + timedelta(days=6)).replace(
                hour=23, minute=59, second=59).timestamp())
        return int(start_d.timestamp())
    if text in ("本月", "这个月"):
        start_d = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if end:
            nxt = (start_d + timedelta(days=32)).replace(day=1)
            return int((nxt - timedelta(seconds=1)).timestamp())
        return int(start_d.timestamp())
    if text in ("上月", "上个月"):
        first = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        prev_end = first - timedelta(seconds=1)
        start_d = prev_end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return int(prev_end.timestamp()) if end else int(start_d.timestamp())
    if text in ("今年", "本年"):
        start_d = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        if end:
            return int(now.replace(hour=23, minute=59, second=59).timestamp())
        return int(start_d.timestamp())

    m_ym = re.fullmatch(r"(?:(\d{4})\s*[-/年]\s*)?(\d{1,2})\s*月(?:份)?", text) or re.fullmatch(r"(\d{4})-(\d{1,2})", text)
    if m_ym:
        yr = int(m_ym.group(1)) if m_ym.group(1) else now.year
        mo = int(m_ym.group(2))
        if 1 <= mo <= 12:
            start_d = datetime(yr, mo, 1, 0, 0, 0)
            if end:
                nxt = (start_d + timedelta(days=32)).replace(day=1)
                return int((nxt - timedelta(seconds=1)).timestamp())
            return int(start_d.timestamp())

    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d",
                "%Y年%m月%d日", "%Y年%m月%d日%H时%M分", "%m-%d", "%m月%d日"):
        try:
            dt = datetime.strptime(text, fmt)
        except ValueError:
            continue
        if fmt in ("%m-%d", "%m月%d日"):
            dt = dt.replace(year=now.year)
        if end and fmt not in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M",
                               "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            dt = dt.replace(hour=23, minute=59, second=59)
        return int(dt.timestamp())
    return 0


def resolve_chat(chat: str, names: dict | None = None) -> dict | None:
    """按 wxid / 群 id / 备注 / 昵称 / 摘要名 找到会话（优先最近会话，回退全量通讯录）。"""
    chat = (chat or "").strip()
    if not chat:
        return None
    names = names if names is not None else _name_map()
    sess_list = sessions(limit=500, names=names)
    for s in sess_list:
        if chat == s["username"] or chat == s["name"]:
            return s
    low = chat.lower()
    for s in sess_list:
        if low in (s["name"] or "").lower() or low in s["username"].lower():
            return s
    for u, disp in names.items():
        if chat == u or chat == disp:
            return {"username": u, "name": disp, "is_group": u.endswith("@chatroom"),
                    "unread": 0, "summary": "", "last_time": "", "last_ts": 0}
    for u, disp in names.items():
        if low in (disp or "").lower() or low in u.lower():
            return {"username": u, "name": disp, "is_group": u.endswith("@chatroom"),
                    "unread": 0, "summary": "", "last_time": "", "last_ts": 0}
    return None


def messages(
    chat: str,
    limit: int = 40,
    since: str = "",
    until: str = "",
    account: str = "",
    names: dict | None = None,
) -> dict:
    sess = resolve_chat(chat, names)
    if not sess:
        return {"ok": False, "error": f"找不到会话: {chat}"}
    md5 = hashlib.md5(sess["username"].encode()).hexdigest()
    t_since, t_until = _parse_time(since), _parse_time(until, end=True)
    names = names if names is not None else _name_map()

    for rel in ("message/message_1.db", "message/message_0.db"):
        path = os.path.join(cache_dir(), rel.replace("/", os.sep))
        if not os.path.exists(path):
            continue
        con = _conn(rel)
        try:
            tabs = _msg_tables(con)
            if md5 not in tabs:
                continue
            table = tabs[md5]
            cols = [c[1] for c in con.execute(f"pragma table_info('{table}')")]
            smap = _sender_map(con)
            where, args = [], []
            if t_since:
                where.append("create_time >= ?")
                args.append(t_since)
            if t_until:
                where.append("create_time <= ?")
                args.append(t_until)
            sql = f"select * from '{table}'"
            if where:
                sql += " where " + " and ".join(where)
            sql += " order by create_time desc limit ?"
            args.append(max(1, int(limit)))
            rows = con.execute(sql, args).fetchall()
            out = []
            for r in rows:
                text = _decode(r, cols)
                sender_wxid, body = _split_group_sender(text)
                if not sender_wxid:
                    sender_wxid = smap.get(r["real_sender_id"], "")
                lt = r["local_type"] or 0
                mtype = lt & 0xFFFFFFFF
                sub_type = lt >> 32
                out.append({
                    "time": _fmt(r["create_time"]),
                    "ts": r["create_time"],
                    "sender": display(sender_wxid, names) or ("系统" if mtype in (10000, 10002) else ""),
                    "sender_wxid": sender_wxid,
                    "type": mtype,
                    "sub_type": sub_type,
                    "type_name": _MSG_TYPES.get(lt, _MSG_TYPES.get(mtype, f"类型{mtype}")),
                    "text": _summarize(mtype, body, sub_type),
                    "raw": body if mtype in (1, 49) else "",
                    "media_ref": _media_ref(r, cols) if mtype in (3, 43, 47) else "",
                    "local_id": r["local_id"],
                })
            out.reverse()
            return {"ok": True, "chat": sess, "db": rel, "count": len(out), "messages": out}
        finally:
            con.close()
    return {"ok": False, "error": f"没有找到该会话的消息表（md5={md5}）", "chat": sess}


_TYPE_FILTER_ALIASES = {
    "text": ("text", "文本", "文字"),
    "file": ("file", "文件", "附件", "文档"),
    "link": ("link", "链接", "网址", "网页", "文章", "公众号", "网盘"),
    "image": ("image", "图片", "照片", "截图"),
    "voice": ("voice", "语音", "录音"),
    "video": ("video", "视频"),
    "quote": ("quote", "引用", "回复"),
    "solitaire": ("solitaire", "接龙", "群接龙"),
    "announcement": ("announcement", "公告", "群公告"),
    "miniprogram": ("miniprogram", "小程序", "云文档", "金山文档", "腾讯文档"),
    "system": ("system", "系统", "系统消息", "撤回", "拍一拍"),
}


def _normalize_msg_type(msg_type: str) -> str:
    raw = (msg_type or "").strip().lower()
    if not raw:
        return ""
    for canonical, aliases in _TYPE_FILTER_ALIASES.items():
        if raw in aliases:
            return canonical
    return raw


def search(
    keyword: str = "",
    chat: str = "",
    limit: int = 50,
    since: str = "",
    until: str = "",
    sender: str = "",
    msg_type: str = "",
    account: str = "",
    names: dict | None = None,
) -> dict:
    """全库或指定会话内多维检索（支持 keyword + sender 发言人 + msg_type 消息类型组合）。"""
    kw = (keyword or "").strip().lower()
    sender_q = (sender or "").strip().lower()
    mtype_q = _normalize_msg_type(msg_type)
    if not kw and not sender_q and not mtype_q:
        return {"ok": False, "error": "keyword、sender（发言人）、msg_type（消息类型）至少需提供一个"}

    names = names if names is not None else _name_map()
    t_since = _parse_time(since)
    t_until = _parse_time(until, end=True)
    targets: list[tuple[str, str, dict]] = []

    sess_list = sessions(limit=500, names=names)
    md5_to_user = {hashlib.md5(u.encode()).hexdigest(): u for u in names}
    for s in sess_list:
        md5_to_user[hashlib.md5(s["username"].encode()).hexdigest()] = s["username"]

    if chat:
        one = resolve_chat(chat, names)
        if not one:
            return {"ok": False, "error": f"找不到会话: {chat}"}
        md5_to_user = {hashlib.md5(one["username"].encode()).hexdigest(): one["username"]}

    matched_sender_wxids: set[str] = set()
    if sender_q:
        for u, disp in names.items():
            if sender_q in (disp or "").lower() or sender_q in u.lower():
                matched_sender_wxids.add(u)

    for rel in ("message/message_1.db", "message/message_0.db"):
        path = os.path.join(cache_dir(), rel.replace("/", os.sep))
        if not os.path.exists(path):
            continue
        con = _conn(rel)
        try:
            smap = _sender_map(con)
            for md5, table in _msg_tables(con).items():
                if md5 not in md5_to_user:
                    continue
                cols = [c[1] for c in con.execute(f"pragma table_info('{table}')")]
                where: list[str] = []
                args: list[Any] = []
                if t_since:
                    where.append("create_time >= ?")
                    args.append(t_since)
                if t_until:
                    where.append("create_time <= ?")
                    args.append(t_until)
                if mtype_q == "image":
                    where.append("(local_type & 0xFFFFFFFF) = 3")
                elif mtype_q == "voice":
                    where.append("(local_type & 0xFFFFFFFF) = 34")
                elif mtype_q == "video":
                    where.append("(local_type & 0xFFFFFFFF) = 43")
                elif mtype_q == "text":
                    where.append("(local_type & 0xFFFFFFFF) = 1")
                elif mtype_q in ("file", "link", "quote", "miniprogram", "announcement"):
                    where.append("((local_type & 0xFFFFFFFF) = 49 OR (local_type & 0xFFFFFFFF) = 1)")

                sql = f"select * from '{table}'"
                if where:
                    sql += " where " + " and ".join(where)
                sql += " order by create_time desc limit 4000"

                for r in con.execute(sql, args):
                    lt = r["local_type"] or 0
                    mtype = lt & 0xFFFFFFFF
                    sub_type = lt >> 32
                    text = _decode(r, cols)
                    sender_wxid, body = _split_group_sender(text)
                    if not sender_wxid:
                        sender_wxid = smap.get(r["real_sender_id"], "")
                    sender_disp = display(sender_wxid, names) or ("系统" if mtype in (10000, 10002) else "")

                    if sender_q:
                        if (sender_wxid not in matched_sender_wxids
                                and sender_q not in sender_disp.lower()
                                and sender_q not in sender_wxid.lower()):
                            continue

                    if mtype_q:
                        info = extract_rich_info(mtype, body, sub_type)
                        kind = info.get("kind", "")
                        if mtype_q == "file" and kind != "file":
                            continue
                        if mtype_q == "link" and kind != "link":
                            continue
                        if mtype_q == "quote" and kind != "quote":
                            continue
                        if mtype_q == "solitaire" and kind != "solitaire":
                            continue
                        if mtype_q == "announcement" and kind != "announcement":
                            continue
                        if mtype_q == "miniprogram" and kind != "miniprogram":
                            continue
                        if mtype_q == "system" and mtype not in (10000, 10002) and kind != "pat":
                            continue

                    summary_text = _summarize(mtype, body, sub_type)
                    if kw and kw not in text.lower() and kw not in summary_text.lower():
                        continue

                    targets.append((rel, md5_to_user[md5], {
                        "time": _fmt(r["create_time"]),
                        "ts": r["create_time"],
                        "chat": display(md5_to_user[md5], names),
                        "username": md5_to_user[md5],
                        "sender": sender_disp,
                        "sender_wxid": sender_wxid,
                        "type": mtype,
                        "sub_type": sub_type,
                        "text": _clip(summary_text, 600),
                    }))
        finally:
            con.close()

    targets.sort(key=lambda x: x[2]["ts"], reverse=True)
    hits = [t[2] for t in targets[: max(1, int(limit))]]
    return {
        "ok": True,
        "keyword": keyword,
        "sender": sender or None,
        "msg_type": mtype_q or None,
        "chat": chat or None,
        "hits": len(hits),
        "results": hits,
    }


def contacts_and_rooms(
    mode: str = "contacts",
    keyword: str = "",
    chat: str = "",
    limit: int = 60,
    names: dict | None = None,
) -> dict[str, Any]:
    """查询微信通讯录、群详情（群主/群公告/完整群成员）、全量群公告或多人共同群。"""
    names = names if names is not None else _name_map()
    con = _conn("contact/contact.db")
    try:
        n2i = {r[0]: r[1] for r in con.execute("SELECT rowid, username FROM name2id")}
        room_rows = con.execute("SELECT id, username, owner FROM chat_room").fetchall()
        room_info = {
            r["username_"]: {
                "announcement": (r["announcement_"] or "").strip(),
                "editor": r["announcement_editor_"] or "",
                "publish_time": _fmt(r["announcement_publish_time_"]),
                "publish_ts": int(r["announcement_publish_time_"] or 0),
            }
            for r in con.execute(
                "SELECT username_, announcement_, announcement_editor_, announcement_publish_time_ "
                "FROM chat_room_info_detail"
            )
        }
        room_members: dict[str, list[str]] = {}
        member_rooms: dict[str, list[str]] = {}
        room_owner: dict[str, str] = {}
        id_to_room = {r["id"]: r["username"] for r in room_rows}
        for r in room_rows:
            room_owner[r["username"]] = r["owner"] or ""
            room_members[r["username"]] = []
        for rm in con.execute("SELECT room_id, member_id FROM chatroom_member"):
            ru = id_to_room.get(rm["room_id"])
            mu = n2i.get(rm["member_id"])
            if not ru or not mu:
                continue
            room_members.setdefault(ru, []).append(mu)
            member_rooms.setdefault(mu, []).append(ru)

        mode = (mode or "").strip().lower()
        if not mode or mode == "auto":
            mode = "group" if chat else "contacts"

        if mode == "announcements":
            kw = (keyword or "").strip().lower()
            anns = []
            for ru, info in room_info.items():
                ann_text = info["announcement"]
                if not ann_text:
                    continue
                rname = display(ru, names)
                if kw and kw not in ann_text.lower() and kw not in rname.lower():
                    continue
                anns.append({
                    "chat": rname,
                    "username": ru,
                    "owner": display(room_owner.get(ru, ""), names),
                    "editor": display(info["editor"], names) if info["editor"] else "",
                    "publish_time": info["publish_time"],
                    "publish_ts": info["publish_ts"],
                    "member_count": len(room_members.get(ru, [])),
                    "announcement": ann_text,
                })
            anns.sort(key=lambda x: x["publish_ts"], reverse=True)
            return {
                "ok": True,
                "mode": "announcements",
                "count": len(anns[: max(1, int(limit))]),
                "total_with_announcement": len(anns),
                "announcements": anns[: max(1, int(limit))],
            }

        if mode == "group":
            target_q = (chat or keyword or "").strip()
            if not target_q:
                return {"ok": False, "error": "mode='group' 需要提供 chat（群名或群 id）"}
            sess = resolve_chat(target_q, names)
            ru = sess["username"] if sess else target_q
            if not ru.endswith("@chatroom"):
                low = target_q.lower()
                cand = next(
                    (u for u in room_members if low in display(u, names).lower() or low in u.lower()),
                    "",
                )
                if not cand:
                    return {"ok": False, "error": f"未找到微信群: {target_q}"}
                ru = cand

            contact_map = {
                r["username"]: r
                for r in con.execute(
                    "SELECT username, alias, remark, nick_name, description FROM contact"
                )
            }
            m_wxids = room_members.get(ru, [])
            members_out = []
            kw = (keyword if chat else "").strip().lower()
            for mu in m_wxids:
                cr = contact_map.get(mu)
                disp = display(mu, names)
                remark = (cr["remark"] if cr else "") or ""
                nick = (cr["nick_name"] if cr else "") or ""
                alias = (cr["alias"] if cr else "") or ""
                if kw and not any(kw in x.lower() for x in (disp, remark, nick, alias, mu)):
                    continue
                members_out.append({
                    "name": disp,
                    "remark": remark,
                    "nick_name": nick,
                    "alias": alias,
                    "wxid": mu,
                    "is_owner": mu == room_owner.get(ru, ""),
                })
            rinfo = room_info.get(ru, {})
            return {
                "ok": True,
                "mode": "group",
                "chat": display(ru, names),
                "username": ru,
                "owner": display(room_owner.get(ru, ""), names),
                "owner_wxid": room_owner.get(ru, ""),
                "member_count": len(m_wxids),
                "returned_members": len(members_out[: max(1, int(limit))]),
                "announcement": rinfo.get("announcement", ""),
                "announcement_time": rinfo.get("publish_time", ""),
                "members": members_out[: max(1, int(limit))],
            }

        if mode == "common_groups":
            tokens = [x.strip() for x in re.split(r"[,，;；\s和与跟]+", keyword or chat) if x.strip()]
            if not tokens:
                return {"ok": False, "error": "mode='common_groups' 需要提供至少 1 个联系人名称（keyword）"}
            person_groups: list[dict[str, Any]] = []
            common_set: set[str] | None = None
            for tok in tokens:
                low = tok.lower()
                matched_u = [
                    u for u, d in names.items()
                    if not u.endswith("@chatroom") and (low in (d or "").lower() or low in u.lower())
                ]
                rooms_for_tok: set[str] = set()
                for u in matched_u:
                    rooms_for_tok.update(member_rooms.get(u, []))
                person_groups.append({
                    "query": tok,
                    "matched_contacts": [{"name": display(u, names), "wxid": u} for u in matched_u[:10]],
                    "group_count": len(rooms_for_tok),
                })
                common_set = rooms_for_tok if common_set is None else (common_set & rooms_for_tok)
            common_list = [
                {
                    "chat": display(ru, names),
                    "username": ru,
                    "member_count": len(room_members.get(ru, [])),
                    "owner": display(room_owner.get(ru, ""), names),
                }
                for ru in sorted(common_set or set())
            ]
            return {
                "ok": True,
                "mode": "common_groups",
                "people": person_groups,
                "common_count": len(common_list),
                "common_groups": common_list[: max(1, int(limit))],
            }

        kw = (keyword or chat or "").strip().lower()
        rows = con.execute(
            "SELECT username, alias, remark, remark_quan_pin, nick_name, quan_pin, description "
            "FROM contact WHERE delete_flag = 0"
        ).fetchall()
        contacts_out = []
        for r in rows:
            u = r["username"] or ""
            if not u or u.startswith("gh_") or u in ("filehelper", "weixin") or "@placeholder" in u:
                continue
            if u.endswith("@chatroom"):
                continue
            remark = r["remark"] or ""
            nick = r["nick_name"] or ""
            alias = r["alias"] or ""
            desc = r["description"] or ""
            qp = (r["remark_quan_pin"] or "") + " " + (r["quan_pin"] or "")
            if kw and not any(kw in x.lower() for x in (remark, nick, alias, u, desc, qp)):
                continue
            in_r = [display(ru, names) for ru in member_rooms.get(u, [])]
            contacts_out.append({
                "name": remark or nick or alias or u,
                "remark": remark,
                "nick_name": nick,
                "alias": alias,
                "wxid": u,
                "description": desc,
                "group_count": len(in_r),
                "in_groups": in_r[:15],
            })
        contacts_out.sort(key=lambda x: (bool(x["remark"]), x["group_count"]), reverse=True)
        return {
            "ok": True,
            "mode": "contacts",
            "keyword": keyword or None,
            "matched": len(contacts_out),
            "returned": len(contacts_out[: max(1, int(limit))]),
            "contacts": contacts_out[: max(1, int(limit))],
        }
    finally:
        con.close()


def solitaires(
    chat: str = "",
    keyword: str = "",
    since: str = "",
    roster_names: list[str] | None = None,
    check_missing: bool = True,
    limit: int = 20,
    account: str = "",
    names: dict | None = None,
) -> dict[str, Any]:
    """读取微信群接龙数据库（solitaire.db），解析接龙明细并自动比对未接龙名单。"""
    acc = account or account_dir()
    if acc:
        ensure_cache(acc, need=NEED_DBS + ["solitaire/solitaire.db"])
    sol_path = os.path.join(cache_dir(), "solitaire", "solitaire.db")
    if not os.path.isfile(sol_path):
        return {"ok": False, "error": "未找到群接龙缓存库 solitaire/solitaire.db"}

    names = names if names is not None else _name_map()
    md5_to_user = {hashlib.md5(u.encode()).hexdigest(): u for u in names}
    for s in sessions(limit=500, names=names):
        md5_to_user[hashlib.md5(s["username"].encode()).hexdigest()] = s["username"]

    target_md5 = ""
    if chat:
        one = resolve_chat(chat, names)
        if one:
            target_md5 = hashlib.md5(one["username"].encode()).hexdigest()

    t_since = _parse_time(since)
    kw = (keyword or "").strip().lower()

    room_member_map: dict[str, list[str]] = {}
    try:
        ccon = _conn("contact/contact.db")
        try:
            n2i = {r[0]: r[1] for r in ccon.execute("SELECT rowid, username FROM name2id")}
            id_to_room = {r[0]: r[1] for r in ccon.execute("SELECT id, username FROM chat_room")}
            for rm in ccon.execute("SELECT room_id, member_id FROM chatroom_member"):
                ru = id_to_room.get(rm[0])
                mu = n2i.get(rm[1])
                if ru and mu:
                    room_member_map.setdefault(ru, []).append(mu)
        finally:
            ccon.close()
    except Exception:
        pass

    con = sqlite3.connect(f"file:{sol_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        tabs = [
            r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Solitaire_%'"
            )
            if not r[0].startswith(("SolitaireValid_", "SolitaireFold_"))
        ]
        items_out: list[dict[str, Any]] = []
        for tab in tabs:
            md5 = tab.split("_", 1)[1]
            if target_md5 and md5 != target_md5:
                continue
            ru = md5_to_user.get(md5, md5)
            chat_disp = display(ru, names)
            if chat and not target_md5 and chat.lower() not in chat_disp.lower():
                continue
            for r in con.execute(f"SELECT * FROM '{tab}'"):
                header = (r["header"] or "").strip()
                content = (r["content"] or "").strip()
                sol_xml = r["solitaire_string"] or ""
                active_ts = int(r["active_time"] or 0)
                if t_since and active_ts < t_since:
                    continue
                if kw and kw not in content.lower() and kw not in header.lower() and kw not in chat_disp.lower():
                    continue
                author_wxid = _xml_val(sol_xml, "au")
                entries = []
                participant_wxids: set[str] = set()
                for idx, im in enumerate(re.finditer(r"<i>(.*?)</i>", sol_xml, re.S), 1):
                    iblock = im.group(1)
                    u = _xml_val(iblock, "u")
                    t_s = _xml_val(iblock, "t")
                    c_text = _xml_val(iblock, "c")
                    if u:
                        participant_wxids.add(u)
                    entries.append({
                        "seq": idx,
                        "sender": display(u, names),
                        "wxid": u,
                        "time": _fmt(int(t_s)) if t_s.isdigit() else "",
                        "text": c_text,
                    })

                item_obj: dict[str, Any] = {
                    "chat": chat_disp,
                    "username": ru,
                    "author": display(author_wxid, names),
                    "author_wxid": author_wxid,
                    "active_time": _fmt(active_ts),
                    "active_ts": active_ts,
                    "header": header,
                    "entry_count": len(entries),
                    "entries": entries,
                    "raw_content": content,
                }
                if roster_names:
                    joined_blob = " ".join(
                        f"{e['sender']} {e['text']}" for e in entries
                    ) + " " + content
                    sub_list = [n for n in roster_names if n in joined_blob]
                    miss_list = [n for n in roster_names if n not in joined_blob]
                    item_obj["audit_basis"] = "roster"
                    item_obj["submitted"] = sub_list
                    item_obj["missing"] = miss_list
                    if miss_list:
                        item_obj["suggested_reminder"] = (
                            f"【群接龙催办提醒】截至目前，以下单位/同志尚未完成接龙：@"
                            f"{' @'.join(miss_list[:25])} ，请抓紧接龙反馈！"
                        )
                elif check_missing and ru in room_member_map:
                    all_m = room_member_map[ru]
                    miss_wxids = [u for u in all_m if u not in participant_wxids]
                    miss_names = [display(u, names) for u in miss_wxids]
                    item_obj["audit_basis"] = "group_members"
                    item_obj["group_member_count"] = len(all_m)
                    item_obj["submitted_count"] = len(participant_wxids)
                    item_obj["missing_count"] = len(miss_names)
                    item_obj["missing_sample"] = miss_names[:30]
                items_out.append(item_obj)

        items_out.sort(key=lambda x: x["active_ts"], reverse=True)
        return {
            "ok": True,
            "count": len(items_out[: max(1, int(limit))]),
            "solitaires": items_out[: max(1, int(limit))],
        }
    finally:
        con.close()


_FAV_TYPES = {
    1: "笔记/文本",
    2: "图片",
    3: "语音",
    4: "视频",
    5: "网页/链接",
    6: "位置",
    8: "文件",
    14: "聊天记录",
    16: "笔记卡片",
    18: "长笔记",
}


def favorites(
    keyword: str = "",
    fav_type: str = "all",
    since: str = "",
    limit: int = 50,
    account: str = "",
    names: dict | None = None,
) -> dict[str, Any]:
    """读取并检索微信收藏夹（favorite/favorite.db）：笔记、密码备忘、网页/网盘链接、收藏文件与聊天记录。"""
    acc = account or account_dir()
    if acc:
        ensure_cache(acc, need=NEED_DBS + ["favorite/favorite.db"])
    fav_path = os.path.join(cache_dir(), "favorite", "favorite.db")
    if not os.path.isfile(fav_path):
        return {"ok": False, "error": "未找到微信收藏缓存库 favorite/favorite.db"}

    names = names if names is not None else _name_map()
    t_since = _parse_time(since)
    kw = (keyword or "").strip().lower()
    ftype = (fav_type or "all").strip().lower()

    con = sqlite3.connect(f"file:{fav_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT local_id, type, update_time, fromusr, realchatname, content "
            "FROM fav_db_item ORDER BY update_time DESC"
        ).fetchall()
        out = []
        for r in rows:
            ts = int(r["update_time"] or 0)
            if t_since and ts < t_since:
                continue
            tp = int(r["type"] or 0)
            if ftype not in ("all", ""):
                if ftype in ("note", "text", "笔记", "文本") and tp not in (1, 16, 18):
                    continue
                if ftype in ("link", "url", "链接", "网页") and tp != 5:
                    continue
                if ftype in ("file", "文件") and tp != 8:
                    continue
                if ftype in ("record", "聊天记录") and tp != 14:
                    continue

            xml = r["content"] or ""
            title = _xml_val(xml, "title")
            desc = _xml_val(xml, "desc")
            datadescs = [
                m.group(1).replace("&#x0A;", "\n").strip()
                for m in re.finditer(r"<datadesc>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</datadesc>", xml, re.S)
                if m.group(1).strip()
            ]
            src_id = _xml_attr(xml, "source", "sourceid")
            urls = re.findall(r"https?://[^\s<>\"'\u4e00-\u9fa5]+", f"{src_id}\n{desc}\n{xml}")
            clean_urls = [
                u for u in dict.fromkeys(urls)
                if not any(x in u for x in ("wx.qlogo.cn", "wxapp.tc.qq.com", "dldir1v6.qq.com"))
            ]
            body_text = "\n".join(x for x in [title, desc, *datadescs[:5]] if x).strip()
            from_disp = display(r["fromusr"] or "", names)
            sender_disp = display(r["realchatname"] or "", names)

            if kw and not any(kw in x.lower() for x in (body_text, " ".join(clean_urls), from_disp, sender_disp)):
                continue

            out.append({
                "local_id": r["local_id"],
                "type": tp,
                "type_name": _FAV_TYPES.get(tp, f"类型{tp}"),
                "time": _fmt(ts),
                "ts": ts,
                "from_chat": from_disp,
                "sender": sender_disp,
                "title": title or _clip(desc or (datadescs[0] if datadescs else ""), 80),
                "text": _clip(body_text, 600),
                "urls": clean_urls[:5],
            })
            if len(out) >= max(1, int(limit)):
                break
        return {
            "ok": True,
            "keyword": keyword or None,
            "fav_type": ftype,
            "count": len(out),
            "favorites": out,
        }
    finally:
        con.close()


_ACTION_RE = re.compile(
    r"截止|截至|请于|前提交|前完成|前报送|前发|点前|下班前|今晚|明天.{0,8}(?:前|交|发|到|汇)|"
    r"@所有人|@全体成员|通知[:：]|关于.{0,30}的通知|收到请回复|请大家|务必|抓紧|报名|缴费|交费|接龙"
)
_DEADLINE_EXTRACT_RE = re.compile(
    r"\d{1,2}\s*月\s*\d{1,2}\s*日[^，。；\n]{0,16}"
    r"|\d{1,2}\s*[:：]\s*\d{2}\s*(?:前|之前|截止)"
    r"|(?:今天|明天|后天|今晚|本周[一二三四五六日天]?|周[一二三四五六日天])[^，。；\n]{0,14}(?:前|截止|点)?"
    r"|(?:截止|截至)[^，。；\n]{0,20}"
)
_MENTION_RE = re.compile(r"@([\u4e00-\u9fa5A-Za-z0-9_\-·]{1,20})")


def chat_insights(
    chat: str = "",
    days: int = 7,
    since: str = "",
    until: str = "",
    limit: int = 500,
    account: str = "",
    names: dict | None = None,
) -> dict[str, Any]:
    """对指定微信会话（或最近活跃群）做深度统计：活跃发言人、待办行动项、分享链接、文件与引用热点。"""
    names = names if names is not None else _name_map()
    if not since and days > 0:
        since = (datetime.now() - timedelta(days=max(1, int(days)))).strftime("%Y-%m-%d")

    target_chats: list[str] = []
    if chat:
        one = resolve_chat(chat, names)
        if not one:
            return {"ok": False, "error": f"找不到会话: {chat}"}
        target_chats = [one["username"]]
    else:
        sess_list = sessions(limit=25, names=names)
        target_chats = [s["username"] for s in sess_list[:12]]

    speaker_counts: dict[str, int] = {}
    type_counts: dict[str, int] = {}
    daily_counts: dict[str, int] = {}
    links: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    quotes: list[dict[str, Any]] = []
    action_items: list[dict[str, Any]] = []
    total_msgs = 0

    per_chat_limit = max(50, int(limit)) if len(target_chats) == 1 else max(60, int(limit) // max(1, len(target_chats)))

    for cu in target_chats:
        res = messages(cu, limit=per_chat_limit, since=since, until=until, names=names)
        if not res.get("ok"):
            continue
        cname = (res.get("chat") or {}).get("name") or display(cu, names)
        for m in res.get("messages") or []:
            total_msgs += 1
            sender = m.get("sender") or "未知"
            if sender != "系统":
                speaker_counts[sender] = speaker_counts.get(sender, 0) + 1
            tname = m.get("type_name") or "其他"
            type_counts[tname] = type_counts.get(tname, 0) + 1
            day_s = (m.get("time") or "")[:10]
            if day_s:
                daily_counts[day_s] = daily_counts.get(day_s, 0) + 1

            mtype = m.get("type", 1)
            sub_type = m.get("sub_type", 0)
            raw = m.get("raw") or ""
            text = m.get("text") or ""
            info = extract_rich_info(mtype, raw, sub_type) if mtype in (1, 49) else {}
            kind = info.get("kind", "")

            if kind in ("link", "miniprogram"):
                links.append({
                    "time": m.get("time"),
                    "chat": cname,
                    "sender": sender,
                    "kind": kind,
                    "title": info.get("title") or text,
                    "source": info.get("source") or "",
                    "url": info.get("url") or "",
                })
            elif kind == "file":
                files.append({
                    "time": m.get("time"),
                    "chat": cname,
                    "sender": sender,
                    "filename": info.get("filename") or text,
                    "ext": info.get("ext") or "",
                    "size": info.get("size") or 0,
                })
            elif kind == "quote":
                quotes.append({
                    "time": m.get("time"),
                    "chat": cname,
                    "sender": sender,
                    "ref_sender": info.get("ref_sender") or "",
                    "ref_preview": info.get("ref_preview") or "",
                    "reply": info.get("reply_text") or "",
                })

            if kind == "announcement" or _ACTION_RE.search(text):
                dl_m = _DEADLINE_EXTRACT_RE.search(text)
                mentions = _MENTION_RE.findall(text)
                action_items.append({
                    "time": m.get("time"),
                    "chat": cname,
                    "sender": sender,
                    "kind": "群公告" if kind == "announcement" else "待办/通知",
                    "deadline": dl_m.group(0).strip() if dl_m else "",
                    "mentions": list(dict.fromkeys(mentions))[:8],
                    "text": _clip(text, 300),
                })

    top_senders = [
        {"sender": k, "count": v, "ratio": round(v / max(1, total_msgs), 3)}
        for k, v in sorted(speaker_counts.items(), key=lambda x: -x[1])[:15]
    ]
    action_items.sort(key=lambda x: (bool(x["deadline"]), x["time"] or ""), reverse=True)
    links.reverse()
    files.reverse()
    quotes.reverse()

    return {
        "ok": True,
        "scope": display(target_chats[0], names) if len(target_chats) == 1 else f"最近活跃的 {len(target_chats)} 个会话",
        "since": since or "不限",
        "until": until or "当前",
        "total_messages": total_msgs,
        "active_speakers": len(speaker_counts),
        "top_senders": top_senders,
        "type_distribution": type_counts,
        "daily_activity": dict(sorted(daily_counts.items())),
        "action_items_count": len(action_items),
        "action_items": action_items[:30],
        "links_count": len(links),
        "links": links[:30],
        "files_count": len(files),
        "files": files[:30],
        "quotes_count": len(quotes),
        "quotes": quotes[:20],
    }
