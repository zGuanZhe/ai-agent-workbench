# WorkBuddy 接入说明（work-toolkit）

## 零、日常使用：独立模块 Skill + 可选 `/work`

装好并重载会话后，可直接使用 `work-office`、`work-comms`、`work-memory` 三个独立 Skill 与对应 MCP。
`/work` 只用于跨模块组合，不再是唯一入口；不打开网页、不依赖 MCP 信任也能通过 `scripts/work.py` 调用。
命令行形式（由 agent 使用，用户不用看）：

```bash
python "<项目根目录>/scripts/work.py" office "<模块内自然语言>"  # Office 单模块
python "<项目根目录>/scripts/work.py" call work_status "{}"
python "<项目根目录>/scripts/work.py" tools <工具名>             # 不确定参数时查
python "<项目根目录>/scripts/work.py" ask "<原话>"               # 仅在 /work 跨模块组合时使用
```

`ask` 是 `/work` 组合入口的路由器；单模块 Skill 应直接调用 MCP。它读取 `web/intents.json`，
把口语翻成确定的 `plan`，并在只读请求中过滤写产物工具。
`tools/check_intents.py` 校验意图表里引用的工具名是否都真实存在，避免出现「功能触发不了」。

`install_skills.py` 会把独立模块 Skill 同步到 `~/.workbuddy/skills/` 与 `~/.codex/skills/`。
网页只做装配与可视化，不是模块调用前提。

---

## 一、接入架构（可选 MCP / 内部实现）

本目录把办公流水线能力以标准 **MCP（stdio）** 服务的形式接给 WorkBuddy 及其他支持 MCP 的客户端（Claude Code / Codex / Cursor）。

```
WorkBuddy / Claude Code / Codex
        │  MCP stdio (JSON-RPC)
        ▼
mcp/work_toolkit_server.py          ← 合体入口：挂载 41 个公共工具（实现分在 work_mods/ 的
                                        comms.py 通讯 / office.py Office / memory.py 记忆 / other.py 其他）
        ├── python_workers/wx_helper.py            微信收件归档 + 未交核对 + 催办
        ├── python_workers/merge_docx_tables.py    Word/Excel 跨表合并 + 勾稽校验
        ├── python_workers/gbt9704_formatter.py    GB/T 9704-2012 公文排版
        └── bin/officecli.exe                      OfficeCLI v1.0.161（inspect/compose/batch…）
```

- 桥接层直接调用本机 Python 与 OfficeCLI，**不依赖 DSH 运行时**，因此 WorkBuddy 原生可用。
- 全部数据只落本地 `workspace/`，不出网。

## 二、注册方式

### 已注册（本机）

`%USERPROFILE%\.workbuddy\mcp.json`：

```json
{
  "mcpServers": {
    "work-toolkit": {
      "command": "<装齐依赖的 Python 解释器绝对路径>",
      "args": ["<项目根目录>\\mcp\\work_toolkit_server.py"],
      "env": { "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1" }
    }
  }
}
```

> 必须用**装了 python-docx / openpyxl 的解释器**。WorkBuddy 自带的精简 Python 3.13 没有这些包，直接用 `python` 会报 `ModuleNotFoundError: docx`。

注册后如需在 WorkBuddy 的「连接器 / MCP 管理」里直接看到 `work_*` 工具，可对 `work-toolkit` 点「信任」并重载会话。
**这一步是可选的**：总 Skill 的 `/work` 走本地桥接，不点信任同样能调用全部工具。

### 换机器 / 重装时

```powershell
powershell -ExecutionPolicy Bypass -File "scripts\register-workbuddy-mcp.ps1"
```

脚本会自动找一个装好依赖的 Python，并把 `work-toolkit` 合并写进 `%USERPROFILE%\.workbuddy\mcp.json`（保留已有条目）。

## 三、41 个公共工具（通讯 23 / Office 12 / 记忆 4 / 其他 2）

