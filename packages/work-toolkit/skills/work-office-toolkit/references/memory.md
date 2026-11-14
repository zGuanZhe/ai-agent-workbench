# 记忆：材料台账、任务经验、改前备份

> 归属**记忆模块**（`mcp/work_mods/memory.py`，独立入口 `mcp/work_memory_server.py`，5 个工具）。
> 改这块只动 memory.py 与本文件；验收 `python tools/regress.py --module memory`。
> 它和通讯 / Office **互不 import**：删掉记忆模块，另外两个模块的工具照常跑。

「记忆」只做一件事：**把东西留下来**——收过什么（台账）、做过什么（经验）、改动前的原件（备份）。
三样都落在本地 workspace，纯文件 / SQLite，可以直接拷走、备份、用别的工具打开。

| 工具 | 一句话 | 落在哪 |
| --- | --- | --- |
| `work_ledger` | 材料收发的可追溯流水：谁交了、谁没交、交了几次 | `workspace/08_材料台账/materials.db` |
| `work_exp` | 一个完整任务 = 一份 Markdown，按需整篇复制 | `workspace/11_任务经验/` |
| `work_backup_file` | 把文件复制一份到备份区，文件名带时间戳 | `workspace/02_自动备份区/` |

## 一、材料台账：回答「谁交了、谁没交、交了几次」

```bash
work.py call work_ledger '{"action":"summary"}'
work.py call work_ledger '{"action":"missing","since":"2026-11-01"}'
work.py call work_ledger '{"action":"departments"}'
```

| action | 用途 |
| --- | --- |
| `summary` | 总量、最近批次、未归科室的文件数 |
| `departments` | 按科室汇总，顺带标出「同一科室多行」= 改了几版 |
| `missing` | 对着《科室底册.xlsx》算未交名单（带联络员与电话）；`since` 只看某个时间之后 |
| `history` | 某科室（或全部）的收件明细 |
| `batches` | 每次收件动作（哪一次扫进来多少） |
| `state` | 增量水位：下次从哪继续 |
| `record` | 把一批文件记进台账，配合 `record_dir` |

口径别搞混（这是被问过最多的一次）：

- **同一文件重复扫到不新增行，只累加 `seen_count`** —— 这是「同一版被扫了几次」，
  不是「改了几版」；
- **同一科室出现多行才是改了几版**，因为文件名/内容变了才是新版本；
- 归不到科室是因为文件名没有「科室_姓名_事由」前缀，返回里的 `note` 会直接说明，
  这时要么让联络员按规范命名，要么改用 `missing` 对底册核对。

通讯收材料（`work_wechat_files(mode='harvest')` / `work_wecom_harvest`）**内部会自动记账**，
不需要再手动调 `record`；只有从别的目录补记历史材料时才用 `record`。

## 二、任务经验：人工按需，绝不自动召回

```bash
work.py call work_exp '{"action":"list"}'
work.py call work_exp '{"action":"get","doc_id":"2026-11-12_模块拆分"}'
work.py call work_exp '{"action":"template","title":"新任务名称"}'
```

- **不自动检索、不主动召回、不往对话上下文注入**；任何工具内部都不会自动写经验。
- 只有用户明确说「把这次写成经验」才 `action=save`；用户粘了一篇经验进来，
  就当既定事实照做，不要重新问，也不要顺手「更新」它。
- 粒度是**一整段任务**，不是会话摘要、不是零散结论句。一篇文档要能脱离对话独立看懂。
- 纯 Markdown，可直接用文本编辑器改、拷走、发给别人；`index.md` 是目录。

## 三、改前备份：只复制，不动原件

```bash
work.py call work_backup_file '{"file":"workspace/03_汇总与成稿输出/汇总母表.xlsx"}'
```

- 复制到 `02_自动备份区/`，文件名带 `YYYYmmdd_HHMMSS` 时间戳，返回 `backup` 路径与体积。
- 只复制、不修改源文件，也不删除任何东西；覆盖式写操作之前建议先走一遍。
- Office 的写工具（`work_office_create/merge/import/batch` 等）在 `allow_write` 下
  会**自动先备份**，这里的手动备份是给「用原生能力直接改文件」的场景兜底。

## 四、边界（别越界）

- 不做语义检索、不做向量库、不打分排序——经验库是文件柜，不是搜索引擎。
- 不把台账 / 经验数据推到外部服务；全部只落本地 workspace。
- 台账库坏了可以直接删掉重建（会丢历史，不丢文件）；经验目录可以直接拷走。
- 记忆模块**不生成文档、不读微信**：写 Word / Excel 找 Office 模块，读聊天找通讯模块。
