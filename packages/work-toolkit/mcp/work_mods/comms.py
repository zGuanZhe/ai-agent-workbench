#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""work_mods/comms.py — 通讯模块（微信 + 企业微信，只读）。

归属：所有 `work_wechat_*` / `work_wecom_*` / `work_comms_*` 工具。
边界：只读本地消息库与收件目录，不发消息、不做桌面自动化发送。
升级本模块不需要动 office.py；跑 `python tools/regress.py --module comms` 单独验收。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from types import SimpleNamespace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from work_core import (  # noqa: F401
    ALLOWED_EXTS, DIR_ARCHIVE, DIR_BACKUP, DIR_HARVESTED, DIR_INBOX, DIR_MEDIA,
    DIR_OUTPUT, DIR_WECOM_HARVESTED, PENDING_FILE, ROOT, ROSTER, STATE_DIR,
    STATE_FILE, WORKERS, WORKSPACE, ToolBox, _dump, _ensure_dirs, _file_arg,
    _list_files, _load_sibling, _load_state, _parse_json, _resolve_path, _run,
    _port_open, _safe_name, _save_state, _unwrap_officecli, ledger,
)

TOOLS = ToolBox("comms")

MODULE = {
    "id": "comms",
    "name": "通讯",
    "version": "1.0.0",
    "scope": "微信 / 企业微信：会话、消息、搜索、收材料、催办、图片、归档",
    "entry": "mcp/work_comms_server.py",
    "docs": "skills/work-office-toolkit/references/wechat.md",
}

# ---- 通道依赖：读不到就让工具返回 ok:false 并说明原因，绝不抛异常 ----
sys.path.insert(0, str(ROOT / "wechat"))
try:  # 微信本地只读收件箱（明文文件目录，无需数据库密钥）
    import inbox as wx_inbox  # type: ignore  # noqa: E402
except Exception:  # pragma: no cover
    wx_inbox = None
try:  # 微信 4.x 数据库只读：取密钥 → 解密副本 → 查会话/消息
    import wxread  # type: ignore  # noqa: E402
    import wxdecrypt  # type: ignore  # noqa: E402
    import wxkey  # type: ignore  # noqa: E402
except Exception:  # pragma: no cover
    wxread = wxdecrypt = wxkey = None

try:  # 企业微信：从 WXWork.exe 内存取密钥 → wxSQLite3 按页解密 → 查会话/消息/文件
    _root_added = str(ROOT) not in sys.path
    if _root_added:
        sys.path.insert(0, str(ROOT))
    from wecom import wcread as wc_read  # type: ignore  # noqa: E402
    if _root_added and str(ROOT) in sys.path:
        sys.path.remove(str(ROOT))  # 只借它一次，别把项目根长期塞进搜索路径
except Exception:  # pragma: no cover
    wc_read = None

wx_media = _load_sibling("work_wx_media", "wechat/media.py")


def media_capability() -> Dict[str, Any]:
    """图片解密能力探测。work_status 与 work_wechat_media 共用这一个口径。"""
    if wx_media is None:
        return {"ok": False, "notes": ["wechat/media.py 未加载"]}
    try:
        return wx_media.capability(_wechat_account_dir())
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "notes": [f"媒体能力探测失败：{exc}"]}


