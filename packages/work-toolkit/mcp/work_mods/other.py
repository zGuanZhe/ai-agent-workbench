#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""work_mods/other.py — 其他模块（状态总览、模块清单）。

这是「剩下的、不属于任何业务模块」的那一类：看整体状态、看模块怎么分。
它是公共底座，**随每个入口一起挂载**，所以没有独立入口；改了它要跑全量回归。
它不 import 具体业务模块的顶层代码，只在 work_status 里按需懒加载；
某个模块没装就如实标「未加载」，不影响其他模块。
"""

from __future__ import annotations

import sys
from typing import Any, Dict, List

from work_core import (  # noqa: F401
    DIR_ARCHIVE, DIR_BACKUP, DIR_HARVESTED, DIR_INBOX, DIR_OUTPUT, MASTER_TEMPLATE,
    ROOT, ROSTER, STATE_DIR, STATE_FILE, WORKERS, WORKSPACE, ToolBox,
    _dump, _ensure_dirs, _list_files, _load_state, _run,
)

TOOLS = ToolBox("other")

MODULE = {
    "id": "other",
    "name": "其他",
    "version": "1.0.0",
    "scope": "状态总览、模块清单——看整体与看划分，不含具体业务",
    "entry": "（无独立入口，随每个入口挂载）",
    "docs": "skills/work-office-toolkit/SKILL.md",
}


def _module(mid: str):
    """懒加载业务模块；导入失败或没装就当不存在，绝不让状态总览整体报错。"""
    try:
        from work_mods import MODULES  # 延迟导入，避免与包初始化顺序打架
        return MODULES.get(mid)
    except Exception:  # pragma: no cover
        return None


# --------------------------------------------------------------------------- #
# 工具：状态总览（组合各模块自检；模块没加载就如实标注，不虚报）
# --------------------------------------------------------------------------- #
@TOOLS.tool(
    name="work_status",
    description=(
        "查看办公工具箱全貌：通讯（微信 / 企业微信）与 Office（OfficeCLI / Word MCP / "
        "Excel MCP / COM）两侧入口的就绪自查（带 bridge 与不可用原因），记忆模块"
        "（材料台账 / 任务经验）是否可用，工作区五个目录的"
        "文件清单，以及最近一次收件、汇总、排版的结果摘要。"
        "引擎的权威判定是 work_office_engines（真实 import）。"
        "reset_demo_fixtures=true 时重建 5 科室演示数据并跑通全流程（用于验收/演示）。"
        "compact=true 时文件树只给数量与样例（/work 默认走这个，避免输出几十 KB 被截断）；"
        "要完整文件清单就留空或传 false。"
    ),
)
def work_status(reset_demo_fixtures: bool = False, compact: bool = False) -> str:
    _ensure_dirs()
    if reset_demo_fixtures:
        res = _run([sys.executable, str(WORKERS / "generate_demo_fixtures.py")], timeout=180)
        if res["exit_code"] != 0:
            return _dump({"ok": False, "stage": "init_demo", **res})

    comms = _module("comms")
    office = _module("office")
    memory = _module("memory")

    cap = comms.media_capability() if comms else {"ok": False, "notes": ["通讯模块未加载"]}

    # 台账 / 经验归记忆模块，自检由它自己报；模块没加载就如实标不可用。
    if memory:
        mem_stat = memory.status_snapshot()
    else:  # pragma: no cover - 只在模块文件缺失时走到
        mem_stat = {
            "ledger": {"ok": False, "error": "记忆模块未加载"},
            "experience": {"ok": False, "error": "记忆模块未加载"},
            "engines": [],
        }
    ledger_stat: Dict[str, Any] = mem_stat.get("ledger") or {"ok": False}
    exp_stat: Dict[str, Any] = mem_stat.get("experience") or {"ok": False}

    # 引擎状态别只看「文件在不在」。以前这里写错了 Word MCP 的入口文件名，
    # 又把微信 3.x 的 wxauto 和没部署的 chatlog 按「目录存在」算成就绪，
    # 体检页会虚报可用。现在每模块自报，真实加载判定仍交给 work_office_engines。
    engines: List[Dict[str, Any]] = []
    if office:
        engines += office.status_engines()
    if comms:
        engines += comms.status_engines(cap)
    engines += mem_stat.get("engines") or []

    officecli_version = office.officecli_version() if office else None

    state = _load_state()
    files = {
        "00_模拟微信收件缓存": _list_files(DIR_INBOX),
        "01_微信收回原件": _list_files(DIR_HARVESTED),
        "02_自动备份区": _list_files(DIR_BACKUP),
        "03_汇总与成稿输出": _list_files(DIR_OUTPUT),
        "04_单位历年材料库": _list_files(DIR_ARCHIVE),
    }
    last_results = {k: v for k, v in state.items() if not k.startswith("_")}
    # /work 路径下用 compact：收件目录常有几百个文件，全量返回会把智能体的输出
    # 撑到几十 KB 并被截断，用户只问「什么状态」时并不需要每个文件名。
    if compact:
        files = {k: {"count": len(v), "sample": v[:5]} for k, v in files.items()}
        last_results = {k: ({"keys": sorted(v)[:12]} if isinstance(v, dict) else v)
                        for k, v in last_results.items()}
    return _dump(
        {
            "ok": True,
            "root": str(ROOT),
            "workspace": str(WORKSPACE),
            "modules": [m for m in ("comms", "office", "memory") if _module(m)],
            "officecli_version": officecli_version,
            "engines": engines,
            "engine_probe_hint": ("engines 只是入口自查；要确认依赖齐全、工具真能调，"
                                  "跑 work_office_engines 逐个真实 import 并数工具数。"
                                  "GB/T 9704 是按需附加能力，不列为核心引擎。"),
            "files": files,
            "roster": {"path": str(ROSTER), "exists": ROSTER.exists()},
            "master_template": {"path": str(MASTER_TEMPLATE), "exists": MASTER_TEMPLATE.exists()},
            "media": cap,
            "ledger": ledger_stat,
            "experience": exp_stat,
            "last_results": last_results,
            "compact": compact,
            "state_file": str(STATE_FILE),
        }
    )

# --------------------------------------------------------------------------- #
# 工具：模块划分（改哪一块、验收哪一块）
# --------------------------------------------------------------------------- #
@TOOLS.tool(
    name="work_modules",
    description=(
        "【只读】看工具箱的模块划分：comms（通讯）/ office / memory（记忆）/ other（其他）"
        "各自的版本、负责范围、入口脚本、文档与工具清单。改需求前先确认归哪个模块——"
        "通讯只改 work_mods/comms.py，Office 只改 work_mods/office.py，"
        "台账与经验只改 work_mods/memory.py；memory 与两个业务模块互不 import。"
        "改完用 tools/regress.py --module comms|office|memory 单独验收。"
        "detail=false 只给模块名与工具数。"
    ),
)
def work_modules(detail: bool = True) -> str:
    try:
        from work_mods import describe
    except Exception as exc:  # noqa: BLE001
        return _dump({"ok": False, "error": f"模块清单读取失败：{type(exc).__name__}: {exc}"})
    mods = describe()
    if not detail:
        mods = [{"id": m["id"], "name": m["name"], "version": m["version"],
                 "tools": m["tools"]} for m in mods]
    return _dump({
        "ok": True,
        "count": len(mods),
        "total_tools": sum(m["tools"] for m in mods),
        "modules": mods,
        "layout": {
            "core": "mcp/work_core.py（注册器、路径、参数说明；不写业务工具）",
            "comms": "mcp/work_mods/comms.py + 入口 mcp/work_comms_server.py",
            "office": "mcp/work_mods/office.py + 入口 mcp/work_office_server.py",
            "memory": "mcp/work_mods/memory.py + 入口 mcp/work_memory_server.py",
            "other": "mcp/work_mods/other.py（状态/模块清单；公共底座，随各入口挂载）",
            "all": "mcp/work_toolkit_server.py（合体入口，只挂载不实现）",
        },
        "hint": ("comms / office / memory 互不 import，可以分别升级；other 是公共底座，"
                 "每个入口都会挂上它。只跑一个模块用对应入口起服务，全都要就用合体入口。"),
    })
