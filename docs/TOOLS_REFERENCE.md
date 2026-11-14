# 五大工具全量功能、场景示例与接口手册（Tools Reference）

---

## 一、`work-comms`（通讯模块 · 23 个 MCP 工具 + 协作 `wechat_chat`）

入口：`packages/work-toolkit/mcp/work_comms_server.py`  
适用对象：需要让 AI 读取本地微信 4.x / 企业微信消息、聚合通知、统计群接龙与群收件核对、解密图片/语音的 Windows 用户。

### 1.1 工具清单与典型用法

| 工具名 | 读写属性 | 具体能做什么 & 核心参数示例 |
| :--- | :--- | :--- |
| `work_comms_notices` | **只读首选** | **跨微信 + 企微最近通知聚合**：默认拉最近 `days=3` 天，每会话最多 8 条、总计 40 条；自动标注 `notice=true`（通知/死线/待办）并提取截止日期原文，把闲聊沉底。泛问“最近微信和企微有什么重要通知”只调这 1 个工具即可。 |
| `work_comms_overview` | 只读 | **双通道状态与会话总览**：同时返回微信（SQLCipher）与企微（wxSQLite3）两条通道的可用性及合并后的最新会话时间线。 |
| `work_wechat_probe` | 只读诊断 | 检查微信客户端版本、运行状态、DPAPI 密钥缓存、WAL 增量状态、明文收件目录与媒体密钥就绪情况（日常查消息会自动刷新缓存，无需预先调用）。 |
| `work_wechat_sync` | 只读缓存 | 手动触发内存提钥与解密缓存刷新（`force=true` 强制全量重解，平时自动增量更新）。 |
| `work_wechat_sessions` | 只读 | 列出最近会话（`limit`, `only_unread`），自动把群 ID/wxid 还原为真实群名/备注名，带未读数与最后摘要。 |
| `work_wechat_messages` | 只读 | 按群名/备注/wxid 读消息：`chat="某某群", limit=40, since="2026-11-10"`；图片/语音/文件自动压缩为可读摘要并附带 `media_ref`。 |
| `work_wechat_search` | 只读 | **多维历史检索**：支持 `keyword`（关键词）、`sender`（发言人备注/昵称，可跨群找某人说过的所有话）、`msg_type`（`text`/`file`/`link`/`image`/`voice`/`quote`/`solitaire`/`announcement`/`miniprogram`/`system`）、`chat`、`since`/`until`。自动解压 zstd 长文。 |
| `work_wechat_contacts` | 只读 | **通讯录与群关系查询**：`mode=contacts`（按姓名/微信号/拼音查人及所在群）、`mode=group`（查群主/公告/完整成员）、`mode=announcements`（跨全群汇总非空群公告）、`mode=common_groups`（查两人共同所在群）。 |
| `work_wechat_solitaire` | 只读 | **群接龙核对**：解析 `solitaire.db` 中的接龙活动与每一棒参与者（已还原备注），传入 `roster` 或 `check_missing=true` 自动算出未接龙名单和 `@催办` 话术。 |
| `work_wechat_favorites` | 只读 | **收藏夹检索**：读取 `favorite.db` 中的笔记/密码备忘（`fav_type=note`）、网页/网盘链接（`link`）、文件（`file`）、聊天记录（`record`）。 |
| `work_wechat_insights` | 只读/可选导出 | **群聊深度画像与待办提取**：统计 `days` 内发言活跃度 Top、消息类型占比、群内所有外链/网盘/文件与自动识别的 `action_items`（待办/死线），传 `output` 可直接存成 Markdown 报告。 |
| `work_wechat_files` | 扫描/写文件 | **收件与归档统一入口**：`mode="scan"`（只读统计近 N 天收到的文件数量与按月分布）、`mode="pull"`（按 `since`/`keyword`/`exts` 复制到 `01_微信收回原件/` 并按 SHA256 去重）、`mode="harvest"`（一键归档并联动《科室底册.xlsx》核对已交/未交名单）。 |
| `work_wechat_media` | 只读/导出 | **聊天图片解密**：把 `msg/attach` 下的 `.dat` 解密落盘；`prefer="thumb"` 出快缩略图，`prefer="orig"` 解原图（WXGF 需 `ffmpeg` 转 PNG）；可传 `ref`（32 位 hex）精准导出单张原图。 |
| `work_wechat_digest` | 写文件 | **聊天摘录成文**：按天分组把某群聊天导出为 `.docx`（仿宋三号 28 磅）或 `.md`，`include_media=true` 时自动解密图片并嵌入正文。 |
| `work_wechat_remind` | 写文件 | **生成催办话术**：把未交名单格式化为 `@张三 @李四 ...` 催办通知写入 `06_运行状态/待发催办通知.md` 供人工复制粘贴。 |
| `work_wecom_probe` | 只读诊断 | 探测企业微信进程是否在运行、`wxSQLite3` 库与密钥缓存状态。 |
| `work_wecom_sync` | 只读缓存 | 从运行中的 `WXWork.exe` 内存扫描并缓存企业微信数据库密钥（首次约 20–30 秒）。 |
| `work_wecom_sessions` | 只读 | 列出企业微信最近会话（群 `R:数字` / 单聊 `S:数字`）。 |
| `work_wecom_messages` | 只读 | 读取指定企微会话消息（自动解码 protobuf 正文与文件卡片）。 |
| `work_wecom_search` | 只读 | 在企业微信时间窗内按关键词搜索消息（建议缩窄 `since` 加速 protobuf 解码）。 |
| `work_wecom_contacts` | 只读 | 查询企业微信通讯录（姓名/拼音/手机/邮箱/职位）。 |
| `work_wecom_files` | 只读 | 列出企业微信收到的文件及是否已在本地落盘（`on_disk=true/false`）。 |
| `work_wecom_harvest` | 写文件 | 按时间窗/群/扩展名将企微已落盘文件去重归档至工作区（默认 `dry_run=true`）。 |

