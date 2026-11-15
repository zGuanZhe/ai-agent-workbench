#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""work.py — WorkBuddy 超级 Skill 的本地桥接（唯一运行入口）。

设计目标：用户只要装了一个总 Skill、会用 ``/work``，不需要打开网页、
不需要在连接器里信任 MCP，也能跑完全部 27 个工具。

    python scripts/work.py ask "把 11 月报表收一下并汇总"   # 第一步：口语 -> 确定该调什么
    python scripts/work.py call <tool> '<JSON 参数>'
    python scripts/work.py tools [工具名或中文关键字]
    python scripts/work.py doctor
    python scripts/work.py setup [--check]
    python scripts/work.py web [--no-open] [--port 8765]

``ask`` 是关键：把「智能体读文档猜工具」变成一条命令给出的确定执行计划，
避免不调工具、调错工具、自己手写脚本绕过、或宣称工具里并不存在的能力。

本文件只用标准库，任何 Python 3.8+ 都能启动；真正跑工具时会自己挑一个装齐
依赖的解释器，并把结果原样回给 WorkBuddy。JSON 参数也可以写成 ``@args.json``
（从文件读）或 ``-``（从 stdin 读），便于传长正文、多行 Markdown。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = ROOT / "tools"
RUNNER = TOOLS_DIR / "run_tool.py"
TOOLS_JSON = ROOT / "web" / "tools.json"
INTENTS_JSON = ROOT / "web" / "intents.json"
CACHE = ROOT / "workspace" / ".pyexe"
sys.path.insert(0, str(TOOLS_DIR))
import doctor  # noqa: E402

# 用正斜杠：Windows 的 Bash 里反斜杠在引号中容易被当转义符，正斜杠 Python 也认。
BRIDGE = 'python "' + (ROOT / "scripts" / "work.py").as_posix() + '"'


def _env() -> dict:
    e = dict(os.environ)
    e["PYTHONIOENCODING"] = "utf-8"
    e.setdefault("PYTHONUTF8", "1")
    return e


def _pick_python() -> str | None:
    """优先用缓存的解释器；没有缓存或缓存失效时交给 doctor 找。"""
    if CACHE.is_file():
        try:
            cached = CACHE.read_text(encoding="utf-8-sig").strip().strip('"')
            if cached and os.path.isfile(cached):
                return cached
        except OSError:
            pass
    best, _ = doctor.find_best()
    if not best:
        return None
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(best, encoding="utf-8", newline="\n")
    except OSError:
        pass
    return best


def _drop_cache() -> None:
    try:
        CACHE.unlink(missing_ok=True)
    except TypeError:  # Python < 3.8 兼容兜底
        if CACHE.exists():
            CACHE.unlink()
    except OSError:
        pass


def _read_stdin(timeout: float = 3.0) -> str:
    """显式写了 ``-`` 就必须读到参数；读不到要报错，绝不静默当成 ``{}``。

    静默退化成 {} 会让工具用默认参数跑出「莫名奇妙」的结果，是最难排查的一类
    使用故障。读取放线程里是为了兼容父进程把 stdin 开成管道却不写也不关的情况。
    """
    if sys.stdin is None or sys.stdin.isatty():
        raise ValueError("stdin 没有可用参数（交互终端或空管道）。请直接把 JSON 写在命令行上")
    box: dict[str, bytes] = {}

    def reader():
        try:
            box["data"] = sys.stdin.buffer.read()
        except Exception:
            box["data"] = b""

    th = threading.Thread(target=reader, daemon=True)
    th.start()
    th.join(timeout)
    if "data" not in box:
        raise ValueError("从 stdin 读取参数超时（管道没关闭）。改用命令行 JSON 或 @args.json")
    raw = box["data"]
    if not raw.strip():
        raise ValueError("stdin 是空的。改用命令行 JSON 或 @args.json")
    for enc in ("utf-8-sig", "utf-8", "gbk", "utf-16"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


def _load_args(raw: str) -> dict:
    raw = (raw or "{}").strip()
    if raw == "-":
        raw = _read_stdin().strip()
    elif raw.startswith("@"):
        p = Path(raw[1:])
        if not p.is_absolute() and not p.exists():
            p = ROOT / p
        if not p.is_file():
            raise ValueError(f"参数文件不存在：{p}")
        raw = p.read_text(encoding="utf-8-sig")
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"参数不是合法 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("参数必须是 JSON 对象")
    return data


def _tool_data() -> list:
    if TOOLS_JSON.is_file():
        return json.loads(TOOLS_JSON.read_text(encoding="utf-8-sig"))
    py = _pick_python()
    if not py:
        raise RuntimeError("web/tools.json 不存在，且找不到可用解释器重新生成")
    r = subprocess.run([py, str(ROOT / "mcp" / "dump_tools.py")], cwd=str(ROOT),
                       env=_env(), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=180)
    if r.returncode != 0:
        raise RuntimeError("生成工具清单失败：" + (r.stderr or "")[-300:])
    data = json.loads(r.stdout)
    TOOLS_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                          encoding="utf-8", newline="\n")
    return data


def _brief(text: str, limit: int = 58) -> str:
    one = (text or "").split("\n")[0].strip()
    for sep in ("。", "；", "："):
        if sep in one and len(one) > limit:
            one = one.split(sep)[0] + sep
            break
    return one[:limit]


def cmd_call(tool: str, raw: str, timeout: int) -> int:
    if not tool.startswith("work_"):
        print(f"[work] 工具名必须以 work_ 开头：{tool}", file=sys.stderr)
        return 2
    try:
        kwargs = _load_args(raw)
    except (OSError, ValueError) as exc:
        print(f"[work] {exc}", file=sys.stderr)
        print(f"[work] 查参数：{BRIDGE} tools {tool}", file=sys.stderr)
        return 2

    py = _pick_python()
    if not py:
        print("[work] 没找到装齐依赖的 Python。用 /work 安装，或运行：python scripts\\setup.py",
              file=sys.stderr)
        return 3

    payload = json.dumps(kwargs, ensure_ascii=False)
    cmd = [py, str(RUNNER), tool, payload]
    for attempt in (0, 1):
        try:
            r = subprocess.run(cmd, cwd=str(ROOT), env=_env(), capture_output=True,
                               text=True, encoding="utf-8", errors="replace",
                               timeout=timeout)
        except subprocess.TimeoutExpired:
            print(f"[work] 工具 {tool} 超过 {timeout} 秒未完成，已中止", file=sys.stderr)
            return 124
        # 缓存的解释器被卸载/换掉时，清掉缓存重挑一次，不让用户看见 ModuleNotFoundError
        if attempt == 0 and r.returncode == 3 and "加载工具箱失败" in (r.stderr or ""):
            _drop_cache()
            py2 = _pick_python()
            if py2 and py2 != py:
                py = py2
                cmd = [py, str(RUNNER), tool, payload]
                continue
        break

    sys.stdout.write(r.stdout or "")
    sys.stderr.write(r.stderr or "")

    data = None
    try:
        data = json.loads((r.stdout or "").strip() or "{}")
    except (ValueError, TypeError):
        pass

    if isinstance(data, dict) and data.get("ok") is False:
        err = str(data.get("error") or data.get("reason") or data.get("detail") or "未说明")
        # 业务失败不是桥接故障：退出码保持 0，免得智能体把「命令失败」当成
        # 工具坏了而直接放弃；同时把可操作的下一步写在 stderr 最显眼处。
        print(f"[work] 工具 {tool} 返回 ok:false → {err}", file=sys.stderr)
        print(f"[work] 这是业务结果不是故障：按 error 修正参数后重试一次；"
              f"查参数用 {BRIDGE} tools {tool}", file=sys.stderr)
        return 0
    if not data and r.returncode != 0:
        print(f"[work] 工具 {tool} 没有返回结构化结果（退出码 {r.returncode}）。"
              f"先看 stderr，再跑 {BRIDGE} doctor", file=sys.stderr)
        return r.returncode or 1
    return r.returncode