def status_engines(cap: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """通讯侧的自查条目，供 work_status 拼总表；只按真实证据标 ready。"""
    cap = cap if cap is not None else media_capability()
    _mcp = ROOT / "mcp_servers"
    _wchat_entry = _mcp / "wechat-chat-mcp" / "entry.py"
    _wchat_py = _mcp / "wechat-chat-mcp" / ".venv" / "Scripts" / "python.exe"
    _wxread_py = ROOT / "wechat" / "wxread.py"
    acc = _wechat_account_dir()
    return [
        {"id": "wechat-chat-mcp", "name": "Haoea1024/wechat-chat-mcp", "path": str(_wchat_entry),
         "ready": _wchat_entry.exists() and _wchat_py.exists(), "file_exists": _wchat_entry.exists(),
         "bridge": "wechat_chat MCP (sync_and_Report / search / voice / media / documents)",
         "note": "开源微信 4.x 深度检索、语音转写(SILK+Whisper)、图片与文档分页阅读 MCP"},
        {"id": "wechat-sqlcipher-wal", "name": "微信 4.x SQLCipher + WAL + DPAPI 只读链路",
         "path": str(_wxread_py), "ready": bool(wxread and acc), "file_exists": _wxread_py.exists(),
         "bridge": "work_comms_notices / work_wechat_messages / work_wechat_search",
         "note": "支持 Config.Cipher 内存提钥、DPAPI 持久化缓存与 -wal 帧级实时补丁"},
        {"id": "media", "name": "微信图片解密（AES+XOR）",
         "path": (cap.get("account") or ""), "ready": bool(cap.get("ok"))},
    ]


# 微信 4.x 的图片引用就是 packed_info_data 里的 32 位 hex；先卡格式再进文件系统查找
_MEDIA_REF_OK = re.compile(r"^[0-9a-fA-F]{32}$")


# --------------------------------------------------------------------------- #
# 微信环境探测
# --------------------------------------------------------------------------- #
def _file_version(exe: Path) -> Optional[str]:
    try:
        import win32api  # type: ignore

        info = win32api.GetFileVersionInfo(str(exe), "\\")
        ms, ls = info["FileVersionMS"], info["FileVersionLS"]
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    except Exception:
        return None


def _find_wechat() -> Dict[str, Any]:
    candidates = [
        Path(r"C:\Program Files\Tencent\Weixin\Weixin.exe"),
        Path(r"C:\Program Files (x86)\Tencent\Weixin\Weixin.exe"),
        Path(r"C:\Program Files\Tencent\WeChat\WeChat.exe"),
        Path(r"C:\Program Files (x86)\Tencent\WeChat\WeChat.exe"),
    ]
    for exe in candidates:
        if exe.exists():
            return {"path": str(exe), "version": _file_version(exe), "exists": True}
    return {"path": None, "version": None, "exists": False}


def _wechat_running() -> bool:
    try:
        out = _run(["tasklist", "/FI", "IMAGENAME eq Weixin.exe"], timeout=15)
        return "weixin.exe" in out["stdout"].lower()
    except Exception:
        return False


def _wechat_account_dir() -> Optional[str]:
    """定位微信 4.x 账号数据目录（含 db_storage / msg 的那一层）。"""
    env = os.environ.get("WECHAT_ACCOUNT_DIR")
    if env and os.path.isdir(env):
        return env
    if wxread is not None:
        try:
            acc = wxread.account_dir()
            if acc and os.path.isdir(acc):
                return acc
        except Exception:
            pass
    for root in (r"D:\聊天记录\xwechat_files", os.path.expanduser(r"~\Documents\xwechat_files"),
                 os.path.expanduser(r"~\Documents\WeChat Files")):
        if not os.path.isdir(root):
            continue
        for name in os.listdir(root):
            cand = os.path.join(root, name)
            if os.path.isdir(os.path.join(cand, "msg", "file")) or os.path.isdir(
                os.path.join(cand, "db_storage")
            ):
                return cand
    return None


def _media_probe() -> Dict[str, Any]:
    if wx_media is None:
        return {"ok": False}
    try:
        return wx_media.capability(_wechat_account_dir())
    except Exception:  # noqa: BLE001
        return {"ok": False}


def _wechat_probe() -> Dict[str, Any]:
    info = _find_wechat()
    version = info.get("version") or ""
    account = _wechat_account_dir()
    inbox_ready = bool(wx_inbox and account and os.path.isdir(wx_inbox.file_root(account)))
    dpapi_cached = bool(wxread and os.path.isfile(wxread.dpapi_cache_path()))
    wal_active = False
    if account:
        for rel in ("message/message_1.db-wal", "session/session.db-wal"):
            wp = os.path.join(account, "db_storage", rel.replace("/", os.sep))
            if os.path.exists(wp) and os.path.getsize(wp) > 32:
                wal_active = True
                break
    return {
        "wechat_installed": info["exists"],
        "wechat_path": info.get("path"),
        "wechat_version": version or None,
        "wechat_running": _wechat_running(),
        "local_inbox_ready": inbox_ready,
        "local_inbox_account": account,
        "local_inbox_root": str(Path(wx_inbox.file_root(account))) if (wx_inbox and account) else None,
        "message_db_readable": bool(wxread and account),
        "message_db_cache": str(wxread.cache_dir()) if wxread else None,
        "dpapi_key_cached": dpapi_cached,
        "wal_active": wal_active,
        "message_db_note": (
            "消息库为 SQLCipher 4 加密，通过 Config.Cipher 内存提钥 + DPAPI 本地加密缓存 + "
            "WAL 帧级实时补丁解密到只读缓存（workspace/07_微信只读缓存）。"
            "work_wechat_sessions / messages / search 会自动按需增量刷新缓存，无需预先调用 sync 或 probe。"
        ),
        "staging_inbox": str(DIR_INBOX),
        "staging_inbox_exists": DIR_INBOX.exists(),
        "media_ready": bool(_media_probe().get("ok")),
        "media_note": (
            "聊天图片可直接解密：msg/attach 下的 .dat 是 AES-128-ECB + XOR，"
            "用 kvcomm 里的 UIN 派生密钥即可还原；缩略图出 JPEG，原图多为 WXGF 需 ffmpeg 转 PNG。"
        ),
    }


# --------------------------------------------------------------------------- #
# 工具 1：微信环境诊断
# --------------------------------------------------------------------------- #
@TOOLS.tool(
    name="work_wechat_probe",
    description=(
        "【只读诊断】检查本机微信客户端版本、是否运行、SQLCipher 消息库只读缓存与 DPAPI 密钥缓存状态、"
        "WAL 实时增量状态、明文收件目录与图片解密就绪情况。"
        "常规读消息、搜索或收件会自动按需刷新缓存，无需预先调用本工具；仅在排查环境故障时调用。"
    ),
)
def work_wechat_probe() -> str:
    return _dump({"ok": True, **_wechat_probe()})


# --------------------------------------------------------------------------- #
# 工具 2：微信收件扫描、拉取与核对归档（统一入口 work_wechat_files）
# --------------------------------------------------------------------------- #
def _count_archived(root: Path) -> int:
    """归档目录现有文件数（含各月份子目录）。"""
    try:
        return sum(1 for p in root.rglob("*") if p.is_file())
    except Exception:  # noqa: BLE001
        return 0


def _harvest_summary(copied: int, dup: int, total: int, dest: Path,
                     submitted: List[str], missing: List[str], roster_basis: str) -> str:
    """把收件结果的数字翻成一句能直接转述的话。"""
    if copied and dup:
        head = f"本次新增归档 {copied} 份，另有 {dup} 份此前已归档、未重复复制"
    elif copied:
        head = f"本次新增归档 {copied} 份"
    elif dup:
        head = (f"本次没有新增文件：匹配到的 {dup} 份此前都已归档，按去重规则未再复制一遍"
                "（不是失败，也不是没收到）")
    else:
        head = "本次没有匹配到材料"
    parts = [head + f"；{dest} 现有 {total} 份归档。"]

    if roster_basis == "none":
        parts.append("没有可用的科室底册，所以给不出「谁没交」——"
                     "要名单请先放《科室底册.xlsx》，或给 roster。")
    elif not submitted and total > 0:
        parts.append(f"底册核对没有结果：归档里有 {total} 份，但按文件名一个科室都没认出来，"
                     "所以「未交」名单不可信，别当成真的没人交。要准确名单，"
                     "得让材料按「科室_发送人_原名」命名归档，或改用台账工具核对。")
    else:
        total_dept = len(submitted) + len(missing)
        basis = {"ledger": "台账", "archive_filenames": "归档目录整体文件名"}.get(
            roster_basis, "底册")
        parts.append(f"按{basis}核对：应交 {total_dept} 家，已交 {len(submitted)} 家，"
                     f"未交 {len(missing)} 家"
                     + (f"（{'、'.join(missing)}）。" if missing else "。"))
        if roster_basis == "archive_filenames":
            parts.append("本次时间窗内没有新材料，已放宽到按归档目录整体核对。")
        if dup:
            parts.append("注意：已交/未交是按归档目录整体判定的，"
                         "含此前批次已归档的材料，不只看本次新增。")
    return "".join(parts)


def _check_time_args(since: str, until: str) -> Optional[str]:
    """时间没解析出来就报错。"""
    if wxread is None:
        return None
    for label, value, is_end in (("since", since, False), ("until", until, True)):
        if value and not wxread._parse_time(value, end=is_end):
            return (f"{label} 无法识别：{value!r}；"
                    "支持 2026-11-01 / 11-01 / 今天 / 昨天 / 本周 / 上周 / 本月 / 上月 / 近3天")
    return None


def _wx_inbox_guard() -> Optional[str]:
    if wx_inbox is None:
        return "微信收件模块加载失败（wechat/inbox.py 缺失或语法错误）。"
    return None


def _read_roster_names(roster: str) -> List[str]:
    """roster 支持逗号分隔字符串，或指向 .xlsx 的路径（取含「科室/单位/姓名/部门」列）。"""
    roster = (roster or "").strip()
    if not roster:
        return []
    if not roster.lower().endswith((".xlsx", ".xls")):
        return [x.strip() for x in re.split(r"[,，;；\s]+", roster) if x.strip()]
    try:
        import openpyxl  # type: ignore
    except Exception:
        return []
    path = Path(roster)
    if not path.exists():
        return []
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[wb.sheetnames[0]]
    header = [str(c.value or "").strip() for c in ws[1]]
    col = 0
    for idx, h in enumerate(header):
        if any(k in h for k in ("科室", "单位", "部门", "姓名", "名称")):
            col = idx
            break
    names: List[str] = []
    for row in ws.iter_rows(min_row=2, min_col=col + 1, max_col=col + 1, values_only=True):
        v = row[0]
        if v and str(v).strip():
            names.append(str(v).strip())
    wb.close()
    return names


def _harvest_local(since_time: str = "", roster_path: str = "", save_dir: str = "", account_dir: str = "") -> str:
    """从微信 msg/file 明文收件目录收材料并联动底册与台账核对（只读原文件，仅复制）。"""
    if wx_inbox is None:
        return _dump({"ok": False, "error": "微信收件模块加载失败（wechat/inbox.py）"})
    account = account_dir or _wechat_account_dir()
    if not account:
        return _dump({"ok": False, "error": "未找到微信账号数据目录"})

    since = since_time or (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    files = wx_inbox.scan(account, since=since, kinds=["document"], limit=300)
    if not files:
        return _dump({
            "ok": False,
            "channel": "local_inbox",
            "is_demo_data": False,
            "error": f"微信收件目录中自 {since} 起没有文档类文件",
            "hint": "可放宽时间范围（传 since='2026-01-01'），或用 work_wechat_files(mode='scan') 先确认收件目录里有什么",
            "inbox_root": wx_inbox.file_root(account),
            "account": account,
        })

    dest = _resolve_path(save_dir, DIR_HARVESTED)
    results = wx_inbox.pull(files, str(dest), prefix_rule="{month}", dedupe=True)
    submitted: List[str] = []
    missing: List[str] = []
    roster_basis = ""

    roster_file = _resolve_path(roster_path, ROSTER)
    names = _read_roster_names(str(roster_file)) if roster_file.exists() else _read_roster_names(roster_path)
    roster_basis = "roster_file" if names else ""
    if names:
        checked = wx_inbox.match_roster(files, names)
        submitted = checked["submitted"]
        missing = checked["missing"]
        if not submitted:
            archived = ([SimpleNamespace(name=p.name) for p in dest.rglob("*") if p.is_file()]
                        if dest.exists() else [])
            if archived:
                recheck = wx_inbox.match_roster(archived, names)
                if recheck["submitted"]:
                    submitted = recheck["submitted"]
                    missing = recheck["missing"]
                    roster_basis = "archive_filenames"

    saved_by_src = {r["src"]: r.get("dst") for r in results if r.get("dst")}

    ledger_result: Dict[str, Any] = {}
    if ledger is not None:
        try:
            items = []
            for f in files:
                dst = saved_by_src.get(f.path)
                if not dst:
                    continue
                items.append({"path": f.path, "name": os.path.basename(dst) or f.name,
                              "dst": dst, "size": f.size, "ext": f.ext,
                              "received_at": datetime.fromtimestamp(f.mtime).isoformat(timespec="seconds")})
            if items:
                ledger_result = ledger.record(items, note=f"微信收件 {since} 起",
                                              source="wechat_inbox")
                if ledger_result.get("ok") and ledger_result.get("added", 0) > 0 and roster_file.exists():
                    miss = ledger.missing(str(roster_file))
                    if miss.get("ok"):
                        submitted = [x["dept"] for x in miss["all"] if x["submitted"]]
                        missing = [x["dept"] for x in miss["missing"]]
                        roster_basis = "ledger"
        except Exception as exc:  # noqa: BLE001
            ledger_result = {"ok": False, "error": f"台账写入失败：{exc}"}

    payload: Dict[str, Any] = {
        "ok": True,
        "mode": "harvest",
        "channel": "local_inbox",
        "data_source": "wechat_inbox",
        "is_demo_data": False,
        "data_source_note": "材料来自微信客户端收到的原件（msg/file），只读复制，不改动微信数据",
        "account": account,
        "inbox_root": wx_inbox.file_root(account),
        "out_dir": str(dest),
        "since": since,
        "matched_files": len(files),
        "copied": len([r for r in results if r.get("dst")]),
        "skipped_duplicate": len([r for r in results if r.get("skipped")]),
        "archived_total": _count_archived(dest),
        "submitted_departments": submitted,
        "missing_departments": missing,
        "roster_basis": roster_basis or "none",
        "roster_file": str(roster_file),
        "ledger": ledger_result,
        "files": [
            {"name": f.name, "size": f.size, "time": f.mtime_text,
             "saved_as": saved_by_src.get(f.path)}
            for f in files[:60]
        ],
    }
    payload["summary"] = _harvest_summary(
        payload["copied"], payload["skipped_duplicate"], payload["archived_total"],
        dest, submitted, missing, payload["roster_basis"])
    payload["suggested_reminder"] = (
        f"【材料催收提醒】截至目前，以下单位/同志尚未提交相关报表：@"
        f"{' @'.join(missing)} 。请于今日下午17:00前抓紧报送，感谢配合！"
        if missing
        else ("所有应交科室均已按时报送完毕！" if names else
              f"自 {since} 起共收到 {len(files)} 份材料，已归档到 {dest}。")
    )
    payload["harvested_at"] = datetime.now().isoformat(timespec="seconds")
    _save_state("last_harvest", {k: v for k, v in payload.items() if k != "files"})
    return _dump(payload)


def work_wechat_harvest(
    chat_group: str = "全局各科室联络群",
    since_time: str = "",
    roster_path: str = "",
    save_dir: str = "",
    inbox_dir: str = "",
    account_dir: str = "",
) -> str:
    """内部辅助：一键收材料归档并对照底册/台账核对未交科室（由 work_wechat_files(mode='harvest') 调用）。"""
    _ensure_dirs()
    staging = _resolve_path(inbox_dir, DIR_INBOX)
    has_staging = bool(inbox_dir) and staging.exists() and any(staging.glob("*"))

    if not has_staging:
        local = _harvest_local(since_time, roster_path, save_dir, account_dir=account_dir)
        if json.loads(local).get("ok") or not (staging.exists() and any(staging.glob("*"))):
            return local

    since = since_time or datetime.now().strftime("%Y-%m-%dT08:00:00")
    roster = _resolve_path(roster_path, ROSTER)
    if not roster.exists():
        return _dump({"ok": False, "error": f"找不到科室底册: {roster}"})

    cmd = [
        sys.executable,
        str(WORKERS / "wx_helper.py"),
        "harvest",
        "--group",
        chat_group,
        "--since",
        since,
        "--roster",
        str(roster),
        "--inbox",
        str(staging),
        "--out",
        str(_resolve_path(save_dir, DIR_HARVESTED)),
    ]
    res = _run(cmd, timeout=180)
    parsed = _parse_json(res["stdout"])
    if not parsed or parsed.get("status") != "success":
        return _dump({"ok": False, "cmd": cmd, **res})

    missing = parsed.get("missing_departments") or []
    parsed["mode"] = "harvest"
    parsed["suggested_reminder"] = (
        f"【材料催收提醒】截至目前，以下单位/同志尚未提交相关报表：@"
        f"{' @'.join(missing)} 。请于今日下午17:00前抓紧报送，感谢配合！"
        if missing
        else "所有应交科室均已按时报送完毕！"
    )
    parsed["harvested_at"] = datetime.now().isoformat(timespec="seconds")
    _save_state("last_harvest", parsed)
    return _dump({"ok": True, **parsed})


def work_wechat_inbox_scan(
    since: str = "",
    until: str = "",
    keyword: str = "",
    exts: Optional[List[str]] = None,
    kinds: Optional[List[str]] = None,
    limit: int = 50,
    order: str = "desc",
    with_stats: bool = True,
    account_dir: str = "",
) -> str:
    """内部辅助：只读扫描微信 msg/file 目录（由 work_wechat_files(mode='scan') 调用）。"""
    err = _wx_inbox_guard()
    if err:
        return _dump({"ok": False, "error": err})
    account = account_dir or _wechat_account_dir()
    if not account:
        return _dump({"ok": False, "error": "未找到微信账号数据目录，可用 account_dir 显式指定"})
    root = wx_inbox.file_root(account)
    if not os.path.isdir(root):
        return _dump({"ok": False, "error": f"收件目录不存在: {root}", "account": account})

    files = wx_inbox.scan(account, since=since, until=until, keyword=keyword,
                          exts=exts, kinds=kinds, limit=limit, order=order)
    payload: Dict[str, Any] = {
        "ok": True,
        "mode": "scan",
        "account": account,
        "inbox_root": root,
        "filter": {"since": since or None, "until": until or None, "keyword": keyword or None,
                   "exts": exts, "kinds": kinds},
        "returned": len(files),
        "files": [f.to_dict() for f in files],
    }
    if with_stats:
        payload["stats"] = wx_inbox.stats(
            wx_inbox.scan(account, since=since, until=until, keyword=keyword,
                          exts=exts, kinds=kinds, limit=0)
        )
    _save_state("last_inbox_scan", {"at": datetime.now().isoformat(timespec="seconds"),
                                   "returned": len(files), "filter": payload["filter"]})
    return _dump(payload)


def work_wechat_inbox_pull(
    since: str = "",
    until: str = "",
    keyword: str = "",
    exts: Optional[List[str]] = None,
    kinds: Optional[List[str]] = None,
    limit: int = 200,
    dest_dir: str = "",
    prefix_rule: str = "{month}",
    dedupe: bool = True,
    roster: str = "",
    account_dir: str = "",
) -> str:
    """内部辅助：筛选复制微信收件文件到工作区（由 work_wechat_files(mode='pull') 调用）。"""
    err = _wx_inbox_guard()
    if err:
        return _dump({"ok": False, "error": err})
    account = account_dir or _wechat_account_dir()
    if not account:
        return _dump({"ok": False, "error": "未找到微信账号数据目录"})

    files = wx_inbox.scan(account, since=since, until=until, keyword=keyword,
                          exts=exts, kinds=kinds, limit=limit)
    if not files:
        return _dump({"ok": False, "mode": "pull", "error": "没有符合条件的文件", "filter": {
            "since": since, "until": until, "keyword": keyword, "exts": exts, "kinds": kinds}})

    dest = _resolve_path(dest_dir, DIR_HARVESTED)
    results = wx_inbox.pull(files, str(dest), prefix_rule=prefix_rule, dedupe=dedupe)
    copied = [r for r in results if r.get("dst")]

    payload: Dict[str, Any] = {
        "ok": True,
        "mode": "pull",
        "account": account,
        "dest_dir": str(dest),
        "matched": len(files),
        "copied": len(copied),
        "skipped_duplicate": len([r for r in results if r.get("skipped")]),
        "archived_total": _count_archived(dest),
        "failed": [r for r in results if r.get("error")],
        "results": results,
    }

    names = _read_roster_names(roster)
    if names:
        matched = wx_inbox.match_roster(files, names)
        payload["roster_check"] = matched
        payload["suggested_reminder"] = (
            f"【材料催收提醒】截至目前，以下单位/同志尚未提交相关报表：@"
            f"{' @'.join(matched['missing'])} 。请于今日17:00前抓紧报送，感谢配合！"
            if matched["missing"]
            else "所有应交单位均已报送完毕！"
        )
        payload["summary"] = _harvest_summary(
            payload["copied"], payload["skipped_duplicate"], payload["archived_total"],
            dest, matched.get("submitted", []), matched.get("missing", []), "roster_file")
    else:
        if payload["copied"] and payload["skipped_duplicate"]:
            head = (f"本次新增归档 {payload['copied']} 份，另有 {payload['skipped_duplicate']} "
                    "份此前已归档、未重复复制")
        elif payload["copied"]:
            head = f"本次新增归档 {payload['copied']} 份"
        elif payload["skipped_duplicate"]:
            head = (f"本次没有新增文件：匹配到的 {payload['skipped_duplicate']} 份此前都已归档，"
                    "按去重规则未再复制一遍（不是失败）")
        else:
            head = "本次没有匹配到材料"
        payload["summary"] = head + f"；{dest} 现有 {payload['archived_total']} 份归档。"
    _save_state("last_inbox_pull", {k: v for k, v in payload.items() if k != "results"})
    return _dump(payload)


@TOOLS.tool(
    name="work_wechat_files",
    description=(
        "【微信收件与归档统一入口】管理本机微信收到的文件（msg/file 明文目录），支持三种模式："
        "mode='scan'（默认，只读扫描与按月/按类型统计，不复制文件）；"
        "mode='pull'（写文件：按时间/关键字/扩展名筛选复制到 01_微信收回原件/，自动 sha256 去重，可传 roster 核对名单）；"
        "mode='harvest'（写文件：一键收取近期文档归档到 01_微信收回原件/，自动联动《科室底册.xlsx》与台账输出已交/未交科室及催办话术）。"
    ),
)
def work_wechat_files(
    mode: str = "scan",
    since: str = "",
    until: str = "",
    keyword: str = "",
    exts: Optional[List[str]] = None,
    kinds: Optional[List[str]] = None,
    limit: int = 100,
    order: str = "desc",
    with_stats: bool = True,
    dest_dir: str = "",
    prefix_rule: str = "{month}",
    dedupe: bool = True,
    roster: str = "",
    inbox_dir: str = "",
    chat_group: str = "全局各科室联络群",
    account_dir: str = "",
) -> str:
    m = (mode or "scan").strip().lower()
    if m == "scan":
        return work_wechat_inbox_scan(
            since=since, until=until, keyword=keyword, exts=exts, kinds=kinds,
            limit=limit, order=order, with_stats=with_stats, account_dir=account_dir,
        )
    if m == "pull":
        return work_wechat_inbox_pull(
            since=since, until=until, keyword=keyword, exts=exts, kinds=kinds,
            limit=limit, dest_dir=dest_dir, prefix_rule=prefix_rule, dedupe=dedupe,
            roster=roster, account_dir=account_dir,
        )
    if m == "harvest":
        return work_wechat_harvest(
            chat_group=chat_group, since_time=since, roster_path=roster,
            save_dir=dest_dir, inbox_dir=inbox_dir, account_dir=account_dir,
        )
    return _dump({"ok": False, "error": f"mode 不支持: {mode!r}，可选 scan（只读扫描）/ pull（筛选复制）/ harvest（归档核对底册）"})


# --------------------------------------------------------------------------- #
# 工具 3：催办通知生成
# --------------------------------------------------------------------------- #
@TOOLS.tool(
    name="work_wechat_remind",
    description=(
        "【写文件】根据未交科室名单生成 @ 未交单位催办通知话术，并追加写入 06_运行状态/待发催办通知.md 供人工一键复制粘贴到微信群。"
        "不触发桌面自动化点击，避免抢占微信 4.x 窗口焦点。"
    ),
)
def work_wechat_remind(
    chat_group: str = "全局各科室联络群",
    missing_list: Optional[List[str]] = None,
    deadline_text: str = "今日下午17:00前",
    custom_message: str = "",
    dry_run: bool = True,
) -> str:
    _ensure_dirs()
    state = _load_state()
    missing = missing_list or state.get("last_harvest", {}).get("missing_departments") or []
    if custom_message:
        message = custom_message.strip()
    elif missing:
        message = (
            f"【材料催收提醒】截至目前，以下单位/同志尚未提交相关报表：@"
            f"{' @'.join(missing)} 。请于{deadline_text}抓紧报送，感谢配合！"
        )
    else:
        message = "【材料催收提醒】请尚未报送材料的单位抓紧时间提交，感谢配合！"

    PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry = (
        f"\n## {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} → {chat_group}\n"
        f"未交/提醒对象：{('、'.join(missing)) or '（全部/自定义）'}\n\n"
        f"```text\n{message}\n```\n"
    )
    with PENDING_FILE.open("a", encoding="utf-8") as fh:
        if PENDING_FILE.stat().st_size == 0:
            fh.write("# 待发催办通知（复制到微信群即可）\n")
        fh.write(entry)
    pending_written = str(PENDING_FILE)

    payload = {
        "ok": True,
        "dry_run": dry_run,
        "chat_group": chat_group,
        "missing_list": missing,
        "message": message,
        "send_result": {
            "status": "success",
            "mode": "pending_queue",
            "chat_group": chat_group,
            "message": message,
            "detail": "已生成催办通知并写入待发催办通知.md（只读安全策略，不触发桌面自动发送）",
        },
        "pending_queue": pending_written,
    }
    _save_state("last_remind", payload)
    return _dump(payload)


# --------------------------------------------------------------------------- #
# 工具 5c：微信消息（取密钥 → 解密副本 → 只读查询）
# --------------------------------------------------------------------------- #
def _wx_read_guard() -> Optional[str]:
    if wxread is None:
        return "微信消息模块加载失败（wechat/wxread.py）。"
    return None


def _wx_sync(force: bool = False, account_dir: str = "") -> Dict[str, Any]:
    if _wx_read_guard():
        return {"ok": False, "error": _wx_read_guard()}
    acc = wxread.account_dir(account_dir)
    if not acc:
        return {"ok": False, "error": "未找到微信账号数据目录"}
    return wxread.ensure_cache(acc, force=force)


@TOOLS.tool(
    name="work_wechat_sync",
    description=(
        "【只读】把微信 4.x 的加密数据库取密钥并解密到本地只读缓存（workspace/07_微信只读缓存）。"
        "取密钥走 4.1.11+ 的 Config.Cipher + XOR 掩码链路，源库与微信文件一律不改动。"
        "缓存会按源库修改时间自动增量刷新；force=true 强制重解。"
        "读消息前若提示缓存缺失，先调本工具。"
    ),
)
def work_wechat_sync(force: bool = False, account_dir: str = "") -> str:
    payload = _wx_sync(force, account_dir)
    _save_state("last_wx_sync", {k: v for k, v in payload.items() if k != "detail"})
    return _dump(payload)


@TOOLS.tool(
    name="work_wechat_sessions",
    description=(
        "【只读】列出微信最近会话：名称（备注/群名已还原）、是否群、未读数、最后一条摘要、最后时间。"
        "自动按需解密缓存。用于「最近谁找我」「哪些群有未读」这类问题。"
    ),
)
def work_wechat_sessions(limit: int = 30, only_unread: bool = False, account_dir: str = "") -> str:
    if err := _wx_read_guard():
        return _dump({"ok": False, "error": err})
    acc = wxread.account_dir(account_dir)
    if not acc:
        return _dump({"ok": False, "error": "未找到微信账号数据目录"})
    sync = wxread.ensure_cache(acc)
    try:
        items = wxread.sessions(limit=limit, only_unread=only_unread)
    except Exception as exc:  # noqa: BLE001
        return _dump({"ok": False, "error": str(exc), "sync": sync})
    return _dump({"ok": True, "count": len(items), "account": acc,
                  "refreshed": sync.get("refreshed"), "sessions": items})


@TOOLS.tool(
    name="work_wechat_messages",
    description=(
        "【只读】按会话读取微信消息。chat 可传群名/备注/wxid/群 id（模糊匹配），"
        "支持 since/until 时间过滤。返回时间、发送人（wxid 已映射为备注名）、类型、正文；"
        "图片/语音/文件等自动压成可读摘要。用于「把这个群最近的消息总结一下」。"
    ),
)
def work_wechat_messages(
    chat: str,
    limit: int = 40,
    since: str = "",
    until: str = "",
    account_dir: str = "",
) -> str:
    if err := _wx_read_guard():
        return _dump({"ok": False, "error": err})
    acc = wxread.account_dir(account_dir)
    if not acc:
        return _dump({"ok": False, "error": "未找到微信账号数据目录"})
    bad = _check_time_args(since, until)
    if bad:
        return _dump({"ok": False, "error": bad})
    wxread.ensure_cache(acc)
    try:
        res = wxread.messages(chat=chat, limit=limit, since=since, until=until)
    except Exception as exc:  # noqa: BLE001
        return _dump({"ok": False, "error": str(exc)})
    _save_state("last_wx_read", {k: v for k, v in res.items() if k != "messages"})
    return _dump(res)


@TOOLS.tool(
    name="work_wechat_search",
    description=(
        "【只读】在微信历史消息里多维搜索：支持按 keyword（关键词）、sender（发言人备注/昵称/wxid，可跨群找某人说过的所有话）、"
        "msg_type（消息类型：text/file/link/image/voice/quote/solitaire/announcement/miniprogram/system）"
        "以及 chat（限定群/联系人）和 since/until（时间窗）自由组合检索。"
        "被 zstd 压缩的长消息会先解压再匹配，引用回复、分享链接 URL、群公告和文件均已结构化还原。"
    ),
)
def work_wechat_search(
    keyword: str = "",
    chat: str = "",
    sender: str = "",
    msg_type: str = "",
    limit: int = 30,
    since: str = "",
    until: str = "",
    account_dir: str = "",
) -> str:
    if err := _wx_read_guard():
        return _dump({"ok": False, "error": err})
    acc = wxread.account_dir(account_dir)
    if not acc:
        return _dump({"ok": False, "error": "未找到微信账号数据目录"})
    bad = _check_time_args(since, until)
    if bad:
        return _dump({"ok": False, "error": bad})
    wxread.ensure_cache(acc)
    try:
        res = wxread.search(
            keyword=keyword, chat=chat, limit=limit,
            since=since, until=until, sender=sender, msg_type=msg_type,
        )
    except Exception as exc:  # noqa: BLE001
        return _dump({"ok": False, "error": str(exc)})
    _save_state("last_wx_search", {k: v for k, v in res.items() if k != "results"})
    return _dump(res)


@TOOLS.tool(
    name="work_wechat_contacts",
    description=(
        "【只读】微信通讯录、群成员详情、全量群公告与共同群查询（直读 contact.db）。"
        "支持四种模式：mode='contacts'（默认，按姓名/备注/微信号/拼音搜索联系人，并列出其所在的微信群）；"
        "mode='group'（查看指定微信群 chat 的群主、群公告与完整群成员名单）；"
        "mode='announcements'（跨全部微信群提取非空群公告，按发布时间倒序）；"
        "mode='common_groups'（查询 1 人或多人在 keyword 中共同所在的微信群，多人用逗号分隔）。"
    ),
)
def work_wechat_contacts(
    mode: str = "contacts",
    keyword: str = "",
    chat: str = "",
    limit: int = 60,
    account_dir: str = "",
) -> str:
    if err := _wx_read_guard():
        return _dump({"ok": False, "error": err})
    acc = wxread.account_dir(account_dir)
    if not acc:
        return _dump({"ok": False, "error": "未找到微信账号数据目录"})
    m = (mode or "contacts").strip().lower()
    if m not in ("contacts", "group", "announcements", "common_groups", "auto"):
        return _dump({
            "ok": False,
            "error": f"mode 不支持: {mode!r}，可选 contacts / group / announcements / common_groups",
        })
    wxread.ensure_cache(acc)
    try:
        res = wxread.contacts_and_rooms(mode=m, keyword=keyword, chat=chat, limit=limit)
    except Exception as exc:  # noqa: BLE001
        return _dump({"ok": False, "error": str(exc)})
    return _dump(res)


@TOOLS.tool(
    name="work_wechat_solitaire",
    description=(
        "【只读】微信群接龙解析与未接龙人员自动核对（直读 solitaire.db）。"
        "列出所有群或指定群 chat 的接龙活动、发起人、活跃时间及每一棒接龙人（已还原备注/昵称）、时间与内容；"
        "可传 roster（《科室底册.xlsx》或逗号分隔名单）或开启 check_missing=true 自动对比该群全体成员，"
        "直接输出已接龙名单、未接龙名单及可一键复制的 @ 催办提醒话术。"
    ),
)
def work_wechat_solitaire(
    chat: str = "",
    keyword: str = "",
    since: str = "",
    roster: str = "",
    check_missing: bool = True,
    limit: int = 20,
    account_dir: str = "",
) -> str:
    if err := _wx_read_guard():
        return _dump({"ok": False, "error": err})
    acc = wxread.account_dir(account_dir)
    if not acc:
        return _dump({"ok": False, "error": "未找到微信账号数据目录"})
    bad = _check_time_args(since, "")
    if bad:
        return _dump({"ok": False, "error": bad})
    roster_names: List[str] = []
    if roster:
        rf = _resolve_path(roster, ROSTER)
        roster_names = _read_roster_names(str(rf)) if rf.exists() else _read_roster_names(roster)
    try:
        res = wxread.solitaires(
            chat=chat, keyword=keyword, since=since,
            roster_names=roster_names or None,
            check_missing=check_missing, limit=limit, account=acc,
        )
    except Exception as exc:  # noqa: BLE001
        return _dump({"ok": False, "error": str(exc)})
    return _dump(res)


@TOOLS.tool(
    name="work_wechat_favorites",
    description=(
        "【只读】读取并检索微信收藏夹（favorite.db）：包括收藏的文本笔记/密码备忘（fav_type='note'）、"
        "网页/公众号/飞书/网盘链接（fav_type='link'，自动提取 URL）、收藏文件（fav_type='file'）"
        "及收藏的合并聊天记录（fav_type='record'）。支持 keyword 与 since 过滤。"
    ),
)
def work_wechat_favorites(
    keyword: str = "",
    fav_type: str = "all",
    since: str = "",
    limit: int = 50,
    account_dir: str = "",
) -> str:
    if err := _wx_read_guard():
        return _dump({"ok": False, "error": err})
    acc = wxread.account_dir(account_dir)
    if not acc:
        return _dump({"ok": False, "error": "未找到微信账号数据目录"})
    bad = _check_time_args(since, "")
    if bad:
        return _dump({"ok": False, "error": bad})
    try:
        res = wxread.favorites(
            keyword=keyword, fav_type=fav_type, since=since, limit=limit, account=acc,
        )
    except Exception as exc:  # noqa: BLE001
        return _dump({"ok": False, "error": str(exc)})
    return _dump(res)


@TOOLS.tool(
    name="work_wechat_insights",
    description=(
        "【只读/可选导出】微信工作群或最近活跃会话深度画像与待办提取："
        "一次调用统计发言人活跃度排行（top_senders）、消息类型与每日趋势，"
        "提取群内所有分享链接/云文档/网盘地址（links）、聊天内文件（files）、引用回复热点（quotes），"
        "并自动识别提炼待办事项/截止死线/群公告行动项（action_items）。"
        "传 output 可同时将画像与待办清单导出为 Markdown 简报落盘到 03_汇总与成稿输出/。"
    ),
)
def work_wechat_insights(
    chat: str = "",
    days: int = 7,
    since: str = "",
    until: str = "",
    limit: int = 500,
    output: str = "",
    account_dir: str = "",
) -> str:
    if err := _wx_read_guard():
        return _dump({"ok": False, "error": err})
    acc = wxread.account_dir(account_dir)
    if not acc:
        return _dump({"ok": False, "error": "未找到微信账号数据目录"})
    bad = _check_time_args(since, until)
    if bad:
        return _dump({"ok": False, "error": bad})
    wxread.ensure_cache(acc)
    try:
        res = wxread.chat_insights(
            chat=chat, days=days, since=since, until=until, limit=limit, account=acc,
        )
    except Exception as exc:  # noqa: BLE001
        return _dump({"ok": False, "error": str(exc)})
    if not res.get("ok"):
        return _dump(res)
    if output:
        _ensure_dirs()
        dst = _resolve_path(output, DIR_OUTPUT / os.path.basename(output))
        if not dst.suffix:
            dst = dst.with_suffix(".md")
        dst.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            f"# 微信会话洞察与待办简报 · {res.get('scope', '')}",
            "",
            f"- **时间范围**：{res.get('since')} 至 {res.get('until')}",
            f"- **消息总数**：{res.get('total_messages', 0)} 条（活跃发言人 {res.get('active_speakers', 0)} 人）",
            "",
            f"## 一、 待办事项与重要通知（{res.get('action_items_count', 0)} 项）",
        ]
        for idx, it in enumerate(res.get("action_items") or [], 1):
            dl = f" 【截止：{it['deadline']}】" if it.get("deadline") else ""
            lines.append(f"{idx}. **[{it.get('chat')}] {it.get('sender')}** ({it.get('time')}){dl}：{it.get('text')}")
        lines += ["", f"## 二、 分享链接与云文档（{res.get('links_count', 0)} 条）"]
        for idx, lk in enumerate(res.get("links") or [], 1):
            u = f" — {lk['url']}" if lk.get("url") else ""
            lines.append(f"{idx}. [{lk.get('chat')}] {lk.get('sender')}：{lk.get('title')}{u}")
        lines += ["", f"## 三、 聊天文件汇总（{res.get('files_count', 0)} 份）"]
        for idx, fl in enumerate(res.get("files") or [], 1):
            lines.append(f"{idx}. [{fl.get('chat')}] {fl.get('sender')} ({fl.get('time')})：`{fl.get('filename')}`")
        lines += ["", "## 四、 活跃发言人 Top 10"]
        for idx, sp in enumerate((res.get("top_senders") or [])[:10], 1):
            lines.append(f"{idx}. **{sp['sender']}**：{sp['count']} 条 ({round(sp['ratio'] * 100, 1)}%)")
        dst.write_text("\n".join(lines) + "\n", encoding="utf-8")
        res["output_path"] = str(dst)
    _save_state("last_wx_insights", {
        "at": datetime.now().isoformat(timespec="seconds"),
        "scope": res.get("scope"),
        "total_messages": res.get("total_messages"),
        "action_items_count": res.get("action_items_count"),
    })
    return _dump(res)


# --------------------------------------------------------------------------- #
# 工具 5b：企业微信本地只读（会话 / 消息 / 搜索 / 联系人 / 文件 / 收材料）
# --------------------------------------------------------------------------- #
def _wc_guard() -> Optional[str]:
    """能不能用企业微信通道。返回错误文案，None 表示可用。"""
    if wc_read is None:
        return "企业微信模块没加载起来（wecom/ 目录缺失或被安全软件隔离）"
    if not wc_read.account_dir():
        return ("没找到企业微信数据目录。默认找 用户目录\\Documents\\WXWork\\<账号>\\Data；"
                "改过存储位置的，把那个目录传给 account_dir 参数")
    return None


def _wc_sync(force: bool = False) -> Dict[str, Any]:
    """解密缓存前置：企业微信的库是加密的，读之前必须先备好明文副本。"""
    acc = wc_read.account_dir()
    if not acc:
        return {"ok": False, "error": "没找到企业微信数据目录"}
    return wc_read.ensure_cache(acc, force=force)


@TOOLS.tool(
    name="work_wecom_probe",
    description=(
        "【只读】探测企业微信能否读取：客户端在不在跑、数据目录在哪、有几个加密库、"
        "密钥缓存有没有、缓存目录与本地文件目录在哪。"
        "企业微信的库是 wxSQLite3 加密的，密钥只在 WXWork.exe 内存里，"
        "所以客户端没开时读不了 —— 这一步先确认这件事。"
    ),
)
def work_wecom_probe() -> str:
    if wc_read is None:
        return _dump({"ok": False, "error": "企业微信模块没加载起来"})
    try:
        return _dump(wc_read.probe())
    except Exception as exc:  # noqa: BLE001
        return _dump({"ok": False, "error": f"{type(exc).__name__}: {exc}"})


@TOOLS.tool(
    name="work_wecom_sync",
    description=(
        "刷新企业微信读取缓存：必要时从 WXWork.exe 内存取密钥，把加密库解成明文副本。"
        "平时读消息会自动按需刷新，只有「刚发完消息要立刻看到」或「换账号/升级客户端」才需要手动跑。"
        "force=true 会丢掉密钥缓存重新扫描（首次约 20–30 秒，之后秒级）。"
    ),
)
def work_wecom_sync(force: bool = False) -> str:
    err = _wc_guard()
    if err:
        return _dump({"ok": False, "error": err})
    res = _wc_sync(force=force)
    if res.get("ok"):
        res["hint"] = "缓存已就绪；接下来可以直接读会话/消息/文件"
    return _dump(res)


@TOOLS.tool(
    name="work_wecom_sessions",
    description=(
        "【只读】列出企业微信最近会话：群名/人名、是群还是单聊、未读数、最后一条时间。"
        "用于「企业微信里最近哪个群在说事」「有哪些群我没看」。"
        "会话 id 形如 R:数字（群）/ S:数字_数字（单聊），可直接喂给 work_wecom_messages。"
    ),
)
def work_wecom_sessions(limit: int = 30, only_unread: bool = False) -> str:
    err = _wc_guard()
    if err:
        return _dump({"ok": False, "error": err})
    sync = _wc_sync()
    if not sync.get("ok"):
        return _dump(sync)
    try:
        items = wc_read.sessions(limit=limit, only_unread=only_unread)
    except Exception as exc:  # noqa: BLE001
        return _dump({"ok": False, "error": str(exc), "sync": sync})
    return _dump({"ok": True, "count": len(items), "account": wc_read.account_dir(),
                  "refreshed": sync.get("refreshed"), "sessions": items})


@TOOLS.tool(
    name="work_wecom_messages",
    description=(
        "【只读】按会话读企业微信消息。chat 可传群名/群名片段/会话 id（如 R:233536007534344），"
        "支持 since/until 时间过滤。正文从 protobuf 里解出来，"
        "图片/表情/文件等压成可读摘要（文件会给出文件名）。"
        "用于「把这个群最近的安排总结成通知」「看看谁请假了」。"
    ),
)
def work_wecom_messages(chat: str, limit: int = 40, since: str = "", until: str = "") -> str:
    err = _wc_guard()
    if err:
        return _dump({"ok": False, "error": err})
    bad = _check_time_args(since, until)
    if bad:
        return _dump({"ok": False, "error": bad})
    sync = _wc_sync()
    if not sync.get("ok"):
        return _dump(sync)
    try:
        res = wc_read.messages(chat=chat, limit=limit, since=since, until=until)
    except Exception as exc:  # noqa: BLE001
        return _dump({"ok": False, "error": str(exc)})
    if res.get("ok"):
        res["wal_pending"] = sync.get("wal_pending")
    _save_state("last_wc_read", {k: v for k, v in res.items() if k != "messages"})
    return _dump(res)


@TOOLS.tool(
    name="work_wecom_search",
    description=(
        "【只读】在企业微信历史消息里搜关键字，可限定会话与时间窗。"
        "正文是 protobuf，SQL 搜不到，工具会按时间窗取候选再逐条解正文匹配，"
        "所以把 since 给窄一点更快。用于「谁在企业微信里提过 XX」。"
    ),
)
def work_wecom_search(keyword: str, chat: str = "", limit: int = 30,
                      since: str = "", until: str = "") -> str:
    err = _wc_guard()
    if err:
        return _dump({"ok": False, "error": err})
    bad = _check_time_args(since, until)
    if bad:
        return _dump({"ok": False, "error": bad})
    sync = _wc_sync()
    if not sync.get("ok"):
        return _dump(sync)
    try:
        res = wc_read.search(keyword=keyword, chat=chat, limit=limit, since=since, until=until)
    except Exception as exc:  # noqa: BLE001
        return _dump({"ok": False, "error": str(exc)})
    _save_state("last_wc_search", {k: v for k, v in res.items() if k != "hits"})
    return _dump(res)


@TOOLS.tool(
    name="work_wecom_contacts",
    description=(
        "【只读】查企业微信通讯录：姓名、拼音名、手机、邮箱、职位。"
        "keyword 留空列全部（用 limit 截断）。用于「这个人在哪个部门」「手机号是多少」。"
    ),
)
def work_wecom_contacts(keyword: str = "", limit: int = 30) -> str:
    err = _wc_guard()
    if err:
        return _dump({"ok": False, "error": err})
    sync = _wc_sync()
    if not sync.get("ok"):
        return _dump(sync)
    try:
        return _dump(wc_read.contacts(keyword=keyword, limit=limit))
    except Exception as exc:  # noqa: BLE001
        return _dump({"ok": False, "error": str(exc)})


@TOOLS.tool(
    name="work_wecom_files",
    description=(
        "【只读】列企业微信收到的文件：文件名、所在群、发送人、时间、大小、md5，"
        "以及「本地有没有这个文件」（path/on_disk）。"
        "企业微信的云端文件要客户端点开才落地，所以 on_disk=false 很常见 —— "
        "这是真实状态，不是工具没找到。用于「谁发过什么表」「这个月收了哪些材料」。"
    ),
)
def work_wecom_files(since: str = "", until: str = "", exts: str = "", limit: int = 100) -> str:
    err = _wc_guard()
    if err:
        return _dump({"ok": False, "error": err})
    bad = _check_time_args(since, until)
    if bad:
        return _dump({"ok": False, "error": bad})
    sync = _wc_sync()
    if not sync.get("ok"):
        return _dump(sync)
    want = [e.strip() for e in re.split(r"[,，\s]+", exts or "") if e.strip()]
    try:
        return _dump(wc_read.files(since=since, until=until, exts=want, limit=limit))
    except Exception as exc:  # noqa: BLE001
        return _dump({"ok": False, "error": str(exc)})


@TOOLS.tool(
    name="work_wecom_harvest",
    description=(
        "【写文件】把企业微信收到的文件收进工作区：按时间窗/群/扩展名筛选后复制到 dest_dir，"
        "可加「群名_发送人_」前缀，按内容去重（重复执行不会产生副本）。"
        "默认 dry_run=true 只给计划不落盘；确认要收再传 dry_run=false。"
        "注意：只收本地已有的文件，云端没下载的会列在 not_downloaded 里。"
    ),
)
def work_wecom_harvest(dest_dir: str = "", since: str = "", until: str = "",
                       chats: str = "", exts: str = "",
                       prefix: str = "{chat}", dry_run: bool = True, limit: int = 200) -> str:
    err = _wc_guard()
    if err:
        return _dump({"ok": False, "error": err})
    bad = _check_time_args(since, until)
    if bad:
        return _dump({"ok": False, "error": bad})
    sync = _wc_sync()
    if not sync.get("ok"):
        return _dump(sync)
    if not dest_dir.strip():
        dest_dir = str(DIR_WECOM_HARVESTED)
    target = _resolve_path(dest_dir, DIR_WECOM_HARVESTED)
    chat_list = [c.strip() for c in re.split(r"[,，]+", chats or "") if c.strip()]
    ext_list = [e.strip() for e in re.split(r"[,，\s]+", exts or "") if e.strip()]
    try:
        res = wc_read.harvest(str(target), since=since, until=until, chats=chat_list,
                              exts=ext_list, prefix=prefix, dry_run=dry_run, limit=limit)
    except Exception as exc:  # noqa: BLE001
        return _dump({"ok": False, "error": str(exc)})
    if not dry_run:
        _save_state("last_wc_harvest", {k: v for k, v in res.items()
                                        if k not in ("copied", "plan", "not_downloaded")})
    return _dump(res)


# --------------------------------------------------------------------------- #
# 工具 5c：通讯总览（微信 + 企业微信合成一个视图，底下仍是两条通道）
# --------------------------------------------------------------------------- #
def _comms_sessions(raw: Any, channel: str, limit: int) -> tuple[bool, str, List[Dict[str, Any]]]:
    """把一个通道的会话结果抹平成前端要的形状。

    微信给 is_group + summary，企业微信给 kind，字段名不一样；总览里两条通道要
    混在同一条时间线上，所以在这里统一，不在前端各写一套判断。
    """
    try:
        data = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}", []
    if not data.get("ok"):
        return False, str(data.get("error") or "会话暂时读不到"), []
    out: List[Dict[str, Any]] = []
    for it in (data.get("sessions") or [])[:limit]:
        kind = it.get("kind") or ("群聊" if it.get("is_group") else "单聊")
        out.append({
            "name": it.get("name") or it.get("username") or "（未命名会话）",
            "kind": kind,
            "unread": int(it.get("unread") or 0),
            "time": it.get("last_time") or "",
            "summary": str(it.get("summary") or "").replace("\n", " ")[:80],
            "channel": channel,
        })
    return True, "", out