### 1.2 能力边界与协作说明
- **刻意不做**：不操控桌面窗口自动点击发送消息或切换群聊，杜绝微信 4.x 焦点抢占与账号风控；企业微信密钥仅存在于 `WXWork.exe` 运行内存中，客户端未启动且无缓存时无法首次提取。
- **多模态协作（`wechat_chat` MCP）**：当需要转写微信语音（SILK+Whisper）、用视觉能力直接解读聊天图片或分页阅读聊天附件里的长 PDF/Word 时，调用开源 `Haoea1024/wechat-chat-mcp`。

### 1.3 开源搭档 `wechat_chat` MCP 工具清单（`Haoea1024/wechat-chat-mcp`）

| 工具名 | 有什么用 |
| :--- | :--- |
| `project_status` / `list_projects` / `configure_project` / `set_project_chats` | 把多个关联群和私聊绑定成“项目”，跨会话持续追踪项目进展 |
| `discover_chats` | 按关键词模糊发现并定位相关群聊或联系人 |
| `find_private_chats` | **通过群成员反推私聊**：从已知项目群成员名单出发，找出你与他们的私聊窗口 |
| `get_project_brief` / `save_project_brief` / `read_project_updates` / `get_project_context` | 读取或保存项目简报、查看项目绑定会话的增量更新与上下文 |
| `search_messages` / `search_chat` / `get_messages` / `read_conversation` | 在本地索引库跨会话搜索、按消息 ID 拉取上下条或分页读长会话 |
| `list_attachments` | 列出项目或聊天中出现的所有附件（图片/语音/PDF/Office 文档）及其 `message_id` |
| `read_attachment` | **核心多模态读附件**：SILK+Whisper 语音转文字、图片/扫描件读取、PDF/Office 分页读取 |
| `backup_attachment` | 将指定 `message_id` 对应的附件一键备份到本地安全目录 |
| `remember_feedback` / `list_feedback` / `revoke_feedback` | 记录、查看或撤销对项目识别结果的纠偏反馈 |

---

## 二、`work-office`（文档与表格模块 · 18 主工具 + 104 开源子工具）

入口：`packages/work-toolkit/mcp/work_office_server.py`

### 2.1 主工具清单

