#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""doctor.py — 跑之前先体检：解释器、依赖、OfficeCLI、微信目录、任务经验目录、端口。

只用标准库，任何 3.8+ 解释器都能跑（哪怕它没装任何依赖）——这是它的意义：
在「工具跑不起来」的时候，还能有个东西告诉你为什么。

用法：
    python tools/doctor.py             # 人看的报告
    python tools/doctor.py --json      # 机器可读
    python tools/doctor.py --pick      # 只输出一个「装齐依赖」的解释器绝对路径

退出码：0 必需项全过；1 有必需项没过；--pick 找不到合格解释器也返回 1。
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (import 名, 显示名, 缺失时的修复命令, 是否必需)
DEPS = [
    ("mcp", "mcp（MCP 协议库）", "pip install mcp", True),
    ("docx", "python-docx（Word）", "pip install python-docx", True),
    ("openpyxl", "openpyxl（Excel）", "pip install openpyxl", True),
    ("pypdf", "pypdf（数 PDF 页数）", "pip install pypdf", False),
    ("PIL", "Pillow（图片解密后校验尺寸）", "pip install Pillow", False),
    ("comtypes", "comtypes（微信 UIA / Office COM）", "pip install comtypes", False),
    ("win32com", "pywin32（Excel/Word COM）", "pip install pywin32", False),
    ("pandas", "pandas（大表汇总加速）", "pip install pandas", False),
    ("fitz", "PyMuPDF（PDF 文字层）", "pip install PyMuPDF", False),
]

# 探测脚本在「临时目录」里跑：项目根下的 mcp/ 目录是个没有 __init__.py 的同名目录，
# 一旦 cwd 在项目里，importlib 会把它当成命名空间包，让「mcp 已安装」变成假阳性。
PROBE = r"""
import json
res = {}
for name, stmt in [
    ("mcp", "import mcp.server.mcpserver"),
    ("docx", "import docx"),
    ("openpyxl", "import openpyxl"),
    ("pypdf", "import pypdf"),
    ("PIL", "import PIL"),
    ("comtypes", "import comtypes"),
    ("win32com", "import win32com.client"),
    ("pandas", "import pandas"),
    ("fitz", "import fitz"),
]:
    try:
        exec(stmt)
        res[name] = True
    except Exception:
        res[name] = False
print("@@" + json.dumps(res))
"""

WECHAT_ROOTS = [
    r"D:\聊天记录\xwechat_files",
    os.path.expanduser(r"~\Documents\xwechat_files"),
    os.path.expanduser(r"~\Documents\WeChat Files"),
]
OFFICECLI = ROOT / "bin" / "officecli.exe"
EXPDIR = ROOT / "workspace" / "11_任务经验"


# ---------------------------------------------------------------- 解释器发现
def _candidates() -> list[str]:
    """列出这台机器上可能装了依赖的解释器，去重后返回绝对路径。"""
    found: list[str] = [sys.executable]

    for name in ("python", "python3", "python.exe"):
        p = shutil.which(name)
        if p:
            found.append(p)
    # 当前进程如果是 py 启动器拉起的，sys.executable 已经是真实解释器，不重复加

    # py 启动器登记的版本（Windows 专有，输出每行形如 " -V:3.14 *  C:\...\python.exe"）
    launcher = shutil.which("py")
    if launcher:
        try:
            out = subprocess.run([launcher, "-0p"], capture_output=True, text=True,
                                 timeout=10, encoding="utf-8", errors="replace").stdout
            for line in out.splitlines():
                if "\\python.exe" in line.lower() or "/python" in line.lower():
                    tail = line.split("*")[-1].strip()
                    if tail.lower().endswith("python.exe"):
                        found.append(tail)
        except Exception:
            pass

    lade = os.environ.get("LOCALAPPDATA")
    if lade:
        found += glob.glob(os.path.join(lade, "Programs", "Python", "Python3*", "python.exe"))
    found += glob.glob(r"C:\Python3*\python.exe")
    found += glob.glob(r"C:\Program Files\Python3*\python.exe")
    # 项目自带虚拟环境优先考虑
    for venv in (".venv", "venv", "env"):
        found.append(str(ROOT / venv / "Scripts" / "python.exe"))

    seen, out = set(), []
    for p in found:
        if not p or not os.path.isfile(p):
            continue
        try:
            key = os.path.normcase(os.path.realpath(p))
        except OSError:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(os.path.abspath(p))
    return out