| 工具 | 说明 |
| --- | --- |
| `work_status` | 九大引擎状态 + 五目录文件清单 + 最近结果 + 图片/台账就绪度；`reset_demo_fixtures=true` 重建演示数据 |
| `work_wechat_probe` | 微信版本、SQLCipher+WAL 与 DPAPI 密钥缓存状态、**本地收件目录就绪度**、媒体解密是否就绪 |
| `work_wechat_files(mode='scan')` | **【主通道·只读】** 扫描 `msg/file` 明文收件目录，按时间/关键字/扩展名/类型筛选 + 统计 |
| `work_wechat_files(mode='pull')` | 复制到 `01_微信收回原件/`（`{month}` 前缀、sha256 去重），可传 `roster` 出已交/未交与催办话术 |
| `work_wechat_files(mode='harvest')` | 群附件归档 + 已交/未交名单 + 催办话术（直接扫描本机收件目录） |
| `work_wechat_sync` | **【只读】** 取 4.1.11+ 数据库密钥并解密到 `07_微信只读缓存/`，源库只读打开 |
| `work_wechat_sessions` | **【只读】** 会话列表：群名/备注还原、未读数、最后摘要与时间 |
| `work_wechat_messages` | **【只读】** 按会话读消息，群名模糊匹配 + since/until，发送人已还原 |
| `work_wechat_search` | **【只读】** 历史消息全文搜索，zstd 压缩长消息先解压再匹配 |
| `work_wechat_remind` | @ 未交单位催办；默认 dry-run，失败自动写待发队列 |
| `work_wechat_media` | **【只读·导出】** 解密 `msg/attach` 下的 `.dat`（AES-128-ECB + XOR）；不传 chat 只看能力，传 `ref` 只导单张原图 |
| `work_wechat_digest` | **【写文件】** 把某个群一段时间的消息摘录成 `.docx`/`.md`，按天分组、带发送人，图片可解密嵌入 |
| `work_wecom_probe` | **【只读】** 企业微信通道探测：`WXWork.exe` 是否在跑、数据目录、加密库数量、密钥缓存 |
| `work_wecom_sync` | **【只读】** 从进程内存按 cipher 结构体取密钥，解明文副本到 `07_企业微信缓存/`，按源库 mtime 增量刷新 |
| `work_wecom_sessions` | **【只读】** 企业微信会话列表：`R:` 群聊 / `S:` 单聊，未读数与最后时间 |
| `work_wecom_messages` | **【只读】** 按会话读企微消息，正文从 protobuf 逐层解出，文件消息带文件名 |
| `work_wecom_search` | **【只读】** 企微正文搜索（SQL 搜不到，按时间窗取候选再逐条解正文匹配） |
| `work_wecom_contacts` | **【只读】** 企微通讯录：姓名 / 拼音 / 手机 / 邮箱 / 职位 |
| `work_wecom_files` | **【只读】** 企微收到的文件，含是否已下载到本地（`on_disk`） |
| `work_wecom_harvest` | **【写文件】** 企微文件按时间窗/群/扩展名复制到 `01_企业微信收回原件/`，`{chat}` 前缀、按内容去重，默认 `dry_run=true` |
| `work_comms_overview` | **【只读】** 通讯总览：微信 + 企业微信两条通道能不能读、各自状态，以及两边会话合成的合并时间线（每行标出通道） |
| `work_office_inspect` | **【只读】** text / outline / stats / issues / annotated / forms / comments / screenshot / html / svg / schema / skills；PDF 不是检视模式 |
| `work_office_compose` | **【写文件】** Markdown、表格、Mermaid、公式生成 DOCX/PPTX；也可按预设重排现有 DOCX |
| `work_office_batch` | **【写文件】** 通用编辑入口：批量 DOM 操作、`create=true` 创建后写入、自动备份与 validate |
| `work_office_consolidate` | 跨表合并 + 漏填/手机号/勾稽校验 + OpenXML 验证 |
| `work_office_merge` | **【写文件】** 模板 `{{key}}` + JSON/CSV/XLSX 数据生成单份或批量文档 |
| `work_office_import` | **【写文件】** CSV/TSV 导入 XLSX；目标不存在时自动创建，已存在时先备份 |
| `work_office_diff` | **【只读/可选写报告】** DOCX 段落与表格差异，或 XLSX 单元格/公式/数值差异 |
| `work_office_join_docx` | **【写文件】** 多份 Word 合并，保留样式与表格，可插分页/标题 |
| `work_office_to_pdf` | **【写文件】** 本机 Office/WPS COM 精准导 PDF；OfficeCLI 本身不支持 PDF 导出 |
| `work_office_com` | **【按动作】** COM：apps / active / info / export_pdf / recalc / refresh_fields / replace / read_cells / set_cells / run_python |
| `work_office_advanced` | **【高级可选·默认只读】** Excel 46 工具 / Word 58 工具统一入口；先 list/schema，再 call；写操作需 allow_write=true |
| `work_office_engines` | **【只读】** 真实加载并体检 OfficeCLI、Excel MCP、Word MCP 与 COM |
| `work_backup_file` | 改动前备份到 `02_自动备份区/` |
| `work_exp` | **【任务经验文档库】** `list` 列目录 / `get` 取整篇原文 / `save` 显式存 / `template` 空白骨架 / `export` 合集。一任务一 md，人工按需复制粘贴；**不检索、不打分、不自动召回、不自动写** |
| `work_ledger` | 材料流水：`summary` / `departments` / `missing` / `history` / `batches` / `state` / `record`；sha256 去重，`seen_count` 记扫到几次 |

