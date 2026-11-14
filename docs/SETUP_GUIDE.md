# 详细安装与配置教程（Setup Guide）

本教程覆盖全部 5 个工具在 **Windows（主力环境）** 及通用 Python 环境下的配置方法，所有模块均支持按需单独启用。

---

## 一、基础环境准备

1. **Python 3.10+**（推荐 Python 3.12 / 3.14，确保命令行可执行 UTF-8 输出）：
   ```powershell
   python --version
   ```
2. **克隆本仓库**：
   ```powershell
   git clone https://github.com/zGuanZhe/ai-agent-workbench.git
   cd ai-agent-workbench
   ```
3. **安装 Python 依赖**（可按需分开安装）：
   ```powershell
   # 若使用工具 1-3 (work-toolkit: comms / office / memory)
   python -m pip install -r scripts/requirements-toolkit.txt

   # 若使用工具 4-5 (instruct-unrestricted / modeltrace-attribution)
   python -m pip install -r scripts/requirements-core.txt
   ```
4. **运行一键自检**：
   ```powershell
   python -X utf8 scripts/bootstrap.py
   python -X utf8 packages/work-toolkit/tools/doctor.py
   ```

---

## 二、配置工具 1-3：`work-toolkit`（MCP + Skills + 装配台）

### 2.1 随仓内置的第三方引擎与一键依赖安装

本仓库已直接附带完整源码与核心二进制，无需再手工克隆第三方仓库：
- `packages/work-toolkit/bin/officecli.exe`（v1.0.161）与 `bin/engram.exe`（v3.7.1）
- `packages/work-toolkit/mcp_servers/excel-mcp-server`（46 工具）
- `packages/work-toolkit/mcp_servers/Office-Word-MCP-Server`（58 工具）
- `packages/work-toolkit/mcp_servers/OfficeMCP`
- `packages/work-toolkit/mcp_servers/wechat-chat-mcp`（语音转写/多模态深聊源码）

一键安装全部 Python 依赖并初始化 `wechat-chat-mcp` 独立 `.venv`：

```powershell
python -X utf8 scripts/bootstrap.py --install
```

> 若需导出 WXGF 高清原图（`work_wechat_media prefer="orig"`），额外安装 `ffmpeg` 并加入系统 `PATH`。

### 2.2 接入 Codex（模块化独立 MCP + 薄 Skill）

1. **安装/同步 Skills 到 `~/.codex/skills` 与 `~/.workbuddy/skills`**：
   ```powershell
   python -X utf8 packages/work-toolkit/scripts/install_skills.py
   ```
   会自动把仓库内的 `<WORK_ROOT>` 回填为你的实际本地路径，并清理历史冗余备份。

2. **在 `C:\Users\<你的用户名>\.codex\config.toml` 中按需登记独立 MCP Server**（将路径替换为你的实际路径）：
   ```toml
   [mcp_servers.work_comms]
   command = 'python'
   args = ["-X", "utf8", 'D:\path	oi-agent-workbench\packages\work-toolkit\mcp\work_comms_server.py']
   enabled = true
   startup_timeout_sec = 30
   tool_timeout_sec = 300

   [mcp_servers.work_comms.env]
   PYTHONIOENCODING = "utf-8"
   PYTHONUTF8 = "1"

   [mcp_servers.work_office]
   command = 'python'
   args = ["-X", "utf-8", 'D:\path	oi-agent-workbench\packages\work-toolkit\mcp\work_office_server.py']
   enabled = true
   startup_timeout_sec = 30
   tool_timeout_sec = 600

   [mcp_servers.work_office.env]
   PYTHONIOENCODING = "utf-8"
   PYTHONUTF8 = "1"

   [mcp_servers.work_memory]
   command = 'python'
   args = ["-X", "utf-8", 'D:\path	oi-agent-workbench\packages\work-toolkit\mcp\work_memory_server.py']
   enabled = true
   startup_timeout_sec = 30
   tool_timeout_sec = 300

   [mcp_servers.work_memory.env]
   PYTHONIOENCODING = "utf-8"
   PYTHONUTF8 = "1"

   [mcp_servers.officecli]
   command = 'D:\path	oi-agent-workbench\packages\work-toolkitin\officecli.exe'
   args = ["mcp"]
   enabled = true
   startup_timeout_sec = 30
   tool_timeout_sec = 300

   [mcp_servers.officecli.env]
   OFFICECLI_RESIDENT_FLUSH = "each"
   ```
   > 若同时配置了 `wechat-chat-mcp`，额外添加 `[mcp_servers.wechat_chat]` 指向其 `.venv\Scripts\python.exe` 与 `-m wechat_assistant.cli mcp`（建议 `tool_timeout_sec = 900`）。