def cmd_tools(query: str, full: bool = False, module: str = "") -> int:
    try:
        data = _tool_data()
    except Exception as exc:
        print(f"[work] {exc}", file=sys.stderr)
        return 1

    module = (module or "").strip().lower()
    if module:
        mods_want = {m.strip().lower() for m in module.replace("+", ",").split(",") if m.strip()}
        data = [t for t in data if (t.get("module") or "") in mods_want]
        if not data:
            print(f"[work] 没有属于模块 {module!r} 的工具；可用模块：comms / office / memory / other（支持逗号组合如 comms,office）",
                  file=sys.stderr)
            return 2

    if full:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0

    if not query:
        out = {"ok": True, "count": len(data),
               "hint": "看某个工具的完整参数：work.py tools <工具名或中文关键字>；"
                       "看原始 schema：work.py tools --all；"
                       "只看某个模块：work.py tools --module comms|office|memory|other",
               "tools": [{"name": t.get("name", ""), "module": t.get("module", ""),
                          "summary": _brief(t.get("description", ""))}
                         for t in data]}
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    q = query.lower()
    intent_tag = ""
    hits = []
    for t in data:
        name = t.get("name", "")
        desc = t.get("description", "")
        if q in name.lower() or q in desc.lower():
            hits.append(t)
    if not hits:
        # 中文口语常常只沾一点边，用 2-gram 做宽松召回，给出候选而不是空结果
        grams = {q[i:i + 2] for i in range(max(0, len(q) - 1)) if len(q[i:i + 2].strip()) == 2}
        def affinity(t):
            text = (t.get("name", "") + t.get("description", "")).lower()
            return sum(1 for g in grams if g in text)
        loose = sorted(data, key=lambda t: -affinity(t))[:6]
        # 中文口语（「收材料」）往往不是任何工具名/描述的子串。先借意图表把它
        # 翻成确定工具，再给完整参数；这样「tools 收材料」也能直接查到参数。
        via_intent = None
        if INTENTS_JSON.is_file():
            try:
                rules = json.loads(INTENTS_JSON.read_text(encoding="utf-8-sig"))
                scores = [(sum(w for kw, w in (it.get("keywords") or {}).items()
                               if str(kw).lower() in q), it)
                          for it in rules.get("intents", [])]
                best = max(scores, key=lambda x: x[0], default=(0, {}))
                if best[0] > 0:
                    via_intent = (best[1].get("id"), best[1].get("primary"))
            except (OSError, ValueError):
                pass
        if via_intent and via_intent[1]:
            primary = via_intent[1]
            intent_tag = str(via_intent[0])
            hits = [t for t in data if t.get("name") == primary]
        if not hits:
            print(json.dumps({"ok": True, "matched": False, "query": query,
                              "hint": "没有同名工具。不要编造工具名、不要手写脚本替代；"
                                      "用 work.py ask \"<用户原话>\" 拿确定的工具，"
                                      "或 work.py tools 看全部清单。",
                              "candidates": [{"name": t.get("name", ""),
                                              "summary": _brief(t.get("description", ""))}
                                             for t in loose]},
                             ensure_ascii=False, indent=2))
            return 0

    out = []
    for t in hits:
        props = (t.get("schema") or {}).get("properties") or {}
        params = [{"name": n, "type": m.get("type", ""),
                   "default": m.get("default"), "description": m.get("description", "")}
                  for n, m in props.items()]
        out.append({"name": t.get("name", ""), "module": t.get("module", ""),
                    "description": t.get("description", ""),
                    "params": params})
    result = {"ok": True, "matched": True, "count": len(out), "tools": out}
    if intent_tag:
        result["via_intent"] = intent_tag
        result["hint"] = f'完整流程用 work.py ask "{query}"'
    elif len(out) > 3:
        result["hint"] = '匹配较多，用 work.py ask "<用户原话>" 拿确定的那个工具'
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_modules(as_json: bool = False) -> int:
    """列出模块划分：通讯 / Office / 公共各自多少工具、从哪进、改完怎么验收。

    拆模块之后最常问的两件事就是「这个需求该改哪个文件」和「只测这一块怎么跑」，
    所以清单里直接把改哪个文件、跑哪条回归写进去，不用再去翻文档。
    """
    py = _pick_python()
    if not py:
        print("[work] 没找到装齐依赖的 Python。用 /work 安装，或运行：python scripts\\setup.py",
              file=sys.stderr)
        return 3
    try:
        r = subprocess.run([py, str(RUNNER), "work_modules", "{}"], cwd=str(ROOT),
                           env=_env(), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=180)
    except subprocess.TimeoutExpired:
        print("[work] 读取模块清单超过 180 秒，已中止", file=sys.stderr)
        return 124
    if r.returncode != 0 or not (r.stdout or "").strip():
        sys.stderr.write(r.stderr or "[work] 模块清单为空\n")
        return r.returncode or 1
    if as_json:
        sys.stdout.write(r.stdout)
        return 0
    try:
        data = json.loads(r.stdout)
    except ValueError:
        sys.stdout.write(r.stdout)
        return 0

    rows = data.get("modules") or []
    print()
    print(f"  工具箱模块划分 · 共 {data.get('total_tools', 0)} 个工具")
    print("  " + "-" * 72)
    for m in rows:
        print(f"  {m['id']:<7} {m['name']:<8} v{m.get('version', '?'):<7} "
              f"{m.get('tools', 0):>2} 个工具")
        print(f"          范围：{m.get('scope', '')}")
        print(f"          入口：{m.get('entry', '')}")
        print(f"          验收：python tools/regress.py --module {m['id']}"
              f"   （文档：{m.get('docs', '')}）")
        if as_json is False and m.get("names"):
            print(f"          工具：{', '.join(m['names'])}")
    print("  " + "-" * 72)
    print("  改哪个模块只动对应文件；通讯 / Office / 记忆互不 import，可以分别升级；其他是公共底座。")
    print("  只看某模块的工具：work.py tools --module comms\n")
    return 0


# 这些 (工具, action) 组合里，参数虽然 schema 有默认值，不给就办不成事
_ACTION_REQUIRED = {
    ("work_office_com", "replace"): ["find"],
    ("work_office_com", "set_cells"): ["cells"],
    # 不给 code 就等于空跑；提前标成 needs_input，让它先把脚本写出来
    ("work_office_com", "run_python"): ["code"],
    ("work_office_advanced", "call"): ["engine", "tool"],
}

# matched:false 时告诉智能体「该用哪个原生能力」，而不是只丢一句「找不到工具」。
# 「容易让 WorkBuddy 丧失原有能力」的根因就在这：路由失败后没有出口，
# 智能体要么硬凑一个 work_* 工具，要么干脆不做。
_NATIVE_ROUTES = (
    # 「通知」同时是文档体裁和通讯内容。意图表没兜住时，靠语境把它俩分开：
    # 「写一份通知」是写文档，「通讯软件里有什么通知」是读消息，后者绝不能
    # 被推给 Word 能力（这正是「通讯软件中有什么值得注意的通知」曾经误判的根因）。
    (("通讯软件", "通讯里", "聊天记录里", "消息里", "群里有什么", "有什么通知",
      "值得注意", "最近有什么消息", "有没有新消息"),
     "工具箱通讯聚合 work_comms_notices（只读，一次跨微信 + 企业微信；"
     "先用 work.py ask 确认路由，不要写文档、不要逐群翻）"),
    (("ppt", "幻灯片", "演示文稿", "宣讲", "路演"),
     "WorkBuddy 原生 PPT 能力（workbuddy-pptx）"),
    (("pdf",),
     "WorkBuddy 原生 PDF 能力（workbuddy-pdf）"),
    (("excel", "xlsx", "csv", "透视", "公式", "图表", "单元格", "可视化"),
     "WorkBuddy 原生表格能力（workbuddy-sheetagent）"),
    (("word", "docx", "报告", "总结", "通知", "方案", "讲话", "汇报", "函", "纪要", "邮件", "润色"),
     "WorkBuddy 原生文档能力（workbuddy-docx）"),
    (("翻译", "解释", "是什么", "区别", "为什么", "讲讲", "介绍"),
     "直接作答，不必调用任何工具"),
    (("天气", "新闻", "股价", "汇率", "查一下", "搜一下", "最新", "查查"),
     "WorkBuddy 联网检索能力（WebSearch / WebFetch）"),
    (("代码", "报错", "python", "脚本", "函数", "重构", "异步", "bug"),
     "WorkBuddy 原生编码能力（直接读写代码）"),
    (("图片", "裁图", "修图", "抠图", "压缩图", "截图"),
     "WorkBuddy 原生图片处理能力"),
    (("网页", "网站", "小程序", "页面", "前端"),
     "WorkBuddy 原生开发能力（sites / mini-program）"),
)

# 兜底：落到这里说明意图表没匹配上，但也不能让调用方看到空白。
# 只说「用原生能力」等于没说，还得按内容类型点名，才接得上 WorkBuddy 的四个原生 Skill。
_NATIVE_FALLBACK = ("意图表没匹配到批量流水：文档走 workbuddy-docx、表格走 workbuddy-sheetagent、"
                    "演示走 workbuddy-pptx、PDF 走 workbuddy-pdf，都不沾就直接作答；"
                    "不要提案 work_* 工具")

# 查 schema 时按原话里的文件类型补 format，省掉「空参只拿到顶层 help」那一步。
_FORMAT_WORDS = (
    (("xlsx", "excel", "表格", "工作表", "单元格", "sheet"), "xlsx"),
    (("pptx", "ppt", "幻灯片", "演示文稿", "slide"), "pptx"),
    (("docx", "word", "文档", "段落", "正文"), "docx"),
)


def _native_suggestion(q: str) -> str:
    """猜这件事该由哪个原生能力接手。

    以前猜不出就返回空串，调用方拿到 native_suggestion:"" 等于没说下一步干什么；
    现在至少给一句通用兜底，保证 out_of_scope 的返回永远有可执行的去向。
    """
    low = q.lower()
    routes = _NATIVE_ROUTES
    # 「写一份值得注意的通知」是写文档，不该被通讯组截走；写动词出现时跳过第一组
    if any(w in low for w in ("写一份", "写一篇", "写个", "起草", "拟一份",
                              "帮我写", "生成一份", "撰写")):
        routes = _NATIVE_ROUTES[1:]
    for words, route in routes:
        if any(w in low for w in words):
            return route
    return _NATIVE_FALLBACK


def _infer_format(q: str) -> str:
    low = q.lower()
    for words, fmt in _FORMAT_WORDS:
        if any(w in low for w in words):
            return fmt
    return ""


def _extract_replace(q: str) -> tuple[str, "str | None"]:
    """从原话里抽 (find, replace)。

    早先只把 find 标成必填、不抽值，用户拿到 ``<必填:要查找的原文>`` 照抄执行，
    replace 留空 = **把「甲方」全删了**。这是会静默毁内容的坑，必须从原话抽。

    优先级：成对的引号 > 「把 X 替换成 Y」句式 > 单个引号（当删除处理）。
    第二个返回值用 ``None`` 表示「没抽到、别猜」，空串才是「明确要删」。
    """
    quoted = re.findall(r'[「“"]([^」”"]{1,40})[」”"]', q)
    if len(quoted) >= 2:
        return quoted[0].strip(), quoted[1].strip()
    m = re.search(
        r'(?:把|将)\s*([^\s，。；、]{1,40}?)\s*(?:全部|全都|都|统统)?\s*'
        r'(?:替换成|替换为|换成|改为|改成)\s*([^\s，。；、]{0,40})', q)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    if len(quoted) == 1:
        return quoted[0].strip(), ""
    return "", None