## 四、验收结果（早期基线，本机实测）

> 下表记录的是阶段性实测快照。任务经验文档库、一键安装链路等**本轮新增验收见文末第九节**。

用 MCP 官方客户端 `mcp/test_client.py` 走完整 JSON-RPC 握手后逐个调用：

| 检查项 | 结果 |
| --- | --- |
| `initialize` + `tools/list` | ✅ 服务 `work-toolkit 0.2.0`，12 个工具全部可见 |
| `work_wechat_probe` | ✅ 识别微信 4.2.0.13；`local_inbox_ready=true`、`message_db_readable=true` |
| `work_wechat_sync` | ✅ 23/23 个库取到密钥（`mode=config-cipher`），解密副本逐页 HMAC 校验通过 |
| `work_wechat_sessions` | ✅ 读出真实会话列表，群名/备注已还原（如「山大-在校生论坛2605群」未读 120） |
| `work_wechat_messages` | ✅ 读到 2026-11-10 00:08 的群消息，发送人还原为昵称，正文已剥离 `id:` 前缀 |
| `work_wechat_search` | ✅ 搜「点名」命中 3 条，跨群、含被 zstd 压缩的长消息 |
| `work_wechat_files(mode='scan')` | ✅ 扫到真实收件 **537 个 / 1.56GB**（2026-01 ~ 2026-11），类型分布 pdf 194、docx 65、zip 59、xlsx 30 |
| `work_wechat_files(mode='pull')` | ✅ 6 份筛选复制成功，sha256 去重拦下 1 份重复 pptx；名单核对得出已交 2 / 未交 1 并生成话术 |
| `work_wechat_files(mode='harvest')` | ✅ 走 `local_inbox` 通道：2026-11 起匹配 12 份、复制 10 份、sha256 去重 2 份 |
| `work_office_consolidate` | ✅ 3 份申报表合并，2 条异常（手机号 10 位、勾稽差额 +1.5 万元），OfficeCLI 校验通过 |
| `work_office_compose` | ✅ 内联 Markdown 生成通知并按预设排版，OpenXML 校验通过 |
| `work_officecli_run` | ✅ validate 通过；`set /body/p[5] --prop bold=true` 写操作生效 |
| `work_office_inspect` | ✅ xlsx 单元格文本与 docx 段落提取正常 |
| `work_wechat_remind` | ✅ dry-run 生成话术并写入 `06_运行状态/待发催办通知.md` |
| DSH 侧 `node --test` | ✅ 1/1 通过（修复了宿主 Python 缺包问题） |

