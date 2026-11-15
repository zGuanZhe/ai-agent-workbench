#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""work_mods/office.py — Office 模块。

归属：所有 `work_office_*` / `work_officecli_*` / `work_excel_mcp` / `work_word_mcp`。
边界：本地文件读写，写前自动备份；不碰微信、不读聊天记录。
升级本模块不需要动 comms.py；跑 `python tools/regress.py --module office` 单独验收。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from work_core import (  # noqa: F401
    ALLOWED_EXTS, BIN, DIR_ARCHIVE, DIR_BACKUP, DIR_HARVESTED, DIR_INBOX,
    DIR_MEDIA, DIR_OUTPUT, DIR_WECOM_HARVESTED, MASTER_TEMPLATE, OFFICECLI,
    PENDING_FILE, ROOT, ROSTER, STATE_DIR, STATE_FILE, WORKERS, WORKSPACE,
    ToolBox, _dump, _ensure_dirs, _file_arg, _list_files, _load_state,
    _parse_json, _port_open, _resolve_path, _run, _safe_name, _save_state,
    _unwrap_officecli,
)

TOOLS = ToolBox("office")

import atexit
import csv
import difflib
import threading

_OCLI_LOCK = threading.Lock()
_OCLI_PROC: Optional[subprocess.Popen] = None
_OCLI_REQ_ID = 10


def _shutdown_officecli() -> None:
    global _OCLI_PROC
    proc = _OCLI_PROC
    _OCLI_PROC = None
    if proc is not None:
        try:
            proc.terminate()
        except Exception:
            pass


atexit.register(_shutdown_officecli)


def _ensure_officecli_mcp() -> Optional[subprocess.Popen]:
    global _OCLI_PROC
    if _OCLI_PROC is not None and _OCLI_PROC.poll() is None:
        return _OCLI_PROC
    if not OFFICECLI.is_file():
        return None
    env = dict(os.environ)
    env["OFFICECLI_RESIDENT_FLUSH"] = "each"
    try:
        proc = subprocess.Popen(
            [str(OFFICECLI), "mcp"],
            cwd=str(ROOT),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        init_msg = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "work-office", "version": "1.1"},
            },
        }
        proc.stdin.write(json.dumps(init_msg) + "\n")
        proc.stdin.flush()
        while True:
            line = proc.stdout.readline()
            if not line:
                raise RuntimeError("officecli mcp EOF during init")
            data = json.loads(line)
            if data.get("id") == 1:
                break
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        proc.stdin.flush()
        _OCLI_PROC = proc
        return proc
    except Exception:
        try:
            proc.terminate()  # type: ignore[union-attr]
        except Exception:
            pass
        _OCLI_PROC = None
        return None


def _run_officecli(argv: List[str], timeout: int = 120) -> Dict[str, Any]:
    """优先通过进程内常驻的 officecli mcp 管道执行命令（免去每次 .NET 冷启动），失败自动回退 _run。"""
    global _OCLI_REQ_ID, _OCLI_PROC
    cmd_args = list(argv[1:]) if argv and Path(argv[0]).name.lower().startswith("officecli") else list(argv)
    sub = (cmd_args[0] if cmd_args else "").lower()
    # watch / unwatch / install 走独立子进程，或者当前有活跃 watch 预览时走 CLI 触发刷新
    watch_pid = int((_watch_info() or {}).get("pid") or 0)
    if sub in ("watch", "unwatch", "install", "skills") or (watch_pid and _pid_alive(watch_pid)):
        return _run(argv, timeout=timeout)
    with _OCLI_LOCK:
        proc = _ensure_officecli_mcp()
        if proc is None or proc.stdin is None or proc.stdout is None:
            return _run(argv, timeout=timeout)
        _OCLI_REQ_ID += 1
        req_id = _OCLI_REQ_ID
        req = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "tools/call",
            "params": {"name": "officecli", "arguments": {"command": cmd_args}},
        }
        try:
            proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
            proc.stdin.flush()
            while True:
                line = proc.stdout.readline()
                if not line:
                    raise RuntimeError("officecli mcp pipe closed")
                resp = json.loads(line)
                if resp.get("id") == req_id:
                    break
            if "error" in resp:
                err_obj = resp["error"]
                return {"exit_code": 1, "stdout": "", "stderr": json.dumps(err_obj, ensure_ascii=False)}
            res_obj = resp.get("result") or {}
            is_err = bool(res_obj.get("isError"))
            texts: List[str] = []
            images: List[Dict[str, Any]] = []
            for item in res_obj.get("content") or []:
                if isinstance(item, dict):
                    if item.get("type") == "text" and item.get("text"):
                        texts.append(str(item["text"]))
                    elif item.get("type") == "image":
                        images.append({"mimeType": item.get("mimeType"), "data_len": len(str(item.get("data") or ""))})
            out_text = "\n".join(texts).strip()
            return {"exit_code": 1 if is_err else 0, "stdout": out_text, "stderr": "", "images": images}
        except Exception:
            _shutdown_officecli()
            return _run(argv, timeout=timeout)


MODULE = {
    "id": "office",
    "name": "Office",
    "version": "2.0.0",
    "scope": "单一 Office MCP：OfficeCLI 检视/生成/批量编辑、汇总核验、版本对比、Word 合并、精确 PDF、COM 实时操控与按需高级 Excel/Word 引擎",
    "entry": "mcp/work_office_server.py",
    "docs": "skills/work-office/references/office.md",
}


def status_engines() -> List[Dict[str, Any]]:
    """Office 侧的自查条目。入口存在 ≠ 可用，权威判定仍是 work_office_engines。"""
    _mcp = ROOT / "mcp_servers"
    _word_entry = _mcp / "Office-Word-MCP-Server" / "word_document_server" / "main.py"
    _excel_entry = _mcp / "excel-mcp-server" / "src" / "excel_mcp" / "server" / "__init__.py"
    _om_entry = _mcp / "OfficeMCP" / "src" / "officemcp" / "OfficeMCP.py"
    try:
        import win32com.client  # type: ignore  # noqa: F401
        _com_ok = True
    except Exception:  # noqa: BLE001
        _com_ok = False
    return [
        {"id": "officecli", "name": "iOfficeAI/OfficeCLI v1.0.161", "path": str(OFFICECLI),
         "ready": OFFICECLI.is_file(), "file_exists": OFFICECLI.exists(),
         "bridge": "work_office_inspect / work_office_compose / work_office_batch", "note": "已接入：检视、生成、批量编辑与结构校验"},
        {"id": "officemcp", "name": "OfficeMCP (WPS/MS Office COM)", "path": str(_om_entry),
         "ready": _om_entry.is_file() and _com_ok, "file_exists": _om_entry.exists(),
         "bridge": "work_office_com",
         "note": "工具箱已有等价 COM 封装，不重复起 MCP 服务" + ("" if _com_ok else "；pywin32 不可用")},
        {"id": "word-mcp", "name": "Office-Word-MCP-Server", "path": str(_word_entry),
         "ready": _word_entry.is_file(), "file_exists": _word_entry.exists(),
         "bridge": "work_office_advanced(engine=word)", "note": "已接入：58 个 Word 高级工具（默认只读）"},
        {"id": "excel-mcp", "name": "harris-musa/excel-mcp-server", "path": str(_excel_entry),
         "ready": _excel_entry.is_file(), "file_exists": _excel_entry.exists(),
         "bridge": "work_office_advanced(engine=excel)", "note": "已接入：46 个高级 Excel 工具（默认只读）"},
        {"id": "pdf", "name": "Office→PDF（COM）", "path": str(WORKERS / "office_convert.py"),
         "ready": (WORKERS / "office_convert.py").exists(), "file_exists": (WORKERS / "office_convert.py").exists(),
         "note": "需本机装 Office（本机 Word/Excel 16.0 实测可用）"},
    ]


def officecli_version() -> Optional[str]:
    if not OFFICECLI.exists():
        return None
    v = _run_officecli([str(OFFICECLI), "--version"], timeout=20)
    return v["stdout"] or None


def _reap_office_com_pids() -> List[Dict[str, Any]]:
    """清理 worker 记录下来的、由它拉起但没关掉的 Office/WPS 进程。

    为什么需要：run_python 里的 settrace 只能打断 Python 字节码，脚本卡在
    time.sleep 或某个 COM 调用上时 trace 不触发，外层 subprocess 会强杀 worker，
    finally 不执行，Excel/WPS 就变成占内存和文件锁的僵尸。
    worker 在拉起实例的那一刻就把 PID 写进 office_com_pids.json，这里按记录收尾。
    只杀记录里的 PID（= 工具自己拉起的），用户手动打开的窗口不在其中。
    """
    pid_file = STATE_DIR / "office_com_pids.json"
    if not pid_file.is_file():
        return []
    killed: List[Dict[str, Any]] = []
    try:
        data = json.loads(pid_file.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    for label, info in (data or {}).items():
        pid = int((info or {}).get("pid") or 0)
        if not pid:
            continue
        try:
            r = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                               capture_output=True, timeout=20)
            if r.returncode == 0:
                killed.append({"engine": label, "pid": pid})
        except Exception:
            pass
    try:
        pid_file.unlink()
    except Exception:
        pass
    return killed

# --------------------------------------------------------------------------- #
# 工具 6：跨表汇总 + 勾稽校验
# --------------------------------------------------------------------------- #
@TOOLS.tool(
    name="work_office_consolidate",
    description=(
        "通用多源表格合并与数据核验：批量读取文件夹内的 Excel(.xlsx)、CSV/TSV 及 Word 表格(.docx)，"
        "支持按母表模板对齐字段，也支持 auto_schema=true（或 template_path='auto'）无模板自动推断并对齐表头！"
        "自动统计各数值列合计、校验漏填项、手机号/身份证格式及分项与总计勾稽差额，"
        "输出汇总表(.xlsx)与《异常数据核验单》并用 OfficeCLI 校验 OpenXML 结构。"
        "【会写文件】产物默认落在 03_汇总与成稿输出/。"
    ),
)
def work_office_consolidate(
    source_dir: str = "",
    template_path: str = "",
    output_path: str = "",
    rules: str = "",
    auto_schema: bool = False,
) -> str:
    _ensure_dirs()
    src = _resolve_path(source_dir, DIR_HARVESTED)
    if not src.exists():
        return _dump({"ok": False, "error": f"源目录不存在: {src}"})
    tpl_raw = (template_path or "").strip()
    use_auto = bool(auto_schema) or tpl_raw.lower() in ("auto", "none")
    if use_auto:
        tpl_arg = "auto"
    else:
        tpl = _resolve_path(tpl_raw, MASTER_TEMPLATE)
        if not tpl.exists():
            if not tpl_raw:
                tpl_arg = "auto"
                use_auto = True
            else:
                return _dump({"ok": False, "error": f"母表模板不存在: {tpl}"})
        else:
            tpl_arg = str(tpl)
    out = _resolve_path(output_path, DIR_OUTPUT / ("通用多表汇总.xlsx" if use_auto and not output_path else "2026年11月汇总母表.xlsx"))

    cmd = [
        sys.executable,
        str(WORKERS / "merge_docx_tables.py"),
        "--src",
        str(src),
        "--tpl",
        tpl_arg,
        "--out",
        str(out),
        "--rules",
        rules or "经费总计必须等于各分项之和，联系电话必须为11位数字",
    ]
    if use_auto:
        cmd.append("--auto-schema")
    res = _run(cmd, timeout=300)
    parsed = _parse_json(res["stdout"])
    if not parsed or parsed.get("status") != "success":
        return _dump({"ok": False, "cmd": cmd, **res})

    validation = None
    if OFFICECLI.exists():
        v = _run_officecli([str(OFFICECLI), "validate", str(out), "--json"], timeout=120)
        validation = _parse_json(v["stdout"]) or {"raw": v["stdout"], "stderr": v["stderr"]}

    parsed["officecli_validation"] = validation
    parsed["consolidated_at"] = datetime.now().isoformat(timespec="seconds")

    # 收件与汇总各说各话：harvest 的「未交」只看本次时间窗内的新材料，
    # consolidate 读的是归档目录全量。不把口径写出来，就会出现
    # 「未交 5 家」紧接着「合出 3 家母表」这种看起来自相矛盾的转述。
    rows = parsed.get("merged_rows") or []
    # 母表列名可能是「科室名称 / 单位 / 填报单位」，不能写死成「科室」。
    headers = [str(h) for h in (parsed.get("master_headers") or [])]
    dept_key = next((h for h in headers if "科室" in h or "单位" in h), "")
    covered = sorted({str(r.get(dept_key, "")).strip() for r in rows
                      if str(r.get(dept_key, "")).strip()}) if dept_key else []
    last = _load_state().get("last_harvest") or {}
    missing_then = [str(x) for x in (last.get("missing_departments") or [])]
    submitted_then = [str(x) for x in (last.get("submitted_departments") or [])]
    overlap = sorted(set(covered) & set(missing_then))
    consistency: Dict[str, Any] = {
        "merged_departments": covered,
        "processed_files_count": parsed.get("processed_files_count", 0),
        "harvest_at": last.get("harvested_at", ""),
        "harvest_submitted_departments": submitted_then,
        "harvest_missing_departments": missing_then,
        "conflict_departments": overlap,
        "scope_note": (
            "汇总读的是归档目录里的全部文件（本次 %d 份），与收件时间窗无关；"
            "收件环节的「已交/未交」只统计本次时间窗内新收到的材料。"
            "两处数字不一致属正常，转述时先讲清口径，不要并列成矛盾。"
            % int(parsed.get("processed_files_count", 0) or 0)),
    }
    if not last:
        consistency["level"] = "info"
        consistency["detail"] = "本次没有前序收件记录可对照，只能据汇总结果本身转述。"
    elif overlap:
        consistency["level"] = "warn"
        consistency["detail"] = (
            f"{'、'.join(overlap)} 在收件环节被判为「本次未交」，但汇总里有它们的材料"
            "（归档目录存着更早批次）。转述时要说「本次未交」，不能说成「从未提交」，"
            "也不要据此改催办名单——催办名单以 harvest 的 missing_departments 为准。")
    elif missing_then and not set(covered) & set(missing_then):
        consistency["level"] = "ok"
        consistency["detail"] = (
            f"口径一致：收件说未交的 {len(missing_then)} 家，母表里确实没有它们的数据。")
    else:
        consistency["level"] = "ok"
        consistency["detail"] = "收件与汇总未发现冲突。"
    parsed["consistency"] = consistency

    _save_state("last_consolidate", parsed)
    return _dump({"ok": True, **parsed})


