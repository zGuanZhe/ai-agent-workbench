---
description: 【独立 Office 入口 /office】只调 Office 模块（富文档/PPT合成、版本对比Diff、多Word合并、无模板跨表汇总、多预设排版、高清截图渲染、46个Excel工具、58个Word工具、COM），不加载微信模块
argument-hint: "例如：/office 读取 2026年11月汇总母表.xlsx 的 A1:G10；/office 对比模板和汇总母表的差异；/office 把 md 合成带目录的现代风格 Word"
---

<user_input>
$ARGUMENTS
</user_input>

---

按独立模块 `office`（`work-office` Skill）执行：
1. 若当前会话已挂载 `work_office` MCP，直接一步调用对应工具（如 `work_office_inspect`、`work_office_compose`、`work_office_diff`、`work_office_join_docx`、`work_office_consolidate`、`work_excel_mcp`、`work_word_mcp`）。
2. 或通过单模块 CLI 执行（仅加载 `office.py`）：
```bash
python "<WORK_ROOT>/scripts/work.py" office --run "<user_input 原话>"
```
