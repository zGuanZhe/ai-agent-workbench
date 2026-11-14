# Unrestricted Controller: 认知编译器与多工作区破限架构规范 (v2.0)

## 1. 核心定位
Unrestricted Controller 是面向全系主流 AI 编程环境（Google Antigravity, Anthropic Claude Code, OpenAI Codex, Cursor IDE, Windsurf, 腾讯 WorkBuddy, 字节 Trae）的运行时认知干预与沙箱管理中枢。其目标是破除模型在安全对齐中形成的非理性防卫、格式漂移与推测性代码破坏。

## 2. 升级后系统架构四层模型
```text
┌────────────────────────────────────────────────────────┐
│  REST API 控制层 (unrestricted_controller.py)          │
│  - 工作区感知 / 状态持久化 / 批量部署与回滚调度        │
└───────────────┬────────────────────────┬───────────────┘
                │                        │
       ┌────────▼────────┐      ┌────────▼────────┐
       │ 动态规则组装器  │      │  统一核心管理器 │
       │ (Dynamic Composer)     │ (UnrestrictedManager)
       │ 角色 x 严格度 x 平台    │  统一接口 / 极速调用  │
       └────────┬────────┘      └────────┬────────┘
                │                        │
┌───────────────▼────────────────────────▼───────────────┐
│  7 大 IDE 独立生命周期管理引擎 (engines/*)             │
│  - CursorEngine (.cursorrules & .cursor/rules/*.mdc)   │
│  - WindsurfEngine (.windsurfrules & .windsurf/rules/*) │
│  - AntigravityEngine (.agents/rules & GEMINI.md)       │
│  - ClaudeEngine (CLAUDE.md)                            │
│  - CodexEngine (config.toml & contract.md)             │
│  - WorkBuddyEngine (.workbuddy/rules/*)                │
│  - TraeEngine (.trae/rules/*)                          │
└───────────────┬────────────────────────┬───────────────┘
                │                        │
┌───────────────▼────────────────────────▼───────────────┐
│  受控物理目标 (用户工程工作区)                         │
│  - 原子快照与 SHA256 校验 (.*-instruct-state.json)     │
│  - 物理安全备份与无损回滚 (.*-backup)                   │
└────────────────────────────────────────────────────────┘
```

## 3. 部署生命周期与事务性保障
1. **探测阶段 (Detection)**:
   检测指定工作区目录，扫描已有规则文件与备份，比对 `.*-instruct-state.json`。
2. **完整性热感知 (SHA256 Integrity Hot-Perception)**:
   实时计算各受管文件的 SHA256 哈希（已免疫 Windows CRLF 与 POSIX LF 换行差异），即时报告 `intact`（完整）、`drifted`（篡改漂移）、`missing`（丢失缺失）、`unmanaged`（未受管外部残留）或 `clean`（纯净原生）。
3. **快照备份 (Atomic Backup)**:
   在对任何既有文件覆盖前，原样生成物理快照（如 `.cursor-backup`, `.windsurf-backup`），并将原始 SHA256 写入状态记录。
4. **注入部署 (Atomic Apply)**:
   原子化解包或动态编译组装规约集并写入目标位置，生成运行时状态文件。
5. **一键回滚 (Deterministic Reset)**:
   调用 `reset`，自动将备份文件恢复原状，物理删除由工具生成的受管文件，恢复纯净基线。
