---
name: work-office
description: 【独立 Office 模块】通用与政务 Office 全栈处理工具箱（OfficeCLI 常驻管道 + 46个高级Excel工具 + 58个高级Word工具 + COM，可单独调用也可与通讯/记忆模块组合）。当用户要求读 Word/Excel/PPT 结构或单元格区域、渲染文档/幻灯片为高清截图、提取批注、将 Markdown+表格+Mermaid流程图+数学公式合成为 Word/PPT、对比两版 Word/Excel 差异(Diff)、合并多份 Word 文档、无模板或有模板跨表汇总校验、多预设排版(现代商务/学术三线表/备忘录/红头公文)、Excel 透视表/图表/公式追踪/条件格式、Word 脚注/文档保护/锚点替换、模板批量套打或转 PDF 时使用。
---

# `work-office` — 通用与政务 Office 模块独立与组合指南

本模块完全独立于微信/企微与记忆模块（底层仅按需加载 `work_mods/office.py`，内置 OfficeCLI 常驻 stdio 管道，单次响应 ~30-150ms，所有工具支持**工作区裸文件名自动定位**）。**无需通过 `/work`，可直接调用单个工具或组合流水线。**

## 1. 单独调用：直接调 `work_office` / `officecli` MCP 工具

| 场景 | 直接调用的工具与核心参数（支持裸文件名与相对路径） |
| :--- | :--- |
| **只读检视 / 区域读表 / 批注提取 / 高清截图 / 构建指南** | `work_office_inspect(file="...", mode="text\|outline\|stats\|issues\|comments\|screenshot\|html\|svg\|pdf\|skills", cell_range="A1:G10", grid="auto", skill="可选指南名")` |
| **Markdown + 表格 + Mermaid + 公式 → 原生 Word/PPT** | `work_office_compose(content_or_path="...", format="docx\|pptx", preset="modern\|academic\|memo\|gbt9704", toc=True, watermark="可选")` |
| **两版 Word 或两张 Excel 版本差异对比 (Diff)** | `work_office_diff(file_a="旧版.docx/.xlsx", file_b="新版.docx/.xlsx", sheet="可选", output="可选导出.md")` |
| **多份 Word 文档一键合并汇编** | `work_office_join_docx(source_dir="目录" 或 files="a.docx,b.docx", output="...", page_break=True, include_title=False)` |
| **无模板/有模板跨表合并(.xlsx/.csv/.docx)与核验** | `work_office_consolidate(source_dir="...", auto_schema=True, template_path="可选", output_path="...")` |
| **多预设专业排版（现代商务/学术三线表/备忘录/红头公文）** | `work_office_format_gbt9704(content_or_path="...", preset="modern\|academic\|memo\|gbt9704", header_unit="可选", doc_number="可选")` |
| **高级 Excel 46 工具（透视表/图表/公式追踪/条件格式）** | `work_excel_mcp(action="list", keyword="...")` 或 `work_excel_mcp(action="call", tool="describe_workbook\|read_cells\|create_pivot_table\|create_chart\|audit_sheet_formulas\|add_conditional_formatting\|goal_seek", args={"path":"...","sheet":"Sheet1"}, allow_write=False)` |
| **高级 Word 58 工具（批注/脚注/保护/锚点替换/表美化）** | `work_word_mcp(action="list", keyword="...")` 或 `work_word_mcp(action="call", tool="get_all_comments\|add_footnote_robust\|apply_table_alternating_rows\|replace_paragraph_block_below_header\|protect_document", args={"filename":"..."}, allow_write=False)` |
| **模板单份或 CSV/Excel 多行批量套打** | `work_office_merge(template="...", output="输出文件或目录", data="JSON对象/JSON数组/.csv/.xlsx路径")` |
| **批量 DOM 修改 / 实时网页预览 / 转 PDF / COM 操控** | `work_office_batch` / `work_office_watch` / `work_office_to_pdf` / `work_office_com` |

## 2. 单模块 CLI 入口（不经 `/work`）

- 短命令直调：`python -X utf8 "<WORK_ROOT>/scripts/work.py" office diff "{\"file_a\":\"模板.xlsx\",\"file_b\":\"母表.xlsx\"}"`
- 按需加载单工具：`python -X utf8 "<WORK_ROOT>/tools/run_tool.py" work_office_compose "{\"content_or_path\":\"...\"}"`

## 3. 跨模块组合调用（`office` + `memory` / `comms`）

- **写前备份 + 结构化修改 + 版本对比**：先调 `work_backup_file`（`memory`），再调 `work_office_batch` / `work_excel_mcp(allow_write=True)`（`office`），最后用 `work_office_diff` 对比备份件与修改件并输出差异报告。
