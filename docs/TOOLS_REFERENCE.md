# 五大工具全量功能与接口参考（Tools Reference）

---

## 一、`work-comms`（通讯模块 · 23 个 MCP 工具）

入口：`packages/work-toolkit/mcp/work_comms_server.py`

| 工具名 | 类型 | 核心功能 |
| :--- | :--- | :--- |
| `work_wechat_probe` | 只读诊断 | 检查微信客户端版本、SQLCipher+WAL、DPAPI 密钥缓存、收件目录与图片密钥状态 |
| `work_wechat_sync` | 只读缓存 | 按需提取微信 4.x `Config.Cipher` 密钥并加密缓存至 `.keys.dpapi`，执行 WAL 增量同步 |
| `work_wechat_sessions` | 只读查询 | 获取会话列表，自动还原群名/备注名、未读数与最后摘要 |
| `work_wechat_messages` | 只读查询 | 按群名/备注读取聊天消息（自动刷新 WAL 增量），支持 `since`/`until`/`limit` |
| `work_wechat_search` | 只读检索 | 多维全文搜索（关键词 / 发言人 / 消息类型：文本、文件、链接、图片、语音、引用、接龙、公告等），自动解压 zstd 长文 |
| `work_wechat_contacts` | 只读查询 | 查询联系人、群成员列表、跨群公告汇总与共同群（`mode=contacts\|group\|announcements\|common_groups`） |
| `work_wechat_solitaire` | 只读解析 | 自动解析群接龙记录并对比底册/群成员生成未接龙名单 |
| `work_wechat_favorites` | 只读检索 | 检索微信收藏夹（笔记/链接/文件/聊天记录） |
| `work_wechat_insights` | 只读/导出 | 群聊统计画像、发言排行、链接与待办提取，支持导出 Markdown |
| `work_wechat_files` | 扫描/归档 | 收件统一入口：`mode=scan`（扫描统计）、`pull`（去重复制）、`harvest`（归档并联动底册核对未交） |
| `work_wechat_media` | 只读/导出 | 解密 `msg/attach` 下的 `.dat`（AES-128-ECB + XOR）导出缩略图或 WXGF 原图 |
| `work_wechat_digest` | 写文件 | 将指定群某时间段消息（含图片）摘录生成 `.docx` 或 `.md` |
| `work_wechat_remind` | 写文件 | 根据未交名单生成标准化 `@催办` 话术写入 `待发催办通知.md`（不自动发送） |
| `work_wecom_probe` | 只读诊断 | 检查企业微信版本、进程状态与 wxSQLite3 密钥缓存 |
| `work_wecom_sync` | 只读缓存 | 提取企业微信内存密钥并解密本地库到只读缓存 |
| `work_wecom_sessions` | 只读查询 | 企业微信会话列表 |
| `work_wecom_messages` | 只读查询 | 读取企业微信会话消息 |
| `work_wecom_search` | 只读检索 | 企业微信历史消息关键词搜索 |
| `work_wecom_contacts` | 只读查询 | 企业微信联系人与群成员查询 |
| `work_wecom_files` | 只读扫描 | 企业微信收件目录扫描 |
| `work_wecom_harvest` | 写文件 | 企业微信群收件归档与底册核对 |
| `work_comms_overview` | 只读聚合 | 微信 + 企业微信双通道状态与合并时间线总览 |
| `work_comms_notices` | 只读聚合 | 跨微信 + 企业微信最近 N 天重要通知、截止时间优先聚合 |

> 配合开源 `wechat_chat` MCP（`Haoea1024/wechat-chat-mcp`）可补齐：**SILK+Whisper 语音转写、图片多模态内容理解、聊天附件 PDF/Office 分页阅读与群成员私聊反推**。

---

## 二、`work-office`（文档与表格模块 · 18 个 MCP 工具）

入口：`packages/work-toolkit/mcp/work_office_server.py`