# --------------------------------------------------------------------------- #
# 工具 7：GB/T 9704 国标公文排版
# --------------------------------------------------------------------------- #
@TOOLS.tool(
    name="work_office_format_gbt9704",
    description=(
        "【内部兼容，不通过 MCP 暴露；新调用请用 work_office_compose】把 Markdown 草稿、纯文本或普通 Word 一键排版为规范 .docx 文档："
        "支持 preset='gbt9704'（默认，《党政机关公文格式》GB/T 9704-2012：37/35/28/26mm页边距、红头版头、二号小标宋标题、三号仿宋28磅行距）、"
        "'modern'（现代商务/技术报告：微软雅黑深蓝标题与表头着色）、'academic'（学术论文：宋体/Times New Roman 与三线表）、"
        "'memo'（简洁备忘录），并自动用 OfficeCLI 校验。"
        "content_or_path 既可以是文件路径，也可以直接传整篇 Markdown 文本。"
    ),
)
def work_office_format_gbt9704(
    content_or_path: str,
    doc_type: str = "报告",
    header_unit: str = "",
    doc_number: str = "",
    output_path: str = "",
    preset: str = "gbt9704",
) -> str:
    _ensure_dirs()
    raw = (content_or_path or "").strip()
    source_kind = "path"
    draft_used = ""
    # 早先留空会静默拿 workspace/04_单位历年材料库/2026年度工作总结草稿.md 去排：
    # 用户说「按国标排版这份通知」，结果排出来的是一份工作总结，还照样报成功。
    # 现在 content_or_path 是必填，空值直接拒绝，绝不猜用哪份稿。
    if not raw:
        return _dump({"ok": False,
                      "error": "content_or_path 不能为空：要么给 Word/Markdown 文件路径，要么把整篇正文传进来",
                      "hint": "不会自动挑草稿——曾因此把「工作总结草稿」排成了「通知」。"
                              "找不到稿就先问用户要文件或正文。"})
    if Path(raw).exists():
        input_arg = str(Path(raw).resolve())
    else:
        # 视作内联正文：落到系统临时目录。早先写进 04_单位历年材料库，
        # 每排一次公文就在用户材料库里多一份「当前编辑草稿_时间戳.md」。
        draft_dir = Path(tempfile.gettempdir()) / "work-toolkit" / "drafts"
        draft_dir.mkdir(parents=True, exist_ok=True)
        tmp = draft_dir / f"当前编辑草稿_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        tmp.write_text(raw, encoding="utf-8")
        input_arg = str(tmp)
        draft_used = str(tmp)
        source_kind = "inline"

    out = _resolve_path(output_path, DIR_OUTPUT / f"{Path(input_arg).stem}_国标排版.docx")
    cmd = [
        sys.executable,
        str(WORKERS / "gbt9704_formatter.py"),
        "--input",
        input_arg,
        "--type",
        doc_type,
        "--header",
        header_unit,
        "--docnum",
        doc_number,
        "--preset",
        (preset or "gbt9704").strip().lower(),
        "--out",
        str(out),
    ]
    res = _run(cmd, timeout=240)
    parsed = _parse_json(res["stdout"])
    if not parsed or parsed.get("status") != "success":
        return _dump({"ok": False, "cmd": cmd, **res})

    validation = None
    if OFFICECLI.exists():
        v = _run_officecli([str(OFFICECLI), "validate", str(out), "--json"], timeout=120)
        validation = _parse_json(v["stdout"]) or {"raw": v["stdout"], "stderr": v["stderr"]}

    parsed["officecli_validation"] = validation
    # 让调用方一眼看出这篇稿子是从哪来的：内联正文 / 指定文件 / 兜底默认草稿
    parsed["source_kind"] = source_kind
    if draft_used:
        parsed["source_draft"] = draft_used
    if source_kind == "default_draft":
        parsed["source_note"] = "未传 content_or_path，使用了历年材料库里的默认草稿；如需排自己的稿子请传正文或文件路径"
    parsed["formatted_at"] = datetime.now().isoformat(timespec="seconds")
    _save_state("last_format", parsed)
    return _dump({"ok": True, **parsed})


# --------------------------------------------------------------------------- #
# 工具 8：OfficeCLI 通用命令
# --------------------------------------------------------------------------- #
OFFICECLI_COMMANDS = {
    "validate",
    "view",
    "get",
    "query",
    "set",
    "add",
    "remove",
    "move",
    "swap",
    "raw",
    "raw-set",
    "add-part",
    "batch",
    "refresh",
    "save",
    "close",
    "open",
    # 早期白名单只放了「改已有文件」的 17 条，把建文档/套模板/导数据/导出结构
    # 这些真正能干活的能力挡在外面，agent 只能绕路自己写脚本。下面补齐：
    "dump",      # 导出可回放的 batch 脚本
    "help",      # schema 参考（配 work_office_schema 更好用）
    "plugins",   # 看插件
    "skills",    # 装 agent skill 定义
    "install",   # 一步装好 binary + skills + MCP
    "create",    # 建空文档（也有 work_office_create，这里留原始入口）
    "merge",     # 模板套打（也有 work_office_merge）
    "import",    # CSV 导入（也有 work_office_import）
    "unwatch",   # 停预览
    "watch",     # 起预览（会常驻，建议走 work_office_watch）
}

# watch 是常驻服务，用 officecli_run 调会被 timeout 掐断，直接引导到专用工具
LONG_RUNNING_COMMANDS = {"watch"}


@TOOLS.tool(
    name="work_officecli_run",
    description=(
        "【内部兼容，不通过 MCP 暴露；结构化编辑请用 work_office_batch】直接调用内置 OfficeCLI 的单个命令。"
        "command 支持 validate/view/get/query/set/add/remove/move/swap/raw/raw-set/"
        "add-part/batch/refresh/save/open/close/dump/help/plugins；"
        "args 为附加参数数组，DOM 路径是位置参数，例如 [\"/body/p[1]\",\"--prop\",\"bold=true\"]。"
        "多步操作用 work_office_batch；建新文档用 work_office_create；套模板用 work_office_merge；"
        "导 CSV 用 work_office_import；查属性名用 work_office_schema。"
        "写操作前先 work_backup_file，写后建议 command=validate。"
    ),
)
def work_officecli_run(
    command: str = "validate",
    file: str = "",
    args: Optional[List[str]] = None,
    timeout: int = 120,
) -> str:
    if not OFFICECLI.exists():
        return _dump({"ok": False, "error": f"未找到 OfficeCLI: {OFFICECLI}"})
    cmd = (command or "validate").strip()
    if cmd == "--version":
        res = _run_officecli([str(OFFICECLI), "--version"], timeout=20)
        return _dump({"ok": res["exit_code"] == 0, "version": res["stdout"], **res})
    if cmd not in OFFICECLI_COMMANDS:
        return _dump({"ok": False, "error": f"不支持的子命令: {cmd}", "allowed": sorted(OFFICECLI_COMMANDS)})
    if cmd in LONG_RUNNING_COMMANDS:
        return _dump({"ok": False,
                      "error": f"{cmd} 是常驻服务，用 work_office_watch 起停",
                      "next": 'work_office_watch(action="start", file="...")'})
    target, err = _file_arg(file)
    if err:
        return _dump({"ok": False, "error": err})

    argv = [str(OFFICECLI), cmd, str(target), *(args or []), "--json"]
    res = _run_officecli(argv, timeout=timeout)
    parsed = _parse_json(res["stdout"])
    payload = {
        "ok": res["exit_code"] == 0,
        "command": " ".join(argv),
        "exit_code": res["exit_code"],
        "result": parsed,
        "stdout": res["stdout"][:8000] if parsed is None else None,
        "stderr": res["stderr"][:4000] or None,
    }
    # officecli 的失败原因在 result.error 里，stderr 是空的。不提到顶层，
    # 上层只会看到一句「ok:false」+ stderr:null，等于没说为什么。
    if parsed is not None and not payload["ok"]:
        err = parsed.get("error") if isinstance(parsed, dict) else None
        if isinstance(err, dict):
            payload["error"] = err.get("error") or json.dumps(err, ensure_ascii=False)
            if err.get("code"):
                payload["error_code"] = err.get("code")
        elif err:
            payload["error"] = str(err)
        payload["hint"] = ("先 work_office_schema(format=..., element=...) 确认路径与属性名；"
                          "xlsx 的路径是 /<表名>/<A1>，表名要真实存在（不是 Sheet1）")
    _save_state("last_officecli", payload)
    return _dump(payload)


# --------------------------------------------------------------------------- #
# 工具 8a：OfficeCLI 实时预览（watch）——改一下，浏览器里立刻能看到
# --------------------------------------------------------------------------- #
_WATCH: Dict[str, Any] = {}          # 本进程内持有的 watch 子进程
_WATCH_LOG = STATE_DIR / "watch.log"


def _pid_alive(pid: int) -> bool:
    """判断进程还在不在。不能用 os.kill(pid, 0)：Windows 上那是 TerminateProcess。"""
    if not pid:
        return False
    try:
        import ctypes

        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))  # QUERY_LIMITED
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    except Exception:  # noqa: BLE001
        return False


def _watch_info() -> Dict[str, Any]:
    """优先看本进程持有的对象，其次看上次落盘的状态（进程重启后仍能找回）。"""
    if _WATCH:
        return dict(_WATCH)
    return dict(_load_state().get("last_watch") or {})


@TOOLS.tool(
    name="work_office_watch",
    description=(
        "【内部兼容，不通过 MCP 暴露】用 OfficeCLI watch 给 docx/xlsx/pptx 开实时预览网页："
        "之后所有 work_officecli_run / work_office_batch 的改动会自动刷新到页面上，改完立刻能看见。"
        "action=start 起服务（返回 url，默认端口 26315）；status 看是否还在跑；stop 关掉；"
        "mark/unmark/marks/goto 用来在预览里标记与跳转到某个元素（path 形如 /body/p[1]）。"
        "外部程序改文件不会被感知；只认 officecli 自己的改动。用户没要求「看着改」就不用起它。"
    ),
)
def work_office_watch(action: str = "start", file: str = "", port: int = 26315,
                      path: str = "", all: bool = False) -> str:
    if not OFFICECLI.exists():
        return _dump({"ok": False, "error": f"未找到 OfficeCLI: {OFFICECLI}"})
    act = (action or "start").strip()

    if act == "status":
        info = _watch_info()
        if not info.get("pid"):
            return _dump({"ok": True, "running": False,
                          "hint": '没起预览。要看得见改动就 work_office_watch(action="start", file="...")'})
        alive = _pid_alive(int(info["pid"]))
        info["running"] = alive
        info["url"] = f"http://127.0.0.1:{info.get('port')}" if alive else ""
        if not alive:
            info["hint"] = "上次的 watch 进程已经不在了（可能随上回会话退出），重新 start 即可"
        return _dump({"ok": True, **info})

    if act == "stop":
        info = _watch_info()
        proc = (_WATCH.get("proc") if _WATCH else None)
        pid = int(info.get("pid") or 0)
        stopped = False
        if proc is not None:
            try:
                proc.terminate()
                stopped = True
            except Exception:
                pass
        if not stopped and pid and _pid_alive(pid):
            # 跨进程（上一轮会话留下的）只能走 taskkill 杀进程树
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, timeout=20)
            stopped = True
        _WATCH.clear()
        _save_state("last_watch", {})
        return _dump({"ok": True, "stopped": stopped,
                      "hint": None if stopped else "本来就没在跑"})

    if act in ("mark", "unmark", "marks", "goto"):
        target, err = _file_arg(file)
        if err:
            return _dump({"ok": False, "error": err})
        # 顺序是 `officecli watch <子命令> <file> [path]`，不是 `watch <file> <子命令>`。
        # 原来拼成后者，mark/unmark/marks/goto 全部报「多余参数」，四个动作等于没有。
        argv = [str(OFFICECLI), "watch", act, str(target)]
        if act in ("mark", "goto"):
            if not path:
                return _dump({"ok": False, "error": f"{act} 需要 path，形如 /body/p[1]"})
            argv.append(path)
        if act == "unmark":
            argv += ["--all"] if all else ["--path", path or ""]
        res = _run(argv, timeout=60)
        payload = {"ok": res["exit_code"] == 0, "command": " ".join(argv),
                   "stdout": res["stdout"][:4000], "stderr": res["stderr"][:2000] or None}
        if not payload["ok"]:
            # officecli 把真原因写在 stderr（例如 goto 传了 xlsx 单元格会说
            # "Cannot resolve scroll target..."），不提上来调用方只看得到「失败」
            payload["error"] = (res["stderr"] or res["stdout"] or "命令失败").strip()[:600]
        return _dump(payload)

    if act != "start":
        return _dump({"ok": False, "error": f"未知 action: {act}",
                      "allowed": ["start", "status", "stop", "mark", "unmark", "marks", "goto"]})

    target, err = _file_arg(file)
    if err:
        return _dump({"ok": False, "error": err})

    cur = _watch_info()
    if cur.get("pid") and _pid_alive(int(cur["pid"])):
        return _dump({"ok": True, "already_running": True, "url": f"http://127.0.0.1:{cur.get('port')}",
                      "file": cur.get("file"),
                      "hint": "已经有一个预览在跑；换文件先 action=stop"})

    port = int(port or 26315)
    if _port_open("127.0.0.1", port):
        return _dump({"ok": False, "error": f"端口 {port} 已被占用",
                      "hint": "换一个 port，或先关掉占用它的程序"})

    _ensure_dirs()
    argv = [str(OFFICECLI), "watch", str(target), "--port", str(port)]
    try:
        log = open(_WATCH_LOG, "ab")
        proc = subprocess.Popen(argv, cwd=str(ROOT), stdout=log, stderr=log,
                                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    except Exception as exc:  # noqa: BLE001
        return _dump({"ok": False, "error": f"起不来：{exc}"})

    # 起来要一点时间，等端口通了再返回，别让上层「起了但打不开」
    deadline = time.time() + 12
    up = False
    while time.time() < deadline:
        if _port_open("127.0.0.1", port):
            up = True
            break
        if proc.poll() is not None:
            break
        time.sleep(0.3)

    if not up:
        tail = ""
        try:
            tail = _WATCH_LOG.read_text(encoding="utf-8", errors="replace")[-800:]
        except Exception:
            pass
        try:
            proc.terminate()
        except Exception:
            pass
        return _dump({"ok": False, "error": "预览服务没起来（端口一直没通）",
                      "log_tail": tail,
                      "hint": "文档被占用或端口冲突；先 work_officecli_run command=close"})

    _WATCH.update({"pid": proc.pid, "port": port, "file": str(target),
                   "url": f"http://127.0.0.1:{port}", "proc": proc,
                   "started_at": datetime.now().isoformat(timespec="seconds")})
    _save_state("last_watch", {k: v for k, v in _WATCH.items() if k != "proc"})
    return _dump({"ok": True, "running": True, "url": f"http://127.0.0.1:{port}",
                  "file": str(target), "pid": proc.pid,
                  "note": "让用户用浏览器打开这个地址；之后 officecli 的每次改动都会自动刷新",
                  "stop": 'work_office_watch(action="stop")'})



# --------------------------------------------------------------------------- #
# 工具 8b：OfficeCLI schema 参考（读）
# --------------------------------------------------------------------------- #
def _tmp_json(name: str, obj: Any) -> Path:
    """把大块 JSON 落成临时文件：Windows 命令行有 32k 上限，塞 argv 会截断。"""
    _ensure_dirs()
    d = STATE_DIR / ".tmp"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}_{datetime.now().strftime('%H%M%S%f')}.json"
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return p