def _step_args(step: str) -> tuple[str, dict]:
    """``work_exp:list`` / ``work_wechat_remind:dry_run`` 这类带动作提示的 step。"""
    if ":" not in step:
        return step, {}
    tool, tag = step.split(":", 1)
    presets = {
        "list": {"action": "list"},
        "get": {"action": "get"},
        "template": {"action": "template"},
        "save": {"action": "save"},
        "distill": {"action": "distill"},
        "search": {"action": "search"},
        "replay": {"action": "replay"},
        "to_skill": {"action": "to_skill"},
        "trace": {"action": "trace"},
        "context": {"action": "context"},
        "stats": {"action": "stats"},
        "doctor": {"action": "doctor"},
        "export_obsidian": {"action": "export_obsidian"},
        "export": {"action": "export"},
        "dry_run": {"dry_run": True},
        "validate": {"command": "validate"},
        "compact": {"compact": True},
        # work_office_com 的动作：让 ask 直接给出可执行命令，而不是只跑默认的 apps
        "apps": {"action": "apps"},
        "info": {"action": "info"},
        "export_pdf": {"action": "export_pdf"},
        "recalc": {"action": "recalc"},
        "refresh_fields": {"action": "refresh_fields"},
        "read_cells": {"action": "read_cells"},
        "set_cells": {"action": "set_cells"},
        "replace": {"action": "replace"},
        "active": {"action": "active"},
        "run_python": {"action": "run_python"},
        "schema": {"mode": "schema"},
        "create": {"create": True},
        "excel_list": {"engine": "excel", "action": "list"},
        "word_list": {"engine": "word", "action": "list"},
        # work_wechat_files 的模式
        "scan": {"mode": "scan"},
        "pull": {"mode": "pull"},
        "harvest": {"mode": "harvest"},
        # work_wechat_contacts 的模式
        "contacts": {"mode": "contacts"},
        "group": {"mode": "group"},
        "announcements": {"mode": "announcements"},
        "common_groups": {"mode": "common_groups"},
    }
    return tool, dict(presets.get(tag, {}))


def _intent_module(primary: str) -> str:
    t = (primary or "").strip()
    if t in ("work_status", "work_modules"):
        return "other"
    if t in ("work_ledger", "work_exp", "work_mem", "work_backup_file"):
        return "memory"
    if t.startswith(("work_wechat_", "work_wecom_", "work_comms_")):
        return "comms"
    if t.startswith("work_office") or t in ("work_excel_mcp", "work_word_mcp"):
        return "office"
    return "other"


