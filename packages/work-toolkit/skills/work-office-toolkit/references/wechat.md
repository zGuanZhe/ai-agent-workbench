# 微信：通讯最近动态、收材料、催交、查消息

> 归属**通讯模块**（`mcp/work_mods/comms.py`，独立入口 `mcp/work_comms_server.py`）。
> 改这块只动 comms.py 与本文件；验收 `python tools/regress.py --module comms`。

## 跨通道「最近有什么通知」——第一选择（只读，一次调用）

用户问「通讯软件里有什么值得注意的通知」「最近有什么消息」「两个微信最近在说什么」
「我是不是错过了什么」时，**只调一次 `work_comms_notices`**，用默认参数即可：

```bash
python "<WORK_ROOT>/scripts/work.py" call work_comms_notices '{}'
```

它一次连接扫完微信与企业微信最近 3 天的消息，默认 `per_chat=8`、`limit=40`、
正文裁到 220 字；**默认 `order=notice`：通知在前、带 `due` 的更前、组内按时间倒序，
闲聊沉底**（`order=time` 才是纯时间线）。旧链路是「总览 → 逐会话读十几个群 → 全文搜关键字」，
每轮都要重起解释器、重新校验解密缓存，慢且把几十 KB 原始正文灌进上下文，已被这个工具取代。

| 返回字段 | 怎么用 |
| --- | --- |
| `channels` | 两条通道各自能不能读、扫了几个会话、取到几条；某条 `ready=false` 要如实说明，不能拿另一条的数据冒充 |
| `items[].notice` | **召回标记**，不是事实判定；宁可多标几条也不能漏掉带死线的那条 |
| `items[].due` | 带截止语义的**原文片段**（如「11月12日12点前」），不解析成日期，避免编造时间 |
| `items[].time_ref` | 时间线索，活动日期也算，**不代表要办** |
| `notice_by_chat` | 哪个通道 / 哪个群有通知，可据此决定要不要细读 |
| `order` / `truncated` | 排序方式与是否被 `limit` 截断 |

收窄手段按这个顺序用：`days` → `keyword` → `only_notices=true` → `channels`，
**不要把 `limit` 一味调大**。

只有三种情况才往下接别的工具：

1. 要读某条全文 → `work_wechat_messages` / `work_wecom_messages`（`full=true`）；
2. 要翻历史、找某句原话 → `work_wechat_search` / `work_wecom_search`；
3. 要确认通道状态 / 客户端有没有开 → `work_comms_overview` / `work_wecom_probe`。

`work_comms_overview` 只给「状态 + 最近会话摘要」，**不含消息正文**，
别拿它的摘要硬答「有什么通知」。全程只读，不发送、不修改聊天数据。

## 先看通道（单通道作业时才需要）

`work_wechat_probe` 返回三个布尔，决定能干什么：

| 字段 | 管什么 | 为 false 时 |
| --- | --- | --- |
| `local_inbox_ready` | 文件归档（收材料） | 收件目录不可读，按 `alternatives` 转述 |
| `message_db_readable` | 聊天文字（搜消息、摘录） | 先 `work_wechat_sync` 取密钥解密副本 |
| `ui_automation_ready` | 桌面直读、真实发送 | 不做真实发送，只出话术 |

微信 4.x 无法桌面自动化是常态，不要反复重试或编数据。

## 收材料

口语「收材料 / 收一下 / 谁没交」统一走 **`work_wechat_files`**：
- `mode='harvest'`：一键归档 + 已交/未交清单 + 催办话术；
- `mode='scan'`：纯只读扫描收件箱并统计；
- `mode='pull'`：按条件筛选复制到工作区（可选传 `roster`）。

### 结果怎么读

优先用返回的 `summary` 转述，它已经把三个容易误解的数字说清了：

| 字段 | 含义 |
| --- | --- |
| `copied` | 本次**新增**归档几份 |
| `skipped_duplicate` | 匹配到但此前已归档、按去重没再复制（**不是失败**） |
| `archived_total` | 归档目录现在一共几份 |
| `roster_basis` | 未交名单的依据：`ledger` 台账 / `roster_file` 底册文件名 / `archive_filenames` 归档整体文件名 / `none` 无底册 |

`copied=0` + `skipped_duplicate=13` 读作「13 份此前都已归档，没再复制一遍」。

### 谁没交，凭什么判

底册是按「文件名里有没有科室名」认人的。本次时间窗内只有新文件，认不出来时会**退回按归档目录整体核对**
（`roster_basis=archive_filenames`）。仍然一个都认不出，`summary` 会直说「名单不可信」——
这时不要把「未交 N 家」当结论报给用户，要说明需要按「科室_发送人_原名」命名归档，或改用台账核对。

## 搜消息、看群

| 要做的事 | 工具 | 关键参数 |
| --- | --- | --- |
| 找某句话 | `work_wechat_search` | `keyword` 必填，`chat` 可空 |
| 看某群最近消息 | `work_wechat_messages` | `chat` 必填，可给 `limit` |
| 列会话 | `work_wechat_sessions` | — |
| 只看收件箱有什么 | `work_wechat_files` | `mode='scan'`（纯只读，不动文件） |
| 消息库陈旧 | `work_wechat_sync` | `force=true` 强制重解 |

「查群里说了什么」不要跑 harvest——那是收文件，不是查聊天。
若是**泛问「最近有什么通知」而没指定某个群**，先走本文首节的 `work_comms_notices`，
不要从这里逐会话循环；只有点名了群、或要读某条全文/搜历史，才用上表这些单通道工具。

## 摘录与催办

- **摘录成文**：`work_wechat_digest`，把某段时间某群的消息整理成 md。
- **催办**：`work_wechat_remind`，`dry_run` 默认 true。失败就把话术原样交给用户手动粘贴，
  不承诺自动发消息或 @ 人。

## 图片

聊天图片是加密的，`work_wechat_media` 负责解密导出到工作区。
只能看到 `[图片]` 占位属于正常，媒体本体不解析。
