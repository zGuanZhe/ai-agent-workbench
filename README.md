# AI Agent Workbench — 五位一体 AI 工程与治理套件

> **本质解构 · 能力执行 × 行为规约 × 模型测谎**  
> 本仓库将 5 个定位完全不同、但共同服务于 AI Agent 实战落地与可控治理的工具进行统一解构与模块化整合，既支持**单模块独立部署与直调**，也支持**跨层协同工作流**。

---

## 一、五工具三维解构与适用场景总览

这 5 个工具并非同质化的“工具堆砌”，而是分别解决 AI Agent 在实际使用中的**五大核心痛点**，对应**三个正交技术层级**：

| 所属层级 | 工具包 | 本质定位 | 解决什么痛点 / 典型场景 | 核心交付形态 |
| :--- | :--- | :--- | :--- | :--- |
| **1. 能力执行层**<br>*(External Tools & Memory)* | **`work-comms`**<br>*(23 工具 + 协作用 `wechat_chat`)* | 本地微信 4.x / 企业微信只读取证与收件归档引擎 | 微信 4.x 无法用 wxauto、消息散落在几十个群里、群图片是加密 `.dat`、群语音/附件长 PDF 看不完、各科室交上来的材料不知谁漏交 | MCP Server + Codex/WorkBuddy Skill + CLI |
| **1. 能力执行层**<br>*(External Tools & Memory)* | **`work-office`**<br>*(18 主工具 + 104 开源子工具)* | 跨引擎文档与表格高保真读写、汇总、套打、转 PDF 与合成引擎 | 几十份不同格式表格汇总核验、Word/Excel 版本对比、按 CSV 批量套打合同/通知、Markdown 一键出带图表/公式的现代报告与 PPT、COM 精准转 PDF | MCP Server + Codex/WorkBuddy Skill + CLI |
| **1. 能力执行层**<br>*(External Tools & Memory)* | **`work-memory`**<br>*(4 主工具 + 装配控制台)* | **「零探索流程配方 + 跨会话长期记忆 + 材料台账」**双轨记忆中枢 | 同类工作每次都要让 AI 重新摸索工具参数；跨对话总忘既定口径和联系人；收发材料无法追溯谁改了几版、还差谁 | MCP Server + Codex/WorkBuddy Skill + 控制台 (`:8765`) |
| **2. 行为规约层**<br>*(System Prompt & IDE Rules)* | **`instruct-unrestricted`** | **多端 AI IDE 规则编译器与破限控制中枢** | 模型动不动以“安全/能力不足”拒答、废话多、改代码跑偏；多款 AI IDE（Cursor/Claude/Codex/Windsurf 等）各自规则格式不统一且容易被改乱 | Python SDK + CLI + REST API (`:5055`) |
| **3. 模型可信层**<br>*(Black-box Verification)* | **`modeltrace-attribution`** | **黑盒 LLM 指纹归因与反代掺水测谎系统** | 第三方中转站/API 疑似用低价模型冒充 Claude/GPT 旗舰、甚至用脚本伪造输出；需要客观数学证据鉴定真实底层模型血统 | Python SDK + CLI + REST API (`:5055`) |

> 另附：`packages/work-toolkit` 内含 **2 个全局自检工具**（`work_status` / `work_modules`，共计 **47 个 MCP 工具**）以及 **Codex 风格本地装配控制台**（`web/serve.py`，端口 `8765`，用于可视化浏览工具、查看记忆图谱并一键开关 Codex `config.toml` 中的各模块）。

---

## 二、五大工具功能精讲（用户视角）

### 工具 1：`work-comms` — 微信 4.x / 企业微信全能只读与收件取证

1. **跨通道通知聚合（不再逐群翻消息）**
   - `work_comms_notices`：**一次调用**跨个人微信 + 企业微信拉取最近 N 天消息，自动识别“通知 / 截止死线 / 待办事项”并置顶排序，把闲聊沉底。
   - `work_comms_overview`：合并显示微信与企微双通道连通状态与最新会话时间线。
