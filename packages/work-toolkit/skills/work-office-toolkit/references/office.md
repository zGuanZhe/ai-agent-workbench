# Office：改文档、COM 操控、实时预览

> 归属**Office 模块**（`mcp/work_mods/office.py`，独立入口 `mcp/work_office_server.py`）。
> 改这块只动 office.py 与本文件；验收 `python tools/regress.py --module office`。

## 先选路

| 要干的事 | 用哪条 | 理由 |
| --- | --- | --- |
| 批量改内容、调格式、加段落表格、套模板、导 CSV | **OfficeCLI**（离线） | 快、不依赖装 Office、不弹窗口 |
| 导出 PDF（版面要和「另存为 PDF」一致） | **COM** `export_pdf` | 真实应用排版最准，公文红头与页码不会跑 |
| 重算公式、刷新域/目录/交叉引用 | **COM** `recalc` / `refresh_fields` | OfficeCLI 算不了 Word 的域 |
| 用户正开着文档，要往里写 | **COM** `run_python` | 直接接管他眼前的窗口，改完看得见 |
| 只读看结构 / 区域读表 / 提取批注 / 高清截图 / 加载构建指南 | `work_office_inspect` | 纯只读，不锁文件，支持 `text/outline/stats/issues/comments/screenshot/html/svg/pdf/skills` |
| Markdown（含表格、Mermaid 流程图、LaTeX 公式）一键合成 `.docx` / `.pptx` | `work_office_compose` | 原生可编辑 Word/PPT，支持目录 `toc=true`、水印与 4 套排版预设 |
| 两版 Word 或两张 Excel 差异对比（含数值增减 delta） | `work_office_diff` | 自动对比段落/表格/单元格变化，可选导出 Markdown 差异报告 |
| 多份 `.docx` 一键合并汇编为单份 Word | `work_office_join_docx` | 保留各自段落样式与表格，可选分页符与标题 |
| 无模板/有模板跨表合并（`.xlsx`/`.csv`/`.docx`）与异常核验 | `work_office_consolidate` | 支持 `auto_schema=True` 自动推导表头与数值列求和 |
| 原生表格/文档能力覆盖不到的**高级 Excel (46) / Word (58)** 离线批量操作 | 克隆引擎桥 `work_excel_mcp` / `work_word_mcp` | `action="list"` 直接返回完整参数 Schema；`action="call"` 自动归一化 `path/file/filename` 与裸文件名 |

## 克隆的 Office / MCP 引擎：什么时候用（新增）

`mcp_servers/` 下克隆的项目以前只躺在磁盘上，现在有两个真正接进调用链：

| 工具 | 引擎 | 能补什么 | 默认 |
| --- | --- | --- | --- |
| `work_office_engines` | 盘点全部克隆仓库 | 逐个**实际 import 入口、数工具数**，给出 `ready` 与对应桥接；路径存在 ≠ 能用 | 只读 |
| `work_excel_mcp` | excel-mcp-server 2.2.9（46 个工具） | 透视表、图表/切片器、条件格式、数据验证、公式追踪与目标求解、VBA 只读查看 | `read_only=true` |
| `work_word_mcp` | Office-Word-MCP-Server（58 个工具） | 文档大纲/XML 检视、批注、脚注尾注、文档保护、按锚点插入段落表格、页眉页脚 | 默认只放行 `get_/list_/find_/search_/view_` 前缀 |

用法固定两步，别猜工具名：

```bash
work_excel_mcp(action="list", keyword="pivot")      # 先列工具名与 read/write 标记
work_excel_mcp(action="call", tool="describe_workbook", args='{"path":"...xlsx"}')
work_word_mcp(action="list", keyword="comment")
work_word_mcp(action="call", tool="get_document_info", args='{"filename":"...docx"}')
```

三条硬口径：

1. **先体检再报能力**：`work_office_engines` 的 `ready` 是唯一依据。OfficeMCP 依赖
   Windows COM，wechat-chat-mcp 依赖独立 `.venv`——
   要按真实探测状态说明，不能把「路径存在」说成「全部能用」。
