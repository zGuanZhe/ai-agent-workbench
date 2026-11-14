#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""work_office_server.py — Office 模块的独立入口（stdin/stdout MCP 服务）。

只挂「其他 + Office」17 个工具，不加载任何微信 / 企业微信依赖。适合把 Office 交给
一个独立的 MCP 连接（不需要微信客户端开着，也不受通讯模块改动影响）。

    python mcp/work_office_server.py         # 手工起一个 stdio 服务
    python scripts/work.py modules            # 看模块与工具归属

升级本模块只需改 `work_mods/office.py`，验收跑
`python tools/regress.py --module office`。
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

VERSION = work_mods.office.MODULE["version"]

server = make_server(
    name="work-office",
    version=VERSION,
    instructions=(
        "Office 模块：多科室跨表汇总与勾稽校验、OfficeCLI 检视/编辑/批量/套模板/导数据、"
        "本机 Office 与 WPS 的 COM 实时操控、导出 PDF，以及克隆的 excel-mcp（46 个）与 "
        "Word MCP（54 个）高级能力。写操作默认只读，需显式 allow_write 且先自动备份。"
        "通讯相关需求不在本服务内，走 work_comms_server 或合体入口。"
    ),
)

register(server, work_mods.boxes(("other", "office")))
annotate(server)


def main() -> None:
    from work_core import STATE_DIR, _ensure_dirs

    _ensure_dirs()
    try:
        server.run("stdio")
    except KeyboardInterrupt:
        pass
    except Exception:  # pragma: no cover
        (STATE_DIR / "office_server_error.log").write_text(traceback.format_exc(), encoding="utf-8")
        raise


if __name__ == "__main__":
    main()