## 五、能力边界

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| OfficeCLI 文档读写校验 | ✅ 完全可用 | 覆盖 docx/xlsx/pptx 的读、改、批量、Schema 校验 |
| 跨表合并与勾稽校验 | ✅ 完全可用 | Word 表格 + Excel 分表混合抽取 |
| GB/T 9704 公文排版 | ✅ 可用 | 本机缺「方正小标宋简体」，已自动降级「华文中宋」并在返回值中标注 `font_fallbacks` |
| 微信收到文件的读取与归档 | ✅ 可用（只读） | 直接读 `msg/file/YYYY-MM/` 明文文件，无需密钥、不解密、不操作窗口 |
| **微信消息正文 / 群名 / 发送人** | ✅ **可用（只读）** | 4.1.11+ Config.Cipher+XOR 链路取密钥 → SQLCipher4 解密副本 → 查询 |
| 微信图片 / 语音 / 视频本体 | ⚠️ 部分 | 能看到 `[图片]` 占位与文件引用，媒体本体未解析 |
| 微信群 @ 催办真实发送 | ⚠️ 只读保护 | 为避免桌面焦点抢占与风控，仅生成催办话术并写入 `待发催办通知.md` 供人工粘贴发送 |
| **本机 Office / WPS（COM）** | ✅ **可用** | `work_office_com` 直连 6 个组件（Word/Excel/PPT × MS/WPS）：导出 PDF、重算、刷新域、读写单元格、替换、拿页数字数 |
| OfficeMCP 的 RunPython 等价能力 | ✅ 已内置 | `work_office_com action="run_python"` 注入 `Officer`（Word/Excel/PPT × MS/WPS），执行任意 Python 操控已打开的窗口；不引入常驻 MCP 进程，也不装上游包 |
| OfficeCLI 的 PDF 导出 | ❌ 不支持 | PDF 必须走 `work_office_to_pdf` 或 `work_office_com(action="export_pdf")` |

### 结果口径（避免「报数字」被读成「出故障」）

三个字段专门用来统一转述口径，`SKILL.md` 与 `commands/work.md` 里都有对应条目：

| 字段 | 出处 | 解决什么 |
| --- | --- | --- |
| `summary` | `work_wechat_files(mode='harvest')` / `work_wechat_files(mode='pull')` | `copied=0` + `skipped_duplicate=13` 曾被读成「复制 0 份的故障」，实际是「此前已归档、按去重没再复制」。`summary` 直接把这句话写出来，并给出 `archived_total`（归档现有总数） |
| `consistency` | `work_office_consolidate` | 收件只看时间窗、汇总读归档全量，「未交 5 家」与「合出 3 家」曾并列成矛盾。现在逐个科室对照，`level=warn` 时明确说「本次未交」≠「从未提交」 |
| `checkpoint` | `work.py ask` | 同一句话同时命中收件与汇总时，提前把两步口径差异讲清楚 |
| `scope` / `native_suggestion` | `work.py ask`（`matched:false`） | 不再只说「找不到工具」，而是给出该用哪个原生能力（docx / pptx / pdf / sheet），把「路由失败」变成「换人接手」 |

另外，收件的「已交/未交」改为文件名认不出任何科室时**退回按归档目录整体核对**（`roster_basis=archive_filenames`），
仍然认不出就直说「名单不可信」，不再输出「归档 33 份、已交 0 家」这种自相矛盾的结论。

### 微信消息是怎么读到的（关键结论）

早期结论是「读不到」，因为按旧格式（内存里 `x'96位hex'`）去找密钥，全部失败。真正的突破口是：
**微信 4.1.11 起把密钥字符串 XOR 掩码后藏进 `com.Tencent.WCDB.Config.Cipher` 对象**，
按明文特征串扫描必然扫不到，必须定位该对象再还原。

| 尝试 | 结果 |
| --- | --- |
| 扫内存 `x'96位hex'` 旧格式密钥 | ❌ 511 个候选 PBKDF2 全失败 |
| 32 字节窗口暴力扫派生密钥 | ❌ 355 万候选 0 命中 |
| chatlog v0.0.31「描述符结构 + 指针」法 | ❌ 2796 个候选全失败（官方仅支持 Windows < 4.0.3.36） |
| `wx_key`（DLL 注入） | ❌ 仓库已被 DMCA 清空 |
| 配置文件 DPAPI 加密块 | ❌ 全目录 0 处，密钥不落盘 |
| 内存明文 SQLite 页 | ⚠️ 部分可用（`favorite.db` 28 张表完整；`message_0` 仅 2359/4535 页）——只能当临时补丁 |
| **Config.Cipher + XOR 掩码** | ✅ **23/23 库全部取到密钥，解密后消息读到实时数据** |