def cmd_ask(query: str, module: str = "", run_exec: bool = False) -> int:
    """把用户原话翻成确定的执行计划（支持限定单模块、多模块组合或直接执行）。"""
    if not INTENTS_JSON.is_file():
        print(json.dumps({"ok": False, "error": "缺 web/intents.json 意图表",
                          "hint": "运行 python scripts/work.py setup 重新准备"},
                         ensure_ascii=False, indent=2))
        return 1
    rules = json.loads(INTENTS_JSON.read_text(encoding="utf-8-sig"))
    q = (query or "").strip()
    ql = q.lower()

    # 「企业微信」和「微信」是两条独立通道，可关键词会撞（都说「收材料」）。
    # 用法：企业微信的意图写 require，微信的意图写 not_with，只靠关键词永远分不清。
    # 用户同时提到两者（「微信和企微都看看」）时，not_with 不生效，两条都留着。
    _WECOM_MARKS = ("企业微信", "企微", "wxwork", "wecom")
    _has_wecom = any(m in ql for m in _WECOM_MARKS)
    _q_wo_wecom = ql
    for _m in _WECOM_MARKS:
        _q_wo_wecom = _q_wo_wecom.replace(_m, "")
    _also_wechat = "微信" in _q_wo_wecom

    allowed_mods = {m.strip().lower() for m in module.replace("+", ",").split(",") if m.strip() and m.strip().lower() != "all"} if module else set()

    def _eligible(it) -> bool:
        if allowed_mods:
            imod = _intent_module(str(it.get("primary") or ""))
            if imod not in allowed_mods:
                return False
        req = [str(x).lower() for x in (it.get("require") or [])]
        if req and not any(x in ql for x in req):
            return False
        nw = [str(x).lower() for x in (it.get("not_with") or [])]
        if nw and _has_wecom and not _also_wechat and any(x in ql for x in nw):
            return False
        return True

    # 将文件名主体剥离（仅保留后缀如 .docx/.xlsx），防止文件名里的业务词（如「专项经费摸底表.docx」「待发催办通知.md」「汇总母表.xlsx」）误触发收件/催办/汇总意图
    def _strip_fname_keep_verb(m: re.Match) -> str:
        full = m.group(0)
        ext = m.group(1)
        mp = re.match(r'^(读取|查看|看看|打开|对比|比较|和|与|把|将|提取|给|从|在|对|导出|转成|转为|备份)+', full)
        kept = mp.group(0) if mp else ""
        return f" {kept} {ext} "

    q_no_files = re.sub(
        r'[\w\u4e00-\u9fa5\-./\\（）()]+(\.(?:docx?|xlsx?|pptx?|pdf|md|csv|tsv|txt))',
        _strip_fname_keep_verb,
        ql,
        flags=re.I,
    ).strip()

    def strict_score(it):
        """关键词整体是原话的子串：置信度高，允许多意图合并。

        嵌套关键词只算一次：「汇总表」命中时「汇总」必然也在原话里，
        那是同一条信息被计了两遍，会让「新建一份汇总表」这种明确的新建诉求
        被汇总意图（17 分）压过新建意图（10 分）。取最长的那条计分。
        """
        if not _eligible(it):
            return 0
        target_q = q_no_files if q_no_files else ql
        hits = [(str(kw).lower(), w) for kw, w in (it.get("keywords") or {}).items()
                if str(kw).lower() in target_q]
        hits.sort(key=lambda x: (-len(x[0]), -x[1]))
        kept: list = []
        for kw, w in hits:
            if any(kw != k2 and kw in k2 for k2, _ in kept):
                continue
            kept.append((kw, w))
        return sum(w for _, w in kept)

    def fuzzy_score(it):
        """口语插字（「列一下经验文档」vs「列经验」）时按字符覆盖率兜底。
        置信度低，只取最可能的一个意图，避免误触发一串工具。

        只对 3 字以上的关键词做模糊：2 字词（公文、群消息）跨词碰撞率太高——
        「替换成乙公司」里同时有「公」和「文」，会把「公文」判成 100% 命中。
        """
        if not _eligible(it):
            return 0
        s = 0.0
        for kw, w in (it.get("keywords") or {}).items():
            k = str(kw).lower()
            # 模糊匹配只认「3 个以上汉字且全部出现」的关键词。
            # 早先按字符覆盖率算，把空格和 pdf/csv 这类拉丁字母也算进去，
            # 「把这份 PDF 总结一下」会命中「转 PDF」（覆盖率 4/5=0.8）。
            cjk = {c for c in k if "\u4e00" <= c <= "\u9fa5"}
            if len(cjk) < 3:
                continue
            if all(c in ql for c in cjk):
                s += w * 0.6
        return s

    items = rules.get("intents", [])
    scored = [(strict_score(it), it) for it in items]
    match_mode = "strict"
    if max((s for s, _ in scored), default=0) <= 0:
        scored = [(fuzzy_score(it), it) for it in items]
        match_mode = "fuzzy"
        if max((s for s, _ in scored), default=0) <= 0:
            match_mode = "none"
    scored.sort(key=lambda x: (-x[0], x[1].get("id", "")))
    top = scored[0][0] if scored else 0
    fuzzy_blocked = None
    if match_mode == "strict":
        picked = [it for s, it in scored[:3] if s > 0 and s >= top * 0.15]
    else:
        picked = [it for s, it in scored[:1] if s > 0]
        # 模糊命中可以接受「查一下」，但不能接受「写一下」。
        # 「把材料整理成一份清单」曾因「整理成材料」被模糊判成微信群摘录，
        # 真跑起来会去翻微信库并写出一份文件。写操作一律要求严格命中。
        if picked and any(it.get("writes") for it in picked):
            fuzzy_blocked = picked[0]
            picked = []
    # 有先后依赖的多意图（先收材料再汇总）按 stage 排，stage 小的先跑；
    # 同 stage 保持得分序。之前「收一下并汇总」会先跑汇总，读到的是空目录。
    _score_of = {id(it): s for s, it in scored}
    picked.sort(key=lambda it: (it.get("stage", 5), -_score_of.get(id(it), 0)))

    # 「xx 有什么属性 / schema / 可选值」是查手册，不是干活。早先它会连带命中
    # office_cells 这类动作意图，plan 里混进一条「读单元格」的命令，白跑一次还答非所问。
    SCHEMA_MARKS = ("schema", "属性", "元素", "可选值", "help", "怎么填", "有哪些值")
    if any(m in ql for m in SCHEMA_MARKS) and any(it.get("id") == "office_schema" for it in picked):
        picked = [it for it in picked if it.get("id") == "office_schema"]

    # 防跨模块泛词污染：命中微信/企微/通讯意图且未提及具体文档/表格后缀或结构词时，屏蔽 office_inspect（如「读一下」误触发）
    OFFICE_DOC_MARKS = (".docx", ".xlsx", ".pptx", ".pdf", ".doc", ".xls",
                        "文档", "表格", "单元格", "工作簿", "工作表", "幻灯片", "大纲")
    if (any(str(it.get("id", "")).startswith(("wechat_", "wecom_", "comms_")) for it in picked)
            and not any(m in ql for m in OFFICE_DOC_MARKS)):
        picked = [it for it in picked if it.get("id") != "office_inspect"]

    # 群接龙 / 台账查询时若未显式要求「收材料/收报表/收文件」，不顺带触发 wechat_collect（写操作）
    if any(it.get("id") in ("wechat_solitaire", "ledger") for it in picked):
        if not any(m in ql for m in ("收材料", "收报表", "收文件", "收一下", "去收")):
            picked = [it for it in picked if it.get("id") != "wechat_collect"]

    # 专门问群成员/群公告/群接龙/收藏夹/群画像时，若没有要求同时读原始聊天记录，去掉伴随的泛 wechat_messages/wechat_search
    SPECIAL_WX = {"wechat_contacts", "wechat_group_info", "wechat_solitaire", "wechat_favorites", "wechat_insights"}
    if any(it.get("id") in SPECIAL_WX for it in picked) and len(picked) > 1:
        if not any(m in ql for m in ("并读", "再看看消息", "同时搜")):
            picked = [it for it in picked if it.get("id") not in ("wechat_messages", "wechat_search", "wechat_sessions")]

    # 明确搜消息（wechat_search）时，去掉因「聊天记录」泛词误入的 wechat_messages
    if any(it.get("id") == "wechat_search" for it in picked) and any(it.get("id") == "wechat_messages" for it in picked):
        if any(m in ql for m in ("搜", "找", "查一下", "谁提过", "发过的", "说过")):
            picked = [it for it in picked if it.get("id") != "wechat_messages"]

    # 文件名本身含「国标/排版」（如 draft_国标排版.docx）但动作是「转 PDF / 备份 / 检视」时，不误触发 office_format
    if any(it.get("id") == "office_format" for it in picked) and len(picked) > 1:
        if not any(m in q_no_files for m in ("公文", "红头", "国标", "排版", "9704", "小标宋", "仿宋")):
            picked = [it for it in picked if it.get("id") != "office_format"]

    # 多份 Word 文档合并（office_join_docx）时，不误触发 Excel 跨表汇总（office_consolidate）
    if any(it.get("id") == "office_join_docx" for it in picked):
        if not any(m in q_no_files for m in ("跨表", "分表", "母表", "核表", "漏填", "勾稽", "excel", "xlsx")):
            picked = [it for it in picked if it.get("id") != "office_consolidate"]

    # 范围读取/检视（office_inspect）命中时，若未要求改单元格，去掉因 "a1" 顺带触发的 COM office_cells
    if any(it.get("id") == "office_inspect" for it in picked) and any(it.get("id") == "office_cells" for it in picked):
        if not any(m in ql for m in ("改单元格", "填几个数", "写单元格", "set_cells")):
            picked = [it for it in picked if it.get("id") != "office_cells"]

    # 对 Office 文档做渲染截图时，若未提微信/聊天，屏蔽因「截图」误触发的 wechat_media
    if any(it.get("id") == "office_inspect" for it in picked) and any(it.get("id") == "wechat_media" for it in picked):
        if any(ext in ql for ext in (".docx", ".xlsx", ".pptx", ".pdf")) and not any(w in q_no_files for w in ("微信", "聊天", "群")):
            picked = [it for it in picked if it.get("id") != "wechat_media"]

    # 命中明确的专项 Office 意图（透视/图表/差异对比/富文档合成/多文档合并）时，去掉泛词顺带触发的 office_inspect / office_cells
    SPECIAL_OFFICE = {"office_excel_bridge", "office_word_bridge", "office_diff", "office_compose", "office_join_docx"}
    if any(it.get("id") in SPECIAL_OFFICE for it in picked) and len(picked) > 1:
        picked = [it for it in picked if it.get("id") not in ("office_inspect", "office_cells")]

    if not picked:
        try:
            names = [{"name": t.get("name", ""), "summary": _brief(t.get("description", ""))}
                     for t in _tool_data()]
        except Exception:
            names = []
        # 走到这里说明连模糊匹配都没命中：原话里没有任何本工具箱的词汇。
        # 早先一律让「用 tools 找最接近的工具」，与 SKILL 第零节「通用任务换原生能力」
        # 直接对撞，智能体就会拿「写总结 / 读 PDF」的活去翻工具箱。这里改为分流：
        # 先给出原生能力落点，再保留「万一真是本行、只是说法不同」的后路。
        native = _native_suggestion(q)
        hint = ("这不是工具箱的主线（通讯最近动态 / 收件催办 / 多科室跨表汇总 / 台账与经验；"
                "国标公文只是按需附加能力）。"
                "除了 native_suggestion 明确指向本工具箱的只读工具，其余一律用 WorkBuddy "
                "原生能力做，不要提案 work_*，也不要在回复里提这个 Skill")
        hint += f"；这件事用：{native}。" if native else "。"
        if fuzzy_blocked:
            # 命中了一个会写文件的流程，但只是模糊命中：说清为什么不动手
            native2 = _native_suggestion(fuzzy_blocked.get("name", "") + q)
            hint = (f"口语有点像「{fuzzy_blocked.get('name', '')}」，但没严格命中，"
                    f"而它是会写文件的流程，不敢替你决定。"
                    f"如果确实要做这件事，请说得更具体（例如明确提到「微信群 / 收件 / 汇总」），"
                    f"或者直接用原生能力：{native2}。")
        hint += ("只有你确认它确实属于上述主线、只是说法不同，才用 "
                 "work.py tools <中文关键字> 找最接近的工具再 call。")
        print(json.dumps({
            "ok": True, "matched": False, "query": q, "match_mode": match_mode,
            "scope": "out_of_scope",
            "fuzzy_blocked": (fuzzy_blocked.get("id") if fuzzy_blocked else None),
            "plan": [], "writes": False, "confirm_required": False,
            "native_suggestion": native,
            "hint": hint,
            "tools": names,
        }, ensure_ascii=False, indent=2))
        return 0

    try:
        schemas = {t.get("name", ""): t.get("schema") or {} for t in _tool_data()}
    except Exception:
        schemas = {}

    def make_item(tool: str, args: dict) -> dict:
        """生成 plan 项，并标出必填但还没给的参数。

        之前 plan 里一律 ``{}``，智能体照抄就必然触发一次 ``ok:false``
        （keyword / file 这类必填项缺失），表现为「工具调用失败」。这里提前
        把缺口结构化暴露出来，让它先补齐再执行，而不是先失败再修。
        """
        schema = schemas.get(tool, {})
        props = schema.get("properties") or {}
        required = [k for k in (schema.get("required") or []) if k not in args]
        # 有些参数 schema 上给了默认值，但某个 action 下不给就必然失败
        # （replace 不给 find、set_cells 不给 cells）。这里补进来，别让它先失败一次。
        for extra in _ACTION_REQUIRED.get((tool, str(args.get("action", ""))), []):
            if not str(args.get(extra, "")).strip() and extra not in required:
                required.append(extra)
        payload_args = dict(args)
        auto = []

        # 1. 自动提取会话/群名 chat 或 chat_group（必填与可选均支持）
        is_common_group_q = any(w in q for w in ("共同群", "共同的群", "一起在的群", "都在哪些群"))
        if not is_common_group_q and ("chat" in required or ("chat" in props and not payload_args.get("chat"))
                or ("chat_group" in props and not payload_args.get("chat_group"))):
            mc = re.search(r'[【「“"\'《]([^】」”"\'》]{1,32})[】」”"\'》]', q)
            is_explicit_chat = bool(mc and "【" in mc.group(0))
            if mc and re.search(r'\.(?:docx?|xlsx?|pptx?|pdf|md|csv|txt)$', mc.group(1), re.I):
                mc = None
                is_explicit_chat = False
            if not mc:
                mc = re.search(
                    r'(?:在|去|把|读一下|看看|查看|查一下|翻一下|总结|整理|分析|统计)?\s*'
                    r'([\u4e00-\u9fa5A-Za-z0-9_\-（()）]{2,24}?)'
                    r'\s*(?:群|的聊天记录|最近的聊天记录|最近消息|里的消息|的群成员|的群公告|的接龙)', q)
                if mc:
                    is_explicit_chat = True
            if mc:
                cand_chat = mc.group(1).strip()
                for prefix in ("帮我", "请", "在", "去", "把", "微信", "企业微信", "企微",
                               "一下", "看下", "看看", "读一下", "查一下", "分析一下", "统计一下", "所有", "全部"):
                    if cand_chat.startswith(prefix):
                        cand_chat = cand_chat[len(prefix):].strip()
                bad_chats = ("微信", "企业微信", "企微", "某个", "这个", "那个", "最近", "所有", "全部",
                             "所有微信", "全部微信", "微信聊天记录", "聊天记录")
                if (cand_chat and cand_chat not in bad_chats
                        and not any(x in cand_chat for x in ("共同", "和", "与", "搜", "找"))):
                    if "chat" in required:
                        payload_args["chat"] = cand_chat
                        required.remove("chat")
                        auto.append("chat")
                    elif "chat" in props and is_explicit_chat and not payload_args.get("chat"):
                        payload_args["chat"] = cand_chat
                        auto.append("chat")
                    elif "chat_group" in props and is_explicit_chat and not payload_args.get("chat_group"):
                        payload_args["chat_group"] = cand_chat
                        auto.append("chat_group")

        # 2. 自动提取时间窗 since（支持相对时间与月份/日期）
        if "since" in props and not str(payload_args.get("since", "")).strip():
            mt = re.search(
                r'(今天|今日|昨天|昨日|前天|本周|这周|上周|本月|这个月|上月|上个月|'
                r'(?:近|最近|过去)\s*\d+\s*(?:天|日|小时|周|个月)|'
                r'\d{4}-\d{1,2}-\d{1,2}|\d{1,2}\s*月\s*\d{1,2}\s*日|\d{1,2}\s*月)', q)
            if mt:
                raw_t = re.sub(r'\s+', '', mt.group(1))
                mm = re.fullmatch(r'(\d{1,2})月', raw_t)
                if mm and 1 <= int(mm.group(1)) <= 12:
                    payload_args["since"] = f"{datetime.now().year}-{int(mm.group(1)):02d}-01"
                else:
                    payload_args["since"] = raw_t
                auto.append("since")

        # 3. 微信多维搜索：自动识别发言人 sender 与消息类型 msg_type
        if tool == "work_wechat_search":
            ms = re.search(
                r'(?:看看|查一下|搜一下|找一下|搜索|搜|把)?\s*([\u4e00-\u9fa5A-Za-z0-9_\-]{2,12}?)'
                r'\s*(?:在[^\s，。]{1,20}?|最近)*(?:发的|发过的|说的|说过的|提过的|交代的)', q)
            if ms:
                cand_s = ms.group(1).strip()
                for pfx in ("微信里", "群里", "聊天里", "微信", "帮我", "搜一下", "查一下", "看看"):
                    if cand_s.startswith(pfx) and len(cand_s) > len(pfx):
                        cand_s = cand_s[len(pfx):].strip()
                if cand_s and cand_s not in ("谁", "大家", "有人", "别人", "所有人", "某个群", "聊天记录", "微信"):
                    payload_args["sender"] = cand_s
                    auto.append("sender")
            for mtype_key, mtype_words in (
                ("link", ("链接", "网址", "网盘", "网页", "云文档", "飞书")),
                ("file", ("文件", "附件", "pdf", "文档")),
                ("quote", ("引用回复", "引用", "回复我")),
                ("announcement", ("群公告",)),
                ("solitaire", ("接龙",)),
            ):
                if any(w in ql for w in mtype_words):
                    payload_args["msg_type"] = mtype_key
                    auto.append("msg_type")
                    break
            if payload_args.get("msg_type") == "link" and not payload_args.get("keyword"):
                mlk = re.search(r'(?:分享的|关于|有关|发的|发过的)\s*([\u4e00-\u9fa5A-Za-z0-9]{2,10}?)\s*(?:链接|网址|网盘|云文档)', q)
                if mlk and mlk.group(1) not in ("聊天记录里", "群里", "微信里", "所有", "全部", "分享"):
                    payload_args["keyword"] = mlk.group(1)
                    auto.append("keyword")
            if (payload_args.get("sender") or payload_args.get("msg_type")) and "keyword" in required:
                required.remove("keyword")

        # 4. 微信通讯录/群详情/群公告/共同群模式自动细化
        if tool == "work_wechat_contacts":
            if "群公告" in q and not payload_args.get("chat"):
                payload_args["mode"] = "announcements"
                payload_args.pop("chat", None)
                auto.append("mode")
            elif is_common_group_q:
                payload_args["mode"] = "common_groups"
                payload_args.pop("chat", None)
                auto.append("mode")
                q_clean = re.sub(r'^(?:帮我|请)?(?:查一下|看看|找一下|查看|查询)?(?:微信里)?', '', q).strip()
                q_clean = re.sub(r'(?:的)?(?:共同群|共同的群|一起在的群|都在哪些群).*$', '', q_clean).strip()
                mp = re.search(r'([\u4e00-\u9fa5A-Za-z0-9_\-]+)\s*(?:和|与|跟|,|，)\s*([\u4e00-\u9fa5A-Za-z0-9_\-]+)', q_clean)
                if mp:
                    payload_args["keyword"] = f"{mp.group(1)},{mp.group(2)}"
                    auto.append("keyword")
            elif payload_args.get("chat") or any(w in q for w in ("群成员", "群主", "群里有哪些人", "群名单")):
                payload_args["mode"] = "group"
                auto.append("mode")
            else:
                payload_args["mode"] = "contacts"
                mc_kw = re.search(r'(?:微信联系人|联系人|通讯录里?的?)\s*([\u4e00-\u9fa5A-Za-z0-9_\-]{2,12})', q)
                if mc_kw and mc_kw.group(1) not in ("微信", "联系人", "通讯录"):
                    payload_args["keyword"] = mc_kw.group(1)
                    auto.append("keyword")

        # 5. 微信收藏夹自动细分 fav_type 与 keyword
        if tool == "work_wechat_favorites":
            for ft, fwords in (("link", ("链接", "网盘", "网页", "网址")),
                               ("note", ("笔记", "密码", "账号", "备忘")),
                               ("file", ("文件", "压缩包")),
                               ("record", ("聊天记录",))):
                if any(w in ql for w in fwords):
                    payload_args["fav_type"] = ft
                    auto.append("fav_type")
                    break
            mf_kw = re.search(r'(?:收藏夹里?的?|收藏里?的?|我收藏的)\s*(?:关于|有关)?\s*([\u4e00-\u9fa5A-Za-z0-9_\-]{2,12})', q)
            if mf_kw:
                cand_fkw = mf_kw.group(1).strip()
                for generic_sfx in ("链接", "笔记", "文件", "聊天记录"):
                    if cand_fkw.endswith(generic_sfx) and len(cand_fkw) > len(generic_sfx):
                        cand_fkw = cand_fkw[:-len(generic_sfx)].strip()
                if cand_fkw and cand_fkw not in ("链接", "笔记", "文件", "聊天记录", "内容", "东西"):
                    payload_args["keyword"] = cand_fkw
                    auto.append("keyword")

        # 6. 企业微信通讯录自动抽取人名
        if tool == "work_wecom_contacts" and not payload_args.get("keyword"):
            mwc = re.search(r'(?:企业微信|企微)?(?:通讯录|联系人|同事)(?:里|中)?(?:的)?\s*([\u4e00-\u9fa5A-Za-z0-9]{2,10})', q)
            if mwc and mwc.group(1) not in ("企业微信", "企微", "通讯录", "联系人", "全部", "所有"):
                payload_args["keyword"] = mwc.group(1)
                auto.append("keyword")

        # 7. 台账 work_ledger 自动细分 action 与 dept
        if tool == "work_ledger":
            if any(w in q for w in ("谁没交", "未交", "没交的", "差哪几家")):
                payload_args["action"] = "missing"
                auto.append("action")
            elif any(w in q for w in ("各科室", "按科室", "交了几版", "交了几次", "重复提交")):
                payload_args["action"] = "departments"
                auto.append("action")
            elif any(w in q for w in ("批次", "收了几次")):
                payload_args["action"] = "batches"
                auto.append("action")
            else:
                md = re.search(r'(?:查一下|看看|查看|找一下|查询)?\s*([\u4e00-\u9fa5]{1,6}(?:科|处|室|中心|部|办))', q)
                if md and any(w in q for w in ("历史", "记录", "交了什么", "明细")):
                    payload_args["action"] = "history"
                    payload_args["dept"] = md.group(1)
                    auto.extend(["action", "dept"])

        # 7b. 经验库 work_exp 与通用记忆 work_mem 自动参数提取
        if tool == "work_exp":
            if payload_args.get("action") in ("distill", "save") or any(w in ql for w in ("整理为经验", "整理成经验", "沉淀为经验", "总结为经验", "写成经验", "生成经验", "保存经验", "sop")):
                payload_args["action"] = "distill"
                if "action" not in auto:
                    auto.append("action")
                mt_title = re.search(r'[【「“"《]([^】」”"》]{2,32})[】」”"》]', q)
                if mt_title and not payload_args.get("title"):
                    payload_args["title"] = mt_title.group(1).strip()
                    auto.append("title")
            elif payload_args.get("action") == "search" or any(w in ql for w in ("搜经验", "查经验", "查找经验", "检索经验", "相关经验")):
                payload_args["action"] = "search"
                if "office" in ql or any(w in ql for w in ("表格", "文档", "汇总", "排版", "excel", "word", "ppt")):
                    payload_args["module"] = "office"
                    auto.append("module")
                elif any(w in ql for w in ("微信", "企微", "群", "收件", "接龙")):
                    payload_args["module"] = "comms"
                    auto.append("module")
            elif any(w in ql for w in ("导出为skill", "转成skill", "生成skill")):
                payload_args["action"] = "to_skill"
                auto.append("action")
        if tool == "work_mem":
            if any(w in ql for w in ("obsidian", "知识图谱")):
                payload_args["action"] = "export_obsidian"
                auto.append("action")
            elif any(w in ql for w in ("记住", "存入记忆", "保存记忆")):
                payload_args["action"] = "save"
                auto.append("action")
            elif any(w in ql for w in ("搜", "查", "检索")):
                payload_args["action"] = "search"
                mq = re.search(r'(?:关于|有关|搜索|检索|查一下记忆)\s*([一-龥A-Za-z0-9_\-]{2,16})', q)
                payload_args["query"] = mq.group(1) if mq else q[:16]
                auto.extend(["action", "query"])

        # 8. 自动提取关键字 keyword（通用）
        if "keyword" in required:
            m = re.search(r'["“”「」\']([^"“”「」\']{1,24})["“”「」\']', q)
            if not m:
                m = re.search(
                    r'(?:(?:搜一下|搜索|搜|找一下|查一下)\s*)?(?:提到|关于|包含|关键词是|关键词|提过)\s*'
                    r'([\u4e00-\u9fa5A-Za-z0-9]{1,14}?)'
                    r'(?:的|这句|那句|消息|内容|记录|话|吗|没|，|。|$)', q)
            if not m:
                m = re.search(
                    r'(?:搜一下|搜索|搜|找一下)\s*'
                    r'([\u4e00-\u9fa5A-Za-z0-9]{1,14}?)'
                    r'(?:的|这句|那句|消息|内容|记录|话|吗|没|，|。|$)', q)
            if m and m.group(1) not in ("微信", "企业微信", "企微", "群里", "聊天记录"):
                payload_args["keyword"] = m.group(1)
                required.remove("keyword")
                auto.append("keyword")

        def _resolve_rel_file(cand_f: str) -> str:
            cand_f = cand_f.strip().lstrip("的").strip()
            for search_dir in (
                ROOT,
                ROOT / "workspace",
                ROOT / "workspace" / "01_微信收回原件",
                ROOT / "workspace" / "03_汇总与成稿输出",
                ROOT / "workspace" / "04_单位历年材料库",
                ROOT / "workspace" / "00_模拟微信收件缓存",
                ROOT / "workspace" / "06_运行状态",
            ):
                p_cand = search_dir / cand_f
                if p_cand.is_file():
                    return str(p_cand.relative_to(ROOT)).replace("\\", "/")
                p_name = search_dir / Path(cand_f).name
                if p_name.is_file():
                    return str(p_name.relative_to(ROOT)).replace("\\", "/")
            return cand_f

        # 9. 自动提取文件路径 file / content_or_path（工作区自动匹配）
        all_files = re.findall(r'([A-Za-z0-9_\u4e00-\u9fa5\-./\\（）()]+?\.(?:docx?|xlsx?|pptx?|pdf|md|csv|tsv|txt))', q, re.I)
        cleaned_files = []
        for raw_f in all_files:
            # 去掉黏连在文件名前面的常见中文动词/介词，如「读取2026...」「对比A.xlsx和B.xlsx」
            cf = re.sub(r'^(?:读取|查看|看看|打开|对比|比较|和|与|把|将|文件|表格|文档|从|在|对)+', '', raw_f.strip())
            if cf:
                cleaned_files.append(cf)

        for fparam in ("file", "content_or_path"):
            if fparam in props and not str(payload_args.get(fparam, "")).strip():
                if cleaned_files:
                    payload_args[fparam] = _resolve_rel_file(cleaned_files[0])
                    if fparam in required:
                        required.remove(fparam)
                    auto.append(fparam)

        # 9b. work_office_diff 自动提取两份对比文件 file_a, file_b
        if tool == "work_office_diff" and len(cleaned_files) >= 2:
            if not str(payload_args.get("file_a", "")).strip():
                payload_args["file_a"] = _resolve_rel_file(cleaned_files[0])
                if "file_a" in required:
                    required.remove("file_a")
                auto.append("file_a")
            if not str(payload_args.get("file_b", "")).strip():
                payload_args["file_b"] = _resolve_rel_file(cleaned_files[1])
                if "file_b" in required:
                    required.remove("file_b")
                auto.append("file_b")

        # 9c. work_office_inspect 自动提取 cell_range 与 mode
        if tool == "work_office_inspect":
            mrng = re.search(r'((?:[\w\u4e00-\u9fa5]+!)?[A-Za-z]{1,3}\d{1,5}:[A-Za-z]{1,3}\d{1,5})', q)
            if mrng and not str(payload_args.get("cell_range", "")).strip():
                payload_args["cell_range"] = mrng.group(1)
                auto.append("cell_range")
            if payload_args.get("mode", "text") == "text":
                if any(w in ql for w in ("schema", "属性名", "属性表", "元素怎么写", "查元素")):
                    payload_args["mode"] = "schema"
                    fmt = _infer_format(q)
                    if fmt:
                        payload_args["format"] = fmt
                        auto.append("format")
                    auto.append("mode")
                elif any(w in ql for w in ("截图", "缩略图", "渲染成图", "screenshot")):
                    payload_args["mode"] = "screenshot"
                    auto.append("mode")
                elif any(w in ql for w in ("批注", "comments")):
                    payload_args["mode"] = "comments"
                    auto.append("mode")
                elif any(w in ql for w in ("大纲", "章节结构", "outline")):
                    payload_args["mode"] = "outline"
                    auto.append("mode")
                elif any(w in ql for w in ("格式问题", "体检", "issues")):
                    payload_args["mode"] = "issues"
                    auto.append("mode")
                elif any(w in ql for w in ("构建指南", "load_skill")):
                    payload_args["mode"] = "skills"
                    auto.append("mode")

        # 9d. work_office_join_docx 自动提取目录 source_dir 或文件列表 files
        if tool == "work_office_join_docx":
            if len(cleaned_files) >= 2 and not str(payload_args.get("files", "")).strip():
                payload_args["files"] = ",".join(_resolve_rel_file(f) for f in cleaned_files if f.lower().endswith(".docx"))
                auto.append("files")
            elif not str(payload_args.get("source_dir", "")).strip():
                for d_kw, d_rel in (
                    ("01_微信收回原件", "workspace/01_微信收回原件"),
                    ("微信收回原件", "workspace/01_微信收回原件"),
                    ("收回原件", "workspace/01_微信收回原件"),
                    ("03_汇总与成稿输出", "workspace/03_汇总与成稿输出"),
                    ("04_单位历年材料库", "workspace/04_单位历年材料库"),
                ):
                    if d_kw in q:
                        payload_args["source_dir"] = d_rel
                        auto.append("source_dir")
                        break

        # 9e. work_office_compose 自动提取 preset / format / toc
        if tool == "work_office_compose":
            if "preset" in props and not str(payload_args.get("preset", "")).strip():
                if any(w in ql for w in ("学术", "三线表", "论文", "academic")):
                    payload_args["preset"] = "academic"
                    auto.append("preset")
                elif any(w in ql for w in ("备忘", "纪要", "memo")):
                    payload_args["preset"] = "memo"
                    auto.append("preset")
                elif any(w in ql for w in ("现代", "商务", "modern")):
                    payload_args["preset"] = "modern"
                    auto.append("preset")
            if tool == "work_office_compose":
                if any(w in ql for w in ("ppt", "演示文稿", "幻灯片")) and not str(payload_args.get("format", "")).strip():
                    payload_args["format"] = "pptx"
                    auto.append("format")
                if any(w in ql for w in ("目录", "toc")) and "toc" not in payload_args:
                    payload_args["toc"] = True
                    auto.append("toc")
        # replace：find / replace 都从原话抽，抽不到就保持 needs_input 让人补
        if tool == "work_office_com" and str(args.get("action", "")) == "replace":
            f, r = _extract_replace(q)
            if f and not str(payload_args.get("find", "")).strip():
                payload_args["find"] = f
                auto.append("find")
                if "find" in required:
                    required.remove("find")
            if f and r is not None and "replace" not in payload_args:
                payload_args["replace"] = r
                auto.append("replace")
        # 「11 月报表」要落到 since，否则收件会去扫全量收件目录
        if tool == "work_wechat_files" and not str(payload_args.get("since", "")).strip():
            mm = re.search(r"(\d{1,2})\s*月", q)
            if mm and 1 <= int(mm.group(1)) <= 12:
                payload_args["since"] = f"{datetime.now().year}-{int(mm.group(1)):02d}-01"
                auto.append("since")
        # 「有了就覆盖」要落成 force=true，否则默认保护会让工具直接报「文件已存在」，
        # 用户还得再解释一遍。只对建/合两类生效，别无脑给所有写工具加 force。
        if tool in ("work_office_compose", "work_office_merge") and "force" not in payload_args:
            if re.search(r"覆盖掉|覆盖|盖掉|强写|强制写|有就换", q):
                payload_args["force"] = True
                auto.append("force")
        for k in required:
            desc = str((props.get(k) or {}).get("description") or k)[:40]
            payload_args[k] = "<必填:" + desc + ">"
        payload = json.dumps(payload_args, ensure_ascii=False)
        argv = [BRIDGE, "call", tool, "'" + payload + "'"]
        item = {"kind": "tool", "tool": tool, "args": payload_args,
                "executable": not required, "argv": argv,
                "command": None if required else " ".join(argv)}
        if auto:
            item["auto_filled"] = auto
        if required:
            item["needs_input"] = required
            item["args_hint"] = {k: (props.get(k) or {}).get("description", "")
                                 for k in required}
            item["fill_first"] = (f"先用 {BRIDGE} tools {tool} 查必填参数，"
                                  f"按 needs_input 从用户原话或工作区补齐后再执行；"
                                  f"不要照抄带 <必填:...> 的 args")
        return item

    intents_out, plan, notes, writes, confirm = [], [], [], False, False
    seen = set()
    for it in picked:
        writes = writes or bool(it.get("writes"))
        confirm = confirm or bool(it.get("confirm"))
        cmds = []
        if it.get("control"):
            ctrl = it["control"]
            if ctrl == "skill":
                item = {"kind": "skill", "target": "work-console-upgrade"}
            else:
                argv = [BRIDGE, ctrl]
                item = {"kind": "control", "argv": argv, "command": " ".join(argv)}
                key = ("control", ctrl)
                if key not in seen:
                    seen.add(key)
                    plan.append(item)
            cmds.append(item)
        for step in it.get("steps") or []:
            tool, args = _step_args(step)
            auto_fmt = False
            # schema 已并入 work_office_inspect；原话提到表格/文档/PPT 时先补 format。
            if tool == "work_office_inspect" and args.get("mode") == "schema" and not str(args.get("format", "")).strip():
                fmt = _infer_format(q)
                if fmt:
                    args["format"] = fmt
                    auto_fmt = True
            key = (tool, json.dumps(args, ensure_ascii=False, sort_keys=True))
            if key in seen:
                continue
            seen.add(key)
            item = make_item(tool, args)
            if auto_fmt:
                item["auto_filled"] = list(dict.fromkeys(
                    (item.get("auto_filled") or []) + ["format"]))
            cmds.append(item)
            plan.append(item)
        if it.get("note"):
            notes.append(it["note"])
        intents_out.append({
            "id": it.get("id"), "name": it.get("name"),
            "score": next(s for s, x in scored if x is it),
            "primary": it.get("primary"), "writes": bool(it.get("writes")),
            "confirm_required": bool(it.get("confirm")),
            "out_dir": it.get("out_dir", ""), "note": it.get("note", ""),
            "commands": cmds,
        })

    if match_mode == "fuzzy":
        notes.append("这条是口语模糊匹配（关键词没完整出现）。默认按 plan 执行；"
                     "只有当 plan 明显不符合用户本意时才改用 work.py tools <中文关键字> 找工具。"
                     "不要因为不确定就停下来不调工具。")

    # 用户在「问能力 / 问参数」时，不能顺手把命中的写产物工具真跑一遍。
    # 之前「有哪些能力？收材料那个工具要什么参数」被判成执行 harvest，静默收了一件材料。
    CONSULT_HINTS = ("有哪些能力", "能做什么", "参数是什么", "吃什么参数", "要什么参数",
                     "什么参数", "怎么用", "都有什么", "有哪几个", "什么意思",
                     "介绍一下", "有哪些工具", "怎么操作")
    EXEC_HINTS = ("帮我收", "去收", "收一下", "执行", "跑一遍", "开始做", "做一份",
                  "生成一份", "整理一份", "导出", "发送", "备份一份", "把这次写成")
    consult = any(h in q for h in CONSULT_HINTS) and not any(h in q for h in EXEC_HINTS)
    if consult:
        plan = [x for x in plan if x.get("kind") == "control"]
        for p in [it.get("primary") for it in picked if it.get("primary")][:2]:
            argv = [BRIDGE, "tools", p]
            plan.append({"kind": "control", "argv": argv, "command": " ".join(argv),
                         "note": f"查 {p} 的参数（只读）"})
        for io in intents_out:
            io["commands"] = [c for c in io["commands"] if c.get("kind") != "tool"]
        writes, confirm = False, False
        notes.append("咨询模式：用户只问能力/参数，不要执行任何 work_* 工具，"
                     "尤其不要跑会写文件的工具。把 tools 查到的参数用人话讲清楚；"
                     "用户想真做时会再说一次。")

    # 用户说「别写文件 / 只读 / 看看就好」时，绝不能把会写产物的工具放进 plan。
    # 之前智能体遇到这种情况会偏离路由、甚至想自己手写 openpyxl 脚本，最后什么都没交付；
    # 这里直接给出确定的只读计划，并把写计划留到 original_plan 供用户改口后用。
    READONLY_HINTS = ("别写", "不要写", "不写文件", "只读", "只看看", "先别生成",
                      "不要生成", "不生成", "看看就好", "不要改", "别生成", "不落盘",
                      "不要动", "先看看")
    READONLY_TOOLS = {
        "work_status", "work_modules", "work_wechat_probe", "work_wechat_sessions",
        "work_wechat_messages", "work_wechat_search", "work_wechat_contacts",
        "work_wechat_solitaire", "work_wechat_favorites", "work_wechat_insights",
        "work_comms_overview", "work_comms_notices",
        "work_wecom_probe", "work_wecom_sessions", "work_wecom_messages",
        "work_wecom_search", "work_wecom_files", "work_wecom_contacts",
        "work_office_inspect", "work_office_engines", "work_ledger",
    }
    readonly = any(h in q for h in READONLY_HINTS) and not consult
    original_plan: list = []
    readonly_suggestion = ""
    if readonly and writes:
        def is_readonly(item):
            if item.get("kind") == "control":
                return item.get("argv", ["", ""])[-1] in ("doctor", "tools", "web")
            if item.get("kind") != "tool":
                return False
            tool = item.get("tool", "")
            if tool == "work_exp":
                return item.get("args", {}).get("action") in ("list", "get", "template")
            if tool == "work_wechat_files":
                return item.get("args", {}).get("mode", "scan") == "scan"
            if tool == "work_office_advanced":
                return item.get("args", {}).get("action", "list") in ("list", "schema")
            return tool in READONLY_TOOLS

        original_plan = plan
        plan = [x for x in plan if is_readonly(x)]
        ids = {str(it.get("id", "")) for it in picked}
        if any(i.startswith("office") or i.startswith("exp") for i in ids):
            ro_tool = "work_office_inspect"
            tip = "看表/文档结构用 work_office_inspect(file=<用户给的文件路径>)（纯只读）"
        elif any(i.startswith("wechat") for i in ids):
            ro_tool = "work_wechat_search"
            tip = "看聊天记录用 work_wechat_search(keyword=<原话里的关键词>)（纯只读）"
        else:
            ro_tool = "work_status"
            tip = "只看状态用 work_status(compact=true)（纯只读）"
        # 过滤后往往只剩 probe 这类准备步骤，补一个真正贴合用户诉求的只读工具，
        # 免得「先只看看汇总表」最后只得到一个工具箱状态。
        if not any(x.get("tool") == ro_tool for x in plan):
            plan.append(make_item(ro_tool, {"compact": True} if ro_tool == "work_status" else {}))
        readonly_suggestion = (
            "用户明确要求不写文件，写产物工具已从 plan 移出。" + tip + "。"
            "不要自己手写只读脚本、不要假装生成了文件；plan 里 executable=false 的项"
            "按 needs_input 补齐参数后再执行；用户改口要产物时按 original_plan 执行。"
        )
        notes.append(readonly_suggestion)
        writes = False

    # 意图表里的 office_create / office_merge 平时是「新建、不覆盖」，confirm 记为 false。
    # 但一旦计划里带了 force=true，动作就变成覆盖已有文件，属于破坏性写入。
    # 这里按实际参数兜底，别让「新建一份」和「新建并覆盖」共用同一个确认等级。
    # work_wechat_sync 的 force 只是重解缓存、不毁数据，不在其列。
    FORCE_OVERWRITE_TOOLS = {"work_office_compose", "work_office_merge"}
    if not consult and not readonly:
        for _it in plan:
            if _it.get("kind") != "tool" or _it.get("tool") not in FORCE_OVERWRITE_TOOLS:
                continue
            if _it.get("args", {}).get("force") is True:
                confirm = True
                notes.append(
                    "计划里有 force=true 的步骤：会直接覆盖同名旧文件，执行前必须先跟用户确认，"
                    "并建议先 work_backup_file 备份原件。")
                break

    # 收件与汇总的口径天然不同：harvest 只认「本次时间窗内的新材料」，
    # consolidate 读的是归档目录里的全部文件。不提前说清，智能体会把
    # 「未交 5 家」和「合出 3 家母表」并列转述，看上去像自相矛盾。
    plan_tools = [str(x.get("tool", "")) for x in plan]
    checkpoint = ""
    if "work_office_consolidate" in plan_tools and "work_wechat_files" in plan_tools:
        checkpoint = (
            "两步的口径不一样，转述时先讲清楚：收件环节的「已交/未交」只统计本次时间窗内"
            "新收到的材料；汇总读的是归档目录里的全部文件（含此前各批次）。"
            "所以「本次未交 N 家」和「母表里合出 M 家」同时成立是正常的，"
            "consolidate 返回的 consistency 会列出两边对不上的科室，"
            "按它的 detail 说明「本次未交」不等于「从未交」，不要把一个说成另一个。")

    out = {
        "ok": True, "matched": True, "query": q, "match_mode": match_mode,
        "intents": intents_out,
        "plan": plan,
        "writes": writes, "confirm_required": confirm,
        "notes": notes,
        "next": "从 plan[0] 开始按顺序执行。executable=false 的项先用 tools 查参数、"
                "按 needs_input 从用户原话或工作区补齐后再执行，不要照抄带 <必填:...> 的 args；"
                "不要跳过工具自己写脚本。工具返回 ok:false 就按 error 修一次参数再重试。",
    }
    if checkpoint:
        out["checkpoint"] = checkpoint
    if consult:
        out["consult_mode"] = True
        out["next"] = ("只执行 plan 里的 tools / control 查询，不要调用任何 work_* 工具"
                       "（尤其别跑会写文件的）；把能力或参数用人话讲清楚。")
    if readonly_suggestion:
        out["readonly_requested"] = True
        out["original_plan"] = original_plan
        out["readonly_suggestion"] = readonly_suggestion
        out["next"] = ("只执行 plan 的只读项（executable=false 的先按 needs_input 补齐）。"
                       "不要调 original_plan 里的写产物工具、不要手写脚本、不要假装生成了文件；"
                       "把只读结果讲清楚，并问用户是否要真正生成产物。")
    if allowed_mods:
        out["module_scope"] = sorted(allowed_mods)
    if any((it.get("primary") or "").startswith("work_wechat") for it in picked):
        out["channel_map"] = rules.get("channel_map", {})
    if run_exec and plan and not consult:
        exec_steps = [{"tool": p["tool"], "args": p.get("args") or {}}
                      for p in plan if p.get("kind") == "tool" and p.get("executable")]
        if exec_steps:
            py = _pick_python()
            if py:
                r_batch = subprocess.run(
                    [py, "-X", "utf8", str(RUNNER), "--batch", json.dumps(exec_steps, ensure_ascii=False)],
                    cwd=str(ROOT), env=_env(), capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=600,
                )
                try:
                    out["execution"] = json.loads((r_batch.stdout or "").strip() or "{}")
                except Exception:
                    out["execution"] = {"raw": (r_batch.stdout or "")[:4000], "stderr": (r_batch.stderr or "")[:1000]}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0