@TOOLS.tool(
    name="work_comms_overview",
    description=(
        "【只读】通讯总览：一次看微信与企业微信两条通道能不能读、各自的状态，"
        "并把两边的会话合成一条按时间排的最近会话时间线（每行标出来自哪个通道）。"
        "用于「通讯里最近有什么新消息」「两个微信都能读吗」「先看一眼通讯通道」。"
        "注意：这是两条独立通道的合视图，不是同一个账号——微信是 SQLCipher，"
        "企业微信是 wxSQLite3，密钥与缓存各管各的；要细看某个群仍用各自的 sessions/messages。"
    ),
)
def work_comms_overview(limit: int = 6) -> str:
    limit = max(1, min(int(limit or 6), 30))
    channels: List[Dict[str, Any]] = []

    # 微信：probe 判状态，sessions 取最近会话；任何一步炸了都不拖累另一条通道
    try:
        wp = _wechat_probe()
    except Exception as exc:  # noqa: BLE001
        wp = {"wechat_installed": False, "wechat_running": False,
              "message_db_readable": False, "local_inbox_ready": False}
        wx_probe_err = str(exc)
    else:
        wx_probe_err = ""
    wx_running = bool(wp.get("wechat_running"))
    wx_db = bool(wp.get("message_db_readable"))
    wx_inbox = bool(wp.get("local_inbox_ready"))
    wx_ready = wx_db or wx_inbox
    if not wp.get("wechat_installed", True):
        wx_status, wx_hint = "没检测到微信客户端", "装好微信 4.x 并登录后再看"
    elif wx_db:
        wx_status = "消息库可只读"
        wx_hint = "会话 / 消息 / 全文搜索都能用" if wx_running else "客户端没开也能读已解密的缓存"
    elif wx_inbox:
        wx_status, wx_hint = "只能扫收件目录", "消息库没解开，聊天文字暂时读不了"
    else:
        wx_status, wx_hint = "通道未就绪", "先跑 work_wechat_probe 看是哪一步卡住"
    wx_ok, wx_err, wx_items = _comms_sessions(
        work_wechat_sessions(limit=limit), "微信", limit)
    channels.append({
        "id": "wechat", "name": "微信", "ready": wx_ready and wx_ok,
        "status": wx_status, "hint": wx_hint, "error": wx_err or wx_probe_err,
        "metrics": [
            {"label": "客户端", "value": wp.get("wechat_version") or ("在运行" if wx_running else "未运行")},
            {"label": "消息库", "value": "可只读" if wx_db else "不可用"},
            {"label": "收件目录", "value": "可扫" if wx_inbox else "不可用"},
        ],
        "sessions": wx_items,
    })

    # 企业微信：密钥只在 WXWork.exe 内存里，客户端没开就是取不到，直说
    try:
        cp = wc_read.probe() if wc_read is not None else {}
    except Exception as exc:  # noqa: BLE001
        cp = {"ok": False, "running": False}
        wc_probe_err = str(exc)
    else:
        wc_probe_err = ""
    wc_running = bool(cp.get("running"))
    wc_ready = bool(cp.get("ok") and wc_running)
    if wc_read is None:
        wc_status, wc_hint = "模块没加载", "wecom/ 目录缺失或被安全软件隔离"
    elif not cp.get("account_dir"):
        wc_status, wc_hint = "没找到数据目录", "把企业微信数据目录传给 account_dir 参数"
    elif not wc_running:
        wc_status, wc_hint = "客户端没开", "密钥只在 WXWork.exe 内存里，先打开企业微信"
    else:
        wc_status, wc_hint = "客户端在跑", "可以取密钥解只读副本"
    wc_ok, wc_err, wc_items = _comms_sessions(
        work_wecom_sessions(limit=limit), "企微", limit)
    channels.append({
        "id": "wecom", "name": "企业微信", "ready": wc_ready and wc_ok,
        "status": wc_status, "hint": wc_hint, "error": wc_err or wc_probe_err,
        "metrics": [
            {"label": "客户端", "value": "在运行" if wc_running else "未运行"},
            {"label": "加密库", "value": cp.get("db_encrypted", "—")},
            {"label": "密钥缓存", "value": "已缓存" if cp.get("cached_key") else "按需取"},
        ],
        "sessions": wc_items,
    })

    # 合并时间线：这是「通讯」这个栏目真正比两列分开多出来的东西
    recent = sorted([it for c in channels for it in c["sessions"]],
                    key=lambda x: x.get("time") or "", reverse=True)[:limit * 2]
    return _dump({
        "ok": True,
        "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ready": sum(1 for c in channels if c["ready"]),
        "total": len(channels),
        "channels": channels,
        "recent": recent,
        "note": ("「通讯」把两条通道合成一个栏目看，但底层仍是两套独立实现："
                 "微信 SQLCipher、企业微信 wxSQLite3，密钥与缓存互不通用；"
                 "要细看某个群仍走各自的 work_wechat_* / work_wecom_* 工具。"),
    })