| 工具名 | 核心功能与适用场景 |
| :--- | :--- |
| `work_office_engines` | **引擎真实体检**：实际加载并探测 `excel-mcp-server`（46 工具）、`Office-Word-MCP-Server`（58 工具）、`OfficeMCP`、`bin/officecli.exe` 与 `wechat-chat-mcp` 的真实就绪状态（绝不拿路径存在冒充可用）。 |
| `work_office_consolidate` | **跨表汇总与核验**：批量合并 `.xlsx`/`.csv`/`.tsv`/`.docx` 表格。支持指定母表模板，也支持 `auto_schema=true` 自动归纳表头；自动检查漏填、手机号/身份证格式错误与合计数勾稽差额，输出汇总表与《异常数据核验单》。 |
| `work_office_compose` | **现代富文档 & PPT 一键合成**：把 Markdown（多级标题、列表、代码块、GFM 表格、`$$数学公式$$`、`mermaid` 流程图）通过 OfficeCLI 原生元素编译为 `.docx` 或 `.pptx`（支持 `modern` / `report` / `academic` 预设与自动目录 `toc=true`）。 |
| `work_office_format_gbt9704` | **一键排版（4 套版式）**：支持 `gbt9704`（党政机关公文红头版式）、`modern`（现代商务深蓝表头）、`academic`（学术三线表）与 `memo`（简洁备忘录）。 |
| `work_office_diff` | **新旧版本对比**：支持 `.docx` 对比段落与表格单元格增删改，以及 `.xlsx` 按主键列 `key_column` 对齐对比数值增减与公式变动；可选输出 Markdown 差异报告。 |
| `work_office_join_docx` | **多份 Word 结构化拼接**：合并多份 `.docx` 为一份完整文档，保留原有样式与表格，可选插分页符与分节标题。 |
| `work_office_inspect` | **全维文档体检与视觉渲染**：支持 `mode=text`（可传 `cell_range="Sheet1!A1:F20"`）、`outline`（大纲）、`stats`（字数/字体分布）、`issues`（未求值公式/断页检查）、`forms`（表单）及 **`screenshot`（高清页面截图渲染 PNG）**。 |
| `work_office_create` | 新建空白 `.docx` / `.xlsx` / `.pptx` 骨架（`force=true` 才允许覆盖）。 |
| `work_office_batch` | 单次开关文件批量执行多条 OfficeCLI DOM 修改指令（`set`/`add`/`remove`/`query`），效率高且无中间态污染。 |
| `work_office_merge` | **模板单份/批量套打**：支持 `{{key}}` 占位符模板 + 单个 JSON 对象、JSON 数组或 `.csv`/`.xlsx` 数据源，一次生成 N 份合同/通知/证书。 |
| `work_office_import` | 将 CSV/TSV 数据导入 `.xlsx` 指定工作表，自动开启筛选与冻结首行。 |
| `work_office_to_pdf` | 基于本机 MS Office / WPS COM 将 Word / Excel / PPT 单文件或整目录精准转为 PDF。 |
| `work_office_com` | 直连本机 Office/WPS COM：`apps`、`active`、`info`、`export_pdf`、`run_python`（带超时保护与自动清理残留进程）。 |
| `work_office_watch` | 在本地启动实时浏览器预览（`start`/`stop`/`mark`/`goto`，默认端口 `26315`），配合批量写入边改边看。 |
| `work_officecli_run` | 直接执行单条 `officecli.exe` 原生命令（`validate`/`view`/`get`/`set` 等）。 |
| `work_office_schema` | 查询 OfficeCLI 元素与属性规范（写 `work_office_batch` 前可查精确属性名）。 |
| `work_excel_mcp` | **高级 Excel 46 工具桥**：`action=list\|schema\|call`，默认只读；覆盖 260+ 公式计算、公式溯源、目标求解（`goal_seek`）、数据透视表、切片器、图表、条件格式与 VBA 只读查看。写操作需显式 `allow_write=true` 并自动备份。 |
| `work_word_mcp` | **高级 Word 58 工具桥**：`action=list\|schema\|call`，默认只读放行查询类前缀；覆盖多文档合并、批注提取、脚注尾注、文档保护、按锚点插段插表与 XML 检视。写操作需显式 `allow_write=true` 并自动备份。 |

---

## 三、`work-memory`（双轨记忆与装配台 · 4 工具）+ 全局（2 工具）

入口：`packages/work-toolkit/mcp/work_memory_server.py`（合体入口：`work_toolkit_server.py`）

| 工具名 | 核心功能与典型用法 |
| :--- | :--- |
| `work_exp` | **零探索流程经验配方库**：<br>• `action="distill"`：从 `06_运行状态/tool_trace.jsonl` 自动抽提本轮实际工具调用序列、子工具名、变量占位符与失败避坑点，生成独立 Markdown SOP 并同步索引到 SQLite FTS5。<br>• `action="search"` / `"get"`：按关键词、模块或工具名检索历史经验与第八节「Agent 零探索直调配方」。<br>• `action="replay"`：传入 `vars_json` 预览（`dry_run=true`）或直接一键重放整套多工具工作流。<br>• `action="to_skill"`：将成熟的高频配方一键导出为独立的 Codex/WorkBuddy 子 Skill。<br>• `action="trace"`：查看近期真实调用轨迹。 |
| `work_mem` | **跨会话长期事实记忆（基于开源 Engram v3.7.1）**：<br>• `action="save"`：保存口径、偏好、决策、配置、避坑规律（支持 `topic_key` 同主题覆盖演进，不产生重复垃圾条）。<br>• `action="search"` / `"context"` / `"stats"`：FTS5 毫秒级检索或加载近期上下文。<br>• `action="export_obsidian"`：将 Engram 记忆与 `work_exp` 经验配方联合导出为可直接在 Obsidian 图谱中浏览的双向链接 Markdown 笔记库。 |
| `work_ledger` | **材料收发台账**：`summary`（总量与最近批次）、`departments`（按科室统计份数与重复版本）、`missing`（核对底册算未交名单及联络人电话）、`history`、`batches`、`state`、`record`。 |
| `work_backup_file` | 覆盖或批量编辑文件前一键带时间戳备份至 `02_自动备份区/`，绝不修改源文件。 |
| `work_status` | 查看全局所有引擎、目录文件数与最近任务摘要（支持 `reset_demo_fixtures=true` 生成演示数据）。 |
| `work_modules` | 列出 `comms` / `office` / `memory` / `other` 模块分工与工具列表。 |