_SHORT_TOOL_ALIASES = {
    "comms": {
        "notices": "work_comms_notices",
        "overview": "work_comms_overview",
        "messages": "work_wechat_messages",
        "search": "work_wechat_search",
        "sessions": "work_wechat_sessions",
        "contacts": "work_wechat_contacts",
        "solitaire": "work_wechat_solitaire",
        "favorites": "work_wechat_favorites",
        "insights": "work_wechat_insights",
        "files": "work_wechat_files",
        "media": "work_wechat_media",
        "digest": "work_wechat_digest",
        "remind": "work_wechat_remind",
        "sync": "work_wechat_sync",
        "probe": "work_wechat_probe",
        "wecom_sessions": "work_wecom_sessions",
        "wecom_messages": "work_wecom_messages",
        "wecom_search": "work_wecom_search",
        "wecom_contacts": "work_wecom_contacts",
        "wecom_files": "work_wecom_files",
        "wecom_harvest": "work_wecom_harvest",
    },
    "office": {
        "inspect": "work_office_inspect",
        "compose": "work_office_compose",
        "diff": "work_office_diff",
        "join": "work_office_join_docx",
        "join_docx": "work_office_join_docx",
        "consolidate": "work_office_consolidate",
        "format": "work_office_compose",
        "gbt9704": "work_office_compose",
        "advanced": "work_office_advanced",
        "excel": "work_office_advanced",
        "excel_mcp": "work_office_advanced",
        "word": "work_office_advanced",
        "word_mcp": "work_office_advanced",
        "merge": "work_office_merge",
        "create": "work_office_batch",
        "batch": "work_office_batch",
        "import": "work_office_import",
        "schema": "work_office_inspect",
        "cli": "work_office_batch",
        "pdf": "work_office_to_pdf",
        "to_pdf": "work_office_to_pdf",
        "com": "work_office_com",
        "engines": "work_office_engines",
    },
    "memory": {
        "ledger": "work_ledger",
        "exp": "work_exp",
        "distill": "work_exp",
        "mem": "work_mem",
        "engram": "work_mem",
        "backup": "work_backup_file",
    },
}


