#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""work_core.py — 办公工具箱公共内核（模块化拆分后的共享层）。

这里只放**跨模块复用**的东西：路径常量、子进程/文件封装、结果 JSON 化、
工具注册器、参数说明表。它不含任何业务工具。

依赖方向是单向的：

    work_core  ←  work_mods/comms     通讯（微信 + 企业微信）
    work_core  ←  work_mods/office    Office（汇总 / 排版 / CLI / COM / 克隆引擎）
    work_core  ←  work_mods/base      公共工具（状态、备份、台账、经验）

两个业务模块之间**不互相 import**，这样升级通讯不必碰 Office，反之亦然。
唯一例外是 base 的 work_status：它在运行时懒加载两个模块，把各自的自检拼成一张总表；
模块不存在就如实标注「未加载」，不会因此报错。
"""

from __future__ import annotations

import functools
import inspect
import json
import time
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

# CLI 直调（work.py call / run_tool.py）只需要拿到「工具名 -> 函数」，并不需要
# 起一套 MCP 框架。实测 mcp.server.mcpserver 单独导入就要 ~0.9s，而泛问通讯通知
# 这类活以前要连打十几轮 call，光框架导入就白烧十几秒。轻量注册器只在 CLI 模式
# 生效；stdio 服务模式仍用真 MCPServer，schema 生成与 tools/list 完全不受影响。
_LIGHT = os.environ.get("WORK_TOOLKIT_LIGHT") == "1"


class _LiteToolRegistry:
    """只记录工具名与函数，不生成 JSON Schema、不启动任何传输层。"""

    def __init__(self, **_kw):
        self.tools: List[Dict[str, Any]] = []

    def tool(self, name: str | None = None, description: str | None = None, **_kw):
        def deco(fn):
            self.tools.append({"name": name or fn.__name__,
                               "description": description or "", "fn": fn})
            return fn
        return deco

    @property
    def _tool_manager(self):
        outer = self

        class _Manager:
            def list_tools(self):
                return [SimpleNamespace(name=t["name"], parameters=None)
                        for t in outer.tools]
        return _Manager()

    def run(self, *_a, **_kw):
        raise RuntimeError("轻量 CLI 模式不提供 stdio 服务；请直接执行本文件")


def make_server(name: str, version: str, instructions: str):
    """按运行模式造一个注册器：stdio 服务用真 MCPServer，CLI 用轻量壳。"""
    if _LIGHT:
        return _LiteToolRegistry(name=name, version=version, instructions=instructions)
    from mcp.server.mcpserver import MCPServer  # 延迟导入，CLI 模式下省掉 ~0.9s
    return MCPServer(name=name, version=version, instructions=instructions)


ROOT = Path(__file__).resolve().parent.parent


def _load_sibling(mod_name: str, rel: str):
    """按文件路径加载同目录模块（不污染 sys.path）。"""
    import importlib.util

    path = ROOT / rel
    if not path.exists():
        return None
    try:
        spec = importlib.util.spec_from_file_location(mod_name, str(path))
        mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        return mod
    except Exception:
        return None


def _load_pkg(pkg: str):
    """加载项目内的包（experience 这种带 __init__.py 的）。

    用 append 而不是 insert(0)：标准库与三方包优先，项目根下的同名目录
    不会去遮别人（「mcp/ 遮蔽 PyPI mcp 包」这个坑踩过一次）。
    """
    import importlib

    root = str(ROOT)
    if root not in sys.path:
        sys.path.append(root)
    try:
        return importlib.import_module(pkg)
    except Exception:
        return None


ledger = _load_sibling("work_materials_ledger", "materials_ledger.py")

exp_lib = None
try:  # 经验文档库是纯标准库增强项，加载失败不能把工具箱带崩
    _exp_pkg = _load_pkg("experience")
    if _exp_pkg is not None:
        exp_lib = getattr(_exp_pkg, "library", None)
except Exception:  # pragma: no cover
    exp_lib = None

# 刻意不留任何「工具内部自动写经验」的钩子。经验文档只在用户显式要求时
# （斜杠命令 / 网页按钮 / 手工放文件）才写入，绝不进 agent 上下文。

WORKSPACE = ROOT / "workspace"
WORKERS = ROOT / "python_workers"
BIN = ROOT / "bin"
OFFICECLI = BIN / "officecli.exe"
STATE_DIR = WORKSPACE / "06_运行状态"
STATE_FILE = STATE_DIR / "last_results.json"
PENDING_FILE = STATE_DIR / "待发催办通知.md"
TRACE_FILE = STATE_DIR / "tool_trace.jsonl"
ENGRAM_BIN = BIN / "engram.exe"
DIR_ENGRAM = WORKSPACE / "10_工作记忆" / "engram"

DIR_INBOX = WORKSPACE / "00_模拟微信收件缓存"
DIR_HARVESTED = WORKSPACE / "01_微信收回原件"
DIR_WECOM_HARVESTED = WORKSPACE / "01_企业微信收回原件"
DIR_BACKUP = WORKSPACE / "02_自动备份区"
DIR_OUTPUT = WORKSPACE / "03_汇总与成稿输出"
DIR_ARCHIVE = WORKSPACE / "04_单位历年材料库"
DIR_MEDIA = WORKSPACE / "09_微信图片"
ROSTER = WORKSPACE / "科室底册.xlsx"
MASTER_TEMPLATE = WORKSPACE / "2026年11月专项经费汇总母表_模板.xlsx"

ALLOWED_EXTS = {".docx", ".doc", ".xlsx", ".xls", ".pptx", ".pdf"}


# --------------------------------------------------------------------------- #
# 工具注册：各模块用 @TOOLS.tool(...) 收集，入口文件决定把哪些模块挂到服务上
# --------------------------------------------------------------------------- #
_TRACE_SKIP_TOOLS = {"work_status", "work_modules", "work_exp"}
_DISCOVERY_TOOLS = {"work_office_engines", "work_office_schema", "work_wechat_probe", "work_wecom_probe"}
_INPUT_KEYS = ("file", "path", "filename", "file_path", "source_dir", "file_a", "file_b",
               "template", "template_path", "roster", "chat", "chat_group", "record_dir", "source")
_OUTPUT_KEYS = ("output", "output_path", "saved_to", "backup", "report_path", "dst", "dest_dir")


def _sanitize_trace_val(val: Any, max_str: int = 300) -> Any:
    if isinstance(val, str):
        return val if len(val) <= max_str else (val[:max_str] + f"...<{len(val)} chars>")
    if isinstance(val, dict):
        return {str(k): _sanitize_trace_val(v, max_str) for k, v in list(val.items())[:20]}
    if isinstance(val, (list, tuple)):
        return [_sanitize_trace_val(v, max_str) for v in list(val)[:15]]
    return val


def _append_tool_trace(module: str, tname: str, bound_args: Dict[str, Any], out: Any, ms: float) -> None:
    if os.environ.get("WORK_TRACE_DISABLE") == "1" or tname in _TRACE_SKIP_TOOLS:
        return
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        parsed = json.loads(out) if isinstance(out, str) and out.lstrip().startswith("{") else (out if isinstance(out, dict) else {})
        if not isinstance(parsed, dict):
            parsed = {}
        ok = bool(parsed.get("ok", True))
        err = ""
        if not ok:
            for k in ("error", "reason", "detail", "message"):
                if parsed.get(k):
                    err = str(parsed[k])[:240]
                    break
        sub_tool = ""
        if tname in ("work_excel_mcp", "work_word_mcp"):
            act = str(bound_args.get("action") or "list")
            sub = str(bound_args.get("tool") or "")
            sub_tool = sub if (act == "call" and sub) else f"[{act}]"
        elif "mode" in bound_args and bound_args.get("mode"):
            sub_tool = str(bound_args["mode"])
        elif "action" in bound_args and bound_args.get("action"):
            sub_tool = str(bound_args["action"])
        elif "command" in bound_args and bound_args.get("command"):
            sub_tool = str(bound_args["command"])

        is_discovery = (
            tname in _DISCOVERY_TOOLS
            or (tname in ("work_excel_mcp", "work_word_mcp") and str(bound_args.get("action") or "list") in ("list", "schema"))
            or (tname == "work_office_inspect" and str(bound_args.get("mode") or "") == "skills")
        )
        inputs: List[str] = []
        for k in _INPUT_KEYS:
            v = bound_args.get(k)
            if isinstance(v, str) and v.strip() and v.strip() not in inputs:
                inputs.append(v.strip()[:160])
        if tname in ("work_excel_mcp", "work_word_mcp") and bound_args.get("args"):
            raw_sub = bound_args.get("args")
            sub_dict = raw_sub if isinstance(raw_sub, dict) else (_parse_json(str(raw_sub)) or {})
            if isinstance(sub_dict, dict):
                for k in _INPUT_KEYS:
                    v = sub_dict.get(k)
                    if isinstance(v, str) and v.strip() and v.strip() not in inputs:
                        inputs.append(v.strip()[:160])
        outputs: List[str] = []
        for k in _OUTPUT_KEYS:
            v = parsed.get(k)
            if isinstance(v, str) and v.strip() and v.strip() not in outputs:
                outputs.append(v.strip()[:200])

        entry = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "module": module,
            "tool": tname,
            "sub_tool": sub_tool,
            "is_discovery": is_discovery,
            "args": _sanitize_trace_val(bound_args, max_str=300),
            "ok": ok,
            "ms": round(ms, 1),
            "inputs": inputs,
            "outputs": outputs,
            "error": err,
        }
        with open(TRACE_FILE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


class ToolBox:
    """一个模块的工具收集器。

    装饰器语义与 MCPServer.tool 一致，但不立即注册到任何 server：
    同一个模块既能挂进「合体服务」，也能单独起一个只含自己的 stdio 服务。
    同时自动包装一层极轻量、零上下文污染的工具调用轨迹记录（供 work_exp 一键提炼经验使用）。
    """

    def __init__(self, module: str):
        self.module = module
        self.tools: List[Dict[str, Any]] = []

    def tool(self, name: str | None = None, description: str | None = None, **_kw):
        def deco(fn):
            tname = name or fn.__name__
            sig = None
            try:
                sig = inspect.signature(fn)
            except Exception:
                sig = None

            @functools.wraps(fn)
            def _wrapped(*args, **kwargs):
                t0 = time.perf_counter()
                out = fn(*args, **kwargs)
                ms = (time.perf_counter() - t0) * 1000
                try:
                    if sig is not None:
                        ba = sig.bind_partial(*args, **kwargs)
                        bound = {k: v for k, v in ba.arguments.items() if v not in ("", None, False, 0, [], {})}
                    else:
                        bound = {k: v for k, v in kwargs.items() if v not in ("", None, False, 0, [], {})}
                except Exception:
                    bound = dict(kwargs)
                _append_tool_trace(self.module, tname, bound, out, ms)
                return out

            self.tools.append({"module": self.module, "name": tname,
                               "description": description or "", "fn": _wrapped})
            return _wrapped
        return deco

    def names(self) -> List[str]:
        return [t["name"] for t in self.tools]


def register(server, boxes) -> int:
    """把若干 ToolBox 挂到 server 上，返回注册的工具数。"""
    n = 0
    for box in boxes:
        for t in box.tools:
            server.tool(name=t["name"], description=t["description"])(t["fn"])
            n += 1
    return n


def tool_map(boxes) -> Dict[str, Any]:
    """工具名 → 函数。合体入口把它铺进 globals()，兼容老的 getattr(G, name) 调用。"""
    return {t["name"]: t["fn"] for box in boxes for t in box.tools}


# --------------------------------------------------------------------------- #
# 基础设施
# --------------------------------------------------------------------------- #
def _ensure_dirs() -> None:
    for d in (DIR_INBOX, DIR_HARVESTED, DIR_WECOM_HARVESTED, DIR_BACKUP, DIR_OUTPUT,
              DIR_ARCHIVE, DIR_MEDIA, STATE_DIR):
        d.mkdir(parents=True, exist_ok=True)


def _run(cmd: List[str], timeout: int = 180, cwd: Optional[Path] = None) -> Dict[str, Any]:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    # officecli 有「常驻进程 + 内存改动延迟落盘」的机制：set/add 返回 ok，但磁盘上
    # 还是旧内容，要 save/close 或等 2~10 秒空闲才写。别的程序（openpyxl、用户双击）
    # 读到的就是没改的版本，表现为「工具说改好了，打开还是老的」。
    # each = 每次命令结束就 flush，代价可以忽略，换来「返回即落盘」。
    if os.path.basename(str(cmd[0])).lower().startswith("officecli"):
        env.setdefault("OFFICECLI_RESIDENT_FLUSH", "each")
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd or ROOT),
            capture_output=True,
            timeout=timeout,
            env=env,
        )
        return {
            "exit_code": proc.returncode,
            "stdout": proc.stdout.decode("utf-8", errors="replace").strip(),
            "stderr": proc.stderr.decode("utf-8", errors="replace").strip(),
        }
    except subprocess.TimeoutExpired:
        return {"exit_code": 124, "stdout": "", "stderr": f"命令超时（>{timeout}s）: {' '.join(cmd)}"}
    except FileNotFoundError as exc:
        return {"exit_code": 127, "stdout": "", "stderr": f"命令不存在: {exc}"}


def _parse_json(text: str) -> Optional[Any]:
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    first, last = text.find("{"), text.rfind("}")
    if first != -1 and last > first:
        try:
            return json.loads(text[first : last + 1])
        except Exception:
            return None
    return None


def _dump(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def _unwrap_officecli(parsed: Any) -> tuple[Any, bool]:
    """剥掉 officecli 的 ``{"success":..., "data":{...}}`` 外壳。

    不剥的话，工具返回里会出现 ``data.data`` 两层，调用方得多点一层才拿到
    真正的内容，而且 ``ok`` 和 ``success`` 两个同义字段并存，容易看错。
    返回 ``(内层内容, 是否成功)``；不是这个外壳形态就原样带回。
    """
    if not isinstance(parsed, dict):
        return parsed, True
    keys = set(parsed)
    if "data" not in keys or not keys <= {"success", "data", "error", "message", "code"}:
        return parsed, True
    inner = parsed.get("data")
    ok = parsed.get("success", parsed.get("error") is None)
    if inner is None:
        return parsed, bool(ok)
    return inner, bool(ok)


def _load_state() -> Dict[str, Any]:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_state(key: str, value: Any) -> None:
    _ensure_dirs()
    state = _load_state()
    state[key] = value
    state["_updated_at"] = datetime.now().isoformat(timespec="seconds")
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _list_files(dir_path: Path) -> List[Dict[str, Any]]:
    if not dir_path.exists():
        return []
    items = []
    for p in sorted(dir_path.iterdir()):
        if not p.is_file() or p.name.startswith("~$"):
            continue
        st = p.stat()
        items.append(
            {
                "name": p.name,
                "path": str(p.resolve()),
                "ext": p.suffix.lower(),
                "size_kb": round(st.st_size / 1024, 1),
                "updated_at": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
            }
        )
    return items


def _safe_name(name: str, fallback: str = "未命名") -> str:
    """把会话名/文件名收敛成可用作目录名的形式（Windows 禁用字符）。"""
    cleaned = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", str(name or "")).strip(" ._")
    return (cleaned or fallback)[:60]

def _Search_workspace_file(raw: str) -> Optional[Path]:
    """在工作区及常用子目录中按相对路径或文件名兜底查找已存在的文件。"""
    clean = (raw or "").strip()
    if not clean:
        return None
    p = Path(clean)
    search_dirs = (WORKSPACE, DIR_OUTPUT, DIR_HARVESTED, DIR_WECOM_HARVESTED,
                   DIR_ARCHIVE, DIR_INBOX, STATE_DIR, DIR_BACKUP)
    if not p.is_absolute():
        for base in (ROOT, WORKSPACE):
            cand = (base / p).resolve()
            if cand.is_file():
                return cand
    fname = p.name
    if fname:
        for d in search_dirs:
            cand = (d / fname).resolve()
            if cand.is_file():
                return cand
    return None


def _resolve_path(raw: str, default: Path) -> Path:
    if not raw:
        return default
    p = Path(raw)
    if not p.is_absolute():
        cand = (ROOT / raw).resolve()
        if cand.exists():
            return cand
        ws_cand = (WORKSPACE / raw).resolve()
        norm = str(raw).replace("\\", "/")
        if ws_cand.exists() or norm.startswith(("00_", "01_", "02_", "03_", "04_", "05_", "06_", "07_", "08_", "09_")):
            return ws_cand
        found = _Search_workspace_file(raw)
        if found is not None:
            return found
        p = cand
    return p


def _file_arg(raw: str, label: str = "file") -> tuple[Optional[Path], Optional[str]]:
    """解析「用户给的文件参数」，失败时给一句人能读懂的原因。

    只判 exists() 是不够的：传空串时 _resolve_path 会退回项目根目录，
    目录 exists() 也是 True，后面 copy2 / OfficeCLI 把目录当文件用，
    报出来就是 PermissionError 或者干脆没有 error 文本（回归实测踩到）。
    """
    if not (raw or "").strip():
        return None, f"{label} 不能为空：传文件绝对路径，或工作区相对路径"
    p = _resolve_path(raw, ROOT / raw)
    if not p.exists():
        found = _Search_workspace_file(raw)
        if found is not None:
            p = found
    if not p.exists():
        return None, f"文件不存在: {p}"
    if not p.is_file():
        return None, f"这不是文件（是目录）: {p}"
    return p, None


def _port_open(host: str, port: int, timeout: float = 0.6) -> bool:
    """本机端口探活：用于本地服务与 Office 实时预览端口连通性检查。"""
    import socket

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# 参数说明：MCPServer 从函数签名生成 schema，参数级 description 一律为空，
# 客户端只看得到 since / limit / dedupe 这种裸名，不知道填什么格式。
# 这里按参数名统一补中文说明，并补齐签名默认值掩盖掉的必填项。
# --------------------------------------------------------------------------- #
_PARAM_DOCS = {
    "limit": "最多返回多少条（默认见 schema）",
    "since": "起始时间，可写 2026-10-01 或 2026-10-01T08:00:00；留空表示不限",
    "until": "截止时间，格式同 since；留空表示到当前",
    "since_time": "从什么时候开始收材料，格式 2026-10-01；留空默认最近 7 天",
    "chat": "会话名（群名、备注名、wxid 或会话 id），支持模糊匹配",
    "chats": "只看这些会话，逗号分隔（群名片段即可），留空表示不限",
    "prefix": "收件文件名前缀规则，可用 {chat} {sender} {month} {ext}，留空不加前缀",
    "chat_name": "会话名（群名或联系人备注名）",
    "chat_group": "微信群名，用于发送或按群收材料",
    "account_dir": "微信账号数据目录，留空自动探测",
    "keyword": "检索关键词，中英文均可，按子串匹配",
    "sender": "按发言人（备注名、昵称或 wxid）筛选消息，支持模糊匹配",
    "msg_type": "按消息类型筛选：text(文本)/file(文件)/link(链接)/image(图片)/voice(语音)/quote(引用回复)/solitaire(接龙)/announcement(群公告)/miniprogram(小程序)/system(系统)",
    "fav_type": "收藏类型过滤：all(全部)/note(笔记与文本)/link(网页与网盘链接)/file(文件)/record(聊天记录)",
    "check_missing": "是否自动对比群成员或底册计算未接龙人员名单，默认 true",
    "exts": "只保留这些扩展名，如 [\"docx\",\"xlsx\"]",
    "kinds": "文件类别过滤，如 [\"document\"] 表示只看文档",
    "order": "排序方向：desc 最新在前，asc 最早在前",
    "with_stats": "是否附带总体统计（份数、总体积、按月分布）",
    "dest_dir": "目标目录，留空用工作区默认目录",
    "prefix_rule": "文件名前缀规则，可用 {month} {kind} {ext}，留空不加前缀",
    "dedupe": "目标目录已有同内容文件时跳过，保证重复执行不产生副本",
    "roster": "科室底册 xlsx 路径，用于判断谁交了、谁没交",
    "roster_path": "科室底册 xlsx 路径",
    "file": "目标文件路径（任意本机文件，绝对路径或相对工作区路径）",
    "output_path": "输出文件路径，留空自动生成到产物目录",
    "mode": "读取模式：text 纯文本 / outline 结构大纲 / stats 统计 / annotated 带标注 / issues 问题清单 / forms 表单；xlsx 想按区域读就用 text + cell_range",
    "max_lines": "最多返回多少行内容",
    "cell_range": "单元格范围，如 A1:D20，仅 xlsx + mode=text 时生效",
    "page": "页码，仅 PDF 类文件适用",
    "timeout": "子进程超时秒数",
    "force": "是否强制（各工具含义不同，见该工具描述）",
    "reset_demo_fixtures": "测试钩子：重建演示数据，正常使用不要传",
    "compact": "精简输出：文件树只给数量与样例，避免几十 KB 把对话撑爆；/work 默认用它",
    "only_unread": "只看有未读数的会话",
    "save_dir": "归档保存目录，留空用 01_微信收回原件",
    "inbox_dir": "收件目录，留空用工作区暂存目录",
    "missing_list": "未交单位名单，用于生成 @ 催办话术",
    "deadline_text": "截止时间描述，如「本周五下班前」",
    "custom_message": "完全自定义的催办话术，传了就不用模板",
    "dry_run": "只生成话术不发消息，默认 true",
    "load_more": "是否向前加载更多历史消息",
    "files_only": "只看文件类消息",
    "source_dir": "要汇总的材料目录",
    "template_path": "母表模板 xlsx 路径，留空用默认模板",
    "rules": "合并规则覆盖，按列名指定取值或校验方式",
    "content_or_path": "正文 Markdown 文本，或一份草稿文件路径",
    "doc_type": "文种：通知 / 报告 / 请示 / 批复 / 函 / 会议纪要",
    "header_unit": "红头单位全称，如「某某市人民政府办公室」",
    "doc_number": "发文字号，如 某政办发〔2026〕12号",
    "command": "OfficeCLI 子命令，如 validate / get / set / add",
    "args": "子命令附加参数数组，如 [\"--json\"]",
    "prefer": "图片取哪种：thumb 缩略图（快、无需 ffmpeg）/ orig 原图（WXGF 需 ffmpeg 转 PNG）",
    "ref": "只导某一张图：32 位 hex 图片引用（消息里的 media_ref），配合 prefer=orig 取原图",
    "output_dir": "产出目录，留空自动落在工作区对应目录",
    "action": ("动作。work_ledger：summary / departments / missing / history / batches / state / record；"
               "work_exp：distill / list / search / get / replay / to_skill / trace / save / template / export；"
               "work_mem：save / search / context / get / timeline / pin / unpin / delete / stats / doctor / export_obsidian"),
    "dept": "科室名，action=history 时只看该科室",
    "record_dir": "要记账的目录，action=record 时必填",
    "note": "批次说明，如「11月专项经费摸底」，便于日后回溯",
    "source": "数据来源标记，如 local_inbox / manual",
    "batch_dir": "整目录批量转换 PDF",
    "output": "输出路径，留空自动生成",
    "sheet": "只导某一张工作表（名称或序号），留空导整个工作簿",
    "title": "标题 / 任务名，留空自动生成",
    "format": "输出格式：docx（按公文习惯排版）/ md",
    "include_media": "是否把图片消息解密后嵌进正文，默认 false",
    "max_images": "最多嵌入几张图片，避免材料过大",
    # ── work_exp：任务经验文档库 ──
    "doc_id": "经验文档 id，取自 action=list 返回的 id；action=get 时必填",
    "content": "action=save 时可直接给整篇 Markdown 原文（给了就按原文存，不再套模板）",
    "day": "归档日期 YYYY-MM-DD，留空用今天",
    "overwrite": "action=save 时同日期同名已存在是否覆盖，默认 false（不覆盖）",
    "summary": "一句话经验：下次遇到同类任务最该先想起的一句",
    "goal": "这次要交付什么、边界与验收标准",
    "materials": "用到的材料清单（输入文件 / 数据来源 / 参考件）",
    "steps": "完整流程，按顺序写，能直接照着做",
    "decisions": "关键口径与决策：当时为什么这么定",
    "pitfalls": "踩过的坑，按「现象 → 根因 → 改法」写",
    "outputs": "产物与落点（文件、路径、给谁用）",
    "snippets": "可整段复制的命令 / 提示词 / 代码",
    "query": "查询词（其他工具用；work_exp 没有检索动作）",
    "body": "正文 / 内容",
    "tags": "标签，逗号分隔，便于按主题翻阅",
    "lesson": "一句话教训：下次别怎么做",
    "outcome": "这次的结果",
    # ── OfficeCLI 建文档 / 套模板 / 导数据 / batch ──
    "ops": ('操作数组的 JSON（也可给 .json 路径），每项如 '
            '{"command":"set","path":"/body/p[1]","props":{"bold":"true"}}'),
    "data": "数据 JSON 对象（如 {\"单位\":\"某局\"}），或 .json 文件路径",
    "template": "模板文件路径，占位符写成 {{key}}",
    "engine": "Office 引擎：auto（先 MS 再 WPS）/ ms / wps",
    "header": "首行是否当表头：true 会加筛选并冻结首行",
    "start_cell": "导入起始单元格，默认 A1",
    "delimiter": "CSV 分隔符（默认按扩展名推断），如分号 ';'",
}

# 同名参数在不同工具里含义不同，必须按工具覆盖，否则描述会误导 agent
_PARAM_DOCS_BY_TOOL = {
    ("work_wecom_messages", "chat"): "群名 / 群名片段 / 会话 id（R: 群聊，S: 单聊），支持模糊匹配",
    ("work_wecom_search", "chat"): "只在某个会话里搜；留空搜全部",
    ("work_wecom_files", "exts"): "只看这些扩展名，逗号分隔，如 docx,xlsx,pdf",
    ("work_wecom_harvest", "exts"): "只收这些扩展名，逗号分隔，如 docx,xlsx,pdf",
    ("work_wecom_harvest", "chats"): "只收这些会话里的文件，逗号分隔",
    ("work_wecom_harvest", "prefix"): "命名前缀，如 {chat}_{sender}；留空只用原文件名",
    ("work_wecom_harvest", "dry_run"): "默认 true 只列计划不复制；确认要收再传 false",
    ("work_wecom_harvest", "dest_dir"): "收件目标目录，留空用 01_企业微信收回原件",
    ("work_wecom_contacts", "keyword"): "姓名/拼音/手机/邮箱/职位 任一包含即可，留空列全部",
    ("work_office_schema", "format"): "docx / xlsx / pptx；留空列全部格式",
    ("work_office_schema", "verb"): "add / set / get / query / remove；留空列元素清单",
    ("work_office_schema", "element"): "元素名，如 paragraph / cell / slide",
    ("work_office_import", "source"): "CSV/TSV 文件路径（与 data 二选一）",
    ("work_office_import", "sheet"): "目标工作表名（如 Sheet1，默认第一张）",
    ("work_office_import", "format"): "数据格式 csv / tsv，默认按扩展名推断",
    ("work_office_create", "file"): (
        "要新建的文件名（不是已存在的文件），如 11月汇总表.xlsx；"
        "留空会报错，扩展名只认 .docx/.xlsx/.pptx"),
    ("work_office_create", "force"): (
        "目标文件已存在时是否覆盖；默认 false 直接报错拒绝写入，true 会盖掉旧文件（建议先 work_backup_file）"),
    ("work_office_merge", "force"): (
        "输出文件已存在时是否覆盖；默认 false 直接报错拒绝写入，true 会盖掉旧文件（建议先 work_backup_file）"),
    ("work_wechat_sync", "force"): "忽略已有缓存，强制重新解密刷新（源库没变时不必传）",
    ("work_wechat_files", "mode"): (
        "运行模式：scan 只读扫描统计微信收到的文件（不复制）/ "
        "pull 按筛选条件复制到目标目录（可选传 roster 核对）/ "
        "harvest 一键归档近期文档并联动《科室底册.xlsx》与台账核对未交名单"),
    ("work_wechat_files", "roster"): "科室底册 xlsx 路径或逗号分隔名单（mode=pull/harvest 时用于核对已交/未交）",
    ("work_wechat_files", "dest_dir"): "归档保存目录，留空默认 01_微信收回原件/",
    ("work_wechat_contacts", "mode"): (
        "查询模式：contacts 查微信联系人（附所在群列表）/ "
        "group 查指定微信群的群主、群公告与完整成员名单 / "
        "announcements 汇总全部微信群的非空群公告 / "
        "common_groups 查多人共同所在的微信群"),
    ("work_wechat_insights", "output"): (
        "可选：把会话深度画像与待办清单导出为 Markdown 简报的路径（留空只返回 JSON 不写文件）"),
    ("work_office_merge", "template"): "模板文件路径（.docx/.xlsx/.pptx），里面写 {{key}} 占位符",
    ("work_office_merge", "output"): "导出文件路径（扩展名要与模板一致），留空会报错",
    ("work_office_com", "action"): (
        "apps / active / info / export_pdf / recalc / refresh_fields / replace / "
        "read_cells / set_cells / run_python（共 10 个）"),
    ("work_office_com", "file"): "目标文件；action=apps / run_python 可留空",
    ("work_office_com", "range"): "读取区域，如 A1:D20",
    ("work_office_com", "find"): "Word 里要查找的原文（action=replace 必填）",
    ("work_office_com", "replace"): "替换成什么（给空串表示删除）",
    ("work_office_com", "cells"): '要写入的单元格 JSON，如 {"A1":"值","B2":12}',
    ("work_office_com", "code"): (
        "【action=run_python 必填】要执行的 Python。可直接用 Officer（=office）、data、output 三个名字；"
        "Officer.Word/.Excel/.PPT 会优先接管用户已打开的窗口，改完必须 Save() 才落盘"),
    ("work_office_com", "data"): "传给脚本的 data 变量（action=run_python）；merge 时是模板占位符 JSON",
    ("work_office_com", "keep_open"): "run_python 执行完是否留着应用窗口（默认 true）",
    ("work_office_com", "visible"): "run_python 拉起的应用是否可见（默认 true）",
    ("work_office_com", "timeout"): "run_python 的脚本超时秒数（默认 120），超时会中断并返回错误",
    # watch 的 action 语义和 ledger/exp 完全不同，不覆盖就会显示成台账的动作列表，
    # 智能体照着「summary / departments」去调会直接报错
    ("work_office_watch", "action"): (
        "start 起预览 / status 查状态 / stop 停 / mark 标记元素 / unmark 取消标记 / "
        "marks 列出标记 / goto 把页面滚到某元素"),
    ("work_office_watch", "file"): "要预览的 docx / xlsx / pptx",
    ("work_office_watch", "port"): "预览服务端口，默认 26315",
    ("work_office_watch", "path"): "元素路径（mark / goto / unmark 用），如 /body/p[1]",
    ("work_office_watch", "all"): "unmark 时是否清掉该文件的全部标记",
    ("work_office_engines", "action"): "probe 盘点全部克隆的 Office/MCP 引擎并实际加载检测可用性",
    ("work_excel_mcp", "action"): "list（或 schema）列出匹配的 Excel 工具与完整参数 Schema；call 调用指定子工具",
    ("work_excel_mcp", "tool"): "要调用的 excel-mcp-server 子工具名，如 describe_workbook / read_range / trace_formula / goal_seek / create_pivot_table / create_chart",
    ("work_excel_mcp", "args"): '传给子工具的参数 JSON 对象字符串，如 {"path":"2026年11月汇总母表.xlsx","range":"A1:G10"}（自动兼容 path/file 与工作区相对路径）',
    ("work_excel_mcp", "allow_write"): "是否允许执行写操作（默认 false 只读；传 true 时写前自动备份到 02_自动备份区/）",
    ("work_word_mcp", "action"): "list（或 schema）列出匹配的 Word 工具与完整参数 Schema；call 调用指定子工具",
    ("work_word_mcp", "tool"): "要调用的 Office-Word-MCP-Server 子工具名，如 get_document_info / get_all_comments / merge_documents / add_footnote_robust / replace_paragraph_block_below_header",
    ("work_word_mcp", "args"): '传给子工具的参数 JSON 对象字符串，如 {"filename":"报告.docx"}（自动兼容 filename/file/path 与工作区相对路径）',
    ("work_word_mcp", "allow_write"): "是否允许执行写操作（默认 false 只读；传 true 时写前自动备份到 02_自动备份区/）",
    ("work_office_consolidate", "auto_schema"): "true 时启用无模板通用合并：自动扫描所有 .xlsx/.csv/.tsv/.docx 表格推断并对齐表头",
    ("work_office_format_gbt9704", "preset"): "排版风格预设：gbt9704（默认，党政机关国标公文）/ modern（现代商务技术报告）/ academic（学术论文三线表）/ memo（简洁备忘录）",
    ("work_office_inspect", "mode"): (
        "读取/渲染模式：text 纯文本 / outline 结构大纲 / stats 统计 / issues 问题与公式体检 / annotated 带路径标注 / "
        "forms 表单控件 / comments 提取全部批注 / screenshot 渲染页面或区域高清截图 PNG / html / svg / pdf / skills 加载内置构建指南"),
    ("work_office_inspect", "grid"): "mode=screenshot 时将多页/多张幻灯片拼成缩略图矩阵，传 'auto' 或列数如 '3'",
    ("work_office_inspect", "cols"): "Excel 列过滤（逗号分隔，如 'A,B,C'）",
    ("work_office_inspect", "issue_type"): "mode=issues 时按问题类型过滤：format / content / structure 或具体子类型",
    ("work_office_inspect", "skill"): "mode=skills 时要加载的内置指南名（如 data-dashboard / financial-model / academic-paper / pitch-deck / word-form / morph-ppt / list）",
    ("work_office_compose", "content_or_path"): "Markdown 文本或 .md 文件路径（支持标题、列表、代码块、GFM 表格、```mermaid 流程图、$$数学公式$$）",
    ("work_office_compose", "format"): "生成目标格式：docx（默认，通用富文档）或 pptx（演示文稿）",
    ("work_office_compose", "toc"): "docx 是否在开头自动插入目录（默认 false）",
    ("work_office_compose", "watermark"): "docx 水印文字（留空不加水印）",
    ("work_office_diff", "file_a"): "基准文件（旧版 .docx 或 .xlsx 路径）",
    ("work_office_diff", "file_b"): "对比文件（新版 .docx 或 .xlsx 路径）",
    ("work_office_diff", "sheet"): "仅对比指定工作表名称（仅 .xlsx 适用，留空对比全部同名表）",
    ("work_office_diff", "output"): "可选：将差异对比报告导出为 Markdown 文件的路径",
    ("work_office_join_docx", "source_dir"): "待合并的 Word 文档所在目录",
    ("work_office_join_docx", "files"): "待合并的 .docx 文件路径列表（逗号分隔或 JSON 数组）",
    ("work_office_join_docx", "output"): "合并后的目标 .docx 输出路径",
    ("work_office_join_docx", "page_break"): "文档之间是否插入分页符（默认 true）",
    ("work_office_join_docx", "include_title"): "是否把每个源文件名作为一级标题插入（默认 false）",
    ("work_exp", "action"): "distill（从近期工具轨迹一键提炼绑定模块工具链的经验）/ list / search / get / replay（一键重放）/ to_skill（导出子Skill）/ trace / save / template / export",
    ("work_exp", "query"): "检索关键词（action=search 时匹配标题、摘要、标签、工具名与正文）",
    ("work_exp", "module"): "按所属模块过滤或标注：comms / office / memory",
    ("work_exp", "tool"): "按绑定工具名过滤（如 work_office_consolidate / work_excel_mcp）",
    ("work_exp", "steps_json"): "显式指定工具步骤数组 JSON，每项如 {\"tool\":\"work_office_diff\",\"args\":{\"file_a\":\"a.xlsx\",\"file_b\":\"b.xlsx\"},\"note\":\"对比差异\"}",
    ("work_exp", "vars_json"): "action=replay 时替换 {{变量}} 的 JSON 对象，如 {\"input_file\":\"新表.xlsx\"}",
    ("work_exp", "dry_run"): "action=replay 时默认 true 只预览代入变量后的工具调用序列；传 false 立即顺序执行",
    ("work_exp", "skill_name"): "action=to_skill 时导出的子 Skill 名称（英文短横线，如 work-sop-consolidate）",
    ("work_exp", "install_codex"): "action=to_skill 时是否同时安装到 skills/ 与 ~/.codex/skills/（默认 false）",
    ("work_exp", "auto_from_trace"): "action=save 且未手写全文时，是否自动融合近期真实工具调用轨迹（默认 true）",
    ("work_exp", "since_minutes"): "采集最近多少分钟内的工具调用轨迹（默认 240 分钟）",
    ("work_mem", "action"): "save（保存记忆）/ search（FTS5全文检索）/ context（近期会话与置顶记忆）/ get / timeline / pin / unpin / delete / stats / doctor / export_obsidian",
    ("work_mem", "query"): "检索关键词（action=search 时必填）",
    ("work_mem", "title"): "记忆标题（action=save 时必填，简短可检索）",
    ("work_mem", "content"): "记忆正文（支持 What / Why / Where / Learned 结构化内容）",
    ("work_mem", "type"): "记忆类别：decision（决策口径）/ pattern（工具流程模式）/ learning（经验规律）/ config（配置约定）/ bugfix（避坑）/ discovery（发现）/ architecture（架构）",
    ("work_mem", "topic_key"): "可选主题唯一键（如 office/default-preset），同主题重复 save 会自动版本演进覆盖而不产生重复记录",
    ("work_mem", "scope"): "作用域：project（当前项目，默认）/ personal（个人跨项目）/ global（全局）",
    ("work_mem", "project"): "所属项目名，默认 work-super-agent",
    ("work_mem", "obs_id"): "记忆条目整数 ID（action=get/timeline/pin/unpin/delete 时必填）",
    ("work_mem", "all_projects"): "action=search 时是否跨所有项目检索（默认 false）",
    ("work_mem", "hard_delete"): "action=delete 时是否永久物理删除（默认 false 软删除）",
    ("work_mem", "vault_path"): "action=export_obsidian 时导出的 Obsidian 库路径（留空默认 workspace/10_工作记忆/obsidian_vault）",
}

# 函数签名给了默认值、但实际不传就办不成事的参数
_PARAM_REQUIRED = {
    "work_wechat_messages": ["chat"],
    "work_wechat_search": ["keyword"],
    "work_wecom_messages": ["chat"],
    "work_wecom_search": ["keyword"],
    "work_office_inspect": ["file"],
    "work_backup_file": ["file"],
    "work_officecli_run": ["file"],
    "work_wechat_digest": ["chat"],
    "work_office_batch": ["file", "ops"],
    "work_office_create": ["file"],
    "work_office_merge": ["template", "output", "data"],
    "work_office_import": ["file"],
    "work_office_com": ["action"],
    "work_office_compose": ["content_or_path"],
    "work_office_diff": ["file_a", "file_b"],
}



def annotate(server) -> None:
    """给工具 schema 补参数中文说明与必填项。"""
    if server is None:  # pragma: no cover
        return
    for tool in server._tool_manager.list_tools():
        params = tool.parameters or {}
        props = params.get("properties") or {}
        for pname, spec in props.items():
            if (tool.name, pname) in _PARAM_DOCS_BY_TOOL:
                spec["description"] = _PARAM_DOCS_BY_TOOL[(tool.name, pname)]
            if not spec.get("description") and pname in _PARAM_DOCS:
                spec["description"] = _PARAM_DOCS[pname]
        extra = _PARAM_REQUIRED.get(tool.name)
        if extra:
            cur = list(params.get("required") or [])
            for pname in extra:
                if pname in props and pname not in cur:
                    cur.append(pname)
            params["required"] = cur
