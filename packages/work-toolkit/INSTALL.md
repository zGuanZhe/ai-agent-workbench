# 安装指南

**一句话：clone → 跑 `scripts\setup.cmd` → 重载 WorkBuddy → 输入 `/work <需求>`。**

安装后它表现出来就是「一个高级点的 Skill」，不是一套需要学着操作的软件：
不用打开网页，不用手动点 MCP 信任，不用记第二个命令。总 Skill 通过 `scripts/work.py` 本地桥接调用全部 42 个工具；收到需求的第一步由 `ask`
把口语翻成确定的工具计划，避免智能体猜工具、跳过工具或宣称工具里没有的能力。
MCP 注册和网页控制台只是「可选增强」与「可选可视化展示」。安装过程不上传任何数据。

---

## 0. 前置条件

| 项目 | 要求 |
| --- | --- |
| 操作系统 | Windows（目前只在 Windows 上验证过） |
| Python | **3.10+**（`mcp` 2.3 要求 ≥3.10），安装时勾选 **Add to PATH**；实测 3.14.4 |
| 网络 | 首次安装 `pip` 需要联网（或内网 pip 源） |
| 客户端 | WorkBuddy（**推荐，装总 Skill 后用 `/work`**）；其他 stdio MCP 客户端可选 |

> 不需要 Node.js，不需要数据库服务，不需要 Docker。核心依赖只有 `mcp`、`python-docx`、`openpyxl`。

---

## 1. 一键安装（推荐）

```bat
git clone https://github.com/zGuanZhe/work-toolkit.git
cd work-toolkit
scripts\setup.cmd
```

`setup.cmd` 是纯 ASCII + CRLF 的批处理（开头设 UTF-8 代码页），中文提示全部由 Python 输出。它会依次完成：

| 步 | 做什么 | 失败会怎样 |
| --- | --- | --- |
| 1/6 | 扫描本机 Python，挑一个**装齐依赖**的解释器 | 找不到 Python 时提示去装 3.10+ |
| 2/6 | 只补装白名单里缺的包 | 联网失败会给出可复制的手动 `pip install` 命令 |
| 3/6 | 把 `work-toolkit` 写进 `~/.workbuddy/mcp.json`（**合并**，不覆盖别的 MCP；**可选增强**） | 配置解析异常会先备份再写 |
| 4/6 | 生成 `web/console.html`（**可选可视化展示**） | 模板占位符缺失会明确报错 |
| 5/6 | 同步总 Skill 到 `~/.workbuddy/skills/`，回填本机路径，清理已合并的旧命令 | 内容一致就跳过；有更新先整体备份 |
| 6/6 | 真跑工具 + 本地桥接 + **意图路由 / 只读保护** 验收 | 逐条列出失败项与原因 |

结束时会打印体检结论。看到 `✅ 准备完成` 后，**重载 WorkBuddy 会话，输入 `/work <需求>` 就能用**。
不需要再打开网页、不需要在连接器里点信任。

**只想看不改动**：

```bat
python scripts\setup.py --check
```

**其他常用开关**（也可直接传给 `setup.cmd`）：

| 开关 | 作用 |
| --- | --- |
| `--no-install` | 不执行 pip，只列出缺哪些包 |
| `--no-mcp` | 不改 `mcp.json`（用别的 MCP 客户端时） |
| `--no-build` | 不重新生成网页 |
| `--no-skills` | 不装 WorkBuddy Skill |
| `--json` | 输出结构化结果，便于脚本/CI |

> `setup.cmd` 结束会 `pause` 等你按键；自动化场景请直接跑 `python scripts\setup.py`，行为一致且不会暂停。

---

## 2. 手工安装（想逐步控制时）

### 2.1 挑解释器

Windows 上 PATH 里的 `python` 经常不是装了依赖的那个，而且项目里有个 `mcp/` 源码目录，容易被误当成 PyPI 的 `mcp` 包。所以统一用体检脚本挑：

```bat
python tools\doctor.py            :: 逐项体检，附修复命令
python tools\doctor.py --pick     :: 只输出最合适的解释器绝对路径
```

`--pick` 的输出是一个绝对路径，手动复制到下面的命令里即可：

```bat
set PYTHONIOENCODING=utf-8
"<上一步输出的 python.exe>" -m pip install mcp python-docx openpyxl pypdf Pillow comtypes pywin32
```

`doctor.py` **只用标准库**，哪怕当前解释器什么都没装也能跑起来并告诉你缺什么。

### 2.2 注册 MCP

自动（推荐，会自己挑解释器并保留其他 MCP）：

```bat
python tools\register_mcp.py
python tools\register_mcp.py --check     :: 只检查
```

