---
description: 【独立记忆入口 /memory】只调记忆模块（材料收发台账 work_ledger、任务经验库 work_exp、写前安全备份 work_backup_file），不加载微信与 Office 模块
argument-hint: "例如：/memory 看看台账里还有谁没交；/memory 列出任务经验；/memory 备份 2026年11月汇总母表.xlsx"
---

<user_input>
$ARGUMENTS
</user_input>

---

按独立模块 `memory`（`work-memory` Skill）执行：
1. 若当前会话已挂载 `work_memory` MCP，直接调用 `work_ledger`、`work_exp` 或 `work_backup_file`。
2. 或通过单模块 CLI 执行（仅加载 `memory.py`）：
```bash
python "<WORK_ROOT>/scripts/work.py" memory --run "<user_input 原话>"
```