def probe(python: str) -> dict:
    """在一个解释器里探一遍依赖。cwd 必须在项目外（见 PROBE 的说明）。"""
    info = {"path": python, "ok": False, "version": "", "deps": {}, "error": ""}
    try:
        r = subprocess.run([python, "-c", PROBE], capture_output=True, text=True,
                           timeout=45, cwd=tempfile.gettempdir(),
                           encoding="utf-8", errors="replace")
        for line in (r.stdout or "").splitlines():
            if line.startswith("@@"):
                info["deps"] = json.loads(line[2:])
                info["ok"] = True
                break
        if not info["ok"]:
            info["error"] = ((r.stderr or "").strip().splitlines() or ["探测失败"])[-1]
            return info
        v = subprocess.run([python, "-c", "import sys;print('%d.%d.%d' % sys.version_info[:3])"],
                           capture_output=True, text=True, timeout=20,
                           encoding="utf-8", errors="replace")
        info["version"] = (v.stdout or "").strip()
        return info
    except Exception as exc:  # 超时、无法执行
        info["error"] = f"{type(exc).__name__}: {exc}"
        return info


# mcp 2.3 要求 >=3.10；3.9 装不上依赖，装上了也 import 失败。
# 早先只判「依赖齐不齐」，会把 3.9 标成 READY，用户装到一半才炸。
MIN_PY = (3, 10)


def _ver_tuple(s: str) -> tuple[int, ...]:
    try:
        parts = tuple(int(x) for x in (s or "").split(".")[:3])
        return parts or (0,)
    except Exception:
        return (0,)


def required_names() -> list[str]:
    return [n for n, _d, _f, req in DEPS if req]


def is_ready(info: dict) -> bool:
    if not info.get("ok"):
        return False
    if _ver_tuple(info.get("version", "")) < MIN_PY:
        return False
    return all(info["deps"].get(n) for n in required_names())


def find_best(verbose: bool = False) -> tuple[str | None, list[dict]]:
    """挑一个装齐必需依赖的解释器。同分时选版本高的。"""
    reports = [probe(p) for p in _candidates()]
    if verbose:
        for r in reports:
            flag = "READY" if is_ready(r) else "no"
            print(f"  探测 {r['path']}  py{r['version'] or '?'}  {flag}"
                  + (f"  ({r['error']})" if r["error"] else ""), file=sys.stderr)
    ready = [r for r in reports if is_ready(r)]

    def vkey(r: dict):
        try:
            return tuple(int(x) for x in r["version"].split("."))
        except Exception:
            return (0,)

    ready.sort(key=vkey, reverse=True)
    return (ready[0]["path"] if ready else None), reports


# ---------------------------------------------------------------- 各项检查
def check_officecli() -> dict:
    if not OFFICECLI.is_file():
        return {"level": "fail", "name": "OfficeCLI", "detail": "缺 bin/officecli.exe",
                "fix": "重新解压完整发布包（bin/ 要一起解出来）；只有源码没有 exe 时，"
                       "8 个 office_* 工具会直接报错，其余工具不受影响"}
    try:
        r = subprocess.run([str(OFFICECLI), "--version"], capture_output=True, text=True,
                           timeout=20, encoding="utf-8", errors="replace")
        ver = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
        ver = ver[0].strip() if ver else ""
        if ver:
            return {"level": "ok", "name": "OfficeCLI", "detail": f"v{ver}"}
        return {"level": "warn", "name": "OfficeCLI", "detail": "存在但没输出版本号",
                "fix": "在本项目目录下运行 bin\\officecli.exe --version 看报错；"
                       "被杀软隔离就加白名单后重解压"}
    except Exception as exc:
        return {"level": "warn", "name": "OfficeCLI", "detail": f"执行失败 {exc}",
                "fix": "确认 bin\\officecli.exe 有执行权限、未被杀软隔离"}


