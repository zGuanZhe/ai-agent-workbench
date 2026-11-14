# bin/ — OfficeCLI（第三方组件）

`officecli.exe` 是上游项目 **iOfficeAI/OfficeCLI**（本项目实测版本 `v1.0.160`）的 Windows 可执行文件，
用于读写与校验 `.docx` / `.xlsx` / `.pptx` 的 OpenXML 结构。

## 授权说明

> **这个二进制不是本项目的原创代码，不在仓库根目录 `LICENSE`（MIT）的授权范围内。**
> 其版权与再分发条款归上游所有，请以其官方发布页的许可证为准。
> 如需商用或二次分发，请先确认上游的授权条件；不确定时可自行从官方渠道获取并替换本文件。

仓库根目录下的 MIT 许可仅覆盖本项目自己编写的 Python 源码、网页模板、Markdown 文档与配置样例。

## 作用范围

| 工具 | 依赖关系 |
| --- | --- |
| `work_officecli_run`、`work_office_inspect` | 强依赖，缺了会明确报错 |
| `work_office_consolidate`、`work_office_format_gbt9704` | 可选：存在时追加一次 OpenXML `validate`，缺了主功能照常可用 |

其余 19 个 MCP 工具完全不依赖它。

验证是否可用：

```bat
bin\officecli.exe --version
python tools\doctor.py
```