2. **个人微信 4.x 深度读取（突破 4.x 加密与无 UIA 限制）**
   - 自动通过内存 `Config.Cipher` + Windows DPAPI 本地加密缓存提钥，并对 `-wal` 日志做 4096B 帧级实时增量补丁，无需每次整库重解。
   - **查未读/读群聊**：`work_wechat_sessions`、`work_wechat_messages`（自动将 `wxid` 映射为备注名/群昵称，支持 `since`/`until` 时间段）。
   - **多维历史检索**：`work_wechat_search` 支持按**关键词、指定发言人（跨群查某人说过的所有话）、消息类型**（`text`/`file`/`link`/`image`/`voice`/`quote`/`solitaire`/`announcement`/`miniprogram`）筛选，自动解压 zstd 压缩长消息。
   - **群生态与社交数据**：`work_wechat_contacts`（查联系人所在全部群、群主与完整群成员、全群公告汇总、两人共同群）；`work_wechat_solitaire`（解析群接龙并自动对比《科室底册》算出未接龙名单与 `@催办` 话术）；`work_wechat_favorites`（检索微信收藏的笔记/密码备忘/网盘链接/合并聊天记录）；`work_wechat_insights`（生成群活跃排行、外链清单、群内文件与待办提取）。
3. **加密图片解密 & 聊天摘录成文**
   - `work_wechat_media`：把 `msg/attach` 下的加密 `.dat`（AES-128-ECB + XOR）还原为缩略图 JPEG，或结合 `ffmpeg` 将 WXGF 原图转为高清 PNG。
   - `work_wechat_digest`：把指定群的聊天记录按天分组导出为可存档的 `.docx`（仿宋三号 28 磅）或 `.md`，并可将聊天图片解密后直接嵌入正文。
4. **收件归档、底册核对与催办（纯只读安全）**
   - `work_wechat_files` 统一入口支持三种模式：`scan`（扫描统计近 N 天收件）、`pull`（按关键字/扩展名筛选复制并 SHA256 去重）、`harvest`（归档并自动对比《科室底册.xlsx》给出已交/未交名单）。
   - `work_wechat_remind`：根据未交名单生成规范化 `@催办` 文本写入 `待发催办通知.md` 供人工粘贴，**绝不抢占焦点自动点发送**。
5. **企业微信全套只读（`work_wecom_*`）+ 开源多模态补全（`wechat_chat`）**
   - `work_wecom_probe / sync / sessions / messages / search / contacts / files / harvest` 覆盖企业微信 wxSQLite3 加密库与 protobuf 消息解码。
   - 协同开源 `Haoea1024/wechat-chat-mcp`（`wechat_chat` MCP）补充 **SILK+Whisper 语音转文字、图片内容识别、聊天附件 PDF/Office 分页阅读**。

---

### 工具 2：`work-office` — 跨引擎文档与表格工程中枢

1. **多表格智能汇总与数据核验（`work_office_consolidate`）**
   - 批量读取文件夹中的 `.xlsx` / `.csv` / `.tsv` / `.docx` 表格，支持按母表模板对齐，也支持 `auto_schema=true` **无模板自动推断字段并合并**。
   - 自动统计合计、检查漏填项、校验手机号/身份证号格式与分项总计勾稽差额，输出汇总母表与《异常数据核验单》。
2. **现代富文档 & PPT 一键合成（`work_office_compose` / `work_office_format_gbt9704`）**
   - `work_office_compose`：将 Markdown（支持 GFM 表格、代码块、LaTeX `$$公式$$`、Mermaid 流程图）一键编译为排版精良的原生 `.docx` 或 `.pptx`（支持 `modern` / `report` / `academic` 预设与自动目录）。
   - `work_office_format_gbt9704`：支持公文 GB/T 9704（红头版头、二号小标宋、三号仿宋 28 磅）、现代商务、学术三线表与备忘录四种预设。
3. **版本差异比对、多文档拼接与批量套打**
   - `work_office_diff`：对比两版 Word 的段落/表格增删改，或对比两张 Excel 表的单元格/公式/数值差异（支持按主键列对齐行），可选导出 Markdown 差异报告。
   - `work_office_join_docx`：将多份 `.docx` 按顺序拼接为完整文档，保留原样式与表格并可选插分页符与分节标题。
   - `work_office_merge`：在 `.docx/.xlsx/.pptx` 模板里写 `{{key}}`，传入 CSV/Excel 多行数据，一次批量生成 N 份合同/通知/证书。
4. **文档深层体检、实时预览与精准转 PDF**
   - `work_office_inspect`：抽取纯文本/Excel 区域、大纲、样式统计、未求值公式/格式问题体检，或直接将每页渲染为高清 PNG 截图用于视觉验收。
   - `work_office_to_pdf` / `work_office_com`：调用本机 MS Office 或 WPS COM 精准导出 PDF（单个或整目录批量），支持 `run_python` 沙箱操控已打开的文档并自动清理超时僵尸进程。
   - `work_office_watch`：在浏览器中打开本地实时预览服务（`26315`），配合 `work_officecli_run` / `work_office_batch` 边改边看。
