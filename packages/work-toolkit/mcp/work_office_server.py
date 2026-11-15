#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""work_office_server.py — Office 模块的独立入口（stdin/stdout MCP 服务）。

只挂「其他 + Office」的精简工具面，不加载任何微信 / 企业微信依赖。Office 对外只有一个
MCP 入口，日常走 inspect/compose/batch，专项能力按需走 advanced。

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
        "单一 Office MCP。优先用 work_office_inspect 读取/查 schema/渲染，"
        "work_office_compose 生成或重排文档，work_office_batch 做结构化批量修改并自动备份、校验。"
        "汇总核验、版本对比、Word 合并、模板套打、CSV 导入各有专用工具；"
        "work_office_advanced 只在常规 OfficeCLI 覆盖不了时调用，背后是 Excel 46 个和 Word 58 个专项工具。"
        "实时 Office/WPS、重算、刷新域和精确 PDF 走 work_office_com / work_office_to_pdf。"
        "通讯相关需求不在本服务内。"
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