def cmd_module_entry(mod_id: str, tokens: list[str], run_exec: bool = False, timeout: int = 600) -> int:
    """单模块直达入口（work.py comms / office / memory）：支持短工具名直调或模块内自然语言路由。"""
    first = (tokens[0] if tokens else "").strip()
    aliases = _SHORT_TOOL_ALIASES.get(mod_id, {})
    resolved_tool = ""
    if first in aliases:
        resolved_tool = aliases[first]
    elif first.startswith("work_") and _intent_module(first) == mod_id:
        resolved_tool = first
    elif f"work_{mod_id}_{first}" in aliases.values():
        resolved_tool = f"work_{mod_id}_{first}"

    if resolved_tool:
        raw_args = tokens[1] if len(tokens) > 1 else "{}"
        try:
            parsed_args = json.loads(raw_args) if str(raw_args).strip() else {}
        except Exception:
            parsed_args = None
        if isinstance(parsed_args, dict):
            if resolved_tool == "work_office_advanced" and first in ("excel", "excel_mcp", "word", "word_mcp"):
                parsed_args.setdefault("engine", "excel" if first.startswith("excel") else "word")
            elif resolved_tool == "work_office_inspect" and first == "schema":
                parsed_args.setdefault("mode", "schema")
            elif resolved_tool == "work_office_batch" and first == "create":
                parsed_args.setdefault("create", True)
            elif resolved_tool == "work_office_compose" and first in ("format", "gbt9704"):
                parsed_args.setdefault("preset", "gbt9704" if first == "gbt9704" else "modern")
            raw_args = json.dumps(parsed_args, ensure_ascii=False)
        return cmd_call(resolved_tool, raw_args, timeout)

    return cmd_ask(" ".join(tokens), module=mod_id, run_exec=run_exec)


