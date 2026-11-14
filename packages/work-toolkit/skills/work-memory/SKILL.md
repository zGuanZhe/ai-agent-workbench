---
name: work-memory
description: 【独立记忆与经验配方模块】流程经验自动提炼与零探索重放（work_exp）、开源 Engram 跨会话通用记忆与 Obsidian 知识图谱（work_mem）、材料收发可追溯台账（work_ledger）及写前自动备份（work_backup_file）。当用户说「将本次工作整理为经验 / 保存为SOP」、按已有经验零探索直调、跨会话存取口径/偏好/决策、导出 Obsidian 知识图谱、核对材料台账或备份文件时使用。
---

# `work-memory` — 记忆模块（工具级经验配方 + Engram 通用记忆 + 材料台账 + 安全备份）

本模块采用**「流程配方（`work_exp`） + 通用事实记忆（`work_mem` / Engram） + 材料台账（`work_ledger`）」三层协同架构**，与 `work-comms` / `work-office` 解耦且可任意组合调用，**不以 `/work` 为唯一入口**。

## 1. 核心场景直调表（拒绝泛泛而谈，0 探索直达）

| 场景 | 直接调用的 MCP 工具与精确参数 |
| :--- | :--- |
| **一句话「将本次工作整理为经验 / 保存为SOP」** | `work_exp(action="distill", title="可选标题", summary="可选一句话总结")` —— **自动从 `06_运行状态/tool_trace.jsonl` 提取本轮实际调用过的 `comms`/`office`/`memory` 工具名、子工具名、参数变量模板与报错避坑**，生成含「精确工具清单」与「Agent 零探索直调配方」的 Markdown，并同步写入 Engram 记忆库 |
| **按关键词 / 模块 / 工具名查找已有经验配方** | `work_exp(action="search", query="关键词", module="office\|comms\|memory", tool="可选工具名")` |
| **读取某篇经验全文与「Agent 零探索直调指令」** | `work_exp(action="get", doc_id="文档id或标题子串")` |
| **按经验文档一键参数化重放执行** | `work_exp(action="replay", doc_id="文档id", dry_run=False, vars_json='{"input_file":"新文件.xlsx"}')`（`dry_run=True` 仅预览代入变量后的工具调用序列） |
| **将高频固定流程一键导出为独立子 Skill** | `work_exp(action="to_skill", doc_id="文档id", skill_name="work-sop-xxx", install_codex=True)` |
| **查看近期真实工具调用轨迹** | `work_exp(action="trace", limit=20)` |
| **保存跨会话通用记忆（口径/偏好/决策/规律）** | `work_mem(action="save", title="标题", content="内容", type="decision\|pattern\|learning\|config\|bugfix", topic_key="可选唯一主题键")`（基于开源 `Gentleman-Programming/engram` v3.7.1，同 `topic_key` 自动版本演进覆盖） |
| **检索跨会话通用记忆 / 查看近期记忆上下文** | `work_mem(action="search", query="关键词")` / `work_mem(action="context")` |
| **联合导出 Engram 记忆 + 任务经验到 Obsidian 图谱** | `work_mem(action="export_obsidian", vault_path="可选Obsidian库路径")` |
| **查材料台账（汇总 / 改了几版 / 未交名单与电话）** | `work_ledger(action="summary\|departments\|missing\|history\|record")` |
| **写前时间戳安全备份（复制到 `02_自动备份区/`）** | `work_backup_file(file="文件路径或工作区裸文件名")` |

## 2. 使用经验文档时的「零探索直调纪律」

1. **生成经验时绝不泛泛而谈**：用户说「将本次工作整理为经验」时，直接调 `work_exp(action="distill")`，让系统从真实调用轨迹生成带 `work_office_*` / `work_excel_mcp:<子工具>` / `work_wechat_*` 精确签名与参数模板的配方文档。
2. **复用经验时跳过探索**：当拿到经验文档或第八节「Agent 零探索直调配方」时，**禁止**先调 `action="list"`、`work_office_schema`、`work_status` 或 `probe`，直接按配方中的 `mcp_server`、`tool` 与 `args` 顺序执行，或直接调用 `work_exp(action="replay", doc_id="...", dry_run=False)`。