或手工写入 `%USERPROFILE%\.workbuddy\mcp.json`：

```json
{
  "mcpServers": {
    "work-toolkit": {
      "command": "<装齐依赖的 python.exe 绝对路径>",
      "args": ["<项目根目录>/mcp/work_toolkit_server.py"],
      "env": { "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1" }
    }
  }
}
```

路径建议用正斜杠或双反斜杠。`command` 必须是**绝对路径**——MCP 宿主的 PATH 和你的终端未必一致。

> **WorkBuddy 用户不必做这一步**：`/work` 走本地桥接，不依赖 MCP 信任。
> 只有想在其他 MCP 客户端里用，或想让 WorkBuddy 工具列表直接出现 `work_*` 时，才需要去「连接器 / MCP 管理」点信任并重载会话。

### 2.3 生成网页

```bat
set PYTHONIOENCODING=utf-8
python mcp\dump_tools.py > web\tools.json   :: 工具增删改后才需要
python web\build.py                         :: 生成 web\console.html
```

`console.html` 是**构建产物**，仓库里不含（它内嵌本机环境快照，含微信账号目录）。每台机器自己生成一份，属正常现象。
`build.py` 会读 `web/custom_nodes.json` 合并自定义节点，并按 UTF-8 / LF 写出，构建日志里的体积数字与文件实际字节一致。

### 2.4 同步模块 Skill（独立调用或 `/work` 组合）

```bat
python scripts\install_skills.py            :: 安装 / 按内容自动更新（一致则跳过）
python scripts\install_skills.py --check   :: 预览，不写文件
python scripts\install_skills.py --force   :: 内容一致也重写（先备份）
```

仓库里的 SKILL.md 用 `<WORK_ROOT>` 占位，安装时替换成本机实际路径，再同步到 `~/.workbuddy/skills/`。
脚本会比对内容：仓库更新后再次运行会自动覆盖旧版，并把独立模块 Skill 同步到 Codex 与 WorkBuddy。

> **备份落在 `~/.workbuddy/skills.backups/`，不在 `skills/` 里。**
> 早期版本把备份写成 `skills/<名字>.bak-<时间戳>/`，WorkBuddy 会把它们当成独立 Skill 再装一遍，
> 「已安装技能」里就出现 3 个 `work-office-toolkit`、2 个 `work-console-upgrade`，行为也会跟着乱。
> 现在 `install_skills.py` 会把这类遗留目录整体挪到 `skills.backups/`，`doctor.py` 也会把重复项标成 ❌。

| Skill | 用户可见命令 |
| --- | --- |
| `work-office` | 直接处理 Office；也可把它当作 `work_office` MCP 的薄路由 |
| `work-comms` | 直接处理微信 / 企微通讯 |
| `work-memory` | 直接处理经验、台账与通用记忆 |
| `work-office-toolkit` | 可选的 `/work` 组合入口，不是唯一入口 |
| `work-console-upgrade` | 无命令；维护者说「改网页」时才触发 |

> 新 Skill 要**重载 WorkBuddy / Codex 会话**后才会被识别。重载后可以直接调用模块 Skill 或 MCP；`/work` 只用于跨模块组合。

**它不会抢走 WorkBuddy 的原有能力**：SKILL.md 第一节就是边界表，只接管微信收件与催办、
跨表汇总核表、国标公文、材料台账、任务经验这几类流水；写普通文档、做 PPT、读 PDF、改单张表、
写代码、查资料照常用原生能力。`ask` 返回 `matched:false` 时也要求改用原生能力继续做，
而不是「没有对应工具所以做不了」。装完可用 `python tools\doctor.py` 确认总 Skill 是带边界的新版。

**为什么不会「有时不调工具」**：总 Skill 规定第一步必须执行
`python "<WORK_ROOT>/scripts/work.py" ask "<用户原话>"`。`web/intents.json` 把口语翻成
确定的 `plan`（工具名 + 参数 + 是否写文件 + 是否要先确认），智能体按 plan 执行即可；
用户说「别写文件」时 `ask` 会自动把写产物工具从 plan 移除。改完意图表跑
`python tools\check_intents.py` 校验（工具名写错会让「功能触发不了」，这里能提前查出来）。

### 2.5 可选：启动可视化控制台 / 本地桥接

```bat
scripts\open-console.cmd                       :: 构建 + 起服务（默认 8765）并打开浏览器
scripts\open-console.cmd --no-open --port 8899 :: 不弹浏览器、换端口
```

> 网页只是能力展示。**日常使用不需要这一节**；`/work` 通过本地桥接跑工具，网页服务没起也照常可用。

