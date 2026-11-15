<div align="center">

# AI Agent Workbench

**五位一体 AI 工程与治理套件：能力执行 × 行为规约 × 模型测谎**

[![License: MIT](https://img.shields.io/badge/License-MIT-black.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](docs/SETUP_GUIDE.md)
[![MCP Tools: 41](https://img.shields.io/badge/MCP%20Tools-41%2B20-111827.svg)](#五详细功能清单与实现原理)
[![IDEs: 7](https://img.shields.io/badge/IDE%20Targets-7-111827.svg)](#4-instruct-unrestricted-破限控制中枢与-ide-规则编译器)
[![Models: 18](https://img.shields.io/badge/Benchmark%20Models-18-111827.svg)](#5-modeltrace-attribution-黑盒模型归因与测谎系统)

[架构解构](docs/ARCHITECTURE.md) · [配置教程](docs/SETUP_GUIDE.md) · [接口手册](docs/TOOLS_REFERENCE.md) · [详细功能与实现](#五详细功能清单与实现原理)

</div>

---

## 一、五工具核心解构

本项目把 5 个本质完全不同、但共同决定 AI Agent 实战上限的工具，按**三个正交层级**解构整合：

| 层级 | 模块 | 本质定位 | 核心能力一句话 | 形态 |
| :--- | :--- | :--- | :--- | :--- |
| **能力执行层** | **1. `work-comms`** | 微信 4.x / 企微只读取证引擎 | 内存提钥 + WAL 实时增量 + 图片解密 + 收件核对 + 语音/PDF 多模态协作 | MCP (23+20) · Skill · CLI |
| **能力执行层** | **2. `work-office`** | 跨引擎文档表格处理中枢 | 12 个公共工具 + 104 个高级子工具：汇总核验、生成与重排、版本对比、套打、精准 PDF | MCP (12+104) · Skill · CLI |
| **能力执行层** | **3. `work-memory`** | 双轨记忆与装配台 | 轨迹提炼「零探索 SOP」+ Engram 长期记忆 + 台账 + 网页可视化装配台 | MCP (4+2) · Skill · Web `:8765` |
| **行为规约层** | **4. `instruct-unrestricted`** | 多端 AI IDE 规则编译器 | 原生编译/托管 7 大 IDE 规则，动态组装角色×严格度，消除无效拒答 | Python SDK · CLI · API `:5055` |
| **模型可信层** | **5. `modeltrace-attribution`** | 黑盒 LLM 指纹与测谎系统 | 18 款官方模型数学指纹归因 + 拦截反代脚本造假（0% 误报 / 100% 拦截） | Python SDK · CLI · API `:5055` |

```text
[层 3 · 模型可信] modeltrace-attribution  ──► 验证 API 背后是不是真模型、有没有掺水造假
        │
        ▼
[层 2 · 行为规约] instruct-unrestricted   ──► 编译 7 大 IDE 底层规则，固化工程纪律与破限协议
        │
        ▼
[层 1 · 能力执行] work-comms / work-office / work-memory (+ Codex 风格装配台 :8765)
```

---

## 二、仓库结构

```text
ai-agent-workbench/
├── packages/
│   ├── work-toolkit/              # 工具 1-3：comms / office / memory + Skills + 装配控制台
│   │   ├── mcp/                   # 4 个 MCP 服务：work_{comms,office,memory,toolkit}_server.py
│   │   ├── wechat/ & wecom/       # 微信 4.x (SQLCipher+WAL+DPAPI+媒体) & 企微 (wxSQLite3)
│   │   ├── experience/            # work_exp 轨迹提炼与参数化重放
│   │   ├── skills/                # work-comms / work-office / work-memory / work-office-toolkit
│   │   ├── web/                   # Codex 风格可视化装配台 (port 8765)
│   │   ├── mcp_servers/           # 内置 4 大开源引擎源码 (excel/word/officemcp/wechat-chat)
│   │   └── bin/                   # 内置 OfficeCLI v1.0.161 + Engram v3.7.1
│   ├── instruct-unrestricted/     # 工具 4：7 大 IDE 引擎 + 动态组装器 + 编译器 + CLI
│   └── modeltrace-attribution/    # 工具 5：18 模型基准库 + SpoofDetector + 并发探针 + CLI
├── services/core_api_server.py    # 工具 4 + 工具 5 联合 REST 微服务 (port 5055)
├── scripts/
│   ├── bootstrap.py               # 一键全模块校验
│   ├── requirements-toolkit.txt   # 工具 1-3 依赖
│   └── requirements-core.txt      # 工具 4-5 依赖
└── docs/                          # ARCHITECTURE / SETUP_GUIDE / TOOLS_REFERENCE
```

---

## 三、5 分钟快速开始

```powershell
# 1. 克隆并安装依赖（按需选装）
git clone https://github.com/zGuanZhe/ai-agent-workbench.git
cd ai-agent-workbench
# 自动安装全部工具依赖 + 初始化 wechat-chat-mcp 独立 .venv + 跑通 5 模块自检
python -X utf8 scripts/bootstrap.py --install
```

<details>
<summary><b>展开各模块常用启动命令</b></summary>

```powershell
# 工具 1-3：同步 Skills 到 WorkBuddy/Codex & 打开可视化装配控制台 (http://127.0.0.1:8765)
python -X utf8 packages/work-toolkit/scripts/install_skills.py
python -X utf8 packages/work-toolkit/tools/register_mcp.py
python -X utf8 packages/work-toolkit/web/build.py
python -X utf8 packages/work-toolkit/web/serve.py

# 工具 4：为当前项目检测/注入/回滚 IDE 规约
python -X utf8 packages/instruct-unrestricted/cli.py status --workspace "D:\your\project"
python -X utf8 packages/instruct-unrestricted/cli.py apply --app cursor --role security_audit --strictness hyperdrive --workspace "D:\your\project"
python -X utf8 packages/instruct-unrestricted/cli.py reset --app cursor --workspace "D:\your\project"

# 工具 5：在线检测第三方 API 是否掺水或脚本造假
python -X utf8 packages/modeltrace-attribution/cli.py bank-info
python -X utf8 packages/modeltrace-attribution/cli.py test-api --base-url "https://api.example.com/v1" --api-key "sk-..." --model "gpt-4o"

# 工具 4+5 联合 REST 微服务 (http://127.0.0.1:5055)
python -X utf8 services/core_api_server.py
```

</details>

---

## 四、进阶文档导航

| 文档 | 内容说明 |
| :--- | :--- |
| [架构解构指南 (`docs/ARCHITECTURE.md`)](docs/ARCHITECTURE.md) | 深入剖析三层五工具本质差异、安全边界与跨模块组合范式 |
| [详细配置教程 (`docs/SETUP_GUIDE.md`)](docs/SETUP_GUIDE.md) | Codex `config.toml`、WorkBuddy、第三方增强组件与环境变量完整配置 |
| [全量工具与接口手册 (`docs/TOOLS_REFERENCE.md`)](docs/TOOLS_REFERENCE.md) | 41 个公共 MCP 工具 + 20 `wechat_chat` 工具 + CLI + REST API 速查表 |

---

## 五、详细功能清单与实现原理

以下按工具逐一列出**每个独立工具的作用**以及**底层技术实现**，不合并、不省略。

---

### 1. `work-comms` — 微信 4.x / 企业微信感知与取证模块（23 + 20 工具）

#### 实现原理
- **个人微信 4.x（`wechat/`）**：放弃失效的 `wxauto` 3.x 桌面自动化与下架的 `chatlog`，直读本地库。`wxkey.py` 从 `Weixin.exe` 内存提取 `Config.Cipher` 经 XOR 掩码的数据库密钥，并通过 Windows `CryptProtectData`（DPAPI 当前用户态）加密缓存于 `.keys.dpapi`；`wxdecrypt.py` 与 `wxread.py` 按 `(db_mtime, wal_mtime)` 做 4096B `-wal` 帧级增量补丁解密到只读副本，支持 zstd 长文解压。
- **媒体解密（`wechat/media.py`）**：从 `kvcomm` 提取 UIN 派生密钥，按 AES-128-ECB + 逐字节 XOR 解密 `msg/attach/**/*.dat`，缩略图直出 JPEG，WXGF 原图调用 `ffmpeg` 转 PNG。
- **企业微信（`wecom/`）**：读取内存中的 wxSQLite3 页级 AES-128-CBC 密钥（无 HMAC/保留区）并缓存，解码 protobuf 消息体。
- **双 MCP 协同**：`work_comms` 聚焦国内业务闭环（跨通道通知、收件归档核对底册、企微），重度多模态任务（SILK+Whisper 语音转写、PDF 分页阅读、群成员反推私聊）交由开源 `Haoea1024/wechat-chat-mcp`（`wechat_chat`）。
- **只读边界**：全程不写源库、不控桌面窗口自动发送消息，催办落盘供人工复制粘贴。

#### 逐工具功能说明

**跨通道聚合（2 个）**
1. **`work_comms_notices`**：一次调用同时扫个人微信 + 企业微信近 N 天消息，自动标注“通知/截止/待办”并置顶排序，闲聊沉底，最快掌握全盘通知。
2. **`work_comms_overview`**：汇总微信与企微双通道可用状态，并把两边最新会话合成一条按时间排序的总览线。

**个人微信 4.x（13 个）**
3. **`work_wechat_probe`**：排查环境故障用，报告微信版本、运行态、DPAPI 密钥缓存、WAL 增量、收件目录和媒体密钥就绪度。
4. **`work_wechat_sync`**：手动触发内存提钥并刷新只读解密缓存（支持 `force=true` 全量重解；日常查消息会自动增量刷新）。
5. **`work_wechat_sessions`**：列出微信最近会话（群名/备注名已还原、未读数、最后一条摘要与时间）。
6. **`work_wechat_messages`**：按群名/备注/wxid 读指定会话聊天记录，支持 `since`/`until` 时间筛选，`wxid` 自动映射人名。
7. **`work_wechat_search`**：全库多维检索，支持**关键词 + 发言人（跨群查某人说过什么）+ 消息类型**（`text`/`file`/`link`/`image`/`voice`/`quote`/`solitaire`/`announcement`/`miniprogram`/`system`）组合过滤。
8. **`work_wechat_contacts`**：查通讯录与群关系——支持搜联系人所在全部群（`contacts`）、查指定群群主/公告/完整成员（`group`）、跨全群提取群公告（`announcements`）、查两人共同群（`common_groups`）。
9. **`work_wechat_solitaire`**：解析群接龙每一棒内容与参与者，对比《科室底册》或群成员自动算出**未接龙名单**与 `@催办` 话术。
10. **`work_wechat_favorites`**：检索微信收藏夹里的笔记/密码备忘（`note`）、网页/网盘链接（`link`）、文件（`file`）和合并聊天记录（`record`）。
11. **`work_wechat_insights`**：生成群聊统计画像（发言排行、消息分布）、提取全部外链/网盘/群内文件与自动提炼待办事项，支持导出 `.md`。
12. **`work_wechat_files`**：收件管理统一入口——`mode="scan"` 只读统计收件；`mode="pull"` 按条件筛选复制并 SHA256 去重；`mode="harvest"` 归档并联动底册列出已交/未交名单。
13. **`work_wechat_media`**：解密 `msg/attach` 下的 `.dat` 聊天图片；`prefer="thumb"` 快出缩略图，`prefer="orig"` 还原 WXGF 高清原图（支持按 `ref` 导出单张）。
14. **`work_wechat_digest`**：把指定群一段时间的消息按天分组导出为 `.docx`（仿宋三号 28 磅）或 `.md`，`include_media=true` 时嵌入解密后的图片。
15. **`work_wechat_remind`**：根据未交名单生成标准化 `@催办` 话术写入 `06_运行状态/待发催办通知.md` 供人工粘贴发送。

**企业微信（8 个）**
16. **`work_wecom_probe`**：探测 `WXWork.exe` 运行状态、加密库数量与密钥缓存状态。
17. **`work_wecom_sync`**：从运行中的企业微信进程内存扫描提钥并刷新明文缓存。
18. **`work_wecom_sessions`**：列出企业微信最近群聊（`R:`）与单聊（`S:`）会话及未读数。
19. **`work_wecom_messages`**：读取指定企业微信会话消息（自动解码 protobuf 正文与文件卡片）。
20. **`work_wecom_search`**：按时间窗和关键词在企业微信历史消息中搜索“谁提过某件事”。
21. **`work_wecom_contacts`**：查询企业微信通讯录（姓名、拼音、部门职位、手机号、邮箱）。
22. **`work_wecom_files`**：列出企业微信收到的文件清单，并标明是否已下载落盘（`on_disk`）。
23. **`work_wecom_harvest`**：把企业微信已落盘文件按时间/群/后缀筛选，去重归档至工作区。

**开源搭档 `wechat_chat` MCP（21 个）**
- **`project_status` / `list_projects` / `configure_project` / `set_project_chats`**：把多个群和私聊绑定成“项目”持续追踪进展。
- **`discover_chats`**：按关键词模糊定位并发现相关群聊或联系人。
- **`find_private_chats`**：**通过群成员反推私聊**，找出你与项目群成员之间的单独私聊。
- **`get_project_brief` / `save_project_brief` / `read_project_updates` / `get_project_context`**：读写项目简报、获取增量消息与上下文。
- **`search_messages` / `search_chat` / `get_messages` / `read_conversation`**：在索引库跨会话深搜、按消息 ID 拉上下文与分页读长对话。
- **`list_attachments`**：列出聊天或项目中的全部附件（语音/图片/PDF/Office）及 `message_id`。
- **`read_attachment`**：**核心多模态工具**——SILK+Whisper 语音转文字、图片识读、聊天附件 PDF/Office 分页阅读。
- **`backup_attachment`**：把指定 `message_id` 的附件备份到本地安全目录。
- **`remember_feedback` / `list_feedback` / `revoke_feedback`**：记录、查看或撤销对项目识别结果的纠偏反馈。

---

### 2. `work-office` — 跨引擎文档与表格处理中枢（12 个公共工具 + 104 个高级子工具）

#### 实现原理
- **一个入口，按任务分流（`mcp/work_mods/office.py`）**：常规读取、创建、重排和结构化编辑走内置 `OfficeCLI v1.0.161` 常驻管道；透视表、公式追踪、脚注、保护、XML 和锚点编辑按需进入 `excel-mcp-server` / `Office-Word-MCP-Server`；精确 PDF、重算、刷新域和当前打开文档控制走 Windows COM。
- **一个 Skill + 一个 MCP**：`skills/work-office` 负责 12 个公共工具的路由，`mcp/work_office_server.py` 只公开这 12 个 Office 工具加 2 个模块底座工具。旧的 create/schema/CLI/watch/Excel/Word 直接入口保留实现但不再暴露，避免 Agent 在重叠工具之间反复试探。
- **写前安全**：`work_office_batch` 对已有文件自动备份并 validate；`work_office_advanced` 默认只读，显式 `allow_write=true` 时才写且先备份。OfficeCLI 本身没有 PDF 导出能力，PDF 只走 `work_office_to_pdf` / COM。

#### 逐工具功能说明
| 工具名 | 具体作用 |
| :--- | :--- |
| `work_office_inspect` | 只读看文本、Excel 区域、结构大纲、字数字体、格式/公式问题、批注、表单、DOM 路径，并可截图或导出 HTML/SVG；也可查 OfficeCLI schema 和内置构建指南。 |
| `work_office_compose` | 把 Markdown（含表格、Mermaid、LaTeX）生成 DOCX/PPTX，或把现有 DOCX 按现代、学术、备忘录、GB/T 9704 预设重排。 |
| `work_office_batch` | 通用编辑：一次提交多条添加、修改、删除、移动操作；支持创建后立即写入，自动备份和校验，适合长文档和复杂表格的多处修改。 |
| `work_office_consolidate` | 把多份 XLSX/CSV/DOCX 表合并为母表，自动对齐表头、计算合计并检查漏填、格式问题和勾稽差额。 |
| `work_office_merge` | 用 Word 模板和 JSON/CSV/XLSX 数据生成单份或批量文档，适合通知、合同、证书和固定表单。 |
| `work_office_import` | 把 CSV/TSV 导入 Excel；目标工作簿不存在会自动创建，已存在会先备份。 |
| `work_office_diff` | 对比两版 DOCX 的段落/表格，或两版 XLSX 的单元格、公式和数值变化，可按主键对齐并导出报告。 |
| `work_office_join_docx` | 把目录或多份 Word 合并成一份，保留样式和表格，可自动分页和加文件标题。 |
| `work_office_to_pdf` | 通过本机 Office/WPS 精准导出 PDF，支持单文件、目录和指定工作表；自动选择可用应用。 |
| `work_office_com` | 直连本机 Office/WPS：查看已安装应用和当前文件、页数字数、重算公式、刷新域、查找替换、单元格读写及受控 Python 实时操作。 |
| `work_office_advanced` | 常规路径覆盖不了时进入：Excel 46 个工具（透视表、图表、条件格式、公式追踪、目标求解）或 Word 58 个工具（批注、脚注、保护、XML、锚点编辑）。默认只读。 |
| `work_office_engines` | 真实加载并体检各 Office 引擎，返回 ready、工具数和调用桥；路径存在不等于可用。 |


---

### 3. `work-memory` — 双轨记忆、台账与本地装配台（4 + 2 工具 & Web Console）

#### 实现原理
- **轨迹蒸馏（`experience/library.py`）**：`work_core.py` 对每一次 MCP 工具调用自动记录脱敏轨迹到 `06_运行状态/tool_trace.jsonl`；`work_exp(action="distill")` 解析同轮真实调用过的工具链、子工具名、参数变量化占位符与失败避坑提示，生成独立 SOP Markdown 并索引进 SQLite FTS5（`.exp_index.db`），实现“零探索重放”。
- **双轨互补**：程序性步骤进 `work_exp`，陈述性长期事实通过开源 `Gentleman-Programming/engram`（`bin/engram.exe`，SQLite FTS5 + `topic_key` 版本演进）持久化；两者可联合导出为 Obsidian 双向链接图谱。
- **配置安全读写（`web/config_manager.py`）**：Web 控制台修改 `~/.codex/config.toml` 时使用正则节替换 + `tomllib` 全文校验 + `.bak` 原子替换，畸形配置绝不写盘。

#### 逐工具功能说明
1. **`work_exp`（经验配方引擎）**：`distill` 从真实调用轨迹提炼带精确工具签名的 SOP；`search`/`get` 按关键词/模块/工具名查配方；`replay` 代入新参数一键预览或重放整条工作流；`to_skill` 一键导出为独立子 Skill；`trace` 查看近期工具调用日志。
2. **`work_mem`（Engram 长期记忆）**：`save` 保存口径/偏好/决策/避坑规律（支持 `topic_key` 同主题覆盖演进）；`search`/`context`/`stats` 检索或加载上下文；`export_obsidian` 联合导出 Engram 记忆 + `work_exp` 经验到 Obsidian 图谱。
3. **`work_ledger`（材料收发台账）**：`summary`/`departments`/`missing`/`history`/`batches`/`state`/`record`，按 SHA256 区分“扫到几次”与“改了几版”，并核对底册算未交单位及联络员电话。
4. **`work_backup_file`（写前安全备份）**：一键把任意本机文件带时间戳复制到 `02_自动备份区/`，绝不改动源文件。
5. **`work_status`**：全局状态快照——各模块引擎就绪度、工作区目录清单及最近一次收件/汇总/排版结果（支持 `reset_demo_fixtures=true` 重置演示数据）。
6. **`work_modules`**：列出 `comms` / `office` / `memory` / `other` 四个模块的版本、负责范围与工具映射。
7. **Codex 风格本地装配台（`web/serve.py` `:8765`）**：在网页浏览 41 个公共工具入参说明、可视化记忆关系图谱与经验列表，并用开关实时启用/停用 `~/.codex/config.toml` 中的各模块 MCP 与 Skill。

---

### 4. `instruct-unrestricted` — 破限控制中枢与 IDE 规则编译器

#### 实现原理
- **生命周期抽象（`engines/base_engine.py`）**：把 7 大客户端的不同规则路径和格式统一抽象为 `BaseIDEEngine`；计算文件内容时统一将 `

` 规范化为 `
` 再求 SHA256，免疫跨平台换行符假漂移，实现 `intact / drifted / missing / unmanaged / clean` 5 态热感知；每次覆盖前生成 `.backup` 物理快照。
- **片段组合编译（`composer/rule_composer.py`）**：将 `base_contract.md`、4 个角色片段与 3 个严格度片段按目标平台语法模板（如 Cursor `.mdc` YAML frontmatter）动态拼接编译。

#### 逐引擎、逐命令与逐接口说明
- **7 大 IDE 引擎（`engines/`）**：
  1. `cursor_engine`：管理 `.cursorrules` 与 `.cursor/rules/unrestricted.mdc`
  2. `windsurf_engine`：管理 `.windsurfrules` 与 `.windsurf/rules/unrestricted.md`
  3. `antigravity_engine`：管理 `.agents/rules/00-unrestricted-contract.md` 与 `GEMINI.md`
  4. `claude_engine`：管理 `CLAUDE.md`
  5. `codex_engine`：编译 `contract.md` / `astra.md`
  6. `workbuddy_engine`：管理 `.workbuddy/rules/unrestricted.md` 与 `.workbuddy/rules.md`
  7. `trae_engine`：管理 `.trae/rules.md` 与 `.trae/rules/project_rules.md`
- **4 角色 × 3 严格度（`composer/`）**：角色含 `fullstack`（全栈架构）、`security_audit`（逆向安全审计/防误杀拒答）、`automation_data`（高吞吐自动化）、`minimal_fix`（最小作用域修补）；严格度含 `standard`（消除伪能力拒绝 + 闭环自测）、`hyperdrive`（零客套极限执行）、`strict_safe`（写前核对精确行号）。
- **5 个 CLI 子命令（`cli.py`）**：`options`（列可选项）、`status`（5 态热感知探测）、`apply`（编译写入并备份）、`verify`（指纹防篡改核验）、`reset`（无损回滚恢复）。
- **6 个 REST 接口（`services/core_api_server.py` `:5055`）**：`GET /api/unrestricted/apps`、`GET /api/unrestricted/workspaces`、`POST /api/unrestricted/apply_app`、`POST /api/unrestricted/reset_app`、`POST /api/unrestricted/apply_all`、`POST /api/unrestricted/reset_all`。

---

### 5. `modeltrace-attribution` — 黑盒模型归因与测谎系统

#### 实现原理
- **统计指纹归因（`engine/fingerprint.py`）**：让目标模型生成 `[1, 355]` 区间 250 个无语义整数。系统统计频数分布后计算与 18 款官方基准模型（194,264+ 特征数字）的 **Hellinger 距离**，通过**干扰子空间正交投影（Nuisance Subspace Projection）**滤除提示词变体带来的共模干扰，叠加**有序分块特征（Ordered Blocks）**并经 Logistic Softmax 输出模型与家族置信度。
- **对抗造假检测（`engine/spoof_detector.py`）**：实时提取差分、周期自相关、模同余步长、熵塌缩等特征，100% 拦截 5 类代理代码伪造攻击，同时在 648 份真实样本上实现 0.00% 误报。

#### 逐模块、逐命令与逐接口说明
- **核心内部模块（`engine/`）**：
  1. `fingerprint.py`：加载基准库并计算 Hellinger 距离、子空间投影与模型/家族归因概率。
  2. `spoof_detector.py`：检测并拦截等差递增、等减序列、固定周期循环、步长同余、退化占位 5 类脚本伪造输出。
  3. `collector.py` & `challenge_suite.py`：使用 `ThreadPoolExecutor` 并发发送 3–5 组中英文 250 整数挑战，自动识别 OpenAI `/chat/completions` 与 Anthropic `/messages` 协议并带防 WAF 请求头。
  4. `enrollment.py` / `bank_builder.py` / `rebuild_unified_bank.py`：采集新模型采样数据并增量重建基准特征库。
- **4 个 CLI 子命令（`cli.py`）**：
  1. `bank-info`：查看内置 18 款基准模型（GPT 8 款 + Claude 10 款）与校准参数；
  2. `check-spoof`：检测任意输入文本是否属于脚本伪造数字流；
  3. `analyze`：对离线保存的模型输出做血统归因；
  4. `test-api`：输入任意 `base_url`、`api_key`、`model`，数秒内在线实测端点真伪与是否掺水。
- **5 个 REST 接口（`services/core_api_server.py` `:5055`）**：`GET /api/modeltrace/info`、`GET /api/modeltrace/models`、`POST /api/modeltrace/spoof_check`、`POST /api/modeltrace/analyze`、`POST /api/modeltrace/test_api`。
