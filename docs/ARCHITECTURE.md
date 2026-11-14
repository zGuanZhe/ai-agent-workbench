# 架构解构指南（Architecture Deconstruction）

理解本仓库最重要的一点是：**这 5 个工具虽然都服务于 AI Agent 实战，但它们的本质完全不同**。强行塞进同一种抽象（例如全做成 MCP 或全做成 Prompt）会导致路由污染、冷启动缓慢或职责混乱。

---

## 一、三层正交架构模型

```text
┌──────────────────────────────────────────────────────────────────────────┐
│ 第 3 层：模型可信层 (Model Authenticity & Attribution)                   │
│   [5] modeltrace-attribution                                             │
│   职责：验证黑盒 API 背后的真实模型血统，拦截反代代码伪造与静默降智      │
└───────────────────────────────────┬──────────────────────────────────────┘
                                    │ 确认底层模型可信
                                    ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ 第 2 层：行为规约层 (IDE Rules & Cognitive Specification)                │
│   [4] instruct-unrestricted                                              │
│   职责：编译与托管 7 大 AI IDE 规则文件，消除无效拒答、固化工程事务协议  │
└───────────────────────────────────┬──────────────────────────────────────┘
                                    │ 塑造 Agent 决策纪律
                                    ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ 第 1 层：能力执行层 (Decoupled MCP Servers + Thin Skills)                │
│   [1] work-comms   (23 tools) ──► 微信 4.x / 企微 / 通知 / 收件取证      │
│   [2] work-office  (18 tools) ──► OfficeCLI / Excel-MCP / Word-MCP / COM │
│   [3] work-memory  ( 4 tools) ──► 轨迹提炼配方 / Engram / 材料台账 / 备份 │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 二、五工具本质拆解

### 1. `work-comms` — 本地即时通讯只读取证与多模态桥接
- **技术本质**：**Windows 进程内存提钥 + 数据库帧级只读解析 + 多模态协作 MCP**。
- **核心难点与解法**：
  - 个人微信 4.x 采用 SQLCipher 4 + XOR 掩码的 `Config.Cipher` 密钥结构，且最新消息暂存在 `-wal` 日志中。`wechat/wxkey.py` + `wechat/wxdecrypt.py` + `wechat/wxread.py` 实现了：**进程内存提钥 → Windows DPAPI 用户态加密缓存（`.keys.dpapi`）→ `(db_mtime, wal_mtime)` 追踪下的 4096B WAL 帧级轻量增量补丁**。
  - 图片附件采用 AES-128-ECB + XOR（`.dat`）及 WXGF 封装，`wechat/media.py` 支持直接解密缩略图或调用 `ffmpeg` 转出高清 PNG 原图。
  - 企业微信使用 wxSQLite3 页级 AES-128-CBC 加密（`wecom/wcread.py`）。
  - **开源协作边界**：复杂长历史检索、群成员反推、SILK+Whisper 语音转写与聊天附件 PDF 分页解读交由开源 `Haoea1024/wechat-chat-mcp`（`wechat_chat` MCP）承担；`work_comms` 专注跨微信/企微通知聚合（`work_comms_notices`）、收件归档核对底册与企微读取，避免重复造轮子。
- **安全边界**：严格**只读**源库，不引入任何桌面点击自动发消息逻辑，催办话术落盘供人工粘贴。

### 2. `work-office` — 跨引擎文档与表格处理中枢
- **技术本质**：**多后端异构调度器 + 只读/写前自动备份安全网关**。
- **内部引擎解构**：
  - **高速 OpenXML 原生引擎**：内置 `OfficeCLI v1.0.161`（`bin/officecli.exe`），支撑结构检视（`work_office_inspect`）、富文档合成（`work_office_compose`）、版面截图渲染与校验。
  - **复杂表格与文档子工具桥**：按需桥接开源 `excel-mcp-server`（46 个工具，260+ 公式/透视/图表/目标求解）与 `Office-Word-MCP-Server`（58 个工具，批注/脚注/XML/锚点编辑）。默认强制只读拦截写工具，显式传入 `allow_write=true` 时先备份再执行。
  - **本机实时环境桥**：Windows COM（`work_office_com`）操控已打开的 Word/Excel/PPT 或 WPS，支持带超时的 `run_python` 沙箱，超时后按 PID 树强杀残留进程防止僵尸实例。

### 3. `work-memory` — 双轨记忆与零探索经验复利系统
- **技术本质**：**“过程性流程配方（Procedural）+ 陈述性长期记忆（Semantic）”双轨分离架构**。
- **为什么不能只有一个记忆库？**
  - **通用事实记忆（`work_mem` / 开源 `Engram`）**：适合存口径、用户偏好、架构决策，支持 `topic_key` 同主题演进与 FTS5 检索，以及一键导出到 Obsidian 知识图谱。
  - **工具级经验配方（`work_exp`）**：每次调用 `comms` / `office` / `memory` 工具都会写入 `06_运行状态/tool_trace.jsonl`。当用户说“将本次工作整理为经验”，`work_exp(action="distill")` 直接从真实轨迹提取**精确工具名、子工具名、参数变量模板与避坑信息**，生成含「Agent 零探索直调配方」的 Markdown。下次复用时 Agent 直接 `work_exp(action="replay")` 或按配方直调，不再反复摸索 schema。
  - **材料台账（`work_ledger`）+ 写前备份（`work_backup_file`）**：以 SHA256 追踪多科室版本提交状态。

### 4. `instruct-unrestricted` — 多端 AI IDE 规则编译器与破限控制中枢
- **技术本质**：**面向 AI Agent 客户端的规则编译器与文件生命周期管理器（非 MCP、非 Skill）**。
- **与 Prompt 粘贴的本质区别**：
  - 针对 7 大客户端（Cursor、Windsurf、Claude Code、Codex、Antigravity、WorkBuddy、Trae）各自不同的规则落盘协议（如 `.cursor/rules/*.mdc` 的 YAML frontmatter、`AGENTS.md` / `GEMINI.md` / `hooks.json` / `.trae/rules/`），抽象出统一 `BaseIDEEngine`。
  - **动态规则组装（`DynamicRuleComposer`）**：按 `4 角色 × 3 严格度 × 7 平台` 编译规约，既提供降低无意义安全拒答的破限约束，也固化事务验证与沙箱护栏。
  - **5 态完整性热感知**：使用规范化换行符（Immune to CRLF/LF）计算 SHA256，毫秒识别 `intact / drifted / missing / unmanaged / clean`，写入前生成 `.backup` 快照，支持一键无损回滚。

### 5. `modeltrace-attribution` — 黑盒 LLM 指纹归因与测谎系统
- **技术本质**：**基于离散概率分布统计特征的非侵入式模型身份取证引擎**。
- **核心数学与对抗机制**：
  - **采样分布指纹**：让目标模型在低语义约束下生成 `[1, 355]` 区间整数序列；不同底座模型因词表、RLHF 与采样动态差异呈现稳定的频数分布特征。通过 **Hellinger 距离**、**干扰子空间正交投影**、**有序分块特征**与 **Logistic Softmax** 对 18 款官方基准模型（GPT / Claude 家族）进行归因。
  - **抗对抗代码伪造（`SpoofDetector`）**：拦截第三方中转代理通过 Python 脚本伪造数字序列（等差数列、周期循环、步长同余、退化占位等 5 类攻击），在 648 份真实样本上实现 **0.00% 误报率**与 **100% 合成攻击拦截率**。
  - **并发探针（`ConcurrentCollector`）**：多线程并发发送 3–5 组探测提示词，自动识别 OpenAI `/chat/completions` 与 Anthropic `/messages` 协议。

---

## 三、跨模块组合实战范式

1. **接入前验真（工具 5 → 工具 4）**：用 `modeltrace-attribution` 对新接入的 API 中转节点做 `test-api` 测谎，确认无掺水降智后，再用 `instruct-unrestricted` 为当前 IDE 注入对应工作模式规则。
2. **微信收件 → 台账 → 跨表汇总 → 经验固化（工具 1 + 工具 3 + 工具 2）**：
   - `work_wechat_files(mode="harvest")` 归档附件并核对花名册；
   - `work_ledger(action="record")` 更新材料版本流水；
   - `work_office_consolidate(auto_schema=True)` 跨表汇总；
   - `work_exp(action="distill")` 将整条流程提炼为可参数化重放的零探索 SOP。
