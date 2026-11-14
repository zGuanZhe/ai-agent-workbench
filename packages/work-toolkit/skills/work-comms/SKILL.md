---
name: work-comms
description: 【独立通讯模块】本机微信 4.x、企业微信与跨通道通知工具箱（可单独调用，也可与 work-office / work-memory 组合调用）。当用户要求读微信/企微群消息、按发言人或类型搜聊天记录、查群成员/群公告/共同群、统计群接龙与未接龙名单、搜微信收藏夹、生成群聊统计画像与提取待办/链接、看跨通道最近通知、扫描或归档微信/企微收件、解密聊天原图或将群聊摘录成文时使用。
---

# `work-comms` — 通讯模块（微信 4.x + 企业微信）独立与组合指南

本模块完全独立于 Office 与记忆模块（底层仅加载 `work_mods/comms.py`，自动维护 DPAPI 密钥缓存与 `-wal` 实时增量补丁）。**无需以 `/work` 或 `work.py ask` 作为前置，直接按需调用单工具或与其他模块组合。**

## 1. 单独调用：直接调 `work_comms` / `wechat_chat` MCP 工具

| 场景 | 直接调用的工具与核心参数 |
| :--- | :--- |
| 读某微信群/联系人最近消息 | `work_wechat_messages(chat="群名/备注", limit=40, since="可选")` |
| 多维搜微信消息（按词/按人/按类型） | `work_wechat_search(keyword="可选", sender="发言人", msg_type="link\|file\|quote\|...", chat="可选", since="可选")` |
| 查微信联系人/群成员/群公告/共同群 | `work_wechat_contacts(mode="contacts\|group\|announcements\|common_groups", chat="群名", keyword="人名")` |
| 微信群接龙解析与未接龙人员自动核对 | `work_wechat_solitaire(chat="可选", keyword="可选", roster="可选", check_missing=True)` |
| 搜微信收藏夹（密码笔记/链接/文件） | `work_wechat_favorites(keyword="可选", fav_type="all\|note\|link\|file\|record")` |
| 群聊深度画像、发言排行、提取链接与待办 | `work_wechat_insights(chat="群名或留空看全局", days=7, output="可选导出.md")` |
| 微信+企微最近重要通知与死线聚合 | `work_comms_notices(days=3, only_notices=True)` / `work_comms_overview()` |
| 微信收件扫描 / 归档并核对《科室底册》 | `work_wechat_files(mode="scan\|pull\|harvest", since="可选", roster="可选")` |
| 生成催办话术 / 解密聊天图 / 摘录成文 | `work_wechat_remind` / `work_wechat_media(chat="...", prefer="orig")` / `work_wechat_digest(chat="...", format="docx\|md")` |
| 企业微信会话/消息/搜索/通讯录/收件 | `work_wecom_sessions` / `work_wecom_messages` / `work_wecom_search` / `work_wecom_contacts` / `work_wecom_files` / `work_wecom_harvest` |
| 语音转写(SILK+Whisper) / 聊天内PDF·Office直读 | `wechat_chat` MCP: `list_attachments` → `read_attachment(message_id=..., start=0)` |

## 2. 单模块 CLI 入口（不经 `/work`）

- 短命令直调：`python -X utf8 "<WORK_ROOT>/scripts/work.py" comms messages "{\"chat\":\"老吴园区\"}"`
- 模块内自然语言执行：`python -X utf8 "<WORK_ROOT>/scripts/work.py" comms --run "看看【老吴园区】最近说了什么"`

## 3. 跨模块组合调用（`comms` + `office` / `memory`）

- **收件归档 + 记入台账 + 无模板跨表合并**：先调 `work_wechat_files(mode="harvest")`（`comms`），再组合 `work_ledger(action="record")`（`memory`）与 `work_office_consolidate(auto_schema=True)`（`office`）。
- **群聊洞察提取 + 合成现代风格 Word/PPT 汇报**：先调 `work_wechat_insights(chat="...")`（`comms`），再将生成的 Markdown 传给 `work_office_compose(format="docx"|"pptx", preset="modern", toc=True)`（`office`）。
