#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_nodes.py — 校验自定义节点配置，改完 custom_nodes.json 跑一遍再构建。

    python tools/check_nodes.py          # 检查
    python tools/check_nodes.py --list   # 顺便列出所有节点（内置 + 自定义）

查的是真正会让页面出问题的事：id 撞车、工具名写错、字段缺失、列名不存在。
工具名写错最坑——页面上按钮点下去才报「工具未登记」，构建时却一切正常。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
CUSTOM = WEB / "custom_nodes.json"
TOOLS = WEB / "tools.json"
TPL = WEB / "_console_template.html"

COLS = {"comms": "通讯", "office": "Office", "other": "其他"}
MODES = {"data", "tool", "search"}
REQUIRED = ("id", "name")
# 内置节点的 id 从模板里粗抓：内置节点全都是 `id: "xxx"` 这一种写法，
# 分组 id 也是同样写法，一并当保留字反而更安全（不会撞上分组）。
_ID = re.compile(r'^\s*id:\s*"([A-Za-z0-9_\-]+)"', re.M)


def builtin_ids() -> set:
    if not TPL.is_file():
        return set()
    return set(_ID.findall(TPL.read_text(encoding="utf-8")))


def tool_names() -> set:
    if not TOOLS.is_file():
        return set()
    try:
        return {t["name"] for t in json.loads(TOOLS.read_text(encoding="utf-8"))}
    except Exception:
        return set()


def check() -> tuple[list[str], list[str], list[str]]:
    errs: list[str] = []
    warns: list[str] = []
    notes: list[str] = []

    if not CUSTOM.is_file():
        return errs, warns, ["没有 web/custom_nodes.json，跳过（只用内置节点）"]

    try:
        raw = json.loads(CUSTOM.read_text(encoding="utf-8"))
    except Exception as e:
        return [f"custom_nodes.json 不是合法 JSON：{e}"], warns, notes

    groups = raw.get("groups")
    if groups is None:
        return errs, warns, ["groups 为空，没有自定义节点"]
    if not isinstance(groups, list):
        return ["groups 必须是数组"], warns, notes

    seen_g: set = set()
    seen_n: set = set()
    builtin = builtin_ids()
    tools = tool_names()

    for i, g in enumerate(groups):
        at = f"groups[{i}]"
        if not isinstance(g, dict):
            errs.append(f"{at}: 必须是对象")
            continue
        gid = str(g.get("id") or "").strip()
        if not gid:
            errs.append(f"{at}: 缺 id")
            continue
        if not re.fullmatch(r"[A-Za-z0-9_\-]+", gid):
            errs.append(f"{at}: id「{gid}」只能用英文、数字、下划线、连字符")
        if gid in seen_g:
            errs.append(f"{at}: 分组 id「{gid}」重复")
        if gid in builtin:
            warns.append(f"{at}: 分组 id「{gid}」与内置节点/分组同名，可能互相覆盖")
        seen_g.add(gid)

        col = str(g.get("col") or "").strip()
        if col not in COLS:
            errs.append(f"{at}: col「{col}」不存在，可选 {', '.join(COLS)}")
        if not g.get("name"):
            warns.append(f"{at}: 没有 name，页面上会显示 id")

        nodes = g.get("nodes")
        if not isinstance(nodes, list) or not nodes:
            errs.append(f"{at}: nodes 必须是非空数组")
            continue

        for j, n in enumerate(nodes):
            nat = f"{at}.nodes[{j}]"
            if not isinstance(n, dict):
                errs.append(f"{nat}: 必须是对象")
                continue
            for k in REQUIRED:
                if not str(n.get(k) or "").strip():
                    errs.append(f"{nat}: 缺 {k}")
            nid = str(n.get("id") or "").strip()
            if nid:
                if not re.fullmatch(r"[A-Za-z0-9_\-]+", nid):
                    errs.append(f"{nat}: id「{nid}」只能用英文、数字、下划线、连字符")
                if nid in seen_n:
                    errs.append(f"{nat}: 节点 id「{nid}」重复")
                if nid in builtin:
                    errs.append(f"{nat}: 节点 id「{nid}」与内置节点冲突，换一个")
                seen_n.add(nid)
                notes.append(f"{nid}")

            mode = n.get("mode") or "tool"
            if mode not in MODES:
                errs.append(f"{nat}: mode「{mode}」不支持，可选 {', '.join(sorted(MODES))}")

            tool = n.get("tool")
            if tool is not None and tool != "":
                if not isinstance(tool, str):
                    errs.append(f"{nat}: tool 必须是字符串或 null")
                elif tool not in tools:
                    errs.append(f"{nat}: 工具「{tool}」不在 web/tools.json 里"
                                f"（先 python mcp/dump_tools.py > web/tools.json）")

            if mode == "tool" and not tool:
                errs.append(f"{nat}: mode=tool 但没有 tool，点「直接调用」会没反应")
            if not n.get("prompt") and not n.get("brief"):
                warns.append(f"{nat}: 既没有 prompt 也没有 brief，「复制提示词」会是空的")
            if not n.get("brief"):
                warns.append(f"{nat}: 缺 brief，节点树上只有名字")
    return errs, warns, notes


def main() -> int:
    errs, warns, notes = check()
    listing = "--list" in sys.argv

    if listing and CUSTOM.is_file():
        try:
            raw = json.loads(CUSTOM.read_text(encoding="utf-8"))
            for g in raw.get("groups") or []:
                print(f"[{g.get('col')}] {g.get('name')} ({g.get('id')})")
                for n in g.get("nodes") or []:
                    print(f"    - {n.get('name')} ({n.get('id')}) "
                          f"tool={n.get('tool')} mode={n.get('mode') or 'tool'}")
        except Exception:
            pass
        print()

    for w in warns:
        print(f"提示  {w}")
    for e in errs:
        print(f"错误  {e}")

    if errs:
        print(f"\n{len(errs)} 个错误，{len(warns)} 个提示。改完再构建。")
        return 1
    if not notes:
        print("通过：没有自定义节点（只用内置的）。")
    else:
        print(f"通过：{len(notes)} 个自定义节点，{len(warns)} 个提示。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