- **静态双击** `web/console.html`：只能浏览，页面上的「直接调用」不可用（没有后端）。
- **起了服务**才有 `/api/*`，「直接调用」会真的执行工具；页面右上角显示「本机已连接」。
- 直接跑服务：`python scripts\launch.py --no-open --port 8765`（`launch.py` 是纯标准库，会自己挑装齐依赖的解释器）。
- 总 Skill 实际使用的本地桥接（同样自动挑解释器，支持 `@参数文件.json` / stdin）：

```bat
:: 命令行直调单个工具（自动挑解释器并缓存到 workspace\.pyexe）
scripts\run-tool.cmd work_status
echo {"action":"list"} | scripts\run-tool.cmd work_exp

:: 总 Skill 实际使用的本地桥接（支持 @参数文件.json / stdin）
python scripts\work.py ask "把 11 月报表收一下并合成汇总母表"
python scripts\work.py call work_status "{}"
python scripts\work.py tools work_office_consolidate
python scripts\work.py setup --check
```

---

## 3. 可选依赖

**41 个公共工具里，只有一部分依赖下面的本机组件；缺了不影响其余工具。**

### OfficeCLI（`bin/officecli.exe`，约 32 MB）

上游项目 `iOfficeAI/OfficeCLI`（本项目实测 v1.0.161），**已随仓库附带，无需另行下载**。开箱即可用：

```bat
bin\officecli.exe --version
```

> 它是第三方二进制，版权与再分发条款归上游所有，**不在本仓库根目录 MIT 许可的覆盖范围内**；商用或二次分发请先确认上游授权，详见 [bin/README.md](bin/README.md)。

若你把它删了或换了机器只同步了源码，对应工具会这样表现：

| 缺了会怎样 | 工具 |
| --- | --- |
| 直接报错 | `work_office_inspect`、`work_office_compose`、`work_office_batch`、`work_office_merge`、`work_office_import` |
| 跳过额外的 OpenXML 校验，主功能仍可用 | `work_office_consolidate`、`work_office_engines` |
| 不依赖 OfficeCLI | `work_office_to_pdf`、`work_office_com`、`work_office_advanced`、`work_office_diff`、`work_office_join_docx` |

`python tools\doctor.py` 会把这一项标成 `❌` 并给出补齐提示。

### 本机 Office / WPS（COM）

需要本机装了 **Microsoft Office 或 WPS** 之一（两个都装也行）：

| 能力 | 工具 | 说明 |
| --- | --- | --- |
| 导 PDF | `work_office_to_pdf`、`work_office_com action=export_pdf` | `engine=auto` 先试 MS Office，失败自动换 WPS；也可指定 `ms` / `wps` |
| 重算公式 | `work_office_com action=recalc` | Excel：`CalculateFullRebuild` 后存盘 |
| 刷新域/目录 | `work_office_com action=refresh_fields` | Word：`Fields.Update` + 目录更新后存盘 |
| 查找替换 | `work_office_com action=replace` | Word 全文替换，改前先 `work_backup_file` |
| 读写单元格 | `work_office_com action=read_cells/set_cells` | 适合「用户正开着表，先看几个格 / 填几个数」 |

`work_office_com action=apps` 会列出本机注册了哪些组件（`Word.Application` / `KWPS.Application` 等）。Excel 导出的 `pages=0` 是正常现象（COM 不回报页数），工具会改用 pypdf 数产物页数。
不装 Office/WPS 也不影响其余工具：28 个里只有 `work_office_com`、`work_office_active` 这类
「实时操控已打开窗口」的动作需要本机组件，其余走内置 OfficeCLI，不需要装 Office。

### 微信数据目录

微信相关工具按「环境变量 → 常见默认位置」探测：`WECHAT_ACCOUNT_DIR` → `WECHAT_DATA_DIR` → `~\Documents\xwechat_files`（4.x 默认）→ `~\Documents\WeChat Files`（3.x 默认），另有几个自定义盘符候选。数据目录挪过位置就显式指定：

```bat
set WECHAT_ACCOUNT_DIR=D:\你的路径\xwechat_files\wxid_xxxx
set WECHAT_DATA_DIR=D:\你的路径\xwechat_files
```

前者指向 **账号目录**（`wxid_xxx` 那一级），后者指向 **`xwechat_files` 根目录**；设前者优先级最高，一般只设它就行。源码里保留了几条常见的自定义盘符候选路径，不含任何账号或个人信息，也都可以被上面两个变量覆盖。

所有微信能力都是**只读**：源库只读打开、解密结果写进 `workspace/07_微信只读缓存/`，绝不改动微信自身的文件。

### `ffmpeg`（可选）

`work_wechat_media` 的 `prefer=orig` 需要把 WXGF 转 PNG；`prefer=thumb` 走缩略图，不需要 ffmpeg。

### 上游参考仓库