def check_wechat() -> dict:
    env = os.environ.get("WECHAT_ACCOUNT_DIR")
    if env and os.path.isdir(env):
        return {"level": "ok", "name": "微信数据目录", "detail": env}
    for root in WECHAT_ROOTS:
        if not os.path.isdir(root):
            continue
        try:
            names = os.listdir(root)
        except OSError:
            continue
        for name in names:
            cand = os.path.join(root, name)
            if os.path.isdir(os.path.join(cand, "msg", "file")):
                return {"level": "ok", "name": "微信数据目录", "detail": cand}
    return {"level": "warn", "name": "微信数据目录", "detail": "没找到（微信相关工具会不可用）",
            "fix": "设环境变量 WECHAT_ACCOUNT_DIR 指向 wxid_xxx 目录"}


WECOM_ROOTS = [
    r"D:\聊天记录\WXWork",
    r"C:\WXWork", r"D:\WXWork", r"E:\WXWork",
]


def _wxwork_running() -> bool:
    """企业微信在不在跑。只用 tasklist，不 import 任何第三方模块。"""
    try:
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq WXWork.exe", "/NH"],
                           capture_output=True, text=True, timeout=15,
                           encoding="utf-8", errors="replace")
        return "WXWork.exe" in (r.stdout or "")
    except Exception:
        return False


def check_wecom() -> dict:
    """企业微信：只查目录、进程与密钥缓存，不 import 加解密模块。

    doctor 必须保持零依赖 —— 它存在的意义就是「依赖装不齐时还能告诉你为什么」，
    所以这里不调用 wecom.wcread（那要 cryptography）。
    """
    env = os.environ.get("WECOM_ACCOUNT_DIR")
    roots = ([env] if env else []) + WECOM_ROOTS + [
        os.path.join(os.path.expanduser("~"), "Documents", "WXWork")]
    acc = None
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        if os.path.isfile(os.path.join(root, "Data", "message.db")):
            acc = root
            break
        try:
            names = sorted(os.listdir(root))
        except OSError:
            continue
        for name in names:
            cand = os.path.join(root, name)
            if os.path.isfile(os.path.join(cand, "Data", "message.db")):
                acc = cand
                break
        if acc:
            break

    if not acc:
        return {"level": "warn", "name": "企业微信",
                "detail": "没找到数据目录（企微相关工具会不可用）",
                "fix": "改过存储位置的，设环境变量 WECOM_ACCOUNT_DIR 指向 WXWork\\<账号> 目录"}

    cached = (ROOT / "workspace" / "06_运行状态" / "wecom_key.json").is_file()
    running = _wxwork_running()
    bits = [acc, "客户端" + ("在跑" if running else "没开"),
            "密钥" + ("已缓存" if cached else "未缓存")]
    if running or cached:
        return {"level": "ok", "name": "企业微信", "detail": " · ".join(bits)}
    return {"level": "warn", "name": "企业微信", "detail": " · ".join(bits),
            "fix": "打开企业微信并保持登录：密钥只在 WXWork.exe 的内存里，客户端关着读不了加密库"}


def check_experience() -> dict:
    """任务经验就是一堆 md 文件，纯目录检查，不连库、不打分。"""
    if not EXPDIR.is_dir():
        return {"level": "warn", "name": "任务经验目录",
                "detail": "还没建（首次打开经验节点会自动建）"}
    docs = [p for p in EXPDIR.glob("*.md") if p.name != "index.md"]
    if not docs:
        return {"level": "warn", "name": "任务经验",
                "detail": "目录已建，但还没有经验文档",
                "fix": "明确要求时说 /work 把这次写成经验，或用网页「新建经验」写第一份"}
    kb = sum(p.stat().st_size for p in docs) / 1024
    return {"level": "ok", "name": "任务经验",
            "detail": f"{len(docs)} 份 / {kb:.0f}KB · 轨迹提炼 + FTS 检索 + 可重放（不自动注入）"}


