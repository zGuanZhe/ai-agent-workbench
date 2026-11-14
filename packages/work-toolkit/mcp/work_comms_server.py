#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""work_comms_server.py — 通讯模块的独立入口（stdin/stdout MCP 服务）。

只挂「其他 + 通讯」24 个工具，不加载任何 Office 工具。适合把通讯交给一个
独立的 MCP 连接（工具列表更短、升级通讯不影响 Office）。

    python mcp/work_comms_server.py          # 手工起一个 stdio 服务
    python scripts/work.py modules            # 看模块与工具归属

升级本模块只需改 `work_mods/comms.py`，验收跑
`python tools/regress.py --module comms`。
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

VERSION = work_mods.comms.MODULE["version"]

server = make_server(
    name="work-comms",
    version=VERSION,
    instructions=(
        "通讯模块（只读）：一次拿到微信 + 企业微信最近动态；查会话、读消息、搜历史、"
        "收材料归档、催办话术、图片解密。不发消息、不做桌面自动化发送。"
        "Office 相关需求不在本服务内，走 work_office_server 或合体入口。"
    ),
)

register(server, work_mods.boxes(("other", "comms")))
annotate(server)


def main() -> None:
    from work_core import STATE_DIR, _ensure_dirs

    _ensure_dirs()
    try:
        server.run("stdio")
    except KeyboardInterrupt:
        pass
    except Exception:  # pragma: no cover
        (STATE_DIR / "comms_server_error.log").write_text(traceback.format_exc(), encoding="utf-8")
        raise


if __name__ == "__main__":
    main()