当前本机 `mcp_servers/` 保留并实际接入 4 个上游项目（`excel-mcp-server`、`Office-Word-MCP-Server`、`OfficeMCP`、`wechat-chat-mcp`）。**它们不随源码仓库打包**；需要时按各自许可证自行获取或初始化独立环境。

---

## 4. 安装后验证

```bat
python tools\doctor.py             :: 环境逐项体检
python scripts\setup.py --check    :: 环境 + MCP 注册状态
python tools\regress.py            :: 101 条用例 / 41 个公共工具（约 23 秒）
python tools\regress.py --module comms   :: 只验通讯模块 + 其他（改通讯时用）
python tools\regress.py --module office  :: 只验 Office 模块 + 其他（改 Office 时用）
python tools\regress.py --module memory  :: 只验记忆模块 + 其他（改台账/经验/备份时用）
python tools\check_nodes.py        :: 自定义节点配置是否写错（改过 custom_nodes.json 才需要）
```

`regress.py` 会自举到装齐依赖的解释器；`run` 真跑只读/空跑用例、`guard` 验参数校验、`env` 依赖本机微信数据（没数据标 `SKIP` 而不是 `FAIL`）。写类工具的产物只落系统临时目录并自动清理，**不会往材料库里塞测试文件**。`--quick` 只跑与机器无关的用例（约 15 秒），`--json` 可接 CI。

前端有 Playwright 的机器可以脚本化验收：打开页面 → 点「任务经验」节点 → 抓 `pageerror` / `console error`。本机实测 42 个工具节点、3 列（通讯 / Office / 其他）、通讯总览两通道状态卡与合并时间线均有数据、搜索过滤可用、两类报错均为 0。

---

## 5. 常见问题

| 现象 | 原因与处理 |
| --- | --- |
| `ModuleNotFoundError: No module named 'mcp'` | PATH 里的 `python` 不是装了依赖的那个。别裸写 `python web/serve.py`，改用 `/work 安装`、`scripts\setup.cmd` 或 `python scripts\work.py call ...`，它们会自动挑解释器 |
| `.cmd` 双击闪退或显示乱码 | 批处理是纯 ASCII + CRLF，且开头 `chcp 65001`。如果自己改过，别加中文注释（cmd 按代码页 936 解析会撑坏脚本），中文提示交给 Python |
| WorkBuddy 里看不到 `work_*` MCP 工具 | **正常，不用处理**；`/work` 走本地桥接，不依赖 MCP。只有想在别处直接用 MCP 时才去点信任 |
| `/work` 不生效 | 重载 WorkBuddy 会话；仍不行就重跑 `python scripts\install_skills.py`，它会同步总 Skill 并清理旧命令 |
| 端口 8765 被占用 | `scripts\open-console.cmd --no-open --port 8899` |
| 本机 `curl` 访问 127.0.0.1 返回 502 | 机器设了 `http_proxy`；访问本地服务时加 `--noproxy 127.0.0.1,localhost`，或设 `NO_PROXY` |
| 找不到微信账号数据目录 | 先 `work_wechat_probe` 看探测结果；目录自定义过就设 `WECHAT_ACCOUNT_DIR` |
| 刚写好的经验文档在网页里刷不出来 | `/api/exp` 特意不走缓存，正常应即时可见；确认文件放在 `workspace/11_任务经验/` 且是 `.md` |
| `pip install` 一直失败 | 多半是没网或公司代理挡了 pip；`python scripts\setup.py --no-install` 会列出缺哪些包，可手动装 |
| 改了模板页面没变 | 忘了跑 `python web\build.py`，或浏览器缓存了旧 `console.html`；改完还要 `python tools\check_nodes.py` |

---

## 6. 数据与隐私

- 仓库里**没有**微信聊天数据库、解密密钥、salt、`wxid`、图片缓存、材料原件或任何真实凭据；`.gitignore` 把它们整体排除。
- 运行时产物只落在 `workspace/` 下（归档文件、只读缓存、台账 db、成稿、任务经验 md）。这些目录首次使用时自动创建，删掉即可清空本机数据。
- 任务经验是普通 Markdown，人工按需复制粘贴；工具**不会**自动读取、不会自动写入、不会注入对话上下文。详见 [docs/EXPERIENCE.md](docs/EXPERIENCE.md)。

---

## 7. 卸载

1. 从 `%USERPROFILE%\.workbuddy\mcp.json` 里删除 `work-toolkit` 条目（或 `python tools\register_mcp.py --check` 确认后手工删）。
2. 删除 `~/.workbuddy/skills/work-office-toolkit` 与 `work-console-upgrade`。
3. 删除项目目录（含 `workspace/`）即可清掉全部本机数据。