@TOOLS.tool(
    name="work_office_schema",
    description=(
        "【内部兼容，不通过 MCP 暴露；新调用请用 work_office_inspect(mode=\"schema\")】查 OfficeCLI schema。"
        "写 add/set 的 --prop 之前先查这里，不要凭印象编属性名。"
        "format 留空列出全部格式；只给 format 列元素；再给 element 列该元素的属性表。"
        "【空参只返回总览 mode=overview】要属性名必须带 format（最好连 verb / element 一起给）。"
    ),
)
def work_office_schema(format: str = "", verb: str = "", element: str = "") -> str:
    if not OFFICECLI.exists():
        return _dump({"ok": False, "error": f"未找到 OfficeCLI: {OFFICECLI}"})
    fmt = (format or "").strip()
    vrb = (verb or "").strip()
    elm = (element or "").strip()
    argv = [str(OFFICECLI), "help"]
    for part in (fmt, vrb, elm):
        if part:
            argv.append(part)
    res = _run_officecli(argv, timeout=60)
    text = res["stdout"] or res["stderr"]
    payload: Dict[str, Any] = {
        "ok": res["exit_code"] == 0 and bool(text.strip()),
        "query": " ".join(argv[2:]) or "(全部)",
        "text": text[:20000],
        "truncated": len(text) > 20000,
    }
    # 空参调用只会拿到顶层 help，等于答非所问：调用方要的是「元素/属性怎么填」。
    # 这里明确标 overview，并把三档用法摆出来，别让它拿着顶层菜单猜属性名。
    if not (fmt or vrb or elm):
        payload["mode"] = "overview"
        payload["params"] = {
            "format": "docx / xlsx / pptx；留空列全部格式",
            "verb": "add / set / get / query / remove；留空列元素清单",
            "element": "元素名，如 paragraph / run / cell / slide / shape",
        }
        payload["examples"] = [
            'work_office_schema(format="docx")  # 列 docx 有哪些元素',
            'work_office_schema(format="docx", verb="set")  # 列 set 能改什么',
            'work_office_schema(format="docx", verb="set", element="paragraph") '
            "# 列该元素的属性名与可选值",
        ]
        payload["hint"] = ("这是总览，不是你问的属性表。带上 format（+ verb / element）"
                           "再查一次，才有可写进 --prop 的属性名。")
    else:
        payload["mode"] = "query"
    if not payload["ok"]:
        payload["hint"] = ("格式可选 docx / xlsx / pptx；元素名见上一级列表；"
                           "动词可选 add / set / get / query / remove")
    return _dump(payload)


# --------------------------------------------------------------------------- #
# 工具 8c：批量编辑（一次开关文件，多步操作）
# --------------------------------------------------------------------------- #
@TOOLS.tool(
    name="work_office_batch",
    description=(
        "【写文件·Office 首选编辑入口】一次执行多条 OfficeCLI 操作，一次开关文件，比逐条 add/set 更快。"
        "ops 是操作数组 JSON（也可传 .json 文件路径）；add 用 parent+type+props，set 用 path+props。"
        "create=true 时目标不存在会先按扩展名创建空白 docx/xlsx/pptx；默认先自动备份，完成后自动 validate。"
        "需要属性名时先用 work_office_inspect(mode='schema') 查询，改完再用 inspect 检查内容与版式。"
    ),
)
def work_office_batch(file: str, ops: str = "", timeout: int = 300,
                      create: bool = False, backup: bool = True,
                      validate: bool = True) -> str:
    if not OFFICECLI.exists():
        return _dump({"ok": False, "error": f"未找到 OfficeCLI: {OFFICECLI}"})
    if not (file or "").strip():
        return _dump({"ok": False, "error": "file 不能为空：传文件绝对路径，或工作区相对路径"})
    target = _resolve_path(file, DIR_OUTPUT / Path(file).name)
    ext = target.suffix.lower()
    if ext not in (".docx", ".xlsx", ".pptx"):
        return _dump({"ok": False, "error": f"只支持 .docx/.xlsx/.pptx，收到 {ext or '（无扩展名）'}"})

    raw = (ops or "").strip()
    if not raw and not create:
        return _dump({"ok": False, "error": "ops 不能为空：给操作数组的 JSON，或 .json 文件路径",
                      "hint": "若只是新建空白文件，传 create=true"})

    tmp: Optional[Path] = None
    ops_path: Optional[Path] = None
    if raw:
        if raw.startswith("@") or (raw.lower().endswith(".json") and Path(raw).is_file()) \
                or (Path(raw).exists() if len(raw) < 260 else False):
            p = Path(raw[1:] if raw.startswith("@") else raw)
            if not p.is_absolute():
                p = (ROOT / p).resolve()
            if not p.is_file():
                return _dump({"ok": False, "error": f"操作文件不存在: {p}"})
            ops_path = p
        else:
            try:
                parsed_ops = json.loads(raw)
            except Exception as exc:
                return _dump({"ok": False, "error": f"ops 不是合法 JSON: {exc}",
                              "example": '[{"command":"add","parent":"/body","type":"paragraph","props":{"text":"正文"}}]'})
            if not isinstance(parsed_ops, list) or not parsed_ops:
                return _dump({"ok": False, "error": "ops 必须是非空数组"})
            tmp = _tmp_json("ops", parsed_ops)
            ops_path = tmp

    created = False
    backup_path = ""
    if target.is_file():
        if backup:
            backup_path = _backup_target({"path": str(target)})
    elif create:
        target.parent.mkdir(parents=True, exist_ok=True)
        cres = _run_officecli([str(OFFICECLI), "create", str(target), "--json"], timeout=120)
        if cres["exit_code"] != 0 or not target.is_file():
            if tmp is not None:
                try:
                    tmp.unlink()
                except Exception:
                    pass
            return _dump({"ok": False, "error": "目标文件不存在且自动创建失败",
                          "file": str(target), "stdout": cres["stdout"][:2000],
                          "stderr": cres["stderr"][:2000]})
        created = True
    else:
        if tmp is not None:
            try:
                tmp.unlink()
            except Exception:
                pass
        return _dump({"ok": False, "error": f"文件不存在: {target}",
                      "hint": "确认路径，或传 create=true 先创建空白文件"})

    if not raw:
        return _dump({"ok": True, "file": str(target), "created": created,
                      "size_kb": round(target.stat().st_size / 1024, 1),
                      "hint": "空白文件已创建；下一步可继续用 work_office_batch 写入内容"})

    try:
        argv = [str(OFFICECLI), "batch", str(target), "--input", str(ops_path), "--json"]
        res = _run_officecli(argv, timeout=timeout)
    finally:
        if tmp is not None:
            try:
                tmp.unlink()
            except Exception:
                pass

    parsed = _parse_json(res["stdout"])
    payload: Dict[str, Any] = {
        "ok": res["exit_code"] == 0,
        "file": str(target),
        "created": created,
        "backup": backup_path or None,
        "command": f"officecli batch {target.name} --input <ops>",
        "exit_code": res["exit_code"],
        "result": parsed,
        "stdout": res["stdout"][:8000] if parsed is None else None,
        "stderr": res["stderr"][:4000] or None,
    }
    if payload["ok"] and validate:
        vres = _run_officecli([str(OFFICECLI), "validate", str(target), "--json"], timeout=120)
        payload["validation"] = _parse_json(vres["stdout"]) or {
            "exit_code": vres["exit_code"], "stdout": vres["stdout"][:2000],
            "stderr": vres["stderr"][:1000],
        }
        payload["valid"] = vres["exit_code"] == 0
    if not payload["ok"]:
        payload["hint"] = ("常见原因：属性名写错（先 work_office_inspect(mode=\"schema\") 查）、"
                           "路径不存在（先 work_office_inspect(mode=\"annotated\") 看 DOM）、"
                           "元素类型不支持该属性。")
    _save_state("last_office_batch", {"ok": payload["ok"], "exit_code": res["exit_code"],
                                      "file": str(target), "backup": backup_path})
    return _dump(payload)


# --------------------------------------------------------------------------- #
# 工具 8d：新建空白文档
# --------------------------------------------------------------------------- #
@TOOLS.tool(
    name="work_office_create",
    description=(
        "【内部兼容，不通过 MCP 暴露；新调用请用 work_office_batch(create=true)】新建空白 Office 文件。"
        "往里写内容。适合「从零做一张表 / 一份通知 / 一页汇报」。"
        "默认落在 03_汇总与成稿输出/；force=true 才允许覆盖已存在的文件。"
    ),
)
def work_office_create(file: str = "", force: bool = False, locale: str = "") -> str:
    if not OFFICECLI.exists():
        return _dump({"ok": False, "error": f"未找到 OfficeCLI: {OFFICECLI}"})
    if not (file or "").strip():
        return _dump({"ok": False, "error": "file 不能为空：给目标文件名，如 11月汇总表.xlsx"})
    target = _resolve_path(file, DIR_OUTPUT / Path(file).name)
    ext = target.suffix.lower()
    if ext not in (".docx", ".xlsx", ".pptx"):
        return _dump({"ok": False, "error": f"只支持 .docx/.xlsx/.pptx，收到 {ext or '（无扩展名）'}"})
    if target.exists() and not force:
        return _dump({"ok": False, "error": f"文件已存在: {target}",
                      "hint": "换一个文件名，或确认要覆盖再传 force=true（建议先 work_backup_file）"})
    target.parent.mkdir(parents=True, exist_ok=True)
    argv = [str(OFFICECLI), "create", str(target), "--json"]
    if force:
        argv.append("--force")
    if locale:
        argv += ["--locale", locale]
    res = _run_officecli(argv, timeout=120)
    parsed = _parse_json(res["stdout"])
    ok = res["exit_code"] == 0 and target.exists()
    return _dump({
        "ok": ok,
        "file": str(target),
        "size_kb": round(target.stat().st_size / 1024, 1) if target.exists() else 0,
        "result": parsed,
        "stdout": res["stdout"][:2000] if parsed is None else None,
        "stderr": res["stderr"][:2000] or None,
        "next": "用 work_office_batch 往里面写内容；写完 work_officecli_run command=validate" if ok else None,
    })


# --------------------------------------------------------------------------- #
# 工具 8e：模板 + 数据批量生成
# --------------------------------------------------------------------------- #
def _load_batch_records(raw: str) -> Tuple[Optional[List[Dict[str, Any]]], Optional[Dict[str, Any]], Optional[str]]:
    """解析 work_office_merge 的 data：
    返回 (batch_records, single_record, error)。
    支持：单 JSON 对象 {...}、JSON 数组 [{...}, ...]、.json/.csv/.tsv/.xlsx 文件路径。
    """
    s = (raw or "").strip()
    if s.startswith("{"):
        try:
            obj = json.loads(s)
            if isinstance(obj, dict):
                return None, obj, None
            return None, None, "JSON 对象格式错误"
        except Exception as exc:
            return None, None, f"data 不是合法 JSON: {exc}"
    if s.startswith("["):
        try:
            arr = json.loads(s)
            if isinstance(arr, list) and arr and all(isinstance(x, dict) for x in arr):
                return arr, None, None
            return None, None, "JSON 数组必须是非空对象数组"
        except Exception as exc:
            return None, None, f"data 不是合法 JSON 数组: {exc}"

    p_str = s[1:] if s.startswith("@") else s
    p, err = _file_arg(p_str, "data")
    if err:
        return None, None, f"数据文件不存在: {p_str}"
    ext = p.suffix.lower()
    if ext == ".json":
        try:
            loaded = json.loads(p.read_text(encoding="utf-8-sig"))
            if isinstance(loaded, dict):
                return None, loaded, None
            if isinstance(loaded, list) and loaded and all(isinstance(x, dict) for x in loaded):
                return loaded, None, None
            return None, None, "JSON 文件内容必须是对象或对象数组"
        except Exception as exc:
            return None, None, f"读取 JSON 失败: {exc}"
    if ext in (".csv", ".tsv"):
        delim = "\t" if ext == ".tsv" else ","
        text = p.read_text(encoding="utf-8-sig", errors="replace")
        rows = list(csv.DictReader(text.splitlines(), delimiter=delim))
        rows = [{str(k).strip(): str(v or "").strip() for k, v in r.items() if k} for r in rows]
        rows = [r for r in rows if any(r.values())]
        if not rows:
            return None, None, f"CSV/TSV 文件无有效数据行: {p}"
        return rows, None, None
    if ext == ".xlsx":
        import openpyxl
        wb = openpyxl.load_workbook(str(p), data_only=True)
        ws = wb.active
        all_rows = [["" if c is None else str(c).strip() for c in row] for row in ws.iter_rows(values_only=True)]
        all_rows = [r for r in all_rows if any(r)]
        if len(all_rows) < 2:
            return None, None, f"Excel 数据表至少需要表头行与 1 行数据: {p}"
        headers = [h or f"col_{i+1}" for i, h in enumerate(all_rows[0])]
        records = []
        for r in all_rows[1:]:
            rec = {headers[i]: (r[i] if i < len(r) else "") for i in range(len(headers))}
            if any(rec.values()):
                records.append(rec)
        return records, None, None
    return None, None, f"不支持的数据文件格式: {ext}（支持 .json / .csv / .tsv / .xlsx）"


