---
name: work-office-toolkit
version: 5.3.0
description: 办公批量流水工具箱（入口 /work）。三条主线：跨微信/企业微信的通讯最近动态与收件催办、多科室表格跨表汇总核表、Office 批量处理；材料台账与任务经验归记忆模块。GB/T 9704 国标公文已降为按需附加能力。写文档、做 PPT、读 PDF、改单张表、写代码、查资料一律用 WorkBuddy 原生能力。仅在用户输入 /work，或明确要求上述批量流水时使用。
---

# 办公流水线

**它只接管「办公室批量流水」，不接管 WorkBuddy 的日常本事。**

## 零、边界（先读这一节，决定要不要用我）

装了本 Skill **不等于**只能用本 Skill。判断标准只有一条：**这件事是不是本工具箱独有的批量流水**。

| 用户要做的事 | 谁来做 |
| --- | --- |
| 通讯里**最近有什么值得注意的通知 / 新消息 / 待办**（跨微信 + 企业微信，只看最近） | **本 Skill**：一次 `work_comms_notices`，别拼十几轮 |
| 通讯收材料、催交、查谁没交、按会话读消息、找某句话、查通讯录、解密聊天图片 | **本 Skill**（`work_wechat_*` / `work_wecom_*`） |
| 多科室 Word/Excel 申报表跨表汇总、勾稽校验、漏填核对 | **本 Skill** |
| 材料台账、任务经验 md、工具箱自检与安装 | **本 Skill** |
| 克隆的 Office/MCP 引擎体检；离线批量的高级 Excel/Word 能力（原生能力覆盖不到时） | **本 Skill**（`work_office_engines` / `work_excel_mcp` / `work_word_mcp`） |
| 按 GB/T 9704 排红头/白头公文 | **附加能力**：只有明确要求国标/红头/9704 才用，不是主流程 |
| 写通知/报告/总结/方案、做 PPT、生成图表页 | **WorkBuddy 原生能力** |
| 读、总结、检索 PDF | **WorkBuddy 原生能力** |
| 单张表格改几个单元格、加公式、画图、透视 | **WorkBuddy 原生能力** |
| 写代码、查资料、翻译、闲聊 | 照常，完全不用本 Skill |

三条硬约束：

1. **不抢活**：通用任务不提案 `work_*` 工具，回复里也不要提这个 Skill。
2. **不硬塞**：`ask` 返回 `matched:false` 就按 `native_suggestion` 换原生能力做完，而不是「没有工具所以做不了」。
3. **不乱降级**：工具箱没有的能力，用原生能力正常完成并说明即可，不要手写一次性脚本模仿工具箱。

## 零之一、通讯泛问：一次调用，别拼轮次（最高优先级）

用户问「通讯软件里有什么值得注意的通知」「最近有什么消息」「两个微信最近在说什么」
「群里最近有啥事」「我是不是错过了什么」——**只调一次 `work_comms_notices`**，用默认参数
（`days=3`，跨微信 + 企业微信）。`ask` 命中 `comms_notices` 时 `plan` 就一条，
不要自作主张再加 `overview` / 逐会话 `messages` / 全文 `search`：
旧链路十几轮、每轮都要重新起解释器，还把几十 KB 原始正文灌进上下文，慢且答非所问。

工具已经把活干在前面了：

- 一次连接扫完两条通道的最近消息，正文裁到 220 字，`notice` 标出像通知的条目；
- 默认 `order=notice`：**通知在前、带 `due` 的更前、组内按时间倒序，闲聊沉底**
  （`order=time` 才切回纯时间线）；
- `due` 是带截止语义的原文片段，`time_ref` 只是时间线索（活动日期也算，别当成要办）；
- `notice_by_chat` 直接告诉你哪个群有料。

**只有这三种情况才追加调用**：① 要读某条全文 → 对应通道 `work_wechat_messages` /
`work_wecom_messages`（`full=true`）；② 要翻历史、找某句原话 → 各通道 `search`；
③ 要确认通道能不能读 → `work_comms_overview` / `probe`。

答复口径：`notice=true`、`due`、`time_ref` 都只是**召回提示，不是事实判定**，
要按原文核对再下结论，别把「像通知」写成「已确认待办」。全程只读，不发送、不修改。

## 零之二、四个模块，各改各的

代码按业务拆成**通讯 / Office / 记忆 / 其他**四个模块，互不 import，可以分别升级：

| 模块 | 实现 | 独立入口 | 改它时看的文档 | 单独验收 |
| --- | --- | --- | --- | --- |
| 通讯 | `mcp/work_mods/comms.py`（19 个） | `mcp/work_comms_server.py`（其他 + 通讯，21 个） | `references/wechat.md`、`references/wecom.md` | `python tools/regress.py --module comms` |
| Office | `mcp/work_mods/office.py`（15 个） | `mcp/work_office_server.py`（其他 + Office，17 个） | `references/office.md` | `python tools/regress.py --module office` |
| 记忆 | `mcp/work_mods/memory.py`（3 个：台账 / 经验 / 备份） | `mcp/work_memory_server.py`（其他 + 记忆，5 个） | `references/memory.md` | `python tools/regress.py --module memory` |
| 其他 | `mcp/work_mods/other.py`（2 个：状态 / 模块清单） | 公共底座，随每个入口挂载 | 本文件 | `python tools/regress.py --module other` |

