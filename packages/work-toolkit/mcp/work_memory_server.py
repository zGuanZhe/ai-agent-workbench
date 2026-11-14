#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""work_memory_server.py — 记忆模块的独立入口（stdin/stdout MCP 服务）。

只挂「其他 + 记忆」5 个工具：状态总览、模块清单、材料台账、任务经验、文件备份。
适合「只查台账 / 只写经验 / 只做备份」这类需求，工具列表短，也不加载微信与 Office。

    python mcp/work_memory_server.py         # 手工起一个 stdio 服务
    python scripts/work.py modules            # 看模块与工具归属

升级本模块只需改 `work_mods/memory.py`，验收跑
`python tools/regress.py --module memory`。
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

_MCP_DIR = Path(__file__).resolve().parent
if str(_MCP_DIR) not in sys.path:
    sys.path.insert(0, str(_MCP_DIR))

from work_core import annotate, make_server, register  # noqa: E402
import work_mods  # noqa: E402

VERSION = work_mods.memory.MODULE["version"]

server = make_server(
    name="work-memory",
    version=VERSION,
    instructions=(
        "记忆模块（本地读写）：材料台账（谁交了 / 谁没交 / 交了几版）、任务经验文档库"
        "（一个任务一份 md，人工按需整篇复制，不自动召回）、文件备份到 02_自动备份区。"
        "不做语义检索、不主动往对话里注入内容；通讯与 Office 相关需求不在本服务内。"
    ),
)

register(server, work_mods.boxes(("other", "memory")))
annotate(server)


def main() -> None:
    from work_core import STATE_DIR, _ensure_dirs

    _ensure_dirs()
    try:
        server.run("stdio")
    except KeyboardInterrupt:
        pass
    except Exception:  # pragma: no cover
        (STATE_DIR / "memory_server_error.log").write_text(traceback.format_exc(), encoding="utf-8")
        raise


if __name__ == "__main__":
    main()