实现见 `wechat/wxkey.py`、`wechat/wxdecrypt.py`、`wechat/wxread.py`，
完整说明见 [`WECHAT-CAPABILITY.md`](./WECHAT-CAPABILITY.md)。

**刻意不做的边界**：桌面自动点开群与自动发消息，催办一律落盘待粘贴，保证只读安全。


## 六、工作台网页（可选可视化展示）

> 日常 `/work` 不需要网页；本节只给想看能力清单、数据侧栏或改界面的维护者。

打开 `web/console.html`（双击即可，自包含单文件，无外链）：白底、四列、无边框细线、全圆角。四列分别是 **通讯 / Office / 记忆 / 其他**，共 45 个节点（41 个内置 + 4 个自定义），列数自动写进 CSS 变量，加列不用改样式表。「通讯」把微信与企业微信合成一列，同名能力成对出现、右侧带通道徽标，顶部「通讯总览」一次看两条通道的状态与合并时间线。

### 1. 节点清单

| 列 | 分组 | 节点 | 底层工具 | 性质 |
|---|---|---|---|---|
| 通讯 | 只读数据 | 会话列表 | `work_wechat_sessions` | 只读 |
| | | 群消息 | `work_wechat_messages` | 只读 |
| | | 全文搜索 | `work_wechat_search` | 只读 |
| | | 收件箱 | `work_wechat_files(mode='scan')` | 只读 |
| | | 图片与截图 | `work_wechat_media` | 只读（导出图片） |
| | 材料流转 | 归档入库 | `work_wechat_files(mode='pull')` | 写文件（复制） |
| | | 一条龙收材料 | `work_wechat_files(mode='harvest')` | 写文件（复制） |
| | | 催办话术 | `work_wechat_remind` | 写话术，不发 |
| | | 摘录成文 | `work_wechat_digest` | 写文件 |
| | 环境与安全 | 通道探测 | `work_wechat_probe` | 只读 |
| | | 解锁消息库 | `work_wechat_sync` | 只读 |
| | 旧通道 | 桌面读消息 | `work_wechat_messages` | 窗口不可用时降级读本地库 |
| Office | 汇总与核验 | 汇总母表 | `work_office_consolidate` | 写文件 |
| | | 文档体检 | `work_office_inspect` | 只读 |
| | 公文成稿 | 国标排版 | `work_office_compose(preset="gbt9704")` | 写文件 |
| | | 导出 PDF | `work_office_to_pdf` | 写文件 |
| | 引擎 | OfficeCLI 直调 | `work_officecli_run` | 视命令而定 |
| 记忆 | 留下记录 | 材料台账 | `work_ledger` | 只读（可 record） |
| | | 任务经验 | `work_exp` | 文档（人工整篇复制） |
| | | 改前备份 | `work_backup_file` | 写文件（复制） |
| 其他 | 工作区 | 资料库 | —（目录浏览） | 只读 |
| | | 运行状态 | —（目录浏览） | 只读 |
| | 系统 | 工具箱自检 | `work_status` | 只读 |
| | 待接入 | 定时任务 / 模板中心 | 未接 | 占位 |

### 2. 详情面板

点节点 → 右栏顺序：一句话简介 → **复制提示词** / **直接调用** → 做什么 → 数据区 → 输入输出 / 注意 / 底层工具。
数据区放在「做什么」之后而不是最顶：先看这段功能是干什么的，再去看/挑数据；
（早期数据区紧跟在按钮下面，会话一多就把说明整个挤出首屏，点开节点像是打开了一个列表页。）

需要预选的节点会先给一份可选列表，点一行即选中，`直接调用` 随之解锁；选中项在「已选」条里可一键取消：
- 要文件的：文档体检、生成/重排、批量编辑、改前备份、精确导出 PDF；
- 要会话的：摘录成文、桌面读消息（`browse: pick`，只选会话不展示消息）。

### 3. 结果渲染

`直接调用` 真的执行工具，结果按工具定制，不吐原始 JSON：