def check_port(port: int = 8765) -> dict:
    s = socket.socket()
    s.settimeout(0.4)
    try:
        s.connect(("127.0.0.1", port))
        return {"level": "ok", "name": f"端口 {port}", "detail": "已有服务在跑（可直接用）"}
    except Exception:
        return {"level": "info", "name": f"端口 {port}", "detail": "空闲"}
    finally:
        s.close()


def check_bridge() -> dict:
    """总 Skill 的本地桥接：这是「不开网页、不连 MCP 也能用」的关键。"""
    p = ROOT / "scripts" / "work.py"
    if not p.is_file():
        return {"level": "fail", "name": "本地桥接", "detail": "缺 scripts/work.py",
                "fix": "重新拉取仓库或跑 python scripts\\setup.py"}
    if not (ROOT / "web" / "tools.json").is_file():
        return {"level": "warn", "name": "本地桥接", "detail": "工具清单未生成，首次 tools 命令会自动补",
                "fix": "python mcp\\dump_tools.py > web\\tools.json"}
    if not (ROOT / "web" / "intents.json").is_file():
        return {"level": "fail", "name": "本地桥接", "detail": "缺 web/intents.json，ask 无法路由",
                "fix": "重新拉取仓库或跑 python scripts\\setup.py"}
    try:
        import check_intents
        rep = check_intents.run()
    except Exception as exc:  # 校验器自身异常也不能把体检搞崩
        return {"level": "warn", "name": "本地桥接",
                "detail": f"意图表校验器异常：{exc}", "fix": "python tools\\check_intents.py"}
    if not rep["ok"]:
        return {"level": "fail", "name": "本地桥接",
                "detail": "意图表有误：" + "；".join(rep["errors"][:2]),
                "fix": "python tools\\check_intents.py 看全部错误"}
    return {"level": "ok", "name": "本地桥接",
            "detail": f"work.py 已就绪 · 意图路由 {rep['intents']} 条 · 支持单模块 (comms/office/memory) 与组合 (pipeline/ask)"}


