---
name: work-console-upgrade
version: 1.1.0
description: 维护者专用——升级办公工具箱的可视化控制台网页（<WORK_ROOT>/web）。用于给控制台加功能节点、加新的 MCP 工具、改列布局或样式、换配色。当用户说「给控制台加一个 XX 功能」「改一下网页颜色/布局」「新做一个工具接到网页上」「控制台节点太少了」时使用。日常办公需求不要走这里，统一由 /work 处理。
---

# 升级控制台网页（维护者专用）

> **日常用户不用读这份文档。** 装好总 Skill 后只需 `/work <需求>`；网页只是可选的能力可视化展示，
> 不开网页也能通过本地桥接跑完全部 42 个工具。只有要改网页本身时才用本 Skill。
> 通讯列首屏首选是「最近通知 · 一次看完」（`work_comms_notices`）；国标公文已降为附加能力，
> 别再加成头条节点。克隆的引擎节点走 `custom_nodes.json`。

> 文档里的项目路径已由 `scripts\install_skills.py` 在安装时自动回填；仓库源文件用占位符书写，不含任何机器特定路径。

控制台是 `<WORK_ROOT>\web\` 下的**自包含单文件网页**：无外链、无字体、无 CDN，双击能开；起本地服务后「直接调用」才真的能跑工具。

**优先用封装脚本，不要裸写 `python web/serve.py`**：PATH 里的 `python` 经常不是装了 mcp 依赖的那个，
裸写会得到 `No module named 'mcp'`。`scripts\open-console.cmd` 和 `scripts\launch.py`
只用标准库启动，然后自己挑一个装齐依赖的解释器跑真正的服务。

## 一、先认三层，别改错地方

| 层 | 文件 | 改它做什么 |
| --- | --- | --- |
| **模板** | `web/_console_template.html` | 内置节点定义、样式、渲染逻辑。**唯一真源** |
| **产物** | `web/console.html` | 由模板构建出来的单文件。**不要手改**，改完会被覆盖 |
| **扩展** | `web/custom_nodes.json` | 用户自己加节点，不用碰模板 |

构建：`python web/build.py`（把工具清单、本机环境快照、自定义节点注入模板）

服务：`scripts\open-console.cmd --no-open --port 8765`（提供 `/api/*`，页面上「直接调用」才真的能跑）

## 二、加一个功能节点（最常见，5 分钟）

### 只用现成工具 → 改 `custom_nodes.json`

在 `web/custom_nodes.json` 的 `groups` 数组里加一项：

```json
{
  "col": "other",
  "id": "mine",
  "name": "我的功能",
  "nodes": [
    {
      "id": "mytool",
      "name": "经验合集导出",
      "tag": "写文件",
      "tool": "work_exp",
      "mode": "tool",
      "brief": "一行说明，节点树上直接显示",
      "intro": ["详情页第一段", "详情页第二段"],
      "io": { "in": "无", "out": "Markdown 文件路径" },
      "note": "注意事项，浅色块显示",
      "args": { "action": "export" },
      "prompt": "把所有任务经验导出成一份 Markdown 合集。"
    }
  ]
}
```

- `col`：`comms` 通讯（微信 + 企业微信合并列）/ `office` / `other`
- `mode`：`tool` 有「直接调用」按钮 / `data` 只出数据 / `search` 带搜索框
- `tool`：必须是 `web/tools.json` 里存在的工具名；纯展示填 `null`
- `args`：点「直接调用」时传的参数对象，**必须是静态 JSON**（这个文件不支持函数）

改完：

```
python tools/check_nodes.py      # 字段、id 重复、工具名写错，都能查出来
python web/build.py              # 重新生成 console.html
```

### 要自定义交互（选会话、选文件、图谱、时间线…）→ 改模板

1. 在 `web/_console_template.html` 里找到对应列的节点数组（`COMMS_GROUPS` 由 `WX_GROUPS` / `WECOM_GROUPS` 合成，另有 `OFFICE_GROUPS` / `OTHER_GROUPS`），照现有节点的字段写一个新的。字段比 JSON 版多两个函数：
   - `args: S => ({ chat: (S.chat && S.chat.name) || "", limit: 40 })`
   - `prompt: S => \`看看「${S.chat.name}」最近 40 条消息…\``
   `S` 是页面状态：`{ node, chat, kw, file, dir, sessions }`。
2. 要自己画界面，就在 `browse` 里挂一个新的 `kind`：

   ```js
   browse: { kind: "mine" }
   ```

   然后在 `loadData()` 的分发处加一行：

   ```js
   if (b.kind === "mine") return loadMine(box);
   ```

   再实现 `loadMine(box)`：先用 `dataShell(标题, 副标题)` 占位，`api()` 取数，最后填 `box.innerHTML`。

3. 需要新接口 → `web/serve.py` 的 `do_GET` 里加 `if u.path == "/api/mine":`，内部用 `call_tool("work_xxx", args)` 调工具。写操作走前端 `api("/api/run", {tool, args})`。

## 三、加一个新的 MCP 工具（要动后端）

1. 先定归属：通讯进 `mcp/work_mods/comms.py`，Office 进 `mcp/work_mods/office.py`，
   台账 / 经验 / 备份进 `mcp/work_mods/memory.py`，状态与模块清单进 `mcp/work_mods/other.py`。
   **不要往 `work_toolkit_server.py` 里写实现**，它只是合体入口（挂载四个 ToolBox）。
   通讯 / Office / 记忆互不 import，按模块分开升级。
2. 在对应模块里用该模块的 `@TOOLS.tool` 注册：

   ```python
   @TOOLS.tool(name="work_xxx", description="【一句话标签】做什么…")
   def work_xxx(param: str = "") -> str:
       ...
       return _dump({"ok": True, ...})
   ```

   - 一律 `return _dump(dict)`，前端和 CLI 都按 JSON 解析
   - 失败返回 `{"ok": False, "error": "...", "hint": "怎么修"}`，别抛异常
   - 有新参数就在 `mcp/work_core.py` 的 `_PARAM_DOCS` 里补一句中文说明（客户端只看得到裸参数名）
3. 刷新清单并重建：

   ```
   python mcp/dump_tools.py > web/tools.json
   python web/build.py
   ```

4. 写操作记得在 `web/serve.py` 的 `_INVALIDATE` 里登记要失效的读缓存。

## 四、改样式

样式全部在模板 `<style>` 里，**没有外部 CSS**。改之前先认清现成的变量和类：

- 颜色：`--surf` `--surf-2` `--text` `--text-2` `--text-3` `--accent` `--r-m`
- 不要引入尖角细线：容器一律 `border-radius: var(--r-m)`，用背景色分层而不是 `border`
- 节点树：`.node`（`.node.top` 是当前选中）、`.grp`、`.cv`
- 任务经验卡片：`.memo` `.memo-h` `.memo-b` `.memo-f` `.mtag` `.expbar` `.exphint` `.expbody`
- 列表：`.lg` `.lg-row`（表头加 `.head`）、`.srow`

## 五、改完必做四件事

```
python tools/doctor.py                 # 0. 环境没问题时可以跳过；报 mcp 缺依赖时先跑这个
python tools/check_intents.py           # 0.5 改过 web/intents.json 必查：意图引用了不存在的工具会在页面上才炸
python web/build.py                    # 1. 构建不报错
python tools/check_nodes.py             # 2. 节点配置没写错（改过 custom_nodes.json 才需要）
python tools/regress.py                # 3. 42 个工具冒烟：全量用例，应该 0 失败
python tools/regress.py --module comms #    只改通讯时：只跑通讯 + 其他用例
python tools/regress.py --module office #   只改 Office 时：只跑 Office + 其他用例
python tools/regress.py --module memory #   只改记忆时：只跑记忆 + 其他用例
```

4. 起服务实测：`scripts\open-console.cmd --no-open --port 8765`，点一遍新加的节点，看控制台有没有 JS 报错。
   有 playwright 的机器可以脚本化：打开页面 → 点节点 → `page.on("pageerror")` 抓错 → 截图。
   要后台常驻，用受管的后台方式启动，别用 `&` / `nohup`（会话一结束就被回收）。

`tools/regress.py` 会自举到装齐依赖的解释器；`run` 真跑只读/dry、`guard` 验参数校验、
`env` 依赖本机微信数据（没数据标 SKIP 而不是 FAIL）。写类工具的产物只落系统临时目录并自动清理，
不会往用户材料库里塞测试文件。加 `--json` 可接 CI。

## 六、常见坑

| 现象 | 原因 |
| --- | --- |
| 改了模板，页面没变 | 忘了跑 `python web/build.py`，或者浏览器缓存了旧 `console.html` |
| 构建报的体积和文件实际大小对不上 | Windows 上 `write_text` 默认把 `\n` 转成 CRLF，凭空多两三千字节；`build.py` 已固定 `newline="\n"`，别改回去 |
| 点「直接调用」提示「工具未登记」 | `web/tools.json` 没刷新，跑 `python mcp/dump_tools.py > web/tools.json` |
| 节点点了没反应 | `browse.kind` 没有对应的 `loadXxx` 分支，或者 `mode: "tool"` 却没填 `args` |
| 页面白屏 | JS 语法错误，看浏览器控制台；模板里的 `${}` 模板字符串最容易漏反引号 |
| 文字挤出屏幕 | 列表默认要收口（`limit` 9–14 行 + 「还有 N 份」折叠），不要把全量往 `innerHTML` 里灌 |
| 任务经验节点列表为空 | `workspace/11_任务经验/` 里没有 md（`index.md` 不算）。`/api/exp` 只列文件、不做检索；要内容就 `action=get` 取整篇原文 |
| `No module named 'mcp'` | 解释器选错了。项目根的 `mcp/` 目录是源码、不是 MCP 库；跑 `python tools/doctor.py` 看「解释器」那行，或直接走 `scripts\open-console.cmd` |
| `.cmd` 一运行就报「不是内部或外部命令」、中文注释乱飞 | cmd.exe 按 OEM 代码页（本机 936）解析批处理文件，UTF-8 中文会撑坏脚本。`.cmd` 必须**纯 ASCII + CRLF**，中文提示交给 Python 输出；脚本开头 `chcp 65001` |
| 用户目录带中文时，脚本拿到乱码路径 | `doctor --pick` 输出 UTF-8，cmd 侧要 `chcp 65001` 才不会把路径读成乱码 |

## 七、别做的事

- 不要手改 `web/console.html`（构建会覆盖）
- 不要在模板里引外链 CDN / 字体 / 图片（破坏「双击即开」）
- 不要把大段数据写进模板（会撑大产物；数据走 `/api/*`）
- 不要删 `web/facts.py` 采集的环境字段（顶栏数字靠它）
- 不要给 `.cmd` 写中文注释或改回 LF 换行（见「六、常见坑」）
- 不要自己再造一份解释器探测逻辑，`tools/doctor.py` 的 `--pick` 是唯一入口
- **不要给任务经验节点加语义检索、打分排序、自动召回或在页面加载时把经验 md 读进上下文**。
  经验是给人整篇复制粘贴的：`list` 列目录、`get` 取整篇，仅此而已；写入只由用户显式触发