| 节点 | 渲染 |
|---|---|
| 通道探测 | 本地状态清单（客户端 / 运行中 / SQLCipher+WAL / DPAPI 密钥 / 收件目录 / 媒体解密） |
| 工具箱自检 | 九个引擎就绪状态 + 底册/母版是否在位 + 各目录文件数 |
| 导出 PDF | 来源 / 产出 / 页数 / 体积 / 引擎 / 耗时（Excel、PPT 的页数由 pypdf 数产物得出） |
| 摘录成文 | 条数、时间跨度、发送人数、嵌图张数、产物名、类型分布 |
| 图片与截图 | 解密后的图片网格（发送人 + 时间），点开进灯箱，缩略图自动换原图 |
| 材料台账 | 总份数、应交/已交/未交、多版本科室、占用体积，科室对齐表 + 未交名单（含联络员电话） |
| 汇总母表 | 读入份数、合并行数、异常条数、母表预览（4 列）、前 3 条异常、结构校验结论 |
| 国标排版 | 文种 / 标题 / 红头 / 页边距 / 产出，接一套字号行，最后是字体降级说明 |
| 文档体检 | 段落 / 表格 / 图片 / 标题数 + 标题大纲 |
| 其他 | 中文标签的键值表，原始返回折叠在底部 |

### 4. 可视化侧栏

- 会话列表：头像块、群名/备注、最后一条摘要、未读数、时间；点一行跳进「群消息」；
- 群消息：聊天气泡（发送人 + 时间 + 正文），自己的发言浅绿底（按账号 wxid 比对）；
- 收件箱：按扩展名着色的文件行，尺寸与收到时间，点一行复制完整路径；
- 全文搜索：关键字输入 → 命中原话（群 / 发送人 / 时间）；
- 资料库：工作区目录树（各目录份数与体积），点进去看文件清单。

数据来自 `web/serve.py` 的只读接口：`/api/comms/overview`、`/api/wx/sessions`、`/api/wx/messages`、`/api/wx/files`、`/api/wecom/sessions`、`/api/wecom/messages`、`/api/wecom/files`、`/api/wecom/contacts`、`/api/tree`、`/api/fs?dir=`。前三个进程内调工具并缓存 20 秒；目录接口只允许 `workspace/` 之下的路径，越界返回 400 级错误。`直接调用` 仍走 `tools/run_tool.py` 子进程，拿超时隔离。

### 5. 版式

启动 `python web/serve.py`，或双击 `scripts/open-console.cmd`（自动构建 + 起服务）。静态打开时数据区提示怎么接上本机，其余功能照常。

- 列表默认收 9–14 行，多的折成「还有 N 份…」；
- 窄于 1080px：四列堆叠成单列，详情变右侧抽屉（关闭钮 / 遮罩 / Esc）；
- 窄于 760px：顶栏只留品牌。

顶部环境数字（微信版本、收件文件数与体积、归档数、成稿数、OfficeCLI 版本）由 `web/build.py` 调 `web/facts.py` 采集（缓存 10 分钟），不是写死文案。

深链：`#comms_overview` `#sessions` `#messages` `#search` `#inbox` `#pull` `#harvest` `#remind` `#probe` `#sync` `#desktop` `#consolidate` `#inspect` `#format` `#backup` `#cli` `#lib` `#runstate` `#status`。

工具增删后重新生成：

```bash
python mcp/dump_tools.py > web/tools.json   # 刷新工具清单
python web/build.py                          # 重新出页面
```

## 七、快速自检

```bash
# 端到端（会真实生成文件）
python mcp/test_client.py

# DSH 插件侧回归
node --test test/*.test.js
```

### 端到端实测基线（37 工具 / 15 接口 / 41 节点前端检查）

