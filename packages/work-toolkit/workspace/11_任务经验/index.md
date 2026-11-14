# 任务经验与可执行工具配方目录

> 一份 md = 一整段已验证的任务流程 + 精确到模块/子工具的零探索直调配方。
> 完成工作后说一句「将本次工作整理为经验」（`work_exp(action="distill")`）即可根据真实工具调用轨迹自动生成。
> 下次遇到同类任务，直接把对应 md 发给 AI，或调用 `work_exp(action="replay", doc_id="...")` 一键重放。

共 5 份：

| 日期 | 任务 | 涉及模块 | 核心工具链 | 一句话经验 | 文档 |
| --- | --- | --- | --- | --- | --- |
| 2026-11-13 | 群聊记录与图片提取_合成现代商务纪要与汇报PPT | comms / office | `work_wechat_messages`, `work_wechat_insights`, `work_office_compose`, `work_office_inspect:issues` | 先调 work_wechat_messages 与 work_wechat_insights 提取群聊关键讨论和待办，再直接调 work_office_compose(format=docx/pptx) 一次性合成带表格和流程图的正式文档或演示文稿。 | [20261113_群聊记录与图片提取_合成现代商务纪要与汇报PPT](20261113_群聊记录与图片提取_合成现代商务纪要与汇报PPT.md) |
| 2026-11-13 | 新旧版表格与文档差异比对_自动备份与高级结构检视 | memory / office | `work_backup_file`, `work_office_inspect:text`, `work_excel_mcp:describe_workbook`, `work_office_diff` | 先调 work_backup_file 备份原件，再用 work_office_inspect(mode=text) 与 work_excel_mcp:describe_workbook 摸清结构，最后调 work_office_diff 精确输出单元格级数值差额(delta)。 | [20261113_新旧版表格与文档差异比对_自动备份与高级结构检视](20261113_新旧版表格与文档差异比对_自动备份与高级结构检视.md) |
| 2026-11-13 | 微信群收件归档_未交名单核对_跨表自动汇总一条龙 | comms / memory / office | `work_wechat_files:harvest`, `work_ledger:missing`, `work_office_consolidate`, `work_office_inspect:text` | 横跨 comms/memory/office 三模块：先调 work_wechat_files(mode=harvest) 收件核名单，再调 work_ledger(action=missing) 查电话，最后用 work_office_consolidate(auto_schema=True) 合并校验。 | [20261113_微信群收件归档_未交名单核对_跨表自动汇总一条龙](20261113_微信群收件归档_未交名单核对_跨表自动汇总一条龙.md) |
| 2026-11-10 | 微信 4.x 本地图片解密：从「打不开」到全库 7216 张可用 | 微信 / 图片解密 | — | 图片解密失败先别怀疑算法，先确认 uin=0 这类合法零值有没有被 if uin 当成没取到；解密后先打印文件头字节再交给 Pillow。 | [20261110_微信4x本地图片解密攻关](20261110_微信4x本地图片解密攻关.md) |
| 2026-11-10 | 把工具箱封装成 WorkBuddy Skill 与斜杠命令（不打开网页也能用） | WorkBuddy / Skill | — | 注册 MCP 时不能只测 docx/openpyxl，必须测 mcp 本身；项目里叫 mcp/ 的目录会遮蔽真正的 PyPI 包，sys.path 用 append 不能用 insert(0)。 | [20261110_封装Skill与斜杠命令](20261110_封装Skill与斜杠命令.md) |