# --------------------------------------------------------------------------- #
# 工具 5d：通讯最近动态（一次调用跨两条通道，消息级时间线）
#
# 为什么要有它：泛问「通讯里最近有什么值得注意的通知」时，旧链路是
#   overview(1 次) -> 逐会话 messages(10~20 次) -> 全文 search(若干次)，
# 每一次 work.py call 都要重新起解释器、重新 import 整个工具箱、重新校验解密缓存，
# 十几轮下来既慢又把几十 KB 原始 XML 灌进上下文。这里把「最近 + 两通道 +
# 消息级 + 紧凑输出」压成一次调用，默认只看最近 3 天。
# --------------------------------------------------------------------------- #
_NOTICE_STRONG = re.compile(
    r"截止|请于|逾期|@所有人|通知[:：]|关于.{0,40}的通知|报名|缴费|交费|"
    r"考场|准考证|务必|收到请回复|以班级为单位|要求全体|请提交|核对|"
    r"前完成|点前|申领|招募|统一(?:开|提交|汇总)|转(?:发|告)|自带|携带")
_NOTICE_WEAK = ("通知", "材料", "名单", "考试", "时间", "地点", "扫码", "讲座",
                "班会", "提醒", "安排", "填写", "提交", "模板", "会议",
                "打印", "请假", "检查", "报名表", "附件", "答疑", "学工", "教务处")