def check_super_skill() -> dict:
    skills_root = Path.home() / ".workbuddy" / "skills"
    # 历史 bug：备份目录被写成 skills/<名字>.bak-*，WorkBuddy 会当成独立 Skill 重复加载。
    # 判据不能只看 .bak-：手工复制出的 `-copy`、`(2)`、`-备份` 同样会被加载成第二份 Skill。
    # 所以按「以本 Skill 名开头、带自己的 SKILL.md、但不是本体」来认。
    stray = []
    if skills_root.is_dir():
        for p in sorted(skills_root.iterdir()):
            if not p.is_dir() or p.name == "work-office-toolkit":
                continue
            if p.name.startswith("work-office-toolkit") and (p / "SKILL.md").is_file():
                stray.append(p.name)
    if stray:
        return {"level": "fail", "name": "总 Skill",
                "detail": (f"skills/ 里有 {len(stray)} 个同名副本（{'、'.join(stray[:3])}），"
                           f"会被当成多个 Skill 重复加载"),
                "fix": "python scripts\\install_skills.py（会把它们移到 skills.backups/）"}

    skill = skills_root / "work-office-toolkit"
    work = skill / "commands" / "work.md"
    if not work.is_file():
        return {"level": "warn", "name": "总 Skill",
                "detail": "未安装 /work 命令", "fix": "python scripts\\install_skills.py"}
    cmds = sorted(p.stem for p in (skill / "commands").glob("*.md"))
    allowed_cmds = {"work", "comms", "office", "memory"}
    extra_cmds = [c for c in cmds if c not in allowed_cmds]
    if "work" not in cmds or extra_cmds:
        return {"level": "warn", "name": "模块化 Skills",
                "detail": "命令集合异常：" + ", ".join(cmds),
                "fix": "python scripts\\install_skills.py（会自动同步并清理旧命令）"}
    try:
        skill_text = (skill / "SKILL.md").read_text(encoding="utf-8-sig")
        cmd_text = work.read_text(encoding="utf-8-sig")
    except OSError as exc:
        return {"level": "warn", "name": "总 Skill", "detail": f"读不到已装 Skill：{exc}",
                "fix": "python scripts\\install_skills.py"}
    if "scripts/work.py" not in skill_text or "ask" not in cmd_text:
        return {"level": "warn", "name": "总 Skill",
                "detail": "已装的是旧版（没有 ask 第一步），智能体容易不调工具",
                "fix": "python scripts\\install_skills.py 同步新版后重载会话"}
    if "不接管" not in skill_text:
        return {"level": "warn", "name": "总 Skill",
                "detail": "已装的是旧版（没有能力边界节），可能抢占 WorkBuddy 原生能力",
                "fix": "python scripts\\install_skills.py 同步新版后重载会话"}
    # 5.0 起 SKILL.md 只留常驻核心，细节搬到 references/ 按需读。
    # 少了它们等于退回「一个大文件塞满上下文」，边界会被稀释。
    refs = skill / "references"
    ref_md = sorted(p.name for p in refs.glob("*.md")) if refs.is_dir() else []
    if len(ref_md) < 3:
        return {"level": "warn", "name": "总 Skill",
                "detail": f"缺 references/ 细节文件（现有 {len(ref_md)} 个，应有 5 个）",
                "fix": "python scripts\\install_skills.py 同步新版后重载会话"}
    mod_skills = [s for s in ("work-comms", "work-office", "work-memory") if (skills_root / s / "SKILL.md").is_file()]
    codex_root = Path.home() / ".codex" / "skills"
    codex_mods = [s for s in ("work-comms", "work-office", "work-memory") if (codex_root / s / "SKILL.md").is_file()]
    return {"level": "ok", "name": "模块化 Skills",
            "detail": f"已解耦 · 独立模块 ({', '.join(mod_skills) or '已就绪'}) + 组合入口 ({'/'.join('/'+c for c in cmds)}) · "
                      f"Codex 独立技能 {len(codex_mods)} 个 · /work 非唯一入口"}


def check_web() -> dict:
    p = ROOT / "web" / "console.html"
    if p.is_file():
        return {"level": "ok", "name": "控制台页面", "detail": f"console.html {p.stat().st_size/1024:.0f}KB（可选展示）"}
    return {"level": "info", "name": "控制台页面", "detail": "未生成（网页只是可选展示）",
            "fix": "python web/build.py"}


def check_shadow() -> dict:
    """项目根的 mcp/ 目录与 PyPI 的 mcp 同名。有人把它塞进 sys.path[0] 就会踩坑，
    这里只提示，不算错误。"""
    local = ROOT / "mcp"
    if local.is_dir() and not (local / "__init__.py").is_file():
        return {"level": "info", "name": "mcp/ 目录",
                "detail": "与 mcp 库同名，别把项目根或 mcp/ 插到 sys.path 最前面"}
    return {"level": "info", "name": "mcp/ 目录", "detail": "无冲突"}


