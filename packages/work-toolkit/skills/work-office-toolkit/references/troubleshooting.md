# 排错

| 现象 | 怎么处理 |
| --- | --- |
| 工具返回 `ok:false` | 业务结果不是故障：读 `error` 修一次参数再试；`tools <工具名>` 查参数名 |
| OfficeCLI 报 `not_found` / `Sheet not found` | 路径或表名写错。xlsx 是 `/<真实表名>/<A1>`，错误信息里会列 `Available sheets` |
| `_prop` 不生效 | 属性名编错了。先 `work_office_schema(format=..., verb="set", element=...)` |
| 提示缺依赖 / 找不到可用 Python | `python "<WORK_ROOT>/scripts/work.py" setup`，修完重试 |
| WorkBuddy 里看不到 `work_*` MCP 工具 | 正常，本地桥接不依赖 MCP |
| 网页打不开 | 不影响功能；只有用户想看可视化时才 `python "<WORK_ROOT>/scripts/work.py" web` |
| 微信工具全失败 | `work_wechat_probe` 看通道，按 `alternatives` 换路，不要反复重试或编数据 |
| 微信搜不到消息 | 多半是缓存陈旧：`work_wechat_sync` 重解一次 |
| Office 文件被占用 | 先 `work_officecli_run command="close"`，或 `save` 落盘再读 |
| COM 报「没有注册类」/ `800401F3` | 本机没装该组件；`work_office_com action="apps"` 看可用应用，换 WPS/MS 或走 OfficeCLI 离线路径 |
| COM 报「文件被占用」/ `0x800A175D` | 文件在别的程序里开着；关掉再试，或改用 OfficeCLI |
| `run_python` 报 `str object is not callable` | 晚绑定下把属性当方法用了。`UsedRange.Cells(1,1).Address` 是属性，不要加括号 |
| `run_python` 改了没生效 | 忘了 `Save()`；或改的是新建的隐藏实例而不是用户开着的窗口（先 `action=active` 看现场） |
| 预览页 502 / 打不开 | 本机有代理时 `127.0.0.1` 也会被转发：浏览器/curl 加 `--noproxy`；或 `work_office_watch action=status` 看进程是否还在 |
| 预览不刷新 | 只认 officecli 自己的改动；外部程序改文件不会感知，重新 `start` |
| 端口 26315 被占 | `work_office_watch(action="start", port=26316)` |
| 汇总 `consistency.level=warn` | 不是错误，是口径提示：按 `detail` 说清「本次未交」≠「从未提交」 |
| `plan` 是空的 / `matched:false` | 这件事不在工具箱范围，按 `native_suggestion` 用原生能力做，别硬凑工具 |