5. **104 个工业级开源子工具直通桥（只读默认 + 写前自动备份）**
   - `work_excel_mcp`（46 子工具）：260+ 函数计算、公式溯源（`trace_formula`）、单变量目标求解（`goal_seek`）、真数据透视表与切片器、条件格式、数据验证。
   - `work_word_mcp`（58 子工具）：提取/按作者筛选批注、脚注尾注管理、文档保护、按锚点插入段落/表格、XML 检视与结构化合并。

---

### 工具 3：`work-memory` — 零探索经验配方 + 长期记忆 + 台账

1. **「一句话沉淀为可重放 SOP」的流程经验库（`work_exp`）**
   - 每次调用工具会自动记录到 `tool_trace.jsonl`。任务完成后只需说「**将本次工作整理为经验**」，`work_exp(action="distill")` 会提取本轮实际用过的**精确工具名、子工具、参数模板与避坑记录**，生成结构化配方文档。
   - 下次遇到同类任务时，Agent 可直接按配方“零探索”调用，或通过 `work_exp(action="replay", vars_json=...)` **参数化一键重放**，甚至用 `to_skill` 将高频流程导出为独立 Codex/WorkBuddy 子 Skill。
2. **跨会话长期事实记忆与知识图谱（`work_mem` / Engram）**
   - 基于开源 `Gentleman-Programming/engram`（SQLite + FTS5），专门记忆跨任务长期事实：汇报口径、用户偏好、关键决策、联系人规律，支持 `topic_key` 同主题自动更新演进。
   - 支持 `export_obsidian` 一键将 Engram 记忆与任务经验联合导出为带双向链接的 Obsidian Markdown 知识图谱。
3. **材料收发台账与安全备份（`work_ledger` / `work_backup_file`）**
   - 按文件内容 SHA256 记录每次提交，区分「扫到几次」与「真正改了几版」，支持对接底册查未交人员与联系电话；`work_backup_file` 一键带时间戳复制到 `02_自动备份区/`。
4. **Codex 风格本地装配控制台（`web/serve.py`，端口 `8765`）**
   - 在浏览器里可视化查看 47 个工具参数、记忆关系图谱与经验列表，并可直接通过界面开关实时启用/停用 `~/.codex/config.toml` 中的各模块 MCP 与 Skill（含 TOML 语法校验与原子 `.bak` 备份）。

---

### 工具 4：`instruct-unrestricted` — 7 大 AI IDE 规则编译器与破限控制中枢

1. **7 大客户端原生格式适配（写到正确的位置与语法）**
   - **Cursor**：`.cursorrules` + `.cursor/rules/unrestricted.mdc`（带 YAML frontmatter）
   - **Windsurf**：`.windsurfrules` + `.windsurf/rules/unrestricted.md`
   - **Antigravity / Gemini CLI**：`.agents/rules/00-unrestricted-contract.md` + `GEMINI.md`
   - **Claude Code**：`CLAUDE.md`
   - **OpenAI Codex / GPT CLI**：`contract.md` / `model_instructions`
   - **WorkBuddy**：`.workbuddy/rules/unrestricted.md` + `.workbuddy/rules.md`
   - **Trae / Trae CN**：`.trae/rules.md` + `.trae/rules/project_rules.md`
2. **动态规则组装器（`4 角色 × 3 严格度 × 自定义附则`）**
   - **4 大角色**：`fullstack`（全栈架构）、`security_audit`（逆向安全审计/零无端拦截）、`automation_data`（高吞吐自动化与数据工程）、`minimal_fix`（极简最小作用域修补）。
   - **3 级严格度**：`standard`（消除伪能力拒绝 + 闭环自测）、`hyperdrive`（零说教/零客套/极限直出执行）、`strict_safe`（写前核对确切行号与快照优先）。
3. **5 态完整性热感知 & 原子快照无损回滚**
   - 跨平台规范化换行符（CRLF/LF 免疫）计算 SHA256，毫秒探测规则处于 `intact`（完整）、`drifted`（被篡改漂移）、`missing`（丢失）、`unmanaged`（有旧规则未托管）或 `clean`（纯净）。
   - 部署前自动物理备份原有规则，调用 `reset` 可零残留恢复原状。

---

### 工具 5：`modeltrace-attribution` — 黑盒 LLM 路由归因与反代测谎系统

