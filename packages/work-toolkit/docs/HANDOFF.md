# 交接文档：work-super-agent（办公工具箱 / `/work`）

> 面向接手的 Agent。本文档写于 2026-11-12 20:10（GMT+8），是**当前仓库的实况快照**，
> 不是设计稿，也不是宣传材料。所有数字都在写文档前重新实测过。
> 接手后请先读第 0 节和第 7 节，再动任何代码。

---

## 0. 交接快照

| 项 | 当前值 |
| --- | --- |
| 仓库根 | `<WORK_ROOT>` |
| 工作区（父目录） | `<WORKSPACE_PARENT>` |
| Git HEAD | 交接文档自身的提交（`git log -1` 可见，hash 会随本文档修订变化）；**代码基线**是 `ef972e3`「记忆与其他也拆成独立模块」 |
| 工作区状态 | 干净（`git status --short` 无输出），无未提交改动、无临时脚本残留 |
| 最近提交链 | 交接文档（本文档）→ `ef972e3` 四模块拆分 → `9738630` 通讯/Office 两模块拆分 → `199db47` 通讯合并 |
| 工具总数 | **42**（通讯 22 / Office 15 / 记忆 3 / 其他 2） |
| 入口 | 合体 42、通讯 24、Office 17、记忆 5（均为 stdio `tools/list` 实测） |
| 全量回归 | **82 条用例 / 通过 82 / 失败 0 / 跳过 0**（本次复跑约 38 秒） |
| 意图路由 | `web/intents.json` 53 条，`check_intents.py` 全合法 |
| 控制台 | 四列（通讯 / Office / 记忆 / 其他），45 个节点（含 4 个自定义），`console.html` 约 176 KB |
| Skill | 用户级 `<USER_HOME>\.workbuddy\skills\work-office-toolkit\`，唯一入口 `/work`，已同步 |
| MCP 注册 | `<USER_HOME>\.workbuddy\mcp.json` 里只有一项 `work-toolkit`，指向合体入口 |
| 环境体检 | `work.py doctor`：必需项全过，2 项可选依赖未装（pandas、PyMuPDF，不影响主流程） |

**当前没有进行中的开发任务。** 用户最后一条需求是「写出详细交接文档」，即本文档。
上一轮需求「记忆和其他也作为两个模块」已完整交付并提交。接手后不需要「继续做完某件事」，
应等待新需求；若要做改进，第 10 节列了按优先级排好的候选项，第 9 节是必须先知道的遗留风险。

---

## 1. 这个项目是什么，边界在哪

一个**本机办公批量流水工具箱**，给 WorkBuddy 用，唯一用户入口是斜杠命令 `/work`。
三条业务主线：

1. **通讯**（只读）：跨微信 4.2 与企业微信，读会话/消息/搜历史、收材料归档、催办话术、图片解密。
   核心是 `work_comms_notices`——一次调用拿最近 N 天的跨通道时间线，通知优先排序，
   把「有什么值得注意的通知」从原来的十几轮逐会话读取压到一次调用（约 1.1 秒）。
2. **Office**：多科室表格跨表汇总核表、OfficeCLI 检视/编辑/批量、COM 实时操控、导 PDF，
   以及桥接克隆的 excel-mcp-server（46 工具）与 Office-Word-MCP-Server（54 工具）。
3. **记忆**：材料台账（谁交了什么）、任务经验文档库（一任务一份 md）、改前文件备份。

**必须守住的边界**（用户明确要求，写进 SKILL.md 第「零」节）：

- 只在用户输入 `/work`，或明确要求上述批量流水时才动这个工具箱。
- 写文档、做 PPT、读 PDF、改单张表、写代码、查资料，一律用 WorkBuddy 原生能力，**不要提案 `work_*` 工具**，
  回复里也别提这个 Skill。
- **GB/T 9704 国标公文已降级为「按需附加能力」**，只有用户明确说国标/红头/9704 才用，
  不是核心功能，意图路由不优先命中，也不要在宣传口径里把它摆回主流程。
- 工具箱没有的能力，用原生能力正常完成并说明，**不要写一次性脚本模仿工具箱**。
- 通讯侧**全程只读**：不发消息、不假装 @ 到人、不做桌面自动化发送。
- 「任务经验」v2 由真实工具调用轨迹生成（`work_exp action=distill`），含精确工具与参数的零探索配方；
  **不自动召回、不注入**，只在用户说「整理为经验 / 搜经验 / 按经验做」时调用；拿到配方按顺序直调，不再 list / schema / probe。
  通用跨会话记忆走开源 Engram（`work_mem`）。详见 docs/EXPERIENCE.md。
- 话不要说过头：会写产物就给路径与关键数字，没有 `dry_run` 的步骤不要宣称能预演。

---

## 2. 环境与启动

**唯一正确的解释器**（其它两个 3.13 没装齐依赖）：

```
<USER_HOME>\AppData\Local\Programs\Python\Python314\python.exe      # Python 3.14.4
```

文档与脚本里凡写 `python`，都指这一项。下文用 `$PY` 代称。

**常用命令**（在仓库根执行）：

```bash
$PY scripts/work.py doctor                # 环境体检：依赖、微信目录、Skill、控制台、端口
$PY scripts/work.py ask "<用户原话>"       # 第一步：把原话路由成确定的工具执行计划（硬路由，非猜）
$PY scripts/work.py call <工具名> '<JSON>' # 直接调一个 work_* 工具，不经 MCP、不经网页
$PY scripts/work.py tools                 # 精简工具清单
$PY scripts/work.py tools --module comms  # 只看该模块工具（comms|office|memory|other）
$PY scripts/work.py tools <工具名>        # 看某个工具的完整参数说明
$PY scripts/work.py modules               # 四模块归属、入口、验收命令
$PY scripts/work.py modules --json        # 机器可读版（含每个模块的工具名列表）
$PY scripts/work.py web                   # 本地起服务并开控制台（可选展示，非主路径）
```

`ask` 是关键：它读 `web/intents.json` 做确定性匹配，返回 `plan` / `scope` / `matched`。
`matched:false` 或 `scope:out_of_scope` 时看 `native_suggestion` 换原生能力，**不要再去翻工具箱**。

**MCP 注册**（`<USER_HOME>\.workbuddy\mcp.json`，只有一项）：

```json
{
  "mcpServers": {
    "work-toolkit": {
      "command": "<USER_HOME>\\AppData\\Local\\Programs\\Python\\Python314\\python.exe",
      "args": ["<WORK_ROOT>\\mcp\\work_toolkit_server.py"],
      "env": { "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1" }
    }
  }
}
```

若要改成单模块入口，只需换 `args` 里的脚本名（`work_comms_server.py` /
`work_office_server.py` / `work_memory_server.py`）；也可以并列注册多个服务。

**Skill 安装**：仓库侧 `skills/work-office-toolkit/SKILL.md` 用 `<WORK_ROOT>` 占位符，
`scripts/install_skills.py` 会按当前仓库位置替换后写到用户级目录。**不要手工编辑用户级 SKILL.md**，
改仓库里的源文件再跑安装脚本；脚本按内容比对，一致则跳过，不一致会自动同步并备份到
`~/.workbuddy/skills.backups/`（这个目录在 skills 扫描根之外，避免被当成独立 Skill 重复加载）。

**本机数据依赖**（doctor 已确认）：

- 微信 4.2 数据：`D:\聊天记录\xwechat_files\<YOUR_WXID>`，SQLCipher 只读解密，密钥自研链路取得。
- 企业微信：`D:\聊天记录\WXWork\<YOUR_WECOM_ID>`，wxSQLite3 加密，**密钥只在 `WXWork.exe` 进程内存里**，
  客户端没开就读不了（当前 doctor 显示「客户端没开 · 密钥已缓存」，能读缓存但换账号/升级后要重扫内存 20–30 秒）。
- Office COM 需 Windows + Office/WPS；`pywin32`、`comtypes` 已装。

---

## 3. 架构与模块边界（改代码前必读）

```
mcp/work_core.py              公共内核 530 行：路径常量、_dump/_run/_resolve_path、
                              ToolBox 注册器、annotate 参数说明注入、_port_open。
                              【不写任何业务工具】