| 项 | 实测值 |
|---|---|
| MCP `tools/list` | 41 个公共工具；合体 41 / 通讯 25 / Office 14 / 记忆 6 |
| `tools/call` | 15 次调用全部 `isError=false` |
| 密钥链路 | 23/23 库取到密钥，`sync --force` 刷新 4 库、0 失败、约 5.0s |
| 会话 / 消息 | 24 个会话、单群 400 条可读，群前缀残留 0 条 |
| 收件目录 | 544 个文件 / 1.56 GB，`exts` 过滤无串味 |
| 归档幂等 | 同命令连跑三次：3 → 0 → 0 份，目录恒 3 个文件 |
| 汇总母表 | 扫 5 份、收 3 份、跳过 2 份无关表；异常 12 → 2 条（均为真实差错） |
| 国标排版 | 页边距 37/35/28/26mm 与文档实测一致；三处字体降级已声明 |
| 接口越权 | `../..`、`/etc` → 403；不存在目录 → 404 |
| 前端 | 四列装配台，41 个公共工具，9 个自定义节点，`node --check` 通过 |
| 工具全量 | `regress.py` 101 条用例 / 41 个公共工具，通过 101 / 失败 0 / 约 22.7s；支持 `--module comms|office|memory|other` 单模块验收 |
| 图片解密 | `work_wechat_media` 12/12 张解出，缩略图 210×280 JPEG；单张 `ref` + `prefer=orig` 得 1280×1706 PNG |
| 图片预览 | 灯箱自动换原图（1280×1706 缩放至 690×920，不溢出 1080p）；`/api/img` 越界 403、非图片 415、>24MB 413 |
| Office 转 PDF | Word 1 页 196KB / Excel 2 页 64KB，页数由 pypdf 数产物得出（Excel COM 本身不回报） |
| 摘录成文 | 老吴园区 133 条 → 3 个发送人、跨 2 天、嵌 6 图，转 PDF 15 页 4.0MB |
| CLI 退出码 | 成功 0 / 业务失败 1 / 用法错误 2；修复「输出正确却因退出时线程卡在 read() 段错误退出 139」 |
| 任务经验目录 | 2 份完整任务 md、共 6226 字；`index.md` 一行一份，纯人工浏览 |
| 经验列表 / 取整篇 | `list` 0.8 ms、`get` <1 ms；整篇 2737 字原样返回，不摘要不截断 |
| 经验写入保护 | 同日期同名默认不覆盖（返回 `existing_id`）；`save` 后自动重建 `index.md` |
| 台账 | 3 个科室归位、257 份无科室前缀的文件单列为 `unassigned_files`，重复记录会补齐科室 |

---

## 九、本轮补充验收（2026-11-10，网页优先路线）

上面第四 / 第七节是阶段性基线。这一轮把「别人下载就能用」「持续更新」「任务经验」
三条补成闭环，重新实测如下。

### 1. 一键装机（换台机器不用懂环境）

`scripts\setup.cmd` 一条命令走完 6 步，实测 **工具与桥接验收 8/8 通过**：

| 步骤 | 结果 |
| --- | --- |
| 挑解释器 | 本机 3 个 Python，只有 3.14.4 装齐 `mcp` / `python-docx` / `openpyxl`，自动选中 |
| 补装依赖 | 已齐则跳过；缺时只 pip 白名单包，不装 pandas / PyMuPDF（省体积与时间） |
| 注册 MCP | 写 `~/.workbuddy/mcp.json`，UTF-8 无 BOM，合并保留其他 MCP；解析失败先备份 |
| 构建网页 | `console.html` 生成成功，41 个公共工具 + 9 个自定义节点（通讯 / Office / 记忆 / 其他 四列） |
| 验收 | `work_status` / `work_exp list` / `work_wechat_probe` 全 ok |

`python tools/doctor.py` 逐项体检：必需项全过、2 项可选未装（pandas / PyMuPDF），
每条都带 `→ pip install …`。`--pick` 给脚本用，`--json` 给 CI 用。
**修掉一个会坑新用户的老问题**：`register-workbuddy-mcp.ps1` 原先只测 `docx/openpyxl`、
不测 `mcp`，在有同名精简运行时的机器上会注册到启动不了的解释器。现在统一走
`tools/register_mcp.py`，`--check` 可随时复验。

### 2. 28 工具回归脚本（长期资产）

`python tools/regress.py` → **101 条用例 / 41 个公共工具 / 通过 101 / 失败 0 / 跳过 0 / 约 20 s**；
`--quick` 只跑与机器无关的 31 条，约 13 s。用例分 `run`（真跑只读或 dry）、
`guard`（参数校验生效）、`env`（依赖本机微信数据，缺数据标 SKIP 不算失败）三类，
不拿「报错成功」冒充「功能跑通」。写类工具产物只落系统临时目录并自动清理。