2. **默认只读，写要显式授权**：两个桥默认拦截写操作；确要写，传 `allow_write=true`，
   工具会先把目标文件复制到 `02_自动备份区/` 并在返回里给 `backup` 路径。
   Excel 走引擎层 `Settings(read_only=True)` 双重拦截（只读实例压根不注册写工具）。
3. **不抢原生能力的活**：单张表改几个单元格/加公式/画图/透视、写报告写通知，
   仍用 WorkBuddy 原生表格与文档能力。这两个桥只补**原生能力覆盖不到**、
   或需要离线批量复现的高级操作；`action=list` 是查手册，不是执行。

输出可能很大，`max_chars` 控制截断；被截断时按 `hint` 用 `range` / `path` 收窄范围，
不要把整个工作簿、整篇 XML 一次拉进上下文。

## 路径怎么写（最容易错的地方）

| 格式 | 路径 | 例子 |
| --- | --- | --- |
| docx | `/body/p[N]`、`/body/tbl[N]/tr[M]/tc[K]` | `/body/p[1]` |
| xlsx | `/<表名>/<A1>` —— **表名要真实存在** | `/2026年11月汇总/A1` |
| pptx | `/slide[N]/shape[M]` | `/slide[1]/shape[2]` |

表名不是 `Sheet1` 就去查：错误信息里会列出 `Available sheets`。
属性名不确定先 `work_office_schema(format="xlsx", element="cell")`，**不要凭印象编 `--prop`**。

## schema 怎么查

`work_office_schema` 必须带 `format`，否则只返回总览（`mode=overview`），答不出属性名。

```
work_office_schema(format="docx")                        # 有哪些元素
work_office_schema(format="docx", verb="set")            # set 能改什么
work_office_schema(format="docx", verb="set", element="paragraph")   # 属性名与可选值
```

## 批量改

多步操作用 `work_office_batch`，一次开关文件，比逐条 add/set 快：

```json
[{"command":"add","parent":"/body","type":"paragraph","props":{"text":"一、总体情况"}},
 {"command":"set","path":"/body/p[3]","props":{"align":"center","bold":"true"}}]
```

`ops` 可传 JSON 字符串或 `.json` 文件路径。写完 `command=validate` 校验。
**没有 dry_run**，想先看结构用 `work_office_inspect` 或 `work_officecli_run command=get`。

## 从零造、套模板、导数据

| 工具 | 用途 | 要点 |
| --- | --- | --- |
| `work_office_create` | 建空白 docx/xlsx/pptx | 已存在会拒绝；`force=true` 才覆盖，覆盖前先备份并告知用户 |
| `work_office_merge` | 模板套打 | 模板里写 `{{key}}`，`data` 传 JSON 对象 |
| `work_office_import` | CSV/TSV 进表 | `sheet` 要给真实表名 |
| `work_office_batch` | 一次做多步 | `ops` 是 JSON 数组，见上 |
| `work_office_schema` | 查属性名 | 必带 `format`，否则只有总览 |
| `work_officecli_run` | 直调 OfficeCLI | `command` 白名单 27 条，写操作结束自动 flush 落盘 |

## 实时预览（边改边看）

```bash
work_office_watch(action="start", file="...")   # 返回 http://127.0.0.1:26315
work_office_watch(action="status")
work_office_watch(action="stop")
work_office_watch(action="goto", file="...", path="/body/p[3]")   # 预览滚到某元素
work_office_watch(action="mark", file="...", path="/body/p[1]")   # 标记元素
work_office_watch(action="marks", file="...")   # 列出所有标记
work_office_watch(action="unmark", file="...", path="/body/p[1]", all=false)
```

起来之后，所有 `work_officecli_run` / `work_office_batch` 的改动都会自动刷新到页面。
只认 officecli 自己的改动，外部程序直接改文件不会被感知。用完记得 `stop`，别一直占端口。
用户没要求「看着改」就别起。

## 导 PDF：两条路别混