---

## 四、`instruct-unrestricted`（7 大 IDE 规则编译器与破限控制中枢）

入口：`packages/instruct-unrestricted/cli.py` 与 `packages/instruct-unrestricted/manager.py`

### 4.1 纳管的 7 大客户端与落盘目标

| 平台 ID | 客户端 | 自动编译并维护的规则文件 |
| :--- | :--- | :--- |
| `cursor` | Cursor IDE | `.cursorrules`、`.cursor/rules/unrestricted.mdc`（含 YAML frontmatter） |
| `windsurf` | Windsurf (Codeium) | `.windsurfrules`、`.windsurf/rules/unrestricted.md` |
| `antigravity` | Antigravity / Gemini CLI | `.agents/rules/00-unrestricted-contract.md`、`GEMINI.md` |
| `claude` | Anthropic Claude Code | `CLAUDE.md` |
| `codex` | OpenAI Codex / GPT CLI | `contract.md` |
| `workbuddy` | 腾讯 WorkBuddy | `.workbuddy/rules/unrestricted.md`、`.workbuddy/rules.md` |
| `trae` | 字节 Trae / Trae CN | `.trae/rules.md`、`.trae/rules/project_rules.md` |

### 4.2 动态组装维度（4 角色 × 3 严格度）
- **角色（`--role`）**：`fullstack`（全栈架构）、`security_audit`（逆向与白帽安全审计，防误杀拦截）、`automation_data`（高吞吐自动化与数据工程）、`minimal_fix`（最小作用域极简补丁）。
- **严格度（`--strictness`）**：`standard`（消除伪能力拒绝 + 闭环自测）、`hyperdrive`（零说教/零客套/极限执行）、`strict_safe`（写前确切行号核验与快照优先）。
- **CLI 命令**：`options` / `status` / `apply` / `verify` / `reset`（均支持 `--json`）。
- **REST API（端口 `5055`）**：`GET /api/unrestricted/apps`、`GET /api/unrestricted/workspaces`、`POST /api/unrestricted/apply_app`、`POST /api/unrestricted/reset_app`、`POST /api/unrestricted/apply_all`、`POST /api/unrestricted/reset_all`。

---

## 五、`modeltrace-attribution`（黑盒 LLM 路由归因与测谎系统）

入口：`packages/modeltrace-attribution/cli.py` 与 `packages/modeltrace-attribution/controller/modeltrace_controller.py`

### 5.1 收录的 18 款官方基准模型（194,264+ 特征数字）
- **Claude 家族（10 款，360 份参考样本）**：`claude-haiku-4-5-20251001`、`claude-sonnet-4-6`、`claude-sonnet-5`、`claude-opus-4-6`、`claude-opus-4-7`、`claude-opus-4-8`、`claude-opus-5`、`claude-opus-5-5`、`claude-sonnet-5-5`、`claude-haiku-5-5`
- **GPT 家族（8 款，288 份参考样本）**：`gpt-5.4`、`gpt-5.5`、`gpt-5.6-sol`、`gpt-5.6-terra`、`gpt-5.6-luna`、`gpt-6-astra`、`gpt-6-sol`、`gpt-6-luna`

### 5.2 核心能力与接口
- **抗代码伪造拦截（`SpoofDetector`）**：实时检测等差递增/递减、固定周期循环、步长同余序列与退化占位，648 份真实样本 0.00% 误报，5 类合成伪造 100% 拦截。
- **CLI 命令**：
  - `bank-info [--json]`：查看基准库模型与校准参数。
  - `check-spoof --input <文本或文件> [--json]`：判断数字输出是否由脚本伪造。
  - `analyze --input <文本或文件> [--json]`：输出 Top 候选模型、家族概率（Claude vs GPT）与 Hellinger 距离相似度。
  - `test-api --base-url <URL> --api-key <KEY> --model <NAME> [--rounds 3] [--json]`：高并发向目标 API 发送中英文 250 整数采样挑战并自动归因与测谎。
- **REST API（端口 `5055`）**：`GET /api/modeltrace/info`、`GET /api/modeltrace/models`、`POST /api/modeltrace/spoof_check`、`POST /api/modeltrace/analyze`、`POST /api/modeltrace/test_api`。
