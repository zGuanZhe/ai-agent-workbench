#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_client.py — 用 MCP 官方客户端连本机 work-toolkit 服务，逐个调用工具做端到端验收。"""

import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters, stdio_client

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "mcp" / "work_toolkit_server.py"


def brief(value):
    try:
        data = json.loads(value)
    except Exception:
        return str(value)[:300]
    if isinstance(data, dict):
        keep = {}
        for k in ("ok", "status", "missing_departments", "missing_count", "submitted_count",
                  "merged_records_count", "anomalies_count", "output_docx", "output_excel",
                  "officecli_validation", "officecli_version", "ui_automation_ready",
                  "wechat_version", "reason", "error"):
            if k in data:
                keep[k] = data[k]
        return keep or list(data)[:8]
    return data if not isinstance(data, list) else f"list[{len(data)}]"


async def main():
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)], cwd=str(ROOT))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print("server:", init.server_info.name, init.server_info.version)

            tools = await session.list_tools()
            print(f"\n[TOOLS] {len(tools.tools)} 个")
            for t in tools.tools:
                print(f"  - {t.name}: {(t.description or '')[:60]}...")

            calls = [
                ("work_wechat_probe", {}),
                ("work_wechat_sync", {}),
                ("work_wechat_sessions", {"limit": 3}),
                ("work_wechat_messages", {"chat": "山大-在校生论坛2605群", "limit": 3}),
                ("work_wechat_search", {"keyword": "点名", "limit": 3}),
                ("work_wechat_files", {"mode": "scan", "since": "2026-11-01", "kinds": ["document"], "limit": 5}),
                ("work_status", {}),
                ("work_wechat_files", {"mode": "harvest"}),
                ("work_office_consolidate", {}),
                ("work_office_format_gbt9704", {
                    "doc_type": "通知",
                    "header_unit": "市数据局办公室",
                    "doc_number": "数政办发〔2026〕21号",
                    "output_path": str(ROOT / "workspace/03_汇总与成稿输出/关于报送专项经费摸底表的通知_国标排版.docx"),
                    "content_or_path": (
                        "# 关于报送2026年度专项经费摸底表的通知\n\n"
                        "各科室：\n\n"
                        "为做好2026年度专项经费预算编制工作，现就有关事项通知如下。\n\n"
                        "## 一、报送内容\n\n"
                        "请各科室据实填报《2026年11月专项经费摸底表》，确保数据真实准确。\n\n"
                        "## 二、报送时限\n\n"
                        "请于2026年11月15日17:00前将电子版报送至财务科。\n\n"
                        "## 三、工作要求\n\n"
                        "各科室要落实专人负责，严格审核把关，确保按时保质完成报送任务。\n\n"
                        "市数据局办公室\n\n"
                        "2026年11月9日\n"
                    ),
                }),
                ("work_officecli_run", {
                    "command": "validate",
                    "file": str(ROOT / "workspace/03_汇总与成稿输出/2026年11月汇总母表.xlsx"),
                }),
                ("work_office_inspect", {
                    "file": str(ROOT / "workspace/03_汇总与成稿输出/2026年11月汇总母表.xlsx"),
                    "mode": "text",
                    "max_lines": 8,
                }),
                ("work_backup_file", {
                    "file": str(ROOT / "workspace/03_汇总与成稿输出/关于报送专项经费摸底表的通知_国标排版.docx"),
                }),
                ("work_officecli_run", {
                    "command": "get",
                    "file": str(ROOT / "workspace/03_汇总与成稿输出/关于报送专项经费摸底表的通知_国标排版.docx"),
                    "args": ["/body/p[1]"],
                }),
                ("work_wechat_remind", {"dry_run": True}),
            ]

            for name, args in calls:
                print(f"\n=== {name} ===")
                try:
                    res = await session.call_tool(name, args)
                    text = ""
                    for block in res.content:
                        text += getattr(block, "text", "")
                    print("isError:", getattr(res, "is_error", getattr(res, "isError", "?")))
                    print(json.dumps(brief(text), ensure_ascii=False, indent=2))
                except Exception as exc:
                    print("EXCEPTION:", exc)


if __name__ == "__main__":
    asyncio.run(main())