| 用哪个 | 什么时候 | 代价 |
| --- | --- | --- |
| `work_office_to_pdf(file=..., output=...)` | 一般文档；要求快、机器上没装 Office | OfficeCLI 排版，复杂版式可能有细微差异 |
| `work_office_com(action="export_pdf", file=..., engine="auto")` | 公文、带红头页码、分节复杂 | 要真实 Office / WPS，会拉起应用 |

给产物路径和页数；两条路都不覆盖原文件。`engine` 可指定 `ms` / `wps`。

## COM：直连本机 Office / WPS

`work_office_com` 的 `engine` 可选 `auto` / `ms` / `wps`；
`KWPS / KET / KWPP` 分别对应 WPS 的文字、表格、演示，`auto` 会挑装了的那个。

| action | 干什么 | 必给 |
| --- | --- | --- |
| `apps` | 装了哪些组件、哪些正在运行 | — |
| `active` | 每个应用当前打开/激活的是哪个文件 | — |
| `info` | 页数、字数、表数、幻灯片数 | `file` |
| `export_pdf` | 用真实应用导 PDF | `file`（`output` 可选） |
| `recalc` | 重算 Excel 并存盘 | `file` |
| `refresh_fields` | 刷新 Word 域与目录 | `file` |
| `replace` | Word 查找替换 | `file`, `find`（`replace` 可空=删除） |
| `read_cells` / `set_cells` | 读写单元格 | `file`（`sheet`, `range` / `cells`） |
| `run_python` | 执行 Python 操控应用 | `code` |

`active` 返回里的 `saved=false` 表示有未保存改动——别当成已经落盘。

## run_python 怎么写

脚本里能直接用三个名字：`Officer`（也叫 `office`）、`data`、`output`。
`Officer.Word` / `.Excel` / `.PPT` 优先**接管用户已经开着的窗口**，没有再新建（默认可见）。

```python
xl = Officer.Excel
ws = xl.ActiveSheet                 # 用户正看着的那个表
ws.Cells(1, 1).Value = "科室"        # 集合从 1 开始，不是 0
ws.Cells(2, 1).Value = data          # data 是调用时传进来的字符串
ws.Parent.Save()                     # 不 Save 不落盘
output = {"a1": ws.Cells(1, 1).Value}
```

```python
doc = Officer.Word.ActiveDocument
rng = doc.Content
rng.Find.Execute(FindText="甲方", ReplaceWith="乙公司", Replace=2)   # 2 = 全部替换
doc.Save()
output = f"替换完成，共 {doc.Characters.Count} 字"
```

- 打印的东西进 `stdout`，最终结果放 `output`（会被 JSON 化后返回）。
- `keep_open=true`（默认）表示拉起的应用留着不关，用户能看到；`false` 则执行完就关。
- 改完一定 `Save()`，否则只是内存里改了。
- 固定动作够用就别上 run_python：导 PDF 用 `export_pdf`、读写单元格用 `read_cells`/`set_cells`。

## 汇总与核表的口径

`work_office_consolidate` 会写 xlsx 和《异常数据核验单》，**没有 dry_run**。返回里：

| 字段 | 怎么用 |
| --- | --- |
| `merged_records_count` | 合出几条记录 |
| `anomalies` | 勾稽差额、手机号位数等，逐条转述 |
| `officecli_validation` | `success=false` 先修再交付 |
| `consistency` | 与上次收件的口径对照 |

`consistency.level=warn` 说明某些科室在收件环节被判「本次未交」，但归档里有它们的旧材料。
这时要讲清「**本次未交**」不等于「**从未提交**」，催办名单仍以 harvest 的 `missing_departments` 为准。

## 只读看结构

`work_office_inspect` 必需的只有 `file`；`mode` 决定看哪一面：
`text` 纯文本（xlsx 给 A1=值，可配 `cell_range` 如 `A1:D20`）、`outline` 结构大纲、
`stats` 统计、`annotated` 带标注、`issues` 问题清单、`forms` 表单。
PDF 可加 `page`。**没有 `cells` 这个 mode**，按区域读表用 `text` + `cell_range`。