_NOISE_MARKS = ("跑腿订单", "mp://", "出南门", "求带", "谁有", "有没有人",
                "二手", "拼单", "代取", "取快递")


_DEADLINE_RE = re.compile(
    r"\d{1,2}\s*月\s*\d{1,2}\s*日[^，。；\n]{0,14}"
    r"|\d{1,2}\s*[:：]\s*\d{2}\s*(?:前|之前|截止)"
    r"|(?:截止|截至|deadline)[^，。；\n]{0,24}"
    r"|周[一二三四五六日天][^，。；\n]{0,14}(?:前|截止)?",
    re.I,
)


_DEADLINE_STRONG = re.compile(
    r"截止|截至|逾期|deadline|前(?:完成|提交|交|修改|发|报|回复|到)|"
    r"之前|点前|时前|前\b", re.I)


def _time_ref(text: str) -> str:
    """抽一句时间线索（活动日期也算），原样返回片段，不解析成 datetime。

    口语里「12号12:00前」「本周五」「11.12不安排我院」混在一起，硬解析成
    日期反而会编造出并不存在的时间；给原文片段最诚实。
    """
    m = _DEADLINE_RE.search((text or "").replace("\n", " "))
    return m.group(0).strip(" ，。；:：") if m else ""


def _deadline_hint(text: str, ref: str = "") -> str:
    """只有带明确截止语义的时间线索才算「死线」。

    「11月6日晚7:00开讲」「9月1日—12月25日可申领」都是时间，但不是死线；
    早先一律塞进 due，会让「按紧急度排」把活动通知排到真正要办的事前面。
    """
    frag = ref if ref else _time_ref(text)
    return frag if frag and _DEADLINE_STRONG.search(frag) else ""


