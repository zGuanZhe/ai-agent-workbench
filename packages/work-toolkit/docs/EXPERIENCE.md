# 记忆模块：任务经验配方 + 通用记忆 + 台账

> 一句话定位：**做完一件事说「将本次工作整理为经验」，得到一份写明“用了哪个工具、什么参数、踩了什么坑”的配方文档；下次直接把它发给 AI 或 `replay`，不再探索。**

代码：`experience/library.py`（经验库）、`mcp/work_mods/memory.py`（四个 MCP 工具）、`mcp/work_core.py`（调用轨迹记录）
数据：`workspace/11_任务经验/*.md`（经验文档）、`.exp_index.db`（FTS5 索引，可重建）、`workspace/10_工作记忆/engram/`（Engram 库）、`workspace/06_运行状态/tool_trace.jsonl`（调用轨迹）

---

## 一、前两版为什么不好用

| 版本 | 做法 | 问题 |
| --- | --- | --- |
| v0 | 从会话里按句抽结论/偏好/报错，SQLite + BM25 打分，自动注入上下文 | 粒度太碎，拼不回一整段流程；自动召回污染上下文 |
| v1 | 八段空白模板，人工填写、人工粘贴 | 写的都是泛泛之谈，**没有绑定具体工具与参数**；下次 AI 拿到仍要重新探索 |

所以 v2 的核心变化是：**经验从真实工具调用轨迹生成，而不是从对话或人工回忆生成**。

## 二、三层架构

```
work_exp   流程配方      一整段任务的工具调用序列（可参数化重放、可导出为 Skill）
work_mem   通用记忆      开源 Engram：口径/偏好/决策/规律，topic_key 同主题覆盖演进
work_ledger 材料台账     谁交了、交了几版、谁没交
work_backup_file         写前时间戳备份
```

### 1. 调用轨迹（自动、零成本）

`ToolBox.tool()` 给每个 `work_*` 工具包了一层记录器，每次调用往 `tool_trace.jsonl` 追加一行：
模块、工具、子工具 / mode、是否探索性调用（status / schema / probe / list 等）、脱敏后的参数、成功与否、耗时、输入输出文件、报错。
不记录 `work_status` / `work_modules` / `work_exp` 自身；设 `WORK_TRACE_DISABLE=1` 可关闭（回归测试即如此）。
MCP schema 不受影响（`functools.wraps` 保留签名）。

### 2. `work_exp action=distill`

1. 取最近一段轨迹（没有轨迹时回退读 Codex 会话 rollout 里的 MCP 调用）；
2. 分成**有效步骤 / 探索调用 / 失败调用**——探索和失败不进配方，但写进“踩坑”；
3. 把文件路径、群名、日期等参数抽成 `{{变量}}`；
4. 生成 9 段 Markdown：一句话经验、交付目标、精确工具清单（模块 / 工具 / 子工具 / 作用）、步骤、关键口径、踩坑（现象→根因→改法）、产物、可复用片段，以及第八节 **Agent 零探索直调配方**：
   - 一段可直接发给 AI 的提示词（“按以下顺序直接调用，不要先 list / schema / probe”）；
   - replay JSON 与 `python tools/run_tool.py --batch` 命令；
   - 嵌入的 `<!-- WORK_EXP_PIPELINE_BEGIN ... END -->` 机器可读流水线（`replay` 用）。
5. 同步一条 pattern 记忆到 Engram，便于跨会话检索。

### 3. 复用

| 需求 | 调用 |
| --- | --- |
| 找经验 | `work_exp(action="search", query="汇总", module="office", tool="work_office_consolidate")` |
| 读全文 | `work_exp(action="get", doc_id="…")` |
| 预览重放 | `work_exp(action="replay", doc_id="…", dry_run=true, vars_json='{"input_dir":"…"}')` |
| 真正执行 | 同上 `dry_run=false` |
| 固化成 Skill | `work_exp(action="to_skill", doc_id="…", skill_name="work-sop-xxx", install_codex=true)` |

### 4. `work_mem`（开源 Engram）

选型：Engram（Go 单文件、SQLite + FTS5、原生支持 Codex MCP、`topic_key` upsert、Obsidian 导出、常驻 stdio 守护单次约 30 ms）。
未选：basic-memory（AGPL，依赖 uv / FastMCP 预发布）、mem0 / zep（偏云端托管）、mempalace（Chroma，依赖重）。

二进制 `bin/engram.exe`，数据 `workspace/10_工作记忆/engram`，必须带 `ENGRAM_NO_UPDATE_CHECK=1`（否则每次启动访问 GitHub 很慢）。
action：`save`（可带 `topic_key`）/ `search` / `context` / `get` / `timeline` / `pin` / `unpin` / `delete` / `stats` / `doctor` / `export_obsidian`。
`export_obsidian` 同时把经验文档复制到 vault 的 `workflows/`。
Codex 里另外注册了 `[mcp_servers.engram]`（`--tools=agent`），可直接用 Engram 原生工具。

## 三、纪律

- **不自动召回、不注入**：任务开始不翻经验库；用户说“搜经验 / 按经验做 / 之前定过什么”才查。
- **不自动写**：只有用户说“整理为经验 / 记住这个口径”才写。轨迹记录是被动日志，不算经验。
- **配方优先于描述**：经验文档的价值在第八节；拿到配方就按顺序直调，跳过 list / schema / status / probe。

## 四、局限

- 轨迹只能自动捕获本项目进程内的 `work_*` 工具；外部 MCP（如 `wechat_chat`）依赖 Codex rollout 回退解析，参数可能不全。OfficeCLI 由 `work_office` 内部调用，不作为独立 MCP 注册。
- 一段轨迹里若混了两件事，distill 会全部纳入，可用 `since` / `limit` 或事后编辑文档裁剪。
- 修改 `~/.codex/config.toml` 后需重启 Codex 才会加载 `engram` / `work_*` MCP。