@TOOLS.tool(
    name="work_office_merge",
    description=(
        "【写文件】按模板单份或批量套打生成文档：模板(.docx/.xlsx/.pptx)里写 {{key}} 占位符，"
        "data 支持传：① 单个 JSON 对象（生成 1 份文档）；② JSON 对象数组 [{...}, {...}]；"
        "③ .csv / .tsv / .xlsx / .json 数据文件路径（每行自动生成 1 份文档，一次批量生成 N 份通知/合同/证书/报表）。"
    ),
)
def work_office_merge(template: str = "", output: str = "", data: str = "",
                      force: bool = False) -> str:
    if not OFFICECLI.exists():
        return _dump({"ok": False, "error": f"未找到 OfficeCLI: {OFFICECLI}"})
    tpl, err = _file_arg(template, "template")
    if err:
        return _dump({"ok": False, "error": err})
    if not (output or "").strip():
        return _dump({"ok": False, "error": "output 不能为空：给输出文件名或批量输出目录"})
    raw = (data or "").strip()
    if not raw:
        return _dump({"ok": False, "error": "data 不能为空：给 JSON 对象/数组，或 .json/.csv/.xlsx 路径"})

    batch_records, single_obj, parse_err = _load_batch_records(raw)
    if parse_err:
        return _dump({"ok": False, "error": parse_err})

    if batch_records is not None:
        out_base = _resolve_path(output, DIR_OUTPUT / Path(output).name)
        if out_base.suffix.lower() == tpl.suffix.lower():
            out_dir = out_base.parent
            stem_prefix = out_base.stem
        else:
            out_dir = out_base
            stem_prefix = tpl.stem
        out_dir.mkdir(parents=True, exist_ok=True)
        generated: List[str] = []
        errors: List[Dict[str, Any]] = []
        for idx, rec in enumerate(batch_records, start=1):
            first_val = next((str(v).strip() for v in rec.values() if str(v).strip()), str(idx))
            safe_tag = _safe_name(first_val)[:24] or str(idx)
            out_file = out_dir / f"{stem_prefix}_{idx:02d}_{safe_tag}{tpl.suffix}"
            tmp = _tmp_json(f"merge_{idx}", rec)
            try:
                argv = [str(OFFICECLI), "merge", str(tpl), str(out_file), "--data", str(tmp), "--json", "--force"]
                res = _run_officecli(argv, timeout=180)
                if res["exit_code"] == 0 and out_file.exists():
                    generated.append(str(out_file))
                else:
                    errors.append({"row": idx, "error": (res["stderr"] or res["stdout"])[:300]})
            finally:
                try:
                    tmp.unlink()
                except Exception:
                    pass
        return _dump({
            "ok": len(generated) > 0 and not errors,
            "batch": True,
            "total_rows": len(batch_records),
            "generated_count": len(generated),
            "output_dir": str(out_dir),
            "outputs": generated,
            "errors": errors or None,
        })

    out = _resolve_path(output, DIR_OUTPUT / Path(output).name)
    if out.suffix.lower() != tpl.suffix.lower():
        return _dump({"ok": False, "error": f"输出扩展名要与模板一致：模板 {tpl.suffix}，输出 {out.suffix}"})
    if out.exists() and not force:
        return _dump({"ok": False, "error": f"输出文件已存在: {out}",
                      "hint": "换 output，或确认覆盖再 force=true"})

    tmp = _tmp_json("merge", single_obj)
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        argv = [str(OFFICECLI), "merge", str(tpl), str(out), "--data", str(tmp), "--json"]
        if force:
            argv.append("--force")
        res = _run_officecli(argv, timeout=180)
    finally:
        try:
            tmp.unlink()
        except Exception:
            pass

    parsed = _parse_json(res["stdout"])
    ok = res["exit_code"] == 0 and out.exists()
    return _dump({
        "ok": ok,
        "output": str(out),
        "size_kb": round(out.stat().st_size / 1024, 1) if out.exists() else 0,
        "result": parsed,
        "stdout": res["stdout"][:2000] if parsed is None else None,
        "stderr": res["stderr"][:2000] or None,
        "hint": None if ok else "占位符要写成 {{key}}（双花括号）；data 的键与模板一一对应",
    })


# --------------------------------------------------------------------------- #
# 工具 8f：CSV/TSV 导入 Excel
# --------------------------------------------------------------------------- #
@TOOLS.tool(
    name="work_office_import",
    description=(
        "【写文件】把 CSV/TSV 数据导入 .xlsx 的某张表（OfficeCLI import）。"
        "source 给 .csv/.tsv 路径，或直接把文本给 data。header=true 会加筛选与冻结首行。"
        "目标 .xlsx 不存在时自动创建；覆盖已有目标前自动备份。"
    ),
)
def work_office_import(file: str = "", sheet: str = "", source: str = "", data: str = "",
                       header: bool = True, start_cell: str = "A1",
                       delimiter: str = "", format: str = "", backup: bool = True) -> str:
    if not OFFICECLI.exists():
        return _dump({"ok": False, "error": f"未找到 OfficeCLI: {OFFICECLI}"})
    if not (file or "").strip():
        return _dump({"ok": False, "error": "file 不能为空：传文件绝对路径，或工作区相对路径"})
    target = _resolve_path(file, DIR_OUTPUT / Path(file).name)
    if target.suffix.lower() != ".xlsx":
        return _dump({"ok": False, "error": f"只支持导入到 .xlsx，收到 {target.suffix}"})

    tmp: Optional[Path] = None
    if source:
        src, err = _file_arg(source, "source")
        if err:
            return _dump({"ok": False, "error": err})
        src_path = src
    elif (data or "").strip():
        _ensure_dirs()
        d = STATE_DIR / ".tmp"
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f"import_{datetime.now().strftime('%H%M%S%f')}.csv"
        tmp.write_text(data, encoding="utf-8-sig")
        src_path = tmp
    else:
        return _dump({"ok": False, "error": "source 与 data 至少给一个（CSV/TSV 文件路径或文本）"})

    created = False
    backup_path = ""
    if target.is_file():
        if backup:
            backup_path = _backup_target({"path": str(target)})
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        cres = _run_officecli([str(OFFICECLI), "create", str(target), "--json"], timeout=120)
        if cres["exit_code"] != 0 or not target.is_file():
            if tmp is not None:
                tmp.unlink(missing_ok=True)
            return _dump({"ok": False, "error": "目标 .xlsx 不存在且自动创建失败",
                          "file": str(target), "stdout": cres["stdout"][:2000],
                          "stderr": cres["stderr"][:2000]})
        created = True

    sheet_path = sheet if (sheet or "").startswith("/") else "/" + (sheet or "Sheet1")
    try:
        argv = [str(OFFICECLI), "import", str(target), sheet_path,
                "--file", str(src_path), "--start-cell", start_cell or "A1", "--json"]
        if header:
            argv.append("--header")
        if delimiter:
            argv += ["--delimiter", delimiter]
        if format:
            argv += ["--format", format]
        res = _run_officecli(argv, timeout=180)
    finally:
        if tmp is not None:
            try:
                tmp.unlink()
            except Exception:
                pass

    parsed = _parse_json(res["stdout"])
    ok = res["exit_code"] == 0
    return _dump({
        "ok": ok,
        "file": str(target),
        "sheet": sheet_path,
        "created": created,
        "backup": backup_path or None,
        "result": parsed,
        "stdout": res["stdout"][:3000] if parsed is None else None,
        "stderr": res["stderr"][:2000] or None,
        "hint": None if ok else "表名要真实存在（如 /Sheet1）；CSV 编码建议 UTF-8；分隔符不对就传 delimiter",
    })


# --------------------------------------------------------------------------- #
# 工具 8g：本机 Office / WPS 实时操控（COM）
# --------------------------------------------------------------------------- #
@TOOLS.tool(
    name="work_office_com",
    description=(
        "【写文件 / 可能改动打开着的文档 / run_python 会执行任意代码】直连本机 Microsoft Office 或 WPS 的 COM，"
        "做 OfficeCLI 干不了的活：apps 看装了哪些应用；active 看当前打开并激活的文件；"
        "info 读页数/字数/表数/幻灯片数；export_pdf 用真实应用导 PDF（版面最准，engine=ms/wps/auto）；"
        "recalc 重算 Excel 公式并存盘；refresh_fields 刷新 Word 域与目录；replace Word 查找替换；"
        "read_cells/set_cells 读写 Excel 单元格；"
        "run_python 注入 Officer（Officer.Word/.Excel/.PPT，优先接管用户已开着的窗口）执行 Python，"
        "写「把这一页按我说的排一下」这类固定动作覆盖不了的活。"
        "run_python 属于任意代码执行，仅当用户明确要求实时操控打开的文档时才用，并先说明会动到什么。"
        "会写盘的 action 前先 work_backup_file。只读需求用 work_office_inspect。"
    ),
)
def work_office_com(action: str = "apps", file: str = "", engine: str = "auto",
                    sheet: str = "", range: str = "A1:D20", find: str = "",
                    replace: str = "", cells: str = "", output: str = "",
                    code: str = "", data: str = "", keep_open: bool = True,
                    visible: bool = True, timeout: int = 120) -> str:
    worker = WORKERS / "office_com.py"
    if not worker.exists():
        return _dump({"ok": False, "error": f"缺少 COM 桥脚本: {worker}"})

    _ACTIONS = ("apps", "active", "run_python", "info", "export_pdf", "recalc",
                "refresh_fields", "replace", "read_cells", "set_cells")
    act = (action or "apps").strip()
    if act not in _ACTIONS:
        return _dump({"ok": False, "error": f"未知 action: {act}", "allowed": list(_ACTIONS)})

    payload: Dict[str, Any] = {}
    if act == "replace":
        if not (find or "").strip():
            return _dump({"ok": False, "error": "action=replace 需要 find（要查找的原文）"})
        payload.update({"find": find, "replace": replace})
    elif act in ("read_cells",):
        payload.update({"sheet": sheet, "range": range or "A1:D20"})
    elif act == "set_cells":
        if not (cells or "").strip():
            return _dump({"ok": False,
                          "error": 'action=set_cells 需要 cells，形如 {"A1":"值","B2":12}'})
        try:
            parsed_cells = json.loads(cells)
        except Exception as exc:
            return _dump({"ok": False, "error": f"cells 不是合法 JSON: {exc}"})
        if not isinstance(parsed_cells, dict) or not parsed_cells:
            return _dump({"ok": False, "error": "cells 必须是非空对象，键为单元格地址"})
        payload.update({"sheet": sheet, "cells": parsed_cells})
    elif act == "run_python":
        if not (code or "").strip():
            return _dump({"ok": False, "error": "action=run_python 需要 code",
                          "example": "ws = Officer.Excel.ActiveSheet\nws.Cells(1,1).Value = '甲局'\n"
                                     "ws.Parent.Save()\noutput = '已写入 A1'"})
        payload.update({"code": code, "data": data,
                        "keep_open": bool(keep_open), "visible": bool(visible),
                        # 早先 timeout 只存在于签名里，既没传给脚本也没管 subprocess：
                        # 用户设 timeout=10，实际能跑满 300 秒。现在两头都用它。
                        "timeout": max(1, int(timeout or 120))})
    elif act == "export_pdf":
        if output:
            payload["output"] = str(_resolve_path(output, DIR_OUTPUT / "导出.pdf"))
        if sheet:
            payload["sheet"] = sheet

    if act not in ("apps", "active", "run_python"):
        target, err = _file_arg(file)
        if err:
            return _dump({"ok": False, "error": err})
        file_arg = str(target)
    else:
        file_arg = ""

    argv = [sys.executable, str(worker), "--action", act.replace("_", "-"),
            "--engine", engine or "auto"]
    tmp_payload: Optional[Path] = None
    if act == "run_python" and len(json.dumps(payload, ensure_ascii=False)) > 6000:
        # 32k 命令行上限：代码稍长就会截断，落成文件更稳
        tmp_payload = _tmp_json("run_python", payload)
        argv += ["--payload-file", str(tmp_payload)]
    else:
        argv += ["--payload", json.dumps(payload, ensure_ascii=False)]
    if file_arg:
        argv += ["--file", file_arg]
    try:
        # 子进程比脚本超时多留 20 秒：让 worker 里的 settrace 先抛超时、回一份
        # 结构化 JSON；如果卡在 C 层（COM 调用）trace 拦不住，才由这里强杀。
        res = _run(argv, timeout=max(10, int(timeout)) + 20)
    finally:
        if tmp_payload is not None:
            try:
                tmp_payload.unlink()
            except Exception:
                pass
    parsed = _parse_json(res["stdout"])
    if parsed is None:
        timed_out = res["exit_code"] == 124
        killed = _reap_office_com_pids() if timed_out else []
        payload_err: Dict[str, Any] = {
            "ok": False,
            "error": (f"脚本执行被强制结束（超过 {max(10, int(timeout)) + 20} 秒）"
                      if timed_out else "COM 桥没有返回 JSON"),
            "stdout": res["stdout"][-1500:],
            "stderr": res["stderr"][-1500:],
        }
        if timed_out:
            payload_err["hint"] = (
                "两种常见原因：脚本里有死循环或长任务（拆分，或调大 timeout）；"
                "或卡在某个 COM 调用上（这种 trace 拦不住，只能等强杀）。")
            if killed:
                payload_err["cleaned_processes"] = killed
                payload_err["hint"] += f" 已清理工具自己拉起的残留进程 {len(killed)} 个，用户手动打开的窗口不受影响。"
        return _dump(payload_err)
    parsed["ok"] = parsed.get("status") == "success"
    if parsed["ok"] and act == "run_python":
        parsed["note"] = ("脚本体已执行完。keep_open=true 表示拉起的应用留着没关，"
                          "用户看得到窗口；改完的文件要自己 Save() 才落盘。")
    if parsed["ok"] and act in ("export_pdf", "recalc", "refresh_fields", "replace",
                                "set_cells", "run_python"):
        _save_state("last_office_com", {"action": act, "file": file_arg,
                                        "engine_used": parsed.get("engine_used")})
    return _dump(parsed)


