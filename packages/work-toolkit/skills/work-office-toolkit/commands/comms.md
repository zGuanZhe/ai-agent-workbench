---
description: 【独立通讯入口 /comms】只调通讯模块（微信 4.x / 企业微信 / 跨通道通知 / 群接龙 / 收藏夹 / 群画像 / 收件归档），不加载 Office 与记忆模块
argument-hint: "例如：/comms 看看【老吴园区】最近说了什么；/comms 通讯软件里最近有什么通知；/comms 统计微信群接龙"
---

<user_input>
$ARGUMENTS
</user_input>

---

按独立模块 `comms`（`work-comms` Skill）执行：
1. 若当前会话已挂载 `work_comms` / `wechat_chat` MCP，直接一步调用对应工具（如 `work_wechat_messages`、`work_wechat_search`、`work_comms_notices`、`work_wechat_solitaire`、`work_wechat_favorites`、`work_wechat_insights`、`work_wechat_files`）。
2. 或通过单模块 CLI 执行（仅加载 `comms.py`）：
```bash
python "<WORK_ROOT>/scripts/work.py" comms --run "<user_input 原话>"
```