| 工具名 | 核心功能 |
| :--- | :--- |
| `work_office_engines` | 真实加载探测 `excel-mcp-server`、`Office-Word-MCP-Server`、`OfficeMCP`、`OfficeCLI` 与 `wechat-chat-mcp` 就绪度 |
| `work_excel_mcp` | 桥接 `excel-mcp-server`（46 个高级表格工具：`action=list\|schema\|call`，默认只读） |
| `work_word_mcp` | 桥接 `Office-Word-MCP-Server`（58 个高级 Word 工具：`action=list\|schema\|call`，默认只读） |
| `work_officecli_run` | 调用内置 `OfficeCLI v1.0.161` 执行文档 OpenXML 命令与校验 |
| `work_office_inspect` | 深度检视 `.docx/.xlsx/.pptx/.pdf` 文本、结构大纲、表单、问题清单或高清页面截图渲染 |
| `work_office_compose` | 从 Markdown（含表格/公式/目录）一键合成现代商务排版 Word / PPT |
| `work_office_diff` | Word/Excel 新旧版本差异对比（只读） |
| `work_office_join_docx` | 多份 Word 文档结构化拼接合并 |
| `work_office_consolidate` | 多份表格/文档跨文件批量汇总到母表（支持自动对齐 schema 与异常校验） |
| `work_office_create` | 按语义骨架创建空白或结构化 `.xlsx` / `.docx` |
| `work_office_batch` | 基于 JSON 指令集对文档/表格执行批量写入与格式修改（写前自动备份） |
| `work_office_merge` | 按模板与 JSON/CSV/Excel 数据源单份或批量套打生成文档 |
| `work_office_import` | CSV 数据规范导入 Excel |
| `work_office_format_gbt9704` | 附加公文 GB/T 9704 排版格式化 |
| `work_office_to_pdf` | 单文件或整目录批量转 PDF（基于本机 Office/WPS COM） |
| `work_office_com` | 本机 Office/WPS COM 操控与超时受控 `run_python` 沙箱 |
| `work_office_watch` | 启动/定位文档实时浏览器预览（mark/goto） |
| `work_office_schema` | 查询 OfficeCLI 文档元素与属性手册 |

---

## 三、`work-memory`（记忆与台账模块 · 4 个工具）+ 全局（2 个工具）

入口：`packages/work-toolkit/mcp/work_memory_server.py`（合体入口：`work_toolkit_server.py`）

| 工具名 | 核心功能 |
| :--- | :--- |
| `work_exp` | 流程经验库：`distill`（从调用轨迹自动提炼零探索直调配方）、`search`、`get`、`replay`（参数化重放）、`to_skill`（一键生成独立 Skill）、`trace` |
| `work_mem` | 开源 Engram 通用跨会话记忆：`save`（支持 `topic_key` 自动演进）、`search`、`context`、`stats`、`export_obsidian`（联合导出至 Obsidian 图谱） |
| `work_ledger` | 材料收发可追溯台账：`summary` / `departments` / `missing` / `history` / `batches` / `state` / `record` |
| `work_backup_file` | 写前时间戳安全备份到 `02_自动备份区/` |
| `work_status` | 全局模块与本地文件快照（`reset_demo_fixtures` 可重置演示数据） |
| `work_modules` | 列出 4 个模块化分组与各自工具映射 |

---

## 四、`instruct-unrestricted`（破限控制中枢 CLI & REST API）

- **CLI 入口**：`packages/instruct-unrestricted/cli.py`
  - `status [--workspace PATH] [--json]`：探测 7 大 IDE 规则状态
  - `options [--json]`：列出 4 角色、3 严格度、7 平台
  - `apply --app <ide> [--role ...] [--strictness ...] [--custom-rules ...] [--workspace PATH]`：动态编译并写入规则（自动备份）
  - `verify [--workspace PATH]`：计算规范化 SHA256 并校验完整性
  - `reset [--app <ide>] [--workspace PATH]`：按快照无损回滚
- **支持平台**：`cursor`、`windsurf`、`claude`、`codex`、`antigravity`、`workbuddy`、`trae`
- **REST API（`:5055`）**：
  - `GET /api/unrestricted/apps`
  - `GET /api/unrestricted/workspaces`
  - `POST /api/unrestricted/apply_app` / `POST /api/unrestricted/reset_app`
  - `POST /api/unrestricted/apply_all` / `POST /api/unrestricted/reset_all`

---

## 五、`modeltrace-attribution`（大模型测谎归因 CLI & REST API）

- **CLI 入口**：`packages/modeltrace-attribution/cli.py`
  - `bank-info [--json]`：查看 18 款基准模型、2 个家族（GPT/Claude）与校准参数
  - `check-spoof --input <text|file> [--json]`：运行 `SpoofDetector` 检测 5 类程序伪造序列
  - `analyze --input <file|text> [--json]`：对输入数字流执行 Hellinger 距离 + 子空间投影模型归因
  - `test-api --base-url <url> --api-key <key> --model <name> [--rounds 3] [--json]`：高并发在线采样测谎
- **REST API（`:5055`）**：
  - `GET /api/modeltrace/info`
  - `GET /api/modeltrace/models`
  - `POST /api/modeltrace/spoof_check`
  - `POST /api/modeltrace/analyze`
  - `POST /api/modeltrace/test_api`