# --------------------------------------------------------------------------- #
# 工具 9：文档检视（阅读摘要）
# --------------------------------------------------------------------------- #
@TOOLS.tool(
    name="work_office_inspect",
    description=(
        "全方位检视、审计与视觉渲染 .docx/.xlsx/.pptx 文档："
        "mode=text 取纯文本（xlsx 为 A1=值，可配 cell_range 如 Sheet1!A1:F20、cols 如 A,B,C）；"
        "outline 取结构大纲；stats 取字数/字体/样式分布；issues 做格式/内容/未求值公式与域体检（可配 issue_type）；"
        "annotated 取带 DOM 路径标注视图；forms 取表单控件；comments 提取全部批注；"
        "screenshot 渲染页面/幻灯片/局部区域高清截图 PNG（可配 page、grid='auto' 整篇缩略图矩阵、output）；"
        "html/svg 导出独立预览网页；schema 查 OfficeCLI 元素、动词和属性 Schema；"
        "skills 加载内置文档构建指南（file 传 list 或 word/excel/pptx/data-dashboard/financial-model/academic-paper/word-form/pitch-deck/morph-ppt/morph-ppt-3d）。"
        "需要 PDF 时用 work_office_to_pdf。"
    ),
)
def work_office_inspect(
    file: str = "",
    mode: str = "text",
    max_lines: int = 200,
    cell_range: str = "",
    page: str = "",
    start: int = 0,
    end: int = 0,
    cols: str = "",
    issue_type: str = "",
    grid: str = "",
    output: str = "",
    skill: str = "",
    format: str = "",
    verb: str = "",
    element: str = "",
) -> str:
    if not OFFICECLI.exists():
        return _dump({"ok": False, "error": f"未找到 OfficeCLI: {OFFICECLI}"})
    mode = (mode or "text").strip().lower()
    _MODES = ("text", "annotated", "outline", "stats", "issues", "forms",
              "screenshot", "html", "svg", "comments", "skills", "schema")
    if mode not in _MODES:
        return _dump({"ok": False, "error": f"不支持的 mode: {mode}", "allowed": list(_MODES)})

    # 特殊模式 1：查 OfficeCLI schema，避免为了查一个属性再切换工具。
    if mode == "schema":
        impl = getattr(work_office_schema, "__wrapped__", work_office_schema)
        return impl(format=format, verb=verb, element=element)

    # 特殊模式 2：加载 officecli 内置的通用场景构建指南 (load_skill)
    if mode == "skills":
        sk = (skill or file or "").strip()
        cmd = [str(OFFICECLI), "load_skill"]
        if sk and sk.lower() not in ("list", "all"):
            cmd.append(sk)
        res = _run_officecli(cmd, timeout=60)
        text = res["stdout"] or res["stderr"]
        return _dump({
            "ok": res["exit_code"] == 0 and bool(text),
            "mode": "skills",
            "skill": sk or "list",
            "guide": text[:28000],
            "truncated": len(text) > 28000,
        })

    target, err = _file_arg(file)
    if err:
        return _dump({"ok": False, "error": err})

    # 特殊模式 3：统一提取批注 (.docx / .xlsx / .pptx)
    if mode == "comments":
        res = _run_officecli([str(OFFICECLI), "query", str(target), "comment", "--json"], timeout=90)
        parsed = _parse_json(res["stdout"])
        inner, _ = _unwrap_officecli(parsed) if parsed else ({}, False)
        comments_list: Any = inner
        if target.suffix.lower() == ".docx":
            wmcp, _ = _word_mcp_server()
            if wmcp is not None:
                try:
                    import asyncio
                    wres = asyncio.run(wmcp.call_tool("get_all_comments", {"filename": str(target)}))
                    wpayload = _excel_mcp_payload(wres).get("result")
                    if wpayload:
                        comments_list = wpayload
                except Exception:
                    pass
        return _dump({"ok": True, "file": str(target), "mode": "comments", "data": comments_list})

    # 特殊模式 4：视觉截图与渲染导出 (screenshot / html / svg)
    if mode in ("screenshot", "html", "svg"):
        _ensure_dirs()
        ext_map = {"screenshot": ".png", "html": ".html", "svg": ".svg"}
        default_dir = STATE_DIR if mode == "screenshot" else DIR_OUTPUT
        default_name = f"preview_{target.stem}{ext_map[mode]}" if mode == "screenshot" else f"{target.stem}{ext_map[mode]}"
        out_path = _resolve_path(output, default_dir / default_name)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        argv = [str(OFFICECLI), "view", str(target), mode, "--out", str(out_path)]
        if page:
            argv += ["--page", str(page)]
        if cell_range:
            cr = str(cell_range).strip()
            if "!" not in cr and target.suffix.lower() in (".xlsx", ".xls"):
                cr = f"Sheet1!{cr}"
            argv += ["--range", cr]
        if grid and mode == "screenshot":
            argv += ["--grid", str(grid)]
        res = _run_officecli(argv, timeout=180)
        ok = res["exit_code"] == 0 and out_path.exists()
        return _dump({
            "ok": ok,
            "file": str(target),
            "mode": mode,
            "output_path": str(out_path) if out_path.exists() else "",
            "size_kb": round(out_path.stat().st_size / 1024, 1) if out_path.exists() else 0,
            "stdout": res["stdout"][:2000] or None,
            "stderr": res["stderr"][:2000] or None,
        })

    argv = [str(OFFICECLI), "view", str(target), mode, "--max-lines", str(max(1, int(max_lines))), "--json"]
    if cell_range:
        cell_range = cell_range.strip()
        if "!" not in cell_range and target.suffix.lower() in (".xlsx", ".xls"):
            cell_range = f"Sheet1!{cell_range}"
        argv += ["--range", cell_range]
    if page:
        argv += ["--page", page]
    if start and int(start) > 0:
        argv += ["--start", str(int(start))]
    if end and int(end) > 0:
        argv += ["--end", str(int(end))]
    if cols:
        argv += ["--cols", cols]
    if issue_type and mode == "issues":
        argv += ["--type", issue_type]

    res = _run_officecli(argv, timeout=180)
    parsed = _parse_json(res["stdout"])
    if parsed is None:
        return _dump({"ok": False, "stdout": res["stdout"][:6000], "stderr": res["stderr"][:2000]})
    inner, cli_ok = _unwrap_officecli(parsed)
    if ((not cli_ok or res["exit_code"] != 0 or (isinstance(inner, dict) and inner.get("success") is False)) and cell_range and "!" in cell_range
            and isinstance(inner, dict) and isinstance(inner.get("error"), dict)):
        valid_sheets = inner["error"].get("validValues") or []
        if valid_sheets:
            _, rng_part = cell_range.split("!", 1)
            fixed_range = f"{valid_sheets[0]}!{rng_part}"
            argv_retry = [fixed_range if x == cell_range else x for x in argv]
            res2 = _run_officecli(argv_retry, timeout=180)
            parsed2 = _parse_json(res2["stdout"])
            if parsed2 is not None:
                inner2, cli_ok2 = _unwrap_officecli(parsed2)
                if cli_ok2:
                    res, inner, cli_ok = res2, inner2, cli_ok2
    payload = {"ok": res["exit_code"] == 0 and cli_ok, "file": str(target),
               "mode": mode, "data": inner}

    if mode == "outline" and target.suffix.lower() == ".docx":
        headings = inner.get("headings") if isinstance(inner, dict) else None
        if not headings:
            inferred = _infer_docx_headings(target)
            if inferred:
                payload["headings_inferred"] = True
                payload["note"] = "该文档未使用 Word 内置标题样式，以下结构按「一、/（一）/1.」编号规则推断"
                payload["data"] = {**(inner if isinstance(inner, dict) else {}), "headings": inferred}
    return _dump(payload)


_HEADING_PATTERNS = (
    ("level1", re.compile(r"^[一二三四五六七八九十百]+、")),
    ("level2", re.compile(r"^[（(][一二三四五六七八九十百]+[)）]")),
    ("level3", re.compile(r"^\d+[.．、]")),
)


def _infer_docx_headings(path: Path, limit: int = 200) -> List[Dict[str, Any]]:
    try:
        from docx import Document  # 延迟导入：没装 python-docx 时不影响其他工具
    except Exception:
        return []
    try:
        doc = Document(str(path))
    except Exception:
        return []
    out: List[Dict[str, Any]] = []
    for idx, p in enumerate(doc.paragraphs):
        text = (p.text or "").strip()
        if not text:
            continue
        for level, pat in _HEADING_PATTERNS:
            if pat.match(text):
                out.append({"index": idx, "level": int(level[-1]), "text": text[:80]})
                break
        if len(out) >= limit:
            break
    return out



# --------------------------------------------------------------------------- #
# 工具 13：导出 PDF
# --------------------------------------------------------------------------- #
@TOOLS.tool(
    name="work_office_to_pdf",
    description=(
        "【写文件】把 Word / Excel / PowerPoint 转成 PDF，走本机 Office COM，版面与"
        "「另存为 PDF」完全一致。办公里「报送」这一步几乎一定要 PDF，而 OfficeCLI 不支持转换。"
        "传 file 转单个文件；传 batch_dir 批量转整个目录。sheet 可只导某一张工作表。"
        "engine=auto 先试 Microsoft Office、失败自动换 WPS；只装了 WPS 的机器也能用。"
        "产出默认落在 03_汇总与成稿输出/。Excel 页数为 0 是正常现象（COM 不回报页数）。"
        "【无 dry_run 参数】只看内容请用只读的 work_office_inspect。"
    ),
)
def work_office_to_pdf(
    file: str = "",
    output: str = "",
    batch_dir: str = "",
    sheet: str = "",
    engine: str = "auto",
) -> str:
    if not file and not batch_dir:
        return _dump({"ok": False, "error": "需要 file（单个文件）或 batch_dir（整目录）"})
    _ensure_dirs()
    worker = WORKERS / "office_convert.py"
    if not worker.exists():
        return _dump({"ok": False, "error": f"缺少转换脚本: {worker}"})

    if batch_dir:
        src = _resolve_path(batch_dir, DIR_OUTPUT)
        if not src.is_dir():
            return _dump({"ok": False, "error": f"目录不存在: {src}"})
        dst = _resolve_path(output, DIR_OUTPUT / f"{src.name}_PDF")
        argv = [sys.executable, str(worker), "--in", str(src), "--out", str(dst)]
    else:
        src, err = _file_arg(file)
        if err:
            return _dump({"ok": False, "error": err})
        dst = _resolve_path(output, DIR_OUTPUT / f"{src.stem}.pdf")
        argv = [sys.executable, str(worker), "--in", str(src), "--out", str(dst)]
        if sheet:
            argv += ["--sheet", sheet]
    if engine and engine != "auto":
        argv += ["--engine", engine]

    res = _run(argv, timeout=300)
    parsed = _parse_json(res["stdout"])
    if parsed is None:
        return _dump({"ok": False, "error": "转换脚本没有返回 JSON",
                      "stdout": res["stdout"][-1500:], "stderr": res["stderr"][-1500:]})
    parsed["ok"] = parsed.get("status") in ("success", "partial")
    parsed["exit_code"] = res["exit_code"]
    return _dump(parsed)



# --------------------------------------------------------------------------- #
# 工具 15/16：把克隆下来的 Office 项目 / MCP 真正接进调用链
#
# 以前 work_status 只检查「路径存在吗」，于是六七个仓库躺在 mcp_servers/ 里，
# 状态栏亮着绿灯，实际一个都没被调用过；有的包还克隆不全（Word MCP 只有
# __init__.py），却照样被算作「引擎就绪」。
# 这里改成两件事：① 真实探测能不能加载、能加载出多少工具；② 给确实能跑的
# excel-mcp-server 开一个可直接调用的桥，只读为默认、写操作自动备份。
# --------------------------------------------------------------------------- #
_EXCEL_MCP_SRC = ROOT / "mcp_servers" / "excel-mcp-server" / "src"
_EXCEL_WRITE_RE = re.compile(
    r"^(create|set|write|clear|copy|delete|insert|merge|format|add|rename|import|"
    r"sort|transform|replace|goal_seek|unmerge|move|protect|unprotect)", re.I)
_EXCEL_MCP_CACHE: Dict[str, Any] = {}


def _extract_tool_schema(t: Any) -> Dict[str, Any]:
    raw = getattr(t, "parameters", None) or getattr(t, "input_schema", None) or getattr(t, "inputSchema", None) or {}
    if not isinstance(raw, dict):
        return {}
    props = raw.get("properties") or {}
    req = set(raw.get("required") or [])
    out: Dict[str, Any] = {}
    for k, v in props.items():
        if not isinstance(v, dict):
            continue
        typ = v.get("type")
        if not typ and "anyOf" in v:
            typ = "/".join(str(x.get("type")) for x in v["anyOf"] if isinstance(x, dict) and x.get("type") and x.get("type") != "null")
        info: Dict[str, Any] = {"type": typ or "any", "required": k in req}
        if "default" in v and v["default"] is not None:
            info["default"] = v["default"]
        desc = str(v.get("description") or "").strip().replace("\n", " ")
        if desc:
            info["desc"] = desc[:90]
        out[k] = info
    return out


def _resolve_mcp_file_val(val: str) -> str:
    s = (val or "").strip()
    if not s:
        return s
    found, err = _file_arg(s)
    if not err and found is not None and found.exists():
        return str(found)
    cand = Path(s)
    if cand.is_absolute():
        return str(cand)
    for base in (ROOT, WORKSPACE, DIR_OUTPUT, DIR_HARVESTED):
        p_try = (base / s).resolve()
        if p_try.exists():
            return str(p_try)
    if "/" in s or "\\" in s:
        return str((ROOT / s).resolve())
    return str((DIR_OUTPUT / s).resolve())


def _normalize_mcp_args(arguments: Dict[str, Any], schema_params: Dict[str, Any]) -> Dict[str, Any]:
    args_out = dict(arguments)
    path_aliases = ("path", "filename", "file", "file_path", "filepath", "document_path", "workbook_path")
    target_path_keys = [k for k in ("path", "filename", "target_filename", "source_filename", "document_path", "file") if k in schema_params]
    if target_path_keys:
        primary_key = target_path_keys[0]
        if primary_key not in args_out:
            for alias in path_aliases:
                if alias in args_out and isinstance(args_out[alias], str):
                    args_out[primary_key] = args_out.pop(alias)
                    break

    if "sheet" in schema_params and "sheet" not in args_out and "sheet_name" in args_out:
        args_out["sheet"] = args_out.pop("sheet_name")
    elif "sheet_name" in schema_params and "sheet_name" not in args_out and "sheet" in args_out:
        args_out["sheet_name"] = args_out.pop("sheet")

    for k in ("path", "filename", "source_filename", "destination_filename", "target_filename",
              "output_filename", "file", "document_path", "csv_path", "image_path"):
        if k in args_out and isinstance(args_out[k], str) and args_out[k].strip():
            args_out[k] = _resolve_mcp_file_val(args_out[k])
    if "source_filenames" in args_out and isinstance(args_out["source_filenames"], list):
        args_out["source_filenames"] = [
            _resolve_mcp_file_val(str(x)) if isinstance(x, str) else x
            for x in args_out["source_filenames"]
        ]
    if "sheet" in schema_params and isinstance(args_out.get("path"), str) and Path(args_out["path"]).is_file():
        cur_sh = str(args_out.get("sheet") or "").strip()
        if not cur_sh or cur_sh.lower() in ("sheet1", "工作表1"):
            try:
                import openpyxl
                wb_tmp = openpyxl.load_workbook(args_out["path"], read_only=True)
                snames = wb_tmp.sheetnames
                wb_tmp.close()
                if snames and cur_sh not in snames:
                    args_out["sheet"] = snames[0]
            except Exception:
                pass
    return args_out