def run_all(verbose: bool = False) -> dict:
    best, reports = find_best(verbose=verbose)
    chosen = best or (reports[0]["path"] if reports else sys.executable)
    chosen_info = next((r for r in reports if r["path"] == chosen), None)
    if chosen_info is None:
        chosen_info = probe(chosen)

    deps = []
    for name, label, fix, req in DEPS:
        have = bool(chosen_info["deps"].get(name))
        # 裸 `pip install mcp` 会装到 PATH 里第一个 pip（常常不是这个解释器），
        # 装完再体检还是缺，用户会以为「装了没用」。fix 必须带解释器绝对路径。
        pkg = fix.split("pip install", 1)[-1].strip()
        fix_cmd = f'"{chosen}" -m pip install {pkg}'
        deps.append({
            "name": label, "import": name, "required": req, "installed": have,
            "fix": fix_cmd,
            "detail": "已装" if have else ("缺失（必需）" if req else "缺失（可选）"),
            "level": "ok" if have else ("fail" if req else "warn"),
        })

    checks = [check_bridge(), check_super_skill(), check_officecli(), check_wechat(),
              check_wecom(), check_experience(), check_web(), check_port(), check_shadow()]
    items = deps + checks
    failed = [i for i in items if i["level"] == "fail"]
    warns = [i for i in items if i["level"] == "warn"]

    hint = ""
    if not is_ready(chosen_info):
        if _ver_tuple(chosen_info.get("version", "")) < MIN_PY:
            hint = (f"选中的解释器是 py{chosen_info.get('version') or '?'}，低于 3.10；"
                    f"mcp 装不上也跑不起来。装一个 3.10+ 再重跑 setup。")
        else:
            hint = (f'缺必需依赖。用这条装（裸 pip 会装到别的解释器）：'
                    f'"{chosen}" -m pip install mcp python-docx openpyxl')

    return {
        "ok": not failed,
        "python": {"path": chosen, "version": chosen_info.get("version", ""),
                   "ready": is_ready(chosen_info)},
        "interpreters": [{"path": r["path"], "version": r["version"], "ready": is_ready(r),
                          "error": r["error"]} for r in reports],
        "items": items,
        "failed": len(failed),
        "warnings": len(warns),
        "hint": hint,
    }


# ---------------------------------------------------------------- 输出
MARK = {"ok": "✅", "warn": "⚠️ ", "fail": "❌", "info": "· "}


def _w(text: str) -> int:
    """按终端显示宽度算长度：中日韩全角字符占两格，直接 len() 会把表格对歪。"""
    import unicodedata

    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _w(text))


def _setup_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def print_report(rep: dict) -> None:
    _setup_stdout()
    py = rep["python"]
    print()
    print("  办公工具箱 · 环境体检")
    print("  " + "-" * 52)
    print(f"  解释器  {py['path']}")
    print(f"  版本    Python {py['version'] or '未知'}"
          f"   {'依赖齐全' if py['ready'] else '缺必需依赖'}")
    print()

    groups = [("依赖", [i for i in rep["items"] if "import" in i]),
              ("环境", [i for i in rep["items"] if "import" not in i])]
    for title, items in groups:
        print(f"  {title}")
        for it in items:
            note = "" if it["level"] in ("ok", "info") else f"  → {it.get('fix', '')}"
            print(f"    {MARK.get(it['level'], '  ')} {_pad(it['name'], 30)} {it['detail']}{note}")
        print()

    if rep["hint"]:
        print(f"  ❌ {rep['hint']}")
        print()

    if rep["ok"]:
        tail = "全部就绪" if not rep["warnings"] else f"必需项全过，{rep['warnings']} 项可选未装"
        print(f"  结论：{tail}。支持单模块独立调用（/comms · /office · /memory 或各模块 MCP）与跨模块组合调用（/work · pipeline）。")
        if rep["warnings"]:
            print("  （可选项缺了只影响对应功能，不影响主流程）")
    else:
        print(f"  结论：{rep['failed']} 项必需检查没过，按上面的 → 修完再跑一次。")
    print()

    ready = [i for i in rep["interpreters"] if i["ready"]]
    if len(rep["interpreters"]) > 1:
        print(f"  这台机器上共 {len(rep['interpreters'])} 个解释器，其中 {len(ready)} 个装齐了：")
        for i in rep["interpreters"]:
            flag = "✅" if i["ready"] else "  "
            print(f"    {flag} py{i['version'] or '?':<8} {i['path']}")
        print()


def main() -> int:
    ap = argparse.ArgumentParser(description="办公工具箱环境体检")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--pick", action="store_true", help="只输出可用的解释器路径")
    ap.add_argument("-v", "--verbose", action="store_true", help="打印探测过程")
    args = ap.parse_args()

    if args.pick:
        best, _ = find_best(verbose=args.verbose)
        if best:
            print(best)
            return 0
        print("找不到装了 mcp / python-docx / openpyxl 的解释器", file=sys.stderr)
        return 1

    rep = run_all(verbose=args.verbose)
    if args.json:
        _setup_stdout()
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print_report(rep)
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