### 2.3 接入 WorkBuddy

```powershell
# 自动探测最佳 Python 解释器并写入 ~/.workbuddy/mcp.json（同时保留原有 MCP）
python -X utf8 packages/work-toolkit/tools/register_mcp.py
python -X utf8 packages/work-toolkit/tools/register_mcp.py --check
```

### 2.4 启动本地可视化装配控制台（端口 `8765`）

```powershell
python -X utf8 packages/work-toolkit/web/build.py
python -X utf8 packages/work-toolkit/web/serve.py
```
浏览器打开 `http://127.0.0.1:8765`，即可查看 47 个工具明细、经验库与 Engram 记忆可视化图谱，并通过开关实时读写 Codex `config.toml`（写入前强制 `tomllib` 校验与 `.bak` 原子备份）。

### 2.5 微信 / 企业微信路径环境变量（如数据不在默认目录）

- 个人微信数据目录可通过环境变量 `WECHAT_ACCOUNT_DIR` 指定（例如 `D:\聊天记录\xwechat_files\wxid_xxx`）。
- 企业微信需在登录状态下自动提取内存密钥并缓存；若数据不在常规目录，可通过 `WECOM_ACCOUNT_DIR` 指定。

---

## 三、配置工具 4：`instruct-unrestricted`（7 大 IDE 规则编译器）

本工具无需注册 MCP，直接以 CLI 或 Python 模块运行，面向指定项目工作区编译并部署规则：

```powershell
# 1. 查看可选角色、严格度与受管 IDE 列表
python -X utf8 packages/instruct-unrestricted/cli.py options

# 2. 查看目标工作区各 IDE 规则当前完整性状态 (intact/drifted/missing/unmanaged/clean)
python -X utf8 packages/instruct-unrestricted/cli.py status --workspace "D:\path	o\your-project"

# 3. 为指定 IDE 动态组装并部署规则（自动生成 .backup 快照）
python -X utf8 packages/instruct-unrestricted/cli.py apply --app cursor --role security_audit --strictness hyperdrive --workspace "D:\path	o\your-project"

# 4. 校验文件指纹完整性
python -X utf8 packages/instruct-unrestricted/cli.py verify --workspace "D:\path	o\your-project"

# 5. 一键无损回滚（恢复到部署前的原始规则）
python -X utf8 packages/instruct-unrestricted/cli.py reset --app cursor --workspace "D:\path	o\your-project"
```

---

## 四、配置工具 5：`modeltrace-attribution`（模型归因与测谎）

同样零外部服务依赖，安装 `requirements-core.txt` 后开箱即用：

```powershell
# 1. 查看 18 款官方基准模型库与采样校准统计
python -X utf8 packages/modeltrace-attribution/cli.py bank-info

# 2. 检测文本是否为代理脚本生成的伪造数字序列
python -X utf8 packages/modeltrace-attribution/cli.py check-spoof --input "1 2 3 4 5 6 7 8 9 10"

# 3. 离线对一段大样本数字输出进行模型血统归因
python -X utf8 packages/modeltrace-attribution/cli.py analyze --input path/to/sample.txt

# 4. 在线高并发探测第三方中转 API（自动识别 OpenAI / Anthropic 协议）
python -X utf8 packages/modeltrace-attribution/cli.py test-api `
  --base-url "https://api.example.com/v1" `
  --api-key "sk-..." `
  --model "claude-sonnet-4-5" `
  --rounds 3
```

---

## 五、启动工具 4+5 联合调试微服务（端口 `5055`）

如需通过 HTTP/Web 页面或外部系统调用工具 4 与工具 5：

```powershell
python -X utf8 services/core_api_server.py
```
- 默认监听 `http://127.0.0.1:5055`（可通过环境变量 `PORT` 修改）。
- 健康检查：`GET http://127.0.0.1:5055/health`。