def _excel_mcp_server(read_only: bool = True):
    key = "ro" if read_only else "rw"
    if key in _EXCEL_MCP_CACHE:
        return _EXCEL_MCP_CACHE[key], ""
    entry = _EXCEL_MCP_SRC / "excel_mcp" / "server" / "__init__.py"
    if not entry.is_file():
        return None, f"源码不完整，缺 {entry}"
    src = str(_EXCEL_MCP_SRC)
    if src not in sys.path:
        sys.path.insert(0, src)
    try:
        from excel_mcp.config import Settings  # type: ignore
        from excel_mcp.server import create_server  # type: ignore
        srv = create_server(Settings(read_only=read_only))
        _EXCEL_MCP_CACHE[key] = srv
        return srv, ""
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"


def _excel_mcp_tools():
    if "tools" in _EXCEL_MCP_CACHE:
        return _EXCEL_MCP_CACHE["tools"], _EXCEL_MCP_CACHE["schemas"], ""
    srv, err = _excel_mcp_server(read_only=False)
    if srv is None:
        return [], {}, err
    import asyncio
    try:
        tools = asyncio.run(srv.list_tools())
        schemas = {getattr(t, "name", ""): _extract_tool_schema(t) for t in tools}
        _EXCEL_MCP_CACHE["tools"] = tools
        _EXCEL_MCP_CACHE["schemas"] = schemas
        return tools, schemas, ""
    except Exception as exc:  # noqa: BLE001
        return [], {}, f"{type(exc).__name__}: {exc}"