mcp/work_mods/
    __init__.py               MODULES 字典、MOUNT_ORDER、boxes()、describe()
    comms.py    1820 行      通讯 22 个（只读）
    office.py   1568 行      Office 15 个
    memory.py    223 行      记忆 3 个（台账 / 经验 / 备份）
    other.py     172 行      其他 2 个（状态总览 / 模块清单），公共底座
mcp/work_toolkit_server.py    69 行，合体入口：只挂四个 ToolBox + annotate + globals() 铺平
mcp/work_comms_server.py      58 行，其他 + 通讯 = 24
mcp/work_office_server.py     59 行，其他 + Office = 17
mcp/work_memory_server.py     58 行，其他 + 记忆 = 5
```

| 模块 | 工具数 | 独立入口 | 单独验收 |
| --- | --- | --- | --- |
| 通讯 `comms` | 22 | `work_comms_server.py`（其他+通讯 24） | `regress.py --module comms` |
| Office `office` | 15 | `work_office_server.py`（其他+Office 17） | `regress.py --module office` |
| 记忆 `memory` | 3 | `work_memory_server.py`（其他+记忆 5） | `regress.py --module memory` |
| 其他 `other` | 2 | **无独立入口**，每个入口都挂 | `regress.py --module other`，但改它要跑全量 |

**不变的规则**：

1. `comms` / `office` / `memory` **互不 import**，唯一共享面是 `work_core`。`other` 只在 `work_status`
   里按 id **懒加载**业务模块（`_module()`），模块没装就如实标「未加载」，绝不让状态总览整体报错。
2. 「其他」故意不设独立入口——它本身就是每个入口的公共底座；改它必须跑**全量**回归。
3. 「记忆」的划分口径是**把东西留下来**：收过什么（台账）、做过什么（经验）、改前的原件（备份）。
   「其他」是**看整体与看划分**。别把这两个的归属改回去。
4. `work_status` 不再自己算台账/经验状态，改调 `memory.status_snapshot()`；引擎权威判定是
   `work_office_engines`（真实 import + 数工具数），`work_status.engines` 只是入口自查。
5. 合体入口用 `globals().update(tool_map(boxes()))` 把工具函数铺进模块全局——
   `run_tool.py`、控制台、回归、外部脚本全都用 `getattr(模块, "work_xxx")` 的老调用面，
   **这行不能删**，删了整个兼容层就断。
6. 注册器是轻量的 `ToolBox`，**不是** MCPServer 导入。CLI 直调走轻量注册器，避免每次调用付
   约 0.9 秒的框架导入开销；只有 stdio 服务入口才 `make_server()`。
7. `mcp/` 目录与 PyPI 包 `mcp` 同名：**别把项目根或 `mcp/` 插到 `sys.path` 最前面**，否则协议库导入会被遮蔽。

---

## 4. 代码地图（按「我要改 X」索引）

| 想改什么 | 去哪 |
| --- | --- |
| 加/改工具实现 | `mcp/work_mods/{comms,office,memory,other}.py`，用 `@TOOLS.tool(name=..., description=...)` 装饰 |
| 工具的中文参数说明 | `mcp/work_core.py` 的 `_PARAM_DOCS` / `_PARAM_REQUIRED`（`annotate()` 注入 schema） |
| 路径常量、公共底座函数 | `mcp/work_core.py` 第 73–330 行 |
| 挂载顺序、模块元数据 | `mcp/work_mods/__init__.py`（`MOUNT_ORDER`）与每个模块的 `MODULE` 字典 |
| 新增一个模块 | 新建 `work_mods/<id>.py`（含 `MODULE` + `TOOLS`），在 `__init__.py` 注册，写入口，再按第 5 节全链路刷新 |
| 意图路由 | `web/intents.json`（`keywords` 权重 + `note` 口径），改完跑 `tools/check_intents.py` |
| CLI 命令 | `scripts/work.py`（子命令 `ask/call/tools/modules/doctor/setup/web`） |
| 回归用例 | `tools/regress.py`（用例表在第 30–130 行，`_BRIDGE_MODULE` 在第 301 行附近） |
| 控制台列 / 节点 | `web/_console_template.html`（`COLS`、`*_GROUPS`、`MODULE_LABEL`），`web/custom_nodes.json` |
| 工具清单导出 | `mcp/dump_tools.py` → `web/tools.json`（带 `module` 字段） |
| 单文件页面构建 | `web/build.py` → `web/console.html`（**被 .gitignore 忽略**） |
| Skill 文案 | `skills/work-office-toolkit/{SKILL.md, commands/work.md, references/*.md}` |
| Skill 同步 | `scripts/install_skills.py` |
| 控制台维护规范 | `skills/work-console-upgrade/SKILL.md` |
| 面向用户的总说明 | `README.md`、`INSTALL.md`、`docs/INTEGRATION-WorkBuddy.md` |
| 微信/企微底层链路 | `wechat/`、`wecom/`、`python_workers/` |
| 克隆的第三方引擎 | `mcp_servers/`（excel-mcp-server、Office-Word-MCP-Server、OfficeMCP、wechat-chat-mcp） |

`references/` 现有 6 份：`wechat.md`、`wecom.md`、`office.md`、`memory.md`、`gongwen.md`、
`troubleshooting.md`。SKILL.md 是薄入口，细节**按需读**，不要一次全读。

---

## 5. 标准变更 SOP（每次改完都要走完，缺一步就会「代码对了但用户看到的还是旧的」）

```bash
cd D:/Test/work1/work-super-agent
PY="<USER_HOME>/AppData/Local/Programs/Python/Python314/python.exe"
# 下文 $PY 即上面这个解释器；别用系统 python 或 WorkBuddy 自带的 3.13

# 1) 改代码：只动对应模块文件，别往入口里写实现
# 2) 工具增删/改名/改参数后，刷新工具清单（会另起子进程做 tools/list 与模块映射）
$PY mcp/dump_tools.py > web/tools.json

# 3) 校验网页节点与意图
$PY tools/check_intents.py
$PY tools/check_nodes.py

# 4) 重建单文件页面
$PY web/build.py

# 5) 抽出内联 <script> 单独做 JS 语法校验（build.py 成功 != JS 语法正确，见第 8 节）
$PY -c "
import re,pathlib
h=pathlib.Path('web/console.html').read_text(encoding='utf-8')
js='\n;\n'.join(re.findall(r'<script[^>]*>(.*?)</script>', h, re.S))
pathlib.Path('web/_check.js').write_text(js, encoding='utf-8')
"
node --check web/_check.js && rm -f web/_check.js

# 6) 同步 Skill 到用户级（改了 skills/ 才需要）
$PY scripts/install_skills.py

# 7) 回归：改单模块跑对应的 --module，改 other/work_core/入口 跑全量
$PY tools/regress.py --module comms
$PY tools/regress.py                 # 全量

# 8) 文档口径同步（README / INSTALL / 集成说明 / SKILL / 命令说明）
# 9) git add -A && git commit
```

**容易漏的三点**：

- `web/console.html` 在 `.gitignore` 里。只提交模板不会更新用户双击打开的那份——
  本机已经构建过，但**换机器/clone 后必须重新 `build.py`**。
- 仓库 SKILL.md 用 `<WORK_ROOT>` 占位，用户级必须是替换后的绝对路径。检查残留用
  `grep -rn "WORK_ROOT" <USER_HOME>/.workbuddy/skills/`（应为空），仓库侧则应只有占位符。
- 工具数/入口数/用例数/节点数是文档里的硬基线，任何一处变了要全局搜一遍改掉
  （历史上多次出现「代码对了、文档还写着 27 个工具」）。

---

## 6. 验收清单与基线数字

| 检查 | 命令 | 期望 |
| --- | --- | --- |
| 全量回归 | `$PY tools/regress.py` | 82 条 / 通过 82 / 失败 0 / 工具登记 42 |
| 单模块回归 | `regress.py --module comms\|office\|memory\|other` | 见本节下方数字，全通过 |
| 意图表 | `$PY tools/check_intents.py` | 53 条全合法 |
| 节点表 | `$PY tools/check_nodes.py` | 0 提示 |
| JS 语法 | 抽 `<script>` 后 `node --check` | 通过（`node --check` 不认 `.html`，别直接对 html 跑） |
| 模块清单 | `$PY scripts/work.py modules --json` | `count:4`、`total_tools:42` |
| 工具分布 | `web/tools.json` | `{'comms':22,'office':15,'memory':3,'other':2}` |
| stdio 握手 | 用 `mcp.ClientSession` 对四个入口 `list_tools()` | 42 / 24 / 17 / 5 |
| 引擎体检 | `$PY scripts/work.py call work_office_engines '{}'` | `total:7, usable:4, bridged:4` |
| 控制台渲染 | Playwright 打开 `console.html` | `--ncol=4`、45 节点、pageerror 0 |

单模块回归本次全部复跑通过：**通讯 38 / Office 42 / 记忆 14 / 其他 6**（各自失败 0、跳过 0）。
`other` 的用例在每个模块的验收里都会被带上，所以四个数字相加大于 82 是正常的；
「工具登记 42 个」在单模块模式下也仍是全量登记数，不是该模块的工具数，别误读。

`work_office_engines` 的权威结论（**不要凭「目录存在」判断可用**）：

| 引擎 | ready | 说明 |
| --- | --- | --- |
| excel-mcp-server 2.2.9 | true | 已桥接 `work_excel_mcp`，46 工具 |
| Office-Word-MCP-Server | true | 已桥接 `work_word_mcp`，54 工具 |
| OfficeCLI v1.0.160 | true | `bin/officecli.exe` |
| OfficeMCP (COM) | true | 源码在、pywin32 可用；工具箱已有等价 COM 封装，不重复起服务 |
| wechat-chat-mcp | **true** | 独立 `.venv`，提供 `wechat_chat` 多模态深度检索 MCP |

---

## 7. 用户既定口径与红线（别改回去）

1. **GB/T 9704 是附加能力，不是核心**。用户原话：「GB/T 9704 国标公文不再作为核心功能」。
   工具 `work_office_format_gbt9704` 保留，但 README / SKILL / 意图 / 控制台 / 命令文档的口径都已降级。
2. **克隆的 Office 项目要真接入，不能只报「路径存在」**。用户原话：「克隆好那么好的 office 项目和 mcp，
   好像没有充分利用」。因此有 `work_office_engines` 的真实 import 体检，缺 COM 的、版本不适配的、
   只有 README 的，一律如实标 `false`。
3. **通讯要快、要「最近的就行」**。用户原话：「现在你思考调用的时间太长，而且最近的就行」。
   所以 `work_comms_notices` 默认 `days=3`、`order=notice`、通知优先、due 保留原文不硬解析；
   泛问**一次命中它**，不许再拼 `overview` + 逐会话 `messages` + 全文 `search` 十几轮。
   当前实测 `ask` 约 0.37 s + 一次 `call` 约 0.76 s ≈ 1.13 s。
4. **模块化：通讯 / Office / 记忆 / 其他，各改各的**。这是用户分两次提出并确认的结构
   （先「分为通讯和 office 两个模块」，后「记忆和其他也作为两个模块」）。
5. **不虚报**：引擎可用性、模块加载状态、写操作是否发生、能否 dry_run，一律如实说。
6. **安全**：两个 MCP 桥默认只读；确要写必须显式 `allow_write=true`，工具会先把目标文件
   复制到 `workspace/02_自动备份区/` 并在返回里给 `backup` 路径。
   `work_office_create` 对已存在文件默认拒绝，`force=true` 才覆盖且覆盖前先备份。
7. **工作区数据不进版本控制**：`.gitignore` 排除 `/workspace/**`（仅放行 `11_任务经验/*.md`）、
   `/outputs/`、`/web/.facts.json`、微信密钥与扫描结果、`*.db` 等。别把本机聊天数据提交上去。

---

## 8. 已知坑与事故记录（血泪，务必先看）

1. **拆分脚本二次运行把源码清空**（最严重）。第一版拆分脚本的 `SRC` 指向
   `mcp/work_toolkit_server.py`，而脚本本身会把这个文件覆盖成薄壳；第二次运行时它读到的是空壳，
   所有代码切片都变空，实现全丢。最后靠 WorkBuddy 的 file-history 备份
   （`<USER_HOME>\.workbuddy\file-history\...\c6109403ac68aec1@v4`，4052 行原文件）才恢复。
   **规则**：做任何批量切分/重写前，先把源文件冻结成只读副本，`SRC` 永远指向副本；
   跑完必须 `grep -c "^def work_" <各模块文件>` 加总等于工具数（42），确认一个都没丢再删临时脚本。
2. **`build.py` 成功 ≠ JS 语法正确**。搬控制台节点时，`experience` 是组内最后一个节点、
   **没有尾逗号**，拼进新数组后变成 `[{...}{...}]`，直接 SyntaxError；而 `build.py` 照样正常退出。
   **规则**：搬节点时统一补尾逗号再拼；构建后必须把 `<script>` 抽出来单独 `node --check`。
3. **`node --check` 不接受 `.html`**。别对 `console.html` 直接跑，要先抽内联脚本。
4. **`.modtag` 与 `.mtag` 冲突**。`.mtag` 原本是「推荐」徽标，模块徽标另起 `.modtag` 样式，别复用。
5. **CSS `--ncol` 默认值仍是 3**，但运行时 `document.documentElement.style.setProperty("--ncol", COLS.length)`
   会覆盖成 4。加/删列时改 `COLS` 即可，不用动样式表；默认值 3 只是历史残留，属低优先级清理项。
6. **`mcp/` 目录名与 PyPI 包 `mcp` 同名**，doctor 里专门有一条提示，别把项目根插到 `sys.path` 最前。
7. **企业微信客户端关着就读不了**（密钥在进程内存）；冷启动首次解密可达 1–2 分钟，
   回归测试里的 `env` 类用例依赖本机微信数据，换机器可能跳过或失败，属预期。
8. **回归里的模块归属有两套映射**：工具用例按 `tool_module`，CLI/桥接用例按 `_BRIDGE_MODULE`。
   新增用例时**两边都要登记**，否则 `--module xxx` 会把它误筛掉或误带上。默认归属是 `other`。

---

## 9. 遗留风险与未完成项（如实标注，别当成已验收）

1. **桥接的 46 个 Excel 工具、54 个 Word 工具只做过冒烟验证**：列工具、读一个 xlsx、
   写操作被只读策略拦截，这三项通过；**没有逐个跑过**。默认只读、写前自动备份的保护策略不变，
   但真实调用某个具体工具仍可能报错。若要推进，建议先挑高频的十来个做真实文件回归。
2. **`work_status` 的 `modules` 字段只列三个业务模块**（`comms/office/memory`），不含 `other`。
   这是有意的（列的是业务模块，不是挂载清单），但与 `work_modules` 返回的 4 个不一致；
   如果接手者要改，先确认用户是否在意这个差异，别顺手「修」成一致而破坏现有断言。
3. **可选依赖未装**：`pandas`（大表汇总加速）、`PyMuPDF`（PDF 文字层）。doctor 明确标为可选，
   不影响主流程，但涉及大表或 PDF 文字提取时能力受限。
4. **`docs/console-preview.png` 是本机 Playwright 截图**，四列版已重拍；若再改列或节点，记得重拍。
5. **企业微信密钥缓存**：当前 doctor 显示客户端没开但有缓存，可读；换账号或升级客户端后
   第一次会重扫内存 20–30 秒，别误判成链路坏了。
6. **`work_toolkit_server.py` 的 docstring 里验收示例仍写 `--module comms|office`**，没带 `memory`。
   纯文案，不影响功能，属可顺手修的小项。

---

## 10. 建议下一步（按优先级，均未开工）

- P1：如果要继续「模块独立升级」，按模块挑一个真实场景做端到端实测，尤其是桥接工具逐项验证（第 9 节第 1 条）。
- P2：给四个单模块入口各写一份最小配置样例（MCP 多服务并列注册），并在 INSTALL 里说明何时该用单模块入口。
- P3：清理小口径残留——`work_toolkit_server.py` docstring 的 `--module` 列表、CSS `--ncol` 默认值 3。
- P4：`work_status.modules` 与 `work_modules.count` 的一致性，先问用户再动。
- 任何改动都按第 5 节 SOP 走完，再 `git add -A && git commit`。

### 本次交接前的复跑记录

写文档时重新实测，四项全部通过：

```
comms    工具登记 42 个 · 用例 38 条 · 通过 38 / 失败 0 / 跳过 0 · 合计 21804 ms
office   工具登记 42 个 · 用例 42 条 · 通过 42 / 失败 0 / 跳过 0 · 合计 25263 ms
memory   工具登记 42 个 · 用例 14 条 · 通过 14 / 失败 0 / 跳过 0 · 合计  2934 ms
other    工具登记 42 个 · 用例  6 条 · 通过  6 / 失败 0 / 跳过 0 · 合计  2338 ms
全量     工具登记 42 个 · 用例 82 条 · 通过 82 / 失败 0 / 跳过 0 · 合计 37894 ms
```

---

## 11. 一句话给接手者

**代码已经拆干净、验收全绿、文档已同步、工作区干净；不要重构，也不要「顺手优化」用户确认过的边界。**
先跑 `$PY scripts/work.py doctor` 和 `$PY tools/regress.py` 确认环境没变，
再等新需求；改动落点、变更流程、红线分别在第 3、5、7 节。