回归抓到并修掉的真问题：

| 问题 | 修法 |
| --- | --- |
| `file=""` 时 `_resolve_path` 退回项目根目录，目录 `exists()` 为真 → 备份报 `PermissionError`、检视返回无文本错误 | 新增 `_file_arg()` 统一校验空值 / 不存在 / 非文件，4 处调用点接入 |
| `build.py` 用字符数报体积（中文页面少报两成） | 按 UTF-8 字节算 |
| 回归脚本自己把秒当毫秒、只认 `error` 不认 `reason` / `detail` | 已修 |

### 3. 接口与前端

| 检查项 | 结果 |
| --- | --- |
| 首页 / status / tree / ledger | 200，分别 2.0 / 3.7 / 4.3 / 7.3 ms |
| `/api/exp?action=list` | 200，228 ms（冷启动，含子进程挑解释器） |
| `/api/run` 真调 `work_exp get` | `ok:true`，整篇 2737 字原样返回，含「五、踩过的坑」章节 |
| `/api/img` 不带参 | 400（预期，非故障） |
| Playwright 前端 | 41 节点、3 列、`LIVE=true`；「通讯总览」两张通道状态卡 + 11 行合并时间线实测有数据、点一行跳到对应通道的消息；点「任务经验」列出 2 份、展开全文 2737 字；**pageerror 0、console error 0** |

### 4. 任务经验文档库：定位校正（本轮最重要的改动）

用户明确指出上一版「记忆」不是想要的东西：粒度太碎，而且 agent 主动召回会污染上下文。
因此把 `work_memory`（SQLite + FTS5 打分检索 + hint 注入 + 自动写钩子）**整体替换为**
`work_exp`（一任务一 md、人工按需整篇复制粘贴）。

| 检查项 | 结果 |
| --- | --- |
| 粒度 | 2 篇样板都是**一整段任务**（图片解密攻关 / Skill 封装），八段骨架齐全，不是会话摘要 |
| 不自动召回 | 模块里没有 `recall` / `hint` / 打分函数；`list` 只按日期排序 |
| 不自动写 | 服务端 `_mem_note` 两处自动写钩子已删除；Skill 明令任务开始不查、收尾不存 |
| 不注入上下文 | 前端没有搜索框、没有置顶/失效；页面加载不读任何 md 内容 |
| 五个动作 | `list` / `get` / `template` / `save` / `export` 全部 CLI 实测通过（含缺 `doc_id` 的 guard） |
| 写入保护 | 同名同日期 `save` 返回 `ok:false` + `existing_id`，不误覆盖；结构化字段按骨架落盘 8 章节 |
| 旧数据 | `workspace/10_工作记忆/memory.db` 保留但停用，不 import、不写入，可随时删除 |

设计取舍与旧版踩过的坑见 **`docs/EXPERIENCE.md`**。

### 5. 体量与路线

- 网页产物 `web/console.html` **约 112 KB 单文件**，无外链 / 字体 / 图片，双击即开。
- 可分发代码与文档（服务端、经验库、网页、工具、脚本、文档，含 `console.html` 与模板两份）合计 **约 560 KB**；
  不含 `bin/officecli.exe`（外部二进制，32 MB）与 `workspace/` 里的本机数据。
- 经验模块 `experience/library.py` 约 270 行纯标准库（普通 Markdown 文件），另有 doctor / regress / register /
  setup / launch 共 1086 行，同样零第三方依赖。
- 日常可独立使用 `work-office` / `work-comms` / `work-memory` 三个 Skill 与对应 MCP；`/work` 仅用于跨模块组合。
  不打开网页、不信任 MCP，也能通过 `scripts/work.py` 本地桥接调用工具。

**Skill 优先，网页只做可视化**：模块可单独调用，也可通过 `/work` 组合；用户改功能只碰
`custom_nodes.json` 或让维护 Skill 改模板；等能力边界与验收基线稳定，再考虑套一层 webview
小窗（复用同一个 `console.html`），不必重写前端。
