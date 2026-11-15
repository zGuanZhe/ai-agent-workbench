#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""work_toolkit_server.py — 可配置组合/全量入口（stdin/stdout MCP 服务）。

支持三种用法：
1. 默认全量挂载（other + comms + office + memory，共 41 个公共工具）：
       python mcp/work_toolkit_server.py
2. 按需组合挂载任意模块（命令行 `--modules` 或环境变量 `WORK_MODULES`）：
       python mcp/work_toolkit_server.py --modules comms,office
       python mcp/work_toolkit_server.py --modules office,memory
       python mcp/work_toolkit_server.py --modules office
3. 专用单模块独立入口：
       python mcp/work_comms_server.py     # 仅加载 other + comms（不触碰 Office / 记忆）
       python mcp/work_office_server.py    # 仅加载 other + office（不触碰微信 / 记忆）
       python mcp/work_memory_server.py    # 仅加载 other + memory（不触碰微信 / Office）
"""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

_MCP_DIR = Path(__file__).resolve().parent
if str(_MCP_DIR) not in sys.path:
    sys.path.insert(0, str(_MCP_DIR))

from work_core import (  # noqa: E402
    STATE_DIR, _ensure_dirs, annotate, make_server, register, tool_map,
)
import work_mods  # noqa: E402

VERSION = "0.4.0"


def _parse_cli_modules() -> str | None:
    env_spec = os.environ.get("WORK_MODULES", "").strip()
    for idx, arg in enumerate(sys.argv[1:], start=1):
        if arg.startswith("--modules="):
            return arg.split("=", 1)[1].strip()
        if arg == "--modules" and idx < len(sys.argv) - 1:
            return sys.argv[idx + 1].strip()
    return env_spec or None


_SPEC = _parse_cli_modules()
_BOXES = work_mods.boxes(_SPEC, include_other=True) if _SPEC else work_mods.boxes()
_LOADED = work_mods.loaded_ids()

server = make_server(
    name="work-toolkit" if not _SPEC else f"work-toolkit({'+'.join(_LOADED)})",
    version=VERSION,
    instructions=(
        f"办公工具箱（已挂载模块：{', '.join(_LOADED)}）：支持单独调用或跨模块组合调用"
        "通讯（微信 4.x / 企业微信）、通用 Office（OfficeCLI 主链 + 按需高级 Excel/Word + COM）"
        "及记忆（材料台账 / 任务经验 / 自动备份）。无需以 /work 作为唯一入口。"
    ),
)
register(server, _BOXES)
annotate(server)

# 兼容老调用面：外部脚本用 getattr(work_toolkit_server, "work_xxx")
globals().update(tool_map(_BOXES))
__all__ = sorted(n for n in globals() if n.startswith("work_"))


def __getattr__(name: str):
    if name.startswith("work_"):
        fn, _ = work_mods.load_tool(name)
        if callable(fn):
            globals()[name] = fn
            return fn
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def main() -> None:
    _ensure_dirs()
    try:
        server.run("stdio")
    except KeyboardInterrupt:
        pass
    except Exception:  # pragma: no cover
        (STATE_DIR / "server_error.log").write_text(traceback.format_exc(), encoding="utf-8")
        raise


if __name__ == "__main__":
    main()
