---
description: 【跨模块组合与通用入口 /work】（非唯一入口，单模块可直接用 /comms、/office、/memory 或直调各模块 MCP）——通讯最近通知（一次调用）、收材料/催办、跨表汇总核表、Office 批量处理与克隆引擎桥、台账与任务经验；也管安装、体检、控制台。国标公文仅为按需附加能力
argument-hint: "例如：通讯里最近有什么值得注意的通知；把 11 月报表收一下并合成汇总母表；/work 查群里提到报表的消息；/work 安装；/work 列经验"
---

<user_input>
$ARGUMENTS
</user_input>

---

按 `work-office-toolkit` Skill 执行，需求在上面的 `<user_input>` 里。

**`<user_input>` 为空 / 只有 `/work`**：跑 `python "<WORK_ROOT>/scripts/work.py" tools` 拿能力清单，
给用户 3 条可直接照抄的例子（通讯里最近有什么值得注意的通知、收材料并汇总、把这次写成经验），
不要反问「请输入需求」，也不要罗列全部工具名。

## 执行

```bash
python "<WORK_ROOT>/scripts/work.py" ask "<user_input 原话>"
```

原话**原样**传（不改写、不先摘要），它返回确定的 `plan`，每项带可复制的 `command`。

- `plan` 有内容 → 从 `plan[0]` 顺序执行。
- `matched:false` → 看 `native_suggestion`，**改用 WorkBuddy 原生能力接着做**，不要硬凑工具、不要手写脚本模仿。
- `consult_mode:true` → 只答能力/参数，不要真跑会写产物的工具。
- `executable=false` / `needs_input` → **仍要调这个工具**：按 `args_hint` 补齐参数后自己拼 `call`，
  别照抄带 `<必填:…>` 的 args，也别因为 `command` 是 null 就跳过。
- `executable=true` 但答非所问 → 不是换工具，是**同一工具补参数重调**：
  `work_office_schema` 要带 `format`（+ `verb`/`element`）；`work_office_inspect` 要带 `file`
  （必需），`mode` 只是选看哪一面（text / outline / stats / annotated / issues / forms）。
- `checkpoint` → 收件与汇总同时命中，两步口径不同，转述前先读。
- `notes` → 该意图的硬口径，逐条遵守。

工具返回 `ok:false` 是业务结果不是故障：读 `error` 修一次参数再试，别直接放弃。

## 细节

通讯泛问与微信口径读 `references/wechat.md`，企业微信读 `references/wecom.md`，
Office / COM / 克隆引擎桥读 `references/office.md`，台账 / 经验 / 备份读 `references/memory.md`，
公文（附加）读 `references/gongwen.md`，报错读 `references/troubleshooting.md`。

工具按模块拆成四个：通讯 `comms.py`、Office `office.py`、记忆 `memory.py`（台账 / 经验 / 备份）、
其他 `other.py`（状态 / 模块清单，公共底座）。三个业务模块互不 import。
用户问「这块归哪」「只改通讯」「单独测一下」时：
`work.py modules` 看归属与验收命令，`work.py tools --module comms|office|memory` 只看该模块工具。

**泛问「最近有什么通知」是特例**：`ask` 命中 `comms_notices` 时 `plan` 只有一条，
照它执行完就答，不要再加总览、逐会话读消息、全文搜索。

## 收尾

把**产物具体路径**和关键数字写进回复（收到几份、未交几家、异常几条、页数、字体降级等）。
收件优先用工具返回的 `summary`（已把「新增几份 / 去重几份 / 归档总数」说清）；
汇总若 `consistency.level=warn`，按 `detail` 说明「本次未交」不等于「从未提交」。
不要只说「已完成」，不要贴大段 JSON，不要给用户讲 MCP / 解释器 / 网页服务。