def _notice_signals(text: str) -> tuple[bool, List[str]]:
    """给一条消息打「像不像通知」的标记。

    这是召回用的弱信号，不是事实判定：宁可多标几条，也不能漏掉带截止时间的那条。
    """
    t = (text or "").replace("\n", " ")
    if len(t) < 10 or t.startswith(("[图片]", "[表情]", "[语音]", "[视频]")):
        return False, []
    strong = [str(s) for s in _NOTICE_STRONG.findall(t)]
    weak = [k for k in _NOTICE_WEAK if k in t]
    noise = sum(1 for k in _NOISE_MARKS if k in t)
    score = min(len(strong), 3) * 2 + min(len(weak), 3) - noise * 2
    if _deadline_hint(t):
        score += 1
    return score >= 2, list(dict.fromkeys(strong + weak))[:4]


def _comms_wechat_items(since_ts: int, per_chat: int, max_chats: int,
                        max_text: int) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """一次连接扫完微信最近会话的消息，不再逐会话开关数据库。"""
    import hashlib

    info: Dict[str, Any] = {"scanned_chats": 0, "error": ""}
    if wxread is None:
        info["error"] = "微信只读模块没加载"
        return [], info
    acc = wxread.account_dir()
    if not acc:
        info["error"] = "没找到微信账号数据目录"
        return [], info
    try:
        sync = wxread.ensure_cache(acc)
        if not sync.get("ok"):
            info["error"] = f"微信缓存未就绪：{sync.get('error') or sync.get('failed')}"
            return [], info
        names = wxread._name_map()
        sess = wxread.sessions(limit=max_chats, names=names)
    except Exception as exc:  # noqa: BLE001
        info["error"] = f"{type(exc).__name__}: {exc}"
        return [], info
    md5_map = {hashlib.md5(s["username"].encode()).hexdigest(): s["name"] for s in sess}
    items: List[Dict[str, Any]] = []
    for rel in ("message/message_1.db", "message/message_0.db"):
        path = os.path.join(wxread.cache_dir(), rel.replace("/", os.sep))
        if not os.path.exists(path):
            continue
        try:
            con = wxread._conn(rel)
        except Exception as exc:  # noqa: BLE001
            info["error"] = f"{type(exc).__name__}: {exc}"
            continue
        try:
            tabs = wxread._msg_tables(con)
            smap = wxread._sender_map(con)
            for md5, table in tabs.items():
                chat = md5_map.get(md5)
                if not chat:
                    continue
                info["scanned_chats"] += 1
                cols = [c[1] for c in con.execute(f"pragma table_info('{table}')")]
                rows = con.execute(
                    f"select * from '{table}' where create_time >= ? "
                    "order by create_time desc limit ?", (since_ts, per_chat)).fetchall()
                for r in rows:
                    raw = wxread._decode(r, cols)
                    swxid, body = wxread._split_group_sender(raw)
                    if not swxid:
                        swxid = smap.get(r["real_sender_id"], "")
                    mtype = (r["local_type"] or 0) & 0xFFFFFFFF
                    text = wxread._summarize(mtype, body)
                    items.append({
                        "time": wxread._fmt(r["create_time"]),
                        "ts": int(r["create_time"] or 0),
                        "channel": "微信",
                        "chat": chat,
                        "sender": wxread.display(swxid, names)
                        or ("系统" if mtype in (10000, 10002) else ""),
                        "text": wxread._clip(text, max_text),
                    })
        except Exception as exc:  # noqa: BLE001
            info["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            con.close()
    return items, info


def _comms_wecom_items(since_ts: int, per_chat: int, max_chats: int,
                       max_text: int) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """企业微信正文是 protobuf，逐条解很贵；用窗口函数按会话限量，一次连接取完。"""
    info: Dict[str, Any] = {"scanned_chats": 0, "error": ""}
    if wc_read is None:
        info["error"] = "企业微信只读模块没加载"
        return [], info
    sync = _wc_sync()
    if not sync.get("ok"):
        info["error"] = str(sync.get("error") or "企业微信缓存未就绪")
        return [], info
    items: List[Dict[str, Any]] = []
    try:
        con = wc_read._conn()
    except Exception as exc:  # noqa: BLE001
        info["error"] = f"{type(exc).__name__}: {exc}"
        return [], info
    try:
        conv = {r["id"]: (r["name"] or r["id"])
                for r in con.execute("SELECT id, name FROM s.conversation_table")}
        types = "0,2,13,15,20,31,35,38,1027"
        try:
            rows = con.execute(
                "SELECT conversation_id, send_time, sender_id, content_type, content FROM ("
                " SELECT conversation_id, send_time, sender_id, content_type, content,"
                " ROW_NUMBER() OVER (PARTITION BY conversation_id ORDER BY send_time DESC) AS rn"
                f" FROM message_table WHERE send_time >= ? AND content_type IN ({types})"
                ") WHERE rn <= ? ORDER BY send_time DESC",
                (since_ts, per_chat)).fetchall()
        except Exception:  # 老 SQLite 没有窗口函数时退成全局限量
            rows = con.execute(
                "SELECT conversation_id, send_time, sender_id, content_type, content"
                f" FROM message_table WHERE send_time >= ? AND content_type IN ({types})"
                " ORDER BY send_time DESC LIMIT ?",
                (since_ts, per_chat * max_chats)).fetchall()
        names = wc_read._sender_names(con, [r["sender_id"] for r in rows])
        seen_chats = set()
        for r in rows:
            cid = r["conversation_id"]
            seen_chats.add(cid)
            ctype = int(r["content_type"] or 0)
            body = wc_read._body(r["content"], ctype)
            sid = int(r["sender_id"] or 0)
            items.append({
                "time": wc_read._fmt(r["send_time"]),
                "ts": int(r["send_time"] or 0),
                "channel": "企微",
                "chat": conv.get(cid, cid),
                "sender": names.get(sid, str(sid)),
                "text": wc_read._clip(body, max_text)
                if hasattr(wc_read, "_clip") else (body or "")[:max_text],
            })
        info["scanned_chats"] = len(seen_chats)
    except Exception as exc:  # noqa: BLE001
        info["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        con.close()
    return items, info


@TOOLS.tool(
    name="work_comms_notices",
    description=(
        "【只读·首选】一次调用跨微信 + 企业微信两条通道，取「最近」消息级时间线，"
        "默认最近 3 天、每会话最多 8 条、总计 40 条，正文裁到 220 字；"
        "给每条打 notice 标记（是否像通知 / 截止 / 待办）并抽出截止时间的原文片段，"
        "默认按「通知在前、有死线的更前、组内按时间倒序」排，闲聊沉底。"
        "用于「通讯软件里最近有什么值得注意的通知」「最近有哪些新消息」「两个微信最近在说什么」"
        "这类泛问——不要再用 overview + 逐会话 messages + 全文 search 拼十几轮。"
        "order=time 可切回纯时间线；要读某条全文、搜历史某句话，仍走各通道的 messages / search。"
    ),
)
def work_comms_notices(days: int = 3, limit: int = 40, per_chat: int = 8,
                       max_chats: int = 30, channels: str = "both",
                       only_notices: bool = False, keyword: str = "",
                       max_text: int = 220, since: str = "",
                       dedupe: bool = True, order: str = "notice") -> str:
    days = max(0, min(int(days or 0), 30))
    limit = max(1, min(int(limit or 40), 200))
    per_chat = max(1, min(int(per_chat or 8), 30))
    max_chats = max(1, min(int(max_chats or 30), 80))
    max_text = max(60, min(int(max_text or 220), 2000))
    order = "time" if (order or "").strip().lower() == "time" else "notice"
    ch = (channels or "both").strip().lower()
    want_wx = ch not in ("wecom", "企微", "企业微信")
    want_wc = ch not in ("wechat", "微信")

    now = datetime.now()
    if since.strip():
        parsed = wxread._parse_time(since.strip()) if wxread is not None else 0
        since_ts = int(parsed or 0)
        if not since_ts:
            return _dump({"ok": False, "error": f"since 无法识别：{since!r}",
                          "hint": "支持 2026-11-01 / 11-01 / 今天 / 昨天 / 近3天；或直接给 days"})
    else:
        since_ts = int((now - timedelta(days=days)).timestamp()) if days else 0

    channel_rows: List[Dict[str, Any]] = []
    all_items: List[Dict[str, Any]] = []
    if want_wx:
        wx_items, wx_info = _comms_wechat_items(since_ts, per_chat, max_chats, max_text)
        all_items.extend(wx_items)
        channel_rows.append({"id": "wechat", "name": "微信",
                             "ready": not wx_info["error"],
                             "status": "可读" if not wx_info["error"] else "读不到",
                             "error": wx_info["error"],
                             "scanned_chats": wx_info["scanned_chats"],
                             "items": len(wx_items)})
    if want_wc:
        wc_items, wc_info = _comms_wecom_items(since_ts, per_chat, max_chats, max_text)
        all_items.extend(wc_items)
        channel_rows.append({"id": "wecom", "name": "企业微信",
                             "ready": not wc_info["error"],
                             "status": "可读" if not wc_info["error"] else "读不到",
                             "error": wc_info["error"],
                             "scanned_chats": wc_info["scanned_chats"],
                             "items": len(wc_items)})

    for it in all_items:
        ok, sig = _notice_signals(it["text"])
        it["notice"] = ok
        it["signals"] = sig
        # 时间线索与死线分开：闲聊里「明天4:40」不该被当成待办，
        # 活动通知里的「11月6日晚7:00」也不该被当成「之前必须办完」。
        ref = _time_ref(it["text"]) if ok else ""
        it["time_ref"] = ref
        it["due"] = _deadline_hint(it["text"], ref)
    if keyword.strip():
        kw = keyword.strip().lower()
        all_items = [x for x in all_items if kw in x["text"].lower()]
    if only_notices:
        all_items = [x for x in all_items if x["notice"]]
    if dedupe:
        seen: set = set()
        uniq: List[Dict[str, Any]] = []
        for x in sorted(all_items, key=lambda y: y["ts"], reverse=True):
            key = (x["channel"], x["chat"], x["text"][:60])
            if key in seen:
                continue
            seen.add(key)
            uniq.append(x)
        all_items = uniq
    # 默认不是「纯时间线」：泛问通知时，最新几条往往是表情/跑腿订单，
    # 按时间倒序会把真正带死线的通知埋到 limit 之外，迫使调用方再翻一轮。
    if order == "time":
        all_items.sort(key=lambda x: x["ts"], reverse=True)
    else:
        all_items.sort(key=lambda x: (0 if x["notice"] else 1,
                                      0 if x["due"] else 1, -x["ts"]))
    total = len(all_items)
    items = all_items[:limit]
    by_chat: Dict[str, int] = {}
    for x in items:
        if x["notice"]:
            key = f"{x['channel']}/{x['chat']}"
            by_chat[key] = by_chat.get(key, 0) + 1
    return _dump({
        "ok": True,
        "at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "window": {
            "since": datetime.fromtimestamp(since_ts).strftime("%Y-%m-%d %H:%M:%S")
            if since_ts else "不限",
            "until": now.strftime("%Y-%m-%d %H:%M:%S"),
            "days": days,
        },
        "channels": channel_rows,
        "order": order,
        "count": len(items),
        "total_matched": total,
        "notice_count": sum(1 for x in items if x["notice"]),
        "due_count": sum(1 for x in items if x["due"]),
        "notice_by_chat": dict(sorted(by_chat.items(), key=lambda kv: -kv[1])[:10]),
        "truncated": total > limit,
        "items": items,
        "hint": ("默认 order=notice：通知在前、带 due 的更前、组内按时间倒序，闲聊沉底；"
                 "order=time 才是纯时间线。notice=true 只是「像通知」的召回标记，不是事实判定，"
                 "due 是带截止语义的原文片段（如「11月12日12点前」），time_ref 只是时间线索"
                 "（活动日期也算，不代表要办），"
                 "都别当成已确认的待办；要读某条全文用 "
                 "work_wechat_messages / work_wecom_messages 并传 full=true，"
                 "要翻历史用各自的 search。输出多时用 days / keyword / only_notices 收窄，"
                 "不要把 limit 一味调大。"),
    })



# --------------------------------------------------------------------------- #
# 工具 11：微信图片 / 附件解密
# --------------------------------------------------------------------------- #
@TOOLS.tool(
    name="work_wechat_media",
    description=(
        "【只读+导出】把微信聊天里的图片真正解出来。微信 4.x 把收到的图片存成 "
        "msg/attach 下的加密 .dat（AES-128-ECB + XOR），只能解出「[图片]」四个字等于没记。"
        "本工具按会话/时间解密图片并落盘，返回路径与尺寸，可直接嵌进材料或拿去核对。"
        "传 chat 说明要哪个会话；不传只看能力（密钥是否就绪、库里有几张图、ffmpeg 在不在）。"
        "prefer=thumb 走缩略图（快、无需 ffmpeg），prefer=orig 出原图（WXGF 需 ffmpeg 转 PNG）。"
        "只想取某一张时传 ref（消息里的 media_ref，32 位 hex）+ prefer=orig，"
        "缩略图只有 210px 宽，截图上的小字要看清楚必须取原图。"
    ),
)
def work_wechat_media(
    chat: str = "",
    limit: int = 20,
    since: str = "",
    until: str = "",
    prefer: str = "thumb",
    output_dir: str = "",
    account_dir: str = "",
    ref: str = "",
) -> str:
    if wx_media is None:
        return _dump({"ok": False, "error": "媒体模块不可用（wechat/media.py 导入失败）"})
    account = account_dir or _wechat_account_dir()
    cap = wx_media.capability(account)
    if not chat and not ref:
        return _dump({"ok": cap.get("ok", False), "mode": "capability", **cap})
    if not cap.get("ok"):
        return _dump({"ok": False, "mode": "capability", **cap,
                      "error": "图片密钥不可用，先看 notes"})
    if prefer not in ("thumb", "orig"):
        return _dump({"ok": False, "error": f"prefer 只能是 thumb 或 orig，收到 {prefer}"})

    # 单张：预览面板里点开一张图时走这条，专门去取同一条消息的原图。
    # 缩略图只有 210px 宽，群里截图上的字根本看不清，必须能按需换原图。
    if ref:
        if not _MEDIA_REF_OK.match(ref.strip()):
            return _dump({"ok": False, "error": f"ref 应为 32 位十六进制，收到 {ref!r}"})
        one = _resolve_path(output_dir, DIR_MEDIA / "单张原图")
        got = wx_media.export_one(account, ref.strip().lower(), str(one), prefer=prefer)
        return _dump({"ok": got.get("ok", False), "mode": "single", "chat": chat, **got})
    if wxread is None:
        return _dump({"ok": False, "error": "消息读取模块不可用（wxread 导入失败）"})
    bad = _check_time_args(since, until)
    if bad:
        return _dump({"ok": False, "error": bad})
    data = wxread.messages(chat, limit=max(20, int(limit) * 4), since=since, until=until)
    if not isinstance(data, dict):
        data = json.loads(data)
    if not data.get("ok"):
        return _dump({"ok": False, "error": data.get("error", "读取会话失败")})

    msgs = data.get("messages") or []
    picks = [m for m in msgs if m.get("media_ref") and m.get("type") == 3][-max(1, int(limit)):]
    if not picks:
        return _dump({"ok": True, "chat": (data.get("chat") or {}).get("name", chat),
                      "found": 0, "images": [],
                      "note": f"该会话最近 {len(msgs)} 条消息里没有图片"})

    chat_name = (data.get("chat") or {}).get("name") or chat
    dest = _resolve_path(output_dir, DIR_MEDIA / _safe_name(chat_name))
    uin = wx_media.discover_uin()
    # 必须用 is None 判断：0 是 falsy，但 `derive_key(0, wxid)` 会算出一把错密钥，
    # 解出来是随机字节，最后在 Pillow 那里报「无法识别图像」，绕远路还查不出根因。
    if uin is None:
        aes_key, xor_key = None, None
    else:
        aes_key, xor_key = wx_media.derive_key(uin, wx_media.wxid_of(account))

    out: List[Dict[str, Any]] = []
    fail = 0
    for m in picks:
        got = wx_media.export_one(account, m["media_ref"], str(dest), prefer=prefer,
                                  aes_key=aes_key, xor_key=xor_key)
        item = {
            "ref": m["media_ref"],
            "time": m.get("time"),
            "sender": m.get("sender"),
            "ok": got.get("ok", False),
        }
        if got.get("ok"):
            item.update({k: got.get(k) for k in ("path", "kind", "width", "height", "bytes", "from_thumb")})
        else:
            item["error"] = got.get("error")
            fail += 1
        out.append(item)

    _save_state("last_media_export", {"at": datetime.now().isoformat(timespec="seconds"),
                                      "chat": chat_name, "count": len(out) - fail,
                                      "dir": str(dest)})
    return _dump({
        "ok": (len(out) - fail) > 0,
        "chat": chat_name,
        "found": len(out),
        "exported": len(out) - fail,
        "failed": fail,
        "prefer": prefer,
        "output_dir": str(dest),
        "images": out,
        "capability": {"uin": cap.get("uin"), "ffmpeg": bool(cap.get("ffmpeg"))},
    })



# --------------------------------------------------------------------------- #
# 工具 14：聊天记录摘录成文
# --------------------------------------------------------------------------- #
@TOOLS.tool(
    name="work_wechat_digest",
    description=(
        "【写文件】把某个群/私聊的一段聊天整理成可存档的材料（docx 或 md）。"
        "按天分组、带发送人与时间，正文用公文习惯的仿宋三号、固定行距 28 磅。"
        "include_media=true 会把图片消息解密后嵌进正文——工作群的关键信息常常是截图，"
        "只留一行「[图片]」等于没记。支持 since/until 时间窗与 keyword 过滤。"
        "产物默认落在 03_汇总与成稿输出/。【无 dry_run 参数】只想看原始消息请用 "
        "work_wechat_search / work_wechat_messages（只读）。"
    ),
)
def work_wechat_digest(
    chat: str,
    since: str = "",
    until: str = "",
    keyword: str = "",
    limit: int = 300,
    title: str = "",
    format: str = "docx",
    output: str = "",
    include_media: bool = False,
    max_images: int = 12,
) -> str:
    _ensure_dirs()
    worker = WORKERS / "wx_digest.py"
    if not worker.exists():
        return _dump({"ok": False, "error": f"缺少摘录脚本: {worker}"})
    if format not in ("docx", "md"):
        return _dump({"ok": False, "error": "format 只能是 docx 或 md"})
    if wxread is not None:
        acc_check = _wechat_account_dir()
        if acc_check:
            wxread.ensure_cache(acc_check)
            if not wxread.resolve_chat(chat):
                return _dump({"ok": False, "error": f"找不到会话: {chat}"})

    argv = [sys.executable, str(worker), "--chat", chat,
            "--limit", str(max(1, int(limit))), "--format", format]
    if since:
        argv += ["--since", since]
    if until:
        argv += ["--until", until]
    if keyword:
        argv += ["--keyword", keyword]
    if title:
        argv += ["--title", title]
    if output:
        dst = _resolve_path(output, DIR_OUTPUT / os.path.basename(output))
        argv += ["--output", str(dst)]
    account = _wechat_account_dir()
    if include_media:
        argv += ["--include-media", "--max-images", str(max(0, int(max_images)))]
        if account:
            argv += ["--account", account]

    res = _run(argv, timeout=300)
    parsed = _parse_json(res["stdout"])
    if parsed is None:
        return _dump({"ok": False, "error": "摘录脚本没有返回 JSON",
                      "stdout": res["stdout"][-1500:], "stderr": res["stderr"][-1500:]})
    parsed["exit_code"] = res["exit_code"]
    if parsed.get("ok"):
        _save_state("last_digest", {"at": datetime.now().isoformat(timespec="seconds"),
                                    "chat": parsed.get("chat"),
                                    "messages": parsed.get("messages"),
                                    "output": parsed.get("output_path")})
    return _dump(parsed)