- 内核 `mcp/work_core.py` 只放注册器、路径常量、参数说明，**不写业务工具**；
  合体入口 `mcp/work_toolkit_server.py` 只挂载四个 ToolBox，自身没有实现（共 39 个）。
- 通讯侧不需要 Office 依赖，Office 侧不需要微信客户端开着；记忆侧只读写本地台账 / md / 备份。
- 「其他」是公共底座：每个入口都会挂上它，改它要跑全量回归。
- 查归属：`work.py modules`（或 `work_modules` 工具）；只看某模块工具：`work.py tools --module comms|office|memory`。

## 一、入口

```bash
python "<WORK_ROOT>/scripts/work.py" ask "<用户原话>"       # 把原话路由成确定的工具与参数
python "<WORK_ROOT>/scripts/work.py" call <工具名> '<JSON>'  # 调用
python "<WORK_ROOT>/scripts/work.py" tools <工具名或中文词>   # 查参数
python "<WORK_ROOT>/scripts/work.py" modules                # 看模块划分与各自的验收命令
```

- 要**动手执行**且属于上表前四行 → 第一步先 `ask`；纯咨询 → 直接答或 `tools`；与工具箱无关 → 不走 `ask`。
- `plan` 从 `plan[0]` 顺序执行；`executable=true` 的项 `command` 可直接复制，
  `executable=false` 的 `command` 是 `null`（先按 `needs_input` 补参数）。
- `executable=false` / `needs_input` → **仍要调这个工具**：按 `args_hint` 补齐必填参数后自己拼 `call`，别照抄带 `<必填:…>` 的 args。
- `writes` / `confirm_required` → 给产物路径；真实群发、真实删除、覆盖原件前先问。
- `readonly_requested` → 用户说了「只看不改」：只执行只读项，写工具留在 `original_plan`；
  用户改口「那还是生成吧」时才按 `original_plan` 执行。
- `matched:false` / `scope:out_of_scope` → 看 `native_suggestion` 换原生能力，不要再去翻工具箱。
- 工具返回 `ok:false` 是业务结果不是故障：读 `error` 修一次参数再试。
- **企业微信和微信是两条通道，合起来叫「通讯」**：用户说「企业微信 / 企微 / WXWork」才走 `work_wecom_*`，
  只说「微信」不许自己升级成企业微信；反过来也不行（会读到一个空收件箱，比报错更难发现）。
  说「通讯总览 / 两个微信都能读吗」走 `work_comms_overview`，但它是**状态 + 最近会话摘要**，
  不是消息内容、不是统一搜索；问「最近有什么通知」一律走 `work_comms_notices`。
- `run_python` 属于任意代码执行，只在用户明确要求「实时操控打开的文档」时用，且先说明会改到什么。

不属于上面三条主线、但单点会用到的一档：`work_status`（自检体检，`compact=true`）、
记忆模块的 `work_backup_file`（改原件前备份）、`work_ledger`（材料台账）、`work_wechat_files`、
`work_wecom_contacts`（查人）、`work_comms_overview`（通道状态）、
`work_office_engines`（克隆引擎体检）、`work_exp`（任务经验 list / get / save / export）。

## 二、细节按需读（不要一次全读）

| 遇到什么 | 读哪个 |
| --- | --- |
| 跨通道最近通知、微信收件归档、催办、搜消息、摘录（**通讯模块**） | `references/wechat.md`（首节是通讯聚合） |
| 企业微信通道（另一套加密、密钥在客户端进程里）、通讯总览（**通讯模块**） | `references/wecom.md` |
| OfficeCLI 改文档、COM/WPS、实时预览、schema、**克隆的 Office/MCP 引擎桥**（**Office 模块**） | `references/office.md` |
| 材料台账（谁交了什么）、任务经验 md、改前备份（**记忆模块**） | `references/memory.md` |
| 国标公文硬指标（**附加能力，非核心**） | `references/gongwen.md` |
| 报错、缺依赖、端口、文件占用 | `references/troubleshooting.md` |

安装后路径：`~/.workbuddy/skills/work-office-toolkit/references/`。

## 三、任务经验：人工按需，绝不自动召回

`workspace/11_任务经验/` 是一任务一份的完整 md，属于**记忆模块**；是完整任务档案，不是会话摘要，也不会被自动召回。

**开工不自动查、收尾不自动写、不读进上下文。** 只有用户说「把这次写成经验」才 `save`；
用户粘了一篇经验进来，就当当前任务的既定事实照做，不要重新问，也不要顺手「更新」它。

## 四、话不要说过头

- 会写产物就给路径与关键数字；没有 dry_run 的步骤不要宣称能预演。
- 通讯聚合的 `notice` / `due` 是召回提示，不是已确认的待办；时间以原文为准。
- 微信只读：不承诺自动发消息、不假装 @ 到人。
- 企业微信同样只读，而且**客户端关着就读不了**：密钥在 `WXWork.exe` 内存里，
  这时要说清「先打开企业微信」而不是含糊地说读取失败。
- 克隆的 Office/MCP 仓库以 `work_office_engines` 的 `ready` 为准：路径存在 ≠ 可用，
  源码不全、依赖缺失、客户端版本不匹配都要如实说。
- 缺字体、缺组件、走了降级路径，必须说明，不能当成完全达标。