1. **为什么能识别套壳/降智/伪造？**
   - 基于无语义 `[1, 355]` 整数采样分布的统计指纹：不同底层大模型受词表概率、对齐策略与采样机制影响，会在该区间呈现显著且可分离的非均匀分布偏好。
   - 内置 **18 款官方权威模型基准库**（涵盖 `gpt-5.4` / `gpt-5.5` / `gpt-5.6-*` / `gpt-6-*` 及 `claude-haiku-4-5` / `claude-sonnet-4-6` / `claude-opus-4-6~5-5` 等，合计 194,264+ 样本数字），结合 **Hellinger 距离**、**干扰子空间正交投影**与 **Logistic Softmax** 给出模型与家族概率分布。
2. **抗对抗代码伪造拦截（`SpoofDetector`）**
   - 针对中转站用 Python 脚本生成的伪造数字流（等差数列、等减序列、固定周期循环、步长同余序列、极简退化占位 5 类攻击向量）实时检测拦截。
   - 在 **648 份真实历史模型样本**上实测 **0.00% 误报率**，对 5 类合成伪造攻击实现 **100% 拦截**。
3. **高并发在线 API 实测与离线分析**
   - `test-api`：多线程并发（`ConcurrentCollector`）同时发出多组中英文采样挑战，自动识别 OpenAI `/chat/completions` 与 Anthropic `/messages` 协议并带防 WAF 请求头，数秒内完成真伪鉴定。
   - `analyze` / `check-spoof`：支持对本地保存的模型输出文本直接离线做血统归因与代码造假检测。

---

## 三、跨工具实战组合场景

1. **场景 A：群收文件 → 核对未交 → 跨表汇总 → 出汇报 PPT → 沉淀 SOP（工具 1 + 3 + 2）**
   - `work_wechat_files(mode="harvest", roster="科室底册.xlsx")` 收齐本周附件并算出未交名单；
   - `work_wechat_remind` 生成 `@催办` 话术，同时 `work_ledger(action="record")` 记入材料台账；
   - `work_office_consolidate(auto_schema=True)` 合并所有交来的 Excel/Word 表格并生成异常核验单；
   - `work_office_compose(format="pptx", preset="modern")` 生成汇报 PPT；
   - 对 AI 说一句「将本次工作整理为经验」，`work_exp(action="distill")` 自动生成可参数化重放的零探索配方。
2. **场景 B：API 中转站验真 → 为 IDE 注入最优规约（工具 5 + 4）**
   - 先用 `modeltrace-attribution` `test-api` 验证 API 端点未掺水/未用脚本造假；
   - 再用 `instruct-unrestricted` `apply --app cursor --role security_audit --strictness hyperdrive` 为当前项目注入工程规约与破限协议。

---

## 四、仓库结构与快速开始

```text
ai-agent-workbench/
├── packages/
│   ├── work-toolkit/              # 工具 1-3：comms / office / memory (MCP + Skills + Web 控制台)
│   ├── instruct-unrestricted/     # 工具 4：7 大 IDE 破限控制中枢与规则编译器
│   └── modeltrace-attribution/    # 工具 5：大模型指纹归因与反代测谎系统
├── services/core_api_server.py    # 工具 4 + 工具 5 联合 REST 微服务 (端口 5055)
├── scripts/
│   ├── bootstrap.py               # 一键校验全部 5 个工具
│   ├── requirements-toolkit.txt   # 工具 1-3 依赖
│   └── requirements-core.txt      # 工具 4-5 依赖
└── docs/
    ├── ARCHITECTURE.md            # 三层五工具深度架构解构
    ├── SETUP_GUIDE.md             # 完整安装与多端配置教程
    └── TOOLS_REFERENCE.md         # 47 MCP 工具 + CLI + REST API 全量参考手册
```

```powershell
# 1. 安装依赖（可按需安装对应组）
python -m pip install -r scripts/requirements-toolkit.txt
python -m pip install -r scripts/requirements-core.txt

# 2. 一键运行全部 5 个模块的自动化校验
python -X utf8 scripts/bootstrap.py
```

更多细节请参阅：
- [深度架构解构 (`docs/ARCHITECTURE.md`)](docs/ARCHITECTURE.md)
- [详细安装与配置教程 (`docs/SETUP_GUIDE.md`)](docs/SETUP_GUIDE.md)
- [全量工具与参数参考手册 (`docs/TOOLS_REFERENCE.md`)](docs/TOOLS_REFERENCE.md)
