#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dump_tools.py — 导出 tools/list 的 name/description/inputSchema，供控制台网页生成表单。

顺带标上每个工具属于哪个模块（comms / office / memory / other）。MCP 协议本身不带模块信息，
所以这里在子进程里另算一份映射：工具名 → 模块，升级某个模块时能一眼看出改哪边。
"""
import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters, stdio_client

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "mcp" / "work_toolkit_server.py"


def _module_map() -> dict:
    """工具名 → 模块 id。算不出来就算了，不让清单导出整体失败。"""
    try:
        os.environ.setdefault("WORK_TOOLKIT_LIGHT", "1")
        sys.path.insert(0, str(ROOT / "mcp"))
        sys.path.append(str(ROOT / "wechat"))
        import work_mods

        return {name: mod.TOOLS.module
                for mod in work_mods.MODULES.values()
                for name in mod.TOOLS.names()}
    except Exception:
        return {}


async def main():
    mods = _module_map()
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)], cwd=str(ROOT))
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tl = await s.list_tools()
            out = []
            for t in tl.tools:
                out.append({
                    "name": t.name,
                    "module": mods.get(t.name, ""),
                    "description": t.description or "",
                    "schema": getattr(t, "input_schema", None) or getattr(t, "inputSchema", None) or {},
                })
            print(json.dumps(out, ensure_ascii=False, indent=2))

asyncio.run(main())