def cmd_pipeline(spec: str, modules: str = "", run_exec: bool = False) -> int:
    """跨模块组合入口：既可传 JSON 步骤数组直接按序跑，也可传跨模块自然语言按模块组合路由。"""
    s = (spec or "").strip()
    if s.startswith("["):
        py = _pick_python()
        if not py:
            print("[work] 没找到装齐依赖的 Python", file=sys.stderr)
            return 3
        return subprocess.call([py, "-X", "utf8", str(RUNNER), "--batch", s], cwd=str(ROOT), env=_env())
    return cmd_ask(s, module=modules, run_exec=run_exec)

def _passthrough(script: Path, extra: list[str]) -> int:
    return subprocess.call([sys.executable, str(script)] + extra,
                           cwd=str(ROOT), env=_env())


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(
        description="work-toolkit 本地桥接：不打开网页、不依赖 MCP 也能调用全部工具")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("ask", help="把用户原话翻成确定的工具执行计划（支持单模块或组合模块）")
    p.add_argument("query", nargs="+", help="用户原话，例如：把 11 月报表收一下并汇总")
    p.add_argument("--module", default="", help="限定模块范围：comms / office / memory 或逗号组合如 comms,office")
    p.add_argument("--run", action="store_true", dest="run_exec", help="生成计划后立即执行所有 executable=true 的步骤")

    for mod_id, mod_help in (
        ("comms", "单独调用【通讯模块】（微信 4.x / 企业微信）：支持短工具名直调或自然语言路由"),
        ("office", "单独调用【Office 模块】（通用富文档 / 版本对比 / 跨表汇总 / Excel & Word MCP）：支持短工具名直调或自然语言路由"),
        ("memory", "单独调用【记忆模块】（材料台账 / 任务经验 / 自动备份）：支持短工具名直调或自然语言路由"),
    ):
        pm = sub.add_parser(mod_id, help=mod_help)
        pm.add_argument("target", nargs="+", help="工具短名（如 inspect/compose/diff/messages/notices/ledger）+ JSON，或自然语言需求")
        pm.add_argument("--run", action="store_true", dest="run_exec", help="自然语言路由后直接执行")
        pm.add_argument("--timeout", type=int, default=600, help="超时秒数")

    pp = sub.add_parser("pipeline", help="跨模块组合调用：支持传 JSON 步骤数组或跨模块自然语言流水线")
    pp.add_argument("spec", nargs="+", help='JSON 数组 [{"tool":"...","args":{}}] 或跨模块自然语言指令')
    pp.add_argument("--modules", default="comms,office,memory", help="参与组合的模块，默认 comms,office,memory")
    pp.add_argument("--run", action="store_true", dest="run_exec", help="自然语言路由后立即按序执行")

    p = sub.add_parser("call", help="调用一个 work_* 工具")
    p.add_argument("tool", help="工具名，例如 work_status")
    p.add_argument("args", nargs="?", default="{}",
                   help="JSON 对象；@文件路径 或 - 表示从文件/stdin 读")
    p.add_argument("--timeout", type=int, default=600, help="超时秒数，默认 600")

    p = sub.add_parser("tools", help="看工具清单和参数，不带参数看精简清单")
    p.add_argument("query", nargs="?", default="", help="工具名或中文关键字")
    p.add_argument("--all", action="store_true", dest="full", help="输出原始完整 schema")
    p.add_argument("--module", default="", help="只看某个模块：comms / office / memory / other")

    p = sub.add_parser("modules", help="看模块划分（通讯 / Office / 记忆 / 其他）与各自的验收命令")
    p.add_argument("--json", action="store_true", dest="as_json", help="输出 JSON")

    p = sub.add_parser("doctor", help="环境体检")
    p.add_argument("extra", nargs=argparse.REMAINDER)

    p = sub.add_parser("setup", help="一键安装/体检/更新 Skill")
    p.add_argument("extra", nargs=argparse.REMAINDER)

    p = sub.add_parser("web", help="启动可视化控制台（可选）")
    p.add_argument("extra", nargs=argparse.REMAINDER)

    args, unknown = ap.parse_known_args()
    # argparse.REMAINDER 在 subparser 下对 `--check` / `--port` 这类选项并不总是收得住；
    # 用 parse_known_args 兜底，保证 setup/doctor/web 的开关原样透传。
    extra = list(getattr(args, "extra", []) or []) + list(unknown)
    if extra and extra[0] == "--":
        extra = extra[1:]
    if not args.cmd:
        ap.print_help()
        return 0
    if args.cmd == "ask":
        return cmd_ask(" ".join(args.query), module=getattr(args, "module", ""), run_exec=getattr(args, "run_exec", False))
    if args.cmd in ("comms", "office", "memory"):
        return cmd_module_entry(args.cmd, args.target, getattr(args, "run_exec", False), getattr(args, "timeout", 600))
    if args.cmd == "pipeline":
        return cmd_pipeline(" ".join(args.spec), getattr(args, "modules", ""), getattr(args, "run_exec", False))
    if args.cmd == "call":
        return cmd_call(args.tool, args.args, args.timeout)
    if args.cmd == "tools":
        return cmd_tools(args.query, args.full, args.module)
    if args.cmd == "modules":
        return cmd_modules(args.as_json)
    if args.cmd == "doctor":
        return _passthrough(ROOT / "tools" / "doctor.py", extra)
    if args.cmd == "setup":
        return _passthrough(ROOT / "scripts" / "setup.py", extra)
    if args.cmd == "web":
        return _passthrough(ROOT / "scripts" / "launch.py", extra)
    return 2


if __name__ == "__main__":
    sys.exit(main())