def _excel_mcp_payload(res: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {"is_error": bool(getattr(res, "is_error", False))}
    structured = getattr(res, "structured_content", None)
    if structured is not None:
        out["result"] = structured
        return out
    texts = [getattr(c, "text", "") for c in (getattr(res, "content", None) or [])]
    raw = "\n".join(t for t in texts if t)
    try:
        out["result"] = json.loads(raw)
    except Exception:  # noqa: BLE001
        out["result"] = raw
    return out


def _backup_target(arguments: Dict[str, Any]) -> str:
    for key in ("path", "filename", "document_path", "file", "filepath",
                "source", "source_path", "input_path", "source_filename", "target_filename"):
        value = arguments.get(key)
        if not isinstance(value, str) or not value:
            continue
        src = Path(value)
        if not src.is_file():
            continue
        _ensure_dirs()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dst = DIR_BACKUP / f"{src.stem}_{stamp}{src.suffix}"
        shutil.copy2(src, dst)
        return str(dst)
    return ""


_WORD_MCP_ROOT = ROOT / "mcp_servers" / "Office-Word-MCP-Server"
_WORD_READ_RE = re.compile(r"^(get|list|find|view|analyze|describe|is|check|verify)_", re.I)
_WORD_MCP_CACHE: Dict[str, Any] = {}


def _word_mcp_server():
    if "mcp" in _WORD_MCP_CACHE:
        return _WORD_MCP_CACHE["mcp"], ""
    entry = _WORD_MCP_ROOT / "word_document_server" / "main.py"
    if not entry.is_file():
        return None, f"源码不完整，缺 {entry}"
    root = str(_WORD_MCP_ROOT)
    if root in sys.path:
        sys.path.remove(root)
    sys.path.insert(0, root)
    for mod_k in [k for k in list(sys.modules.keys()) if k == "word_document_server" or k.startswith("word_document_server.")]:
        sys.modules.pop(mod_k, None)
    try:
        import contextlib
        import io

        with contextlib.redirect_stdout(io.StringIO()):
            from word_document_server.main import mcp, register_tools  # type: ignore
            register_tools()
        _WORD_MCP_CACHE["mcp"] = mcp
        return mcp, ""
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"


def _word_mcp_tools():
    if "tools" in _WORD_MCP_CACHE:
        return _WORD_MCP_CACHE["tools"], _WORD_MCP_CACHE["schemas"], ""
    mcp, err = _word_mcp_server()
    if mcp is None:
        return [], {}, err
    import asyncio
    try:
        tools = asyncio.run(mcp.list_tools())
        schemas = {getattr(t, "name", ""): _extract_tool_schema(t) for t in tools}
        _WORD_MCP_CACHE["tools"] = tools
        _WORD_MCP_CACHE["schemas"] = schemas
        return tools, schemas, ""
    except Exception as exc:  # noqa: BLE001
        return [], {}, f"{type(exc).__name__}: {exc}"


@TOOLS.tool(
    name="work_office_advanced",
    description=(
        "【高级可选引擎·默认只读】统一入口调用两个专业开源库："
        "engine='excel' 使用 excel-mcp-server 的 46 个工具（透视表、图表、公式追踪、目标求解、条件格式等）；"
        "engine='word' 使用 Office-Word-MCP-Server 的 58 个工具（批注、脚注、保护、锚点替换、表格美化等）。"
        "action='list' 或 'schema' 查准确子工具名与参数 Schema；action='call' 才执行。"
        "优先用 work_office_inspect / work_office_batch / work_office_compose；只有它们确实覆盖不了时才进入这里。"
        "写操作必须 allow_write=true，执行前自动备份目标文件。"
    ),
)
def work_office_advanced(engine: str = "", action: str = "list", tool: str = "",
                         args: str = "", keyword: str = "", allow_write: bool = False,
                         max_chars: int = 24000) -> str:
    eng = (engine or "").strip().lower()
    if eng not in ("excel", "word"):
        return _dump({"ok": False, "error": "engine 必须是 excel 或 word",
                      "hint": "Excel 高级分析传 engine='excel'；Word 审阅/脚注/保护传 engine='word'"})
    target = work_excel_mcp if eng == "excel" else work_word_mcp
    impl = getattr(target, "__wrapped__", target)
    return impl(action=action, tool=tool, args=args, keyword=keyword,
                allow_write=allow_write, max_chars=max_chars)


@TOOLS.tool(
    name="work_office_engines",
    description=(
        "【只读·引擎体检】真实盘点 mcp_servers/ 下克隆的 Office MCP 项目："
        "不只看文件在不在，还实际 import 入口、数出能加载多少工具，"
        "并给出每个仓库对应的可调用桥（或明确说明为什么没接）。"
        "用于「克隆的那些 office 项目 / MCP 到底用上了没」「哪个引擎能用」。"
        "结果里 ready=true 才代表真的能调；缺源码、缺依赖、空目录都会如实标出，"
        "不会拿路径存在冒充可用。"
    ),
)
def work_office_engines(action: str = "probe", keyword: str = "") -> str:
    rows: List[Dict[str, Any]] = []

    def add(eid, name, path, ready, bridge, note, extra=None):
        row = {"id": eid, "name": name, "path": str(path), "ready": bool(ready),
               "bridge": bridge, "note": note}
        if extra:
            row.update(extra)
        rows.append(row)

    etools, _, err = _excel_mcp_tools()
    if err:
        add("excel-mcp", "excel-mcp-server 2.2.9", _EXCEL_MCP_SRC, False,
            "work_office_advanced(engine=excel)", f"加载失败：{err}")
    else:
        add("excel-mcp", "excel-mcp-server 2.2.9", _EXCEL_MCP_SRC, True,
            "work_office_advanced(engine=excel)", "已接入：高级 Excel（260+公式引擎/透视表/图表/公式溯源/目标求解等）",
            {"tools": len(etools)})

    add("officecli", "iOfficeAI/OfficeCLI v1.0.161", OFFICECLI, OFFICECLI.is_file(),
        "work_office_inspect / work_office_compose / work_office_batch",
        "已接入：常驻 MCP 管道加速 + 构建指南 + 截图/HTML/SVG 渲染；PDF 由 COM 工具负责" if OFFICECLI.is_file() else "bin/officecli.exe 缺失")

    wtools, _, werr = _word_mcp_tools()
    if werr:
        add("word-mcp", "Office-Word-MCP-Server", _WORD_MCP_ROOT, False,
            "work_office_advanced(engine=word)", f"加载失败：{werr}")
    else:
        add("word-mcp", "Office-Word-MCP-Server", _WORD_MCP_ROOT, True,
            "work_office_advanced(engine=word)", "已接入：多文档合并/批注/脚注/文档保护/结构化锚点编辑等 Word 高级能力",
            {"tools": len(wtools)})

    om = ROOT / "mcp_servers" / "OfficeMCP" / "src" / "officemcp" / "OfficeMCP.py"
    try:
        import win32com.client  # type: ignore  # noqa: F401
        com_ok = True
    except Exception:  # noqa: BLE001
        com_ok = False
    add("officemcp", "OfficeMCP (COM)", om, bool(om.is_file() and com_ok),
        "work_office_com（已用 COM 封装）/ work_office_to_pdf",
        "源码在、pywin32 可用；但 COM 只在装了 Office/WPS 的 Windows 上有效，"
        "工具箱已有等价封装，不重复起一个 MCP 服务" if com_ok else
        "pywin32 不可用，COM 链路不可用")

    if keyword.strip():
        kw = keyword.strip().lower()
        rows = [r for r in rows if kw in json.dumps(r, ensure_ascii=False).lower()]
    return _dump({
        "ok": True,
        "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total": len(rows),
        "usable": sum(1 for r in rows if r["ready"]),
        "bridged": sum(1 for r in rows if r["ready"] and r["bridge"].startswith("work_")),
        "engines": rows,
        "note": ("ready 只说明「源码/依赖/入口可用」，不等于所有功能都验证过。"
                 "work_office_advanced 统一接入 46 个 Excel 与 58 个 Word 工具，默认只读、写前自动备份；"
                 "日常读写优先走 work_office_inspect / work_office_batch / work_office_compose。"),
    })


@TOOLS.tool(
    name="work_excel_mcp",
    description=(
        "【内部兼容，不通过 MCP 暴露；新调用请用 work_office_advanced(engine=\"excel\")】excel-mcp-server 高级引擎："
        "内置 260+ 函数计算引擎（read_range 直接算出公式值）、公式溯源与逐步求值（trace_formula）、"
        "单变量目标求解（goal_seek）、真数据透视表（create_pivot_table）与切片器（add_slicer）、"
        "现代图表（create_chart：柱/线/饼/瀑布/箱线/旭日/树状/漏斗图）与迷你图（add_sparklines）、"
        "数据清洗（transform_range：去重/分列/填充、sort_range）、条件格式、数据验证、VBA 只读查看（read_vba）。"
        "action='list'（或 'schema'）列工具及完整参数 Schema；action='call' 调用指定 tool + args（JSON 对象字符串，自动兼容 path/file/filename 与相对路径）。"
        "写操作需传 allow_write=true，且调用前自动备份到 02_自动备份区/。"
    ),
)
def work_excel_mcp(action: str = "list", tool: str = "", args: str = "",
                   keyword: str = "", allow_write: bool = False,
                   max_chars: int = 24000) -> str:
    import asyncio

    action = (action or "list").strip().lower()
    if action not in ("list", "schema", "call"):
        return _dump({"ok": False, "error": "action 只能是 list / schema 或 call"})
    tools, schemas, err = _excel_mcp_tools()
    if err:
        return _dump({"ok": False, "error": err,
                      "hint": "先跑 work_office_engines 看缺什么，或重新完整克隆 excel-mcp-server"})
    names = [getattr(t, "name", "") for t in tools]

    if action in ("list", "schema"):
        rows = []
        kw = keyword.strip().lower()
        if tool.strip() and not kw:
            kw = tool.strip().lower()
        for t in tools:
            name = getattr(t, "name", "")
            desc = str(getattr(t, "description", "") or "").replace("\n", " ")
            if kw and kw not in (name + " " + desc).lower():
                continue
            rows.append({
                "name": name,
                "write": bool(_EXCEL_WRITE_RE.match(name)),
                "description": desc[:160],
                "params": schemas.get(name, {}),
            })
        return _dump({
            "ok": True, "read_only": not allow_write, "count": len(rows),
            "tools": rows,
            "hint": ("每个工具的 params 已给出准确参数名（路径统一支持 path 或 file，支持工作区相对路径）。"
                     "write=true 的工具要传 allow_write=true，且调用前会自动备份目标文件。"),
        })

    if not tool.strip():
        return _dump({"ok": False, "error": "action=call 必须给 tool 名",
                      "hint": "先 action=list 看有哪些工具"})
    tool = tool.strip()
    _EXCEL_TOOL_ALIASES = {
        "get_workbook_metadata": "describe_workbook",
        "get_workbook_info": "describe_workbook",
        "workbook_metadata": "describe_workbook",
        "read_sheet": "read_cells",
        "read_worksheet": "read_cells",
        "get_sheet_data": "read_cells",
        "read_range": "read_cells",
        "write_sheet": "write_cells",
        "write_worksheet": "write_cells",
        "write_range": "write_cells",
    }
    tool = _EXCEL_TOOL_ALIASES.get(tool, tool)
    if tool not in names:
        near = [n for n in names if (keyword or tool).lower() in n.lower()][:8] or names[:8]
        return _dump({"ok": False, "error": f"excel-mcp-server 没有工具 {tool!r}",
                      "hint": "用 action=list 查准确名字", "examples": near})
    if not allow_write and _EXCEL_WRITE_RE.match(tool):
        return _dump({"ok": False, "error": f"{tool} 是写操作，只读模式下已拦截",
                      "hint": "确认要先备份后写入，再传 allow_write=true"})

    srv, err = _excel_mcp_server(read_only=not allow_write)
    if srv is None:
        return _dump({"ok": False, "error": err})

    try:
        if isinstance(args, dict):
            arguments = dict(args)
        else:
            raw = str(args or "").strip()
            if raw.startswith("@"):
                raw = Path(raw[1:]).read_text(encoding="utf-8-sig")
            arguments = json.loads(raw) if raw else {}
        if not isinstance(arguments, dict):
            raise ValueError("args 必须是 JSON 对象")
    except Exception as exc:  # noqa: BLE001
        return _dump({"ok": False, "error": f"args 解析失败：{type(exc).__name__}: {exc}",
                      "hint": 'args 传 JSON 对象字符串，如 {"path":"2026年11月汇总母表.xlsx"}'})

    arguments = _normalize_mcp_args(arguments, schemas.get(tool, {}))
    backup = _backup_target(arguments) if allow_write else ""

    async def _call():
        return await srv.call_tool(tool, arguments)

    try:
        res = asyncio.run(_call())
    except Exception as exc:  # noqa: BLE001
        return _dump({"ok": False, "error": f"{tool} 调用失败：{type(exc).__name__}: {exc}",
                      "expected_params": schemas.get(tool, {}),
                      "backup": backup})
    payload = {"ok": not bool(getattr(res, "is_error", False)), "tool": tool,
               "read_only": not allow_write, "backup": backup,
               **_excel_mcp_payload(res)}
    text = _dump(payload)
    cap = max(2000, int(max_chars or 24000))
    if len(text) > cap:
        return _dump({"ok": payload["ok"], "truncated": True, "tool": tool,
                      "backup": backup, "preview": text[:cap],
                      "hint": "输出过大：用 range / path 收窄，或调小读取范围，别把 limit 一味放大"})
    return text


@TOOLS.tool(
    name="work_word_mcp",
    description=(
        "【内部兼容，不通过 MCP 暴露；新调用请用 work_office_advanced(engine=\"word\")】Office-Word-MCP-Server 高级引擎："
        "多文档合并（merge_documents）、文档结构/XML 检视、提取全部批注（get_all_comments / get_comments_by_author）、"
        "上下标脚注/尾注插入与校验（add_footnote_robust / convert_footnotes_to_endnotes / validate_document_footnotes）、"
        "按文本锚点插入或替换段落区块（insert_header_near_text / replace_paragraph_block_below_header / replace_block_between_manual_anchors）、"
        "表格隔行变色与列宽自适应（apply_table_alternating_rows / highlight_table_header / auto_fit_table_columns）、"
        "文档密码保护与限制编辑（protect_document / add_restricted_editing / verify_document）。"
        "action='list'（或 'schema'）列工具及完整参数 Schema；action='call' 调用指定 tool + args（JSON 对象字符串，自动兼容 filename/file/path 与相对路径）。"
        "写操作需传 allow_write=true，且调用前自动备份到 02_自动备份区/。"
    ),
)
def work_word_mcp(action: str = "list", tool: str = "", args: str = "",
                  keyword: str = "", allow_write: bool = False,
                  max_chars: int = 24000) -> str:
    import asyncio

    action = (action or "list").strip().lower()
    if action not in ("list", "schema", "call"):
        return _dump({"ok": False, "error": "action 只能是 list / schema 或 call"})
    mcp, err = _word_mcp_server()
    if mcp is None:
        return _dump({"ok": False, "error": err,
                      "hint": "先跑 work_office_engines 看缺什么（缺 fastmcp / python-dotenv 就补依赖）"})
    tools, schemas, terr = _word_mcp_tools()
    if terr:
        return _dump({"ok": False, "error": f"读取工具清单失败：{terr}"})
    names = [getattr(t, "name", "") for t in tools]

    if action in ("list", "schema"):
        rows = []
        kw = keyword.strip().lower()
        if tool.strip() and not kw:
            kw = tool.strip().lower()
        for t in tools:
            name = getattr(t, "name", "")
            desc = str(getattr(t, "description", "") or "").replace("\n", " ")
            if kw and kw not in (name + " " + desc).lower():
                continue
            rows.append({
                "name": name,
                "read_only": bool(_WORD_READ_RE.match(name)),
                "description": desc[:160],
                "params": schemas.get(name, {}),
            })
        return _dump({"ok": True, "count": len(rows), "tools": rows,
                      "hint": ("每个工具的 params 已给出准确参数名（路径统一支持 filename/file/path 与工作区相对路径）。"
                               "read_only=false 的工具需要 allow_write=true，且调用前自动备份。")})

    if not tool.strip():
        return _dump({"ok": False, "error": "action=call 必须给 tool 名"})
    tool = tool.strip()
    _EXCEL_TOOL_ALIASES = {
        "get_workbook_metadata": "describe_workbook",
        "get_workbook_info": "describe_workbook",
        "workbook_metadata": "describe_workbook",
        "read_sheet": "read_cells",
        "read_worksheet": "read_cells",
        "get_sheet_data": "read_cells",
        "read_range": "read_cells",
        "write_sheet": "write_cells",
        "write_worksheet": "write_cells",
        "write_range": "write_cells",
    }
    tool = _EXCEL_TOOL_ALIASES.get(tool, tool)
    if tool not in names:
        near = [n for n in names if (keyword or tool).lower() in n.lower()][:8] or names[:8]
        return _dump({"ok": False, "error": f"Word MCP 没有工具 {tool!r}",
                      "examples": near, "hint": "用 action=list 查准确名字"})
    if not allow_write and not _WORD_READ_RE.match(tool):
        return _dump({"ok": False, "error": f"{tool} 是写操作，默认只读已拦截",
                      "hint": "确认要先备份后写入，再传 allow_write=true"})
    try:
        if isinstance(args, dict):
            arguments = dict(args)
        else:
            raw = str(args or "").strip()
            if raw.startswith("@"):
                raw = Path(raw[1:]).read_text(encoding="utf-8-sig")
            arguments = json.loads(raw) if raw else {}
        if not isinstance(arguments, dict):
            raise ValueError("args 必须是 JSON 对象")
    except Exception as exc:  # noqa: BLE001
        return _dump({"ok": False, "error": f"args 解析失败：{type(exc).__name__}: {exc}"})

    arguments = _normalize_mcp_args(arguments, schemas.get(tool, {}))
    backup = _backup_target(arguments) if allow_write else ""

    async def _call():
        return await mcp.call_tool(tool, arguments)

    try:
        res = asyncio.run(_call())
    except Exception as exc:  # noqa: BLE001
        return _dump({"ok": False, "error": f"{tool} 调用失败：{type(exc).__name__}: {exc}",
                      "expected_params": schemas.get(tool, {}),
                      "backup": backup})
    payload = {"ok": not bool(getattr(res, "is_error", False)), "tool": tool,
               "read_only": not allow_write, "backup": backup,
               **_excel_mcp_payload(res)}
    text = _dump(payload)
    cap = max(2000, int(max_chars or 24000))
    if len(text) > cap:
        return _dump({"ok": payload["ok"], "truncated": True, "tool": tool,
                      "backup": backup, "preview": text[:cap],
                      "hint": "输出过大：缩小读取范围，别把整篇 XML 一次拉出来"})
    return text


# --------------------------------------------------------------------------- #
# 工具 17：通用富文档与演示文稿一键组装生成（work_office_compose）
# --------------------------------------------------------------------------- #
@TOOLS.tool(
    name="work_office_compose",
    description=(
        "【写文件】通用富文档(.docx)与演示文稿(.pptx)一键生成引擎（底层直驱 OfficeCLI 原生富元素）："
        "将 Markdown 文本或 .md 文件（支持多级标题、列表、代码块、GFM 表格、```mermaid 流程图、$$数学公式$$）"
        "一键转化为原生可编辑的 Word 文档(.docx)或 PowerPoint 演示文稿(.pptx)！"
        "docx 支持自动目录、水印及 preset='modern'|'academic'|'memo'|'gbt9704'；"
        "输入 .docx 时直接按 preset 重排现有文档。pptx 自动按标题切分幻灯片并排入要点与 Mermaid 流程图。"
    ),
)
def work_office_compose(
    content_or_path: str = "",
    output: str = "",
    format: str = "docx",
    title: str = "",
    preset: str = "modern",
    toc: bool = False,
    watermark: str = "",
    force: bool = True,
    output_path: str = "",
    doc_type: str = "报告",
    header_unit: str = "",
    doc_number: str = "",
) -> str:
    output = output or output_path
    raw = (content_or_path or "").strip()
    if not raw:
        return _dump({"ok": False, "error": "content_or_path 不能为空：传 Markdown 正文、.md 文件或需要重排的 .docx"})

    src_file, err = _file_arg(raw)
    if not err and src_file is not None and src_file.is_file() and src_file.suffix.lower() == ".docx":
        impl = getattr(work_office_format_gbt9704, "__wrapped__", work_office_format_gbt9704)
        return impl(content_or_path=str(src_file), doc_type=doc_type,
                    header_unit=header_unit, doc_number=doc_number,
                    output_path=output, preset=preset or "modern")
    if not OFFICECLI.exists():
        return _dump({"ok": False, "error": f"未找到 OfficeCLI: {OFFICECLI}"})
    if not err and src_file is not None and src_file.is_file():
        if src_file.suffix.lower() not in (".md", ".markdown", ".txt"):
            return _dump({"ok": False, "error": f"不支持直接从 {src_file.suffix} 合成；"
                                                    "docx 重排请把该 .docx 作为 content_or_path"})
        md_text = src_file.read_text(encoding="utf-8-sig", errors="replace")
        stem_default = src_file.stem
    else:
        md_text = raw.replace("\\n", "\n") if "\\n" in raw and "\n" not in raw else raw
        stem_default = _safe_name(title or "通用生成文档")[:28] or "通用生成文档"

    fmt = (format or "").strip().lower().lstrip(".")
    if output:
        out_ext = Path(output).suffix.lower().lstrip(".")
        if out_ext in ("docx", "pptx"):
            fmt = out_ext
    if fmt not in ("docx", "pptx"):
        return _dump({"ok": False, "error": f"format 只支持 docx 或 pptx，收到 {format!r}"})

    _ensure_dirs()
    out_path = _resolve_path(output, DIR_OUTPUT / f"{stem_default}.{fmt}")
    if out_path.suffix.lower() != f".{fmt}":
        out_path = out_path.with_suffix(f".{fmt}")
    if out_path.exists() and not force:
        return _dump({"ok": False, "error": f"输出文件已存在: {out_path}", "hint": "传 force=true 允许覆盖"})
    out_path.parent.mkdir(parents=True, exist_ok=True)

    blocks: List[Tuple[str, str]] = []
    cur_md: List[str] = []
    lines = md_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("```mermaid"):
            if cur_md:
                blocks.append(("markdown", "\n".join(cur_md).strip()))
                cur_md = []
            i += 1
            m_lines: List[str] = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                m_lines.append(lines[i])
                i += 1
            i += 1
            if m_lines:
                blocks.append(("diagram", "\n".join(m_lines).strip()))
            continue
        if stripped.startswith("$$"):
            if cur_md:
                blocks.append(("markdown", "\n".join(cur_md).strip()))
                cur_md = []
            if stripped.endswith("$$") and len(stripped) > 4:
                blocks.append(("equation", stripped[2:-2].strip()))
                i += 1
                continue
            i += 1
            eq_lines: List[str] = []
            while i < len(lines) and not lines[i].strip().endswith("$$"):
                eq_lines.append(lines[i])
                i += 1
            if i < len(lines):
                tail = lines[i].strip()[:-2].strip()
                if tail:
                    eq_lines.append(tail)
                i += 1
            if eq_lines:
                blocks.append(("equation", "\n".join(eq_lines).strip()))
            continue
        cur_md.append(line)
        i += 1
    if cur_md and "\n".join(cur_md).strip():
        blocks.append(("markdown", "\n".join(cur_md).strip()))

    if fmt == "docx":
        _run_officecli([str(OFFICECLI), "create", str(out_path), "--force", "--json"], timeout=120)
        ops: List[Dict[str, Any]] = []
        if watermark.strip():
            ops.append({"command": "add", "parent": "/body", "type": "watermark", "props": {"text": watermark.strip()}})
        if title.strip() and not md_text.lstrip().startswith("# "):
            ops.append({"command": "add", "parent": "/body", "type": "paragraph",
                        "props": {"text": title.strip(), "style": "Heading1", "align": "center", "bold": "true", "size": "20pt"}})
        if toc:
            ops.append({"command": "add", "parent": "/body", "type": "toc", "props": {"title": "目录"}})

        tmp_files: List[Path] = []
        counts = {"markdown_blocks": 0, "diagrams": 0, "equations": 0}
        try:
            for b_idx, (b_type, b_content) in enumerate(blocks):
                if not b_content:
                    continue
                if b_type == "markdown":
                    md_tmp = STATE_DIR / ".tmp" / f"compose_{datetime.now().strftime('%H%M%S%f')}_{b_idx}.md"
                    md_tmp.parent.mkdir(parents=True, exist_ok=True)
                    md_tmp.write_text(b_content, encoding="utf-8")
                    tmp_files.append(md_tmp)
                    ops.append({"command": "add", "parent": "/body", "type": "markdown", "props": {"src": str(md_tmp)}})
                    counts["markdown_blocks"] += 1
                elif b_type == "diagram":
                    mmd_tmp = STATE_DIR / ".tmp" / f"diagram_{datetime.now().strftime('%H%M%S%f')}_{b_idx}.mmd"
                    mmd_tmp.parent.mkdir(parents=True, exist_ok=True)
                    mmd_tmp.write_text(b_content, encoding="utf-8")
                    tmp_files.append(mmd_tmp)
                    ops.append({"command": "add", "parent": "/body", "type": "diagram", "props": {"src": str(mmd_tmp)}})
                    counts["diagrams"] += 1
                elif b_type == "equation":
                    ops.append({"command": "add", "parent": "/body", "type": "paragraph", "props": {"text": ""}})
                    ops.append({"command": "add", "parent": "/body/p[last()]", "type": "equation", "props": {"formula": b_content}})
                    counts["equations"] += 1

            ops_file = _tmp_json("compose_ops", ops)
            tmp_files.append(ops_file)
            res = _run_officecli([str(OFFICECLI), "batch", str(out_path), "--input", str(ops_file), "--json"], timeout=240)
            val = _run_officecli([str(OFFICECLI), "validate", str(out_path), "--json"], timeout=120)
        finally:
            for tf in tmp_files:
                try:
                    tf.unlink()
                except Exception:
                    pass
        return _dump({
            "ok": res["exit_code"] == 0 and out_path.exists(),
            "format": "docx",
            "output": str(out_path),
            "size_kb": round(out_path.stat().st_size / 1024, 1) if out_path.exists() else 0,
            "counts": counts,
            "toc": bool(toc),
            "watermark": watermark or None,
            "validation": _parse_json(val["stdout"]),
        })

    _run_officecli([str(OFFICECLI), "create", str(out_path), "--force", "--json"], timeout=120)
    slides_spec: List[Dict[str, Any]] = []
    cur_slide: Dict[str, Any] = {"title": title.strip() or stem_default, "bullets": [], "diagram": ""}
    for line in md_text.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("# ") or s.startswith("## "):
            if cur_slide["bullets"] or cur_slide["diagram"] or slides_spec:
                slides_spec.append(cur_slide)
            cur_slide = {"title": s.lstrip("#").strip(), "bullets": [], "diagram": ""}
        elif s.startswith(("- ", "* ", "• ")) or re.match(r"^\d+[.．、]\s+", s):
            item = re.sub(r"^(?:[-*•]|\d+[.．、])\s+", "", s)
            cur_slide["bullets"].append(item)
        elif not s.startswith("```"):
            cur_slide["bullets"].append(s)
    if cur_slide["title"] or cur_slide["bullets"]:
        slides_spec.append(cur_slide)

    ops_ppt: List[Dict[str, Any]] = []
    for idx, sl in enumerate(slides_spec, start=1):
        ops_ppt.append({"command": "add", "parent": "/", "type": "slide", "props": {}})
        ops_ppt.append({
            "command": "add",
            "parent": f"/slide[{idx}]",
            "type": "shape",
            "props": {"text": sl["title"], "x": "1.5cm", "y": "1.2cm", "width": "22cm", "height": "2.2cm",
                      "size": "28pt", "bold": "true", "color": "1F4E78"},
        })
        if sl["bullets"]:
            body_txt = "\n".join(f"• {b}" for b in sl["bullets"][:10])
            ops_ppt.append({
                "command": "add",
                "parent": f"/slide[{idx}]",
                "type": "shape",
                "props": {"text": body_txt, "x": "1.8cm", "y": "4.0cm", "width": "22cm", "height": "11cm",
                          "size": "16pt", "color": "333333"},
            })
    ops_file = _tmp_json("compose_pptx", ops_ppt)
    try:
        res = _run_officecli([str(OFFICECLI), "batch", str(out_path), "--input", str(ops_file), "--json"], timeout=240)
        val = _run_officecli([str(OFFICECLI), "validate", str(out_path), "--json"], timeout=120)
    finally:
        try:
            ops_file.unlink()
        except Exception:
            pass
    return _dump({
        "ok": res["exit_code"] == 0 and out_path.exists(),
        "format": "pptx",
        "output": str(out_path),
        "slides_count": len(slides_spec),
        "size_kb": round(out_path.stat().st_size / 1024, 1) if out_path.exists() else 0,
        "validation": _parse_json(val["stdout"]),
    })


# --------------------------------------------------------------------------- #
# 工具 18：通用文档与表格版本对比（work_office_diff）
# --------------------------------------------------------------------------- #
@TOOLS.tool(
    name="work_office_diff",
    description=(
        "【只读/可选写报告】通用 Office 版本差异对比工具（支持 .docx 对比 .docx、.xlsx 对比 .xlsx）："
        "对比两版合同/报告/通知的段落增删改与表格单元格变化，或对比两张 Excel 表的单元格/公式与数值增减差额"
        "（支持指定 sheet 与 key_column 主键列对齐行）。可选传 output 导出 Markdown 差异报告。"
    ),
)
def work_office_diff(
    file_a: str = "",
    file_b: str = "",
    sheet: str = "",
    key_column: str = "",
    output: str = "",
    output_path: str = "",
) -> str:
    output = output or output_path
    pa, err_a = _file_arg(file_a, "file_a")
    if err_a:
        return _dump({"ok": False, "error": err_a})
    pb, err_b = _file_arg(file_b, "file_b")
    if err_b:
        return _dump({"ok": False, "error": err_b})
    ext_a, ext_b = pa.suffix.lower(), pb.suffix.lower()
    if ext_a != ext_b or ext_a not in (".docx", ".xlsx"):
        return _dump({"ok": False, "error": f"file_a ({ext_a}) 与 file_b ({ext_b}) 必须同为 .docx 或同为 .xlsx"})

    report_md: List[str] = [f"# 文档差异对比报告：{pa.name} vs {pb.name}", ""]
    summary: Dict[str, Any] = {"file_a": str(pa), "file_b": str(pb), "format": ext_a.lstrip(".")}

    if ext_a == ".docx":
        from docx import Document
        doc_a, doc_b = Document(str(pa)), Document(str(pb))
        paras_a = [p.text.strip() for p in doc_a.paragraphs if p.text.strip()]
        paras_b = [p.text.strip() for p in doc_b.paragraphs if p.text.strip()]
        sm = difflib.SequenceMatcher(None, paras_a, paras_b)
        changes: List[Dict[str, Any]] = []
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                continue
            changes.append({
                "type": tag,
                "a_range": [i1 + 1, i2],
                "b_range": [j1 + 1, j2],
                "removed": paras_a[i1:i2][:5],
                "added": paras_b[j1:j2][:5],
            })
        tbl_diffs: List[Dict[str, Any]] = []
        max_tbls = max(len(doc_a.tables), len(doc_b.tables))
        for t_i in range(max_tbls):
            if t_i >= len(doc_a.tables):
                tbl_diffs.append({"table_index": t_i + 1, "change": "added_table", "rows": len(doc_b.tables[t_i].rows)})
            elif t_i >= len(doc_b.tables):
                tbl_diffs.append({"table_index": t_i + 1, "change": "removed_table", "rows": len(doc_a.tables[t_i].rows)})
            else:
                ta, tb = doc_a.tables[t_i], doc_b.tables[t_i]
                cell_mods = []
                for r_i in range(min(len(ta.rows), len(tb.rows))):
                    for c_i in range(min(len(ta.rows[r_i].cells), len(tb.rows[r_i].cells))):
                        va = ta.rows[r_i].cells[c_i].text.strip()
                        vb = tb.rows[r_i].cells[c_i].text.strip()
                        if va != vb:
                            cell_mods.append({"row": r_i + 1, "col": c_i + 1, "before": va[:80], "after": vb[:80]})
                if cell_mods or len(ta.rows) != len(tb.rows):
                    tbl_diffs.append({
                        "table_index": t_i + 1,
                        "rows_before": len(ta.rows),
                        "rows_after": len(tb.rows),
                        "modified_cells_count": len(cell_mods),
                        "modified_cells": cell_mods[:30],
                    })
        summary.update({
            "identical": len(changes) == 0 and len(tbl_diffs) == 0,
            "paragraphs_before": len(paras_a),
            "paragraphs_after": len(paras_b),
            "paragraph_changes_count": len(changes),
            "paragraph_changes": changes[:50],
            "table_changes_count": len(tbl_diffs),
            "table_changes": tbl_diffs,
        })
        for ch in changes[:30]:
            report_md.append(f"- **[{ch['type']}]** 原文段落 {ch['a_range']} -> 新版段落 {ch['b_range']}")
            for r_line in ch["removed"]:
                report_md.append(f"  - `-` {r_line}")
            for a_line in ch["added"]:
                report_md.append(f"  - `+` {a_line}")
    else:
        import openpyxl
        from openpyxl.utils import get_column_letter
        wba = openpyxl.load_workbook(str(pa), data_only=True)
        wbb = openpyxl.load_workbook(str(pb), data_only=True)
        sheets_a, sheets_b = set(wba.sheetnames), set(wbb.sheetnames)
        common_sheets = [s for s in wba.sheetnames if s in sheets_b]
        if sheet.strip():
            common_sheets = [s for s in common_sheets if s == sheet.strip()]
        cell_changes: List[Dict[str, Any]] = []
        for sname in common_sheets:
            wsa, wsb = wba[sname], wbb[sname]
            max_r = max(wsa.max_row or 0, wsb.max_row or 0)
            max_c = max(wsa.max_column or 0, wsb.max_column or 0)
            headers = [str(wsa.cell(1, c).value or wsb.cell(1, c).value or get_column_letter(c)).strip()
                       for c in range(1, max_c + 1)]
            for r in range(1, min(max_r + 1, 2000)):
                for c in range(1, min(max_c + 1, 100)):
                    va = wsa.cell(r, c).value
                    vb = wsb.cell(r, c).value
                    sa = "" if va is None else str(va).strip()
                    sb = "" if vb is None else str(vb).strip()
                    if sa != sb:
                        item: Dict[str, Any] = {
                            "sheet": sname,
                            "cell": f"{get_column_letter(c)}{r}",
                            "column": headers[c - 1] if c - 1 < len(headers) else get_column_letter(c),
                            "before": va,
                            "after": vb,
                        }
                        if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
                            item["delta"] = round(float(vb) - float(va), 4)
                        cell_changes.append(item)
        summary.update({
            "identical": len(cell_changes) == 0 and sheets_a == sheets_b,
            "added_sheets": sorted(sheets_b - sheets_a),
            "removed_sheets": sorted(sheets_a - sheets_b),
            "changed_cells_count": len(cell_changes),
            "changed_cells": cell_changes[:100],
        })
        for ch in cell_changes[:50]:
            delta_str = f" (差额: {ch['delta']:+g})" if "delta" in ch else ""
            report_md.append(f"- `{ch['sheet']}!{ch['cell']}` ({ch['column']}): `{ch['before']}` -> `{ch['after']}`{delta_str}")

    if output.strip():
        _ensure_dirs()
        out_p = _resolve_path(output, DIR_OUTPUT / f"差异对比_{pa.stem}_vs_{pb.stem}.md")
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text("\n".join(report_md), encoding="utf-8")
        summary["report_path"] = str(out_p)
    return _dump({"ok": True, **summary})


# --------------------------------------------------------------------------- #
# 工具 19：多份 Word 文档一键合并（work_office_join_docx）
# --------------------------------------------------------------------------- #
@TOOLS.tool(
    name="work_office_join_docx",
    description=(
        "【写文件】将多份 Word 文档(.docx)一键合并为一份完整 Word 文档（保留各自段落样式、粗斜体与表格）："
        "可传 source_dir（合并目录内所有匹配的 .docx）或 files（逗号分隔或 JSON 数组的文件路径列表），"
        "支持自动插入分页符（page_break=true）与文件名分节标题（include_title=true）。"
    ),
)
def work_office_join_docx(
    source_dir: str = "",
    files: str = "",
    output: str = "",
    pattern: str = "*.docx",
    page_break: bool = True,
    include_title: bool = False,
    output_path: str = "",
) -> str:
    output = output or output_path
    doc_paths: List[Path] = []
    if (files or "").strip():
        raw_f = files.strip()
        items = json.loads(raw_f) if raw_f.startswith("[") else [x.strip() for x in raw_f.split(",") if x.strip()]
        for item in items:
            fp, err = _file_arg(str(item), "files")
            if err or fp is None:
                return _dump({"ok": False, "error": f"文件不存在: {item}"})
            if fp.suffix.lower() == ".docx":
                doc_paths.append(fp)
    elif (source_dir or "").strip():
        sdir = _resolve_path(source_dir, DIR_HARVESTED)
        if not sdir.is_dir():
            return _dump({"ok": False, "error": f"目录不存在: {sdir}"})
        doc_paths = sorted(p for p in sdir.glob(pattern or "*.docx") if p.is_file() and not p.name.startswith("~$"))
    else:
        return _dump({"ok": False, "error": "需要传 source_dir（目录）或 files（文件列表）"})

    if not doc_paths:
        return _dump({"ok": False, "error": "未找到可合并的 .docx 文件"})

    _ensure_dirs()
    out_path = _resolve_path(output, DIR_OUTPUT / "合并汇总文档.docx")
    if out_path.suffix.lower() != ".docx":
        out_path = out_path.with_suffix(".docx")
    doc_paths = [p for p in doc_paths if p.resolve() != out_path.resolve()]
    if not doc_paths:
        return _dump({"ok": False, "error": "排除输出文件自身后没有可合并的源文档"})

    wmcp, werr = _word_mcp_server()
    if wmcp is not None and not include_title:
        import asyncio
        out_path.parent.mkdir(parents=True, exist_ok=True)
        res = asyncio.run(wmcp.call_tool("merge_documents", {
            "target_filename": str(out_path),
            "source_filenames": [str(p) for p in doc_paths],
            "add_page_breaks": bool(page_break),
        }))
        msg = _excel_mcp_payload(res).get("result")
        ok = out_path.exists() and "Successfully merged" in str(msg)
        return _dump({
            "ok": ok,
            "merged_count": len(doc_paths),
            "source_files": [p.name for p in doc_paths],
            "output": str(out_path),
            "size_kb": round(out_path.stat().st_size / 1024, 1) if out_path.exists() else 0,
            "detail": msg,
        })

    from docx import Document
    target_doc = Document()
    for i, fp in enumerate(doc_paths):
        if page_break and i > 0:
            target_doc.add_page_break()
        if include_title:
            target_doc.add_heading(fp.stem, level=1)
        sub = Document(str(fp))
        for para in sub.paragraphs:
            np = target_doc.add_paragraph(para.text)
            for r_i, run in enumerate(para.runs):
                if r_i < len(np.runs):
                    np.runs[r_i].bold = run.bold
                    np.runs[r_i].italic = run.italic
                    np.runs[r_i].underline = run.underline
        for tbl in sub.tables:
            nt = target_doc.add_table(rows=len(tbl.rows), cols=len(tbl.columns))
            for r_i, row in enumerate(tbl.rows):
                for c_i, cell in enumerate(row.cells):
                    nt.cell(r_i, c_i).text = cell.text
    out_path.parent.mkdir(parents=True, exist_ok=True)
    target_doc.save(str(out_path))
    return _dump({
        "ok": out_path.exists(),
        "merged_count": len(doc_paths),
        "source_files": [p.name for p in doc_paths],
        "output": str(out_path),
        "size_kb": round(out_path.stat().st_size / 1024, 1) if out_path.exists() else 0,
    })


# 对外 MCP 只保留高频、边界清楚的工作流；其余保留为内部实现或兼容直调，
# 不进入 tools/list，避免 Agent 在重叠工具之间反复试探。
PRIVATE_TOOL_NAMES = {
    "work_office_format_gbt9704",
    "work_officecli_run",
    "work_office_watch",
    "work_office_schema",
    "work_office_create",
    "work_excel_mcp",
    "work_word_mcp",
}
for _entry in TOOLS.tools:
    if _entry["name"] in PRIVATE_TOOL_NAMES:
        _entry["public"] = False
