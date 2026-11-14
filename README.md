# AI Agent Workbench — 五位一体 AI 工程与治理套件

> **本质解构 · 能力执行 × 行为规约 × 模型测谎**  
> 本仓库将 5 个定位完全不同、但共同服务于 AI Agent 实战落地与可控治理的工具进行统一解构与模块化整合，既支持**单模块独立部署与直调**，也支持**跨层协同工作流**。

---

## 一、五工具三维解构总览

这 5 个工具并非同质化的“工具堆砌”，而是分别处于 AI Agent 系统的**三个正交层级**：

| 所属层级 | 工具包 | 本质定位 | 运行形态 | 核心价值 |
| :--- | :--- | :--- | :--- | :--- |
| **1. 能力执行层**<br>*(External Tools & Memory)* | **`work-comms`**<br>*(23 工具)* | 本地微信 4.x / 企业微信只读感知与收件归档引擎 | MCP Server + Codex/WorkBuddy Skill + CLI | 突破 4.x 加密与无 UIA 限制：DPAPI 密钥缓存 + WAL 帧级增量解密 + `.dat`/WXGF 解图 + 协同 `wechat-chat-mcp` 多模态解读 |
| **1. 能力执行层**<br>*(External Tools & Memory)* | **`work-office`**<br>*(18 工具)* | 跨引擎文档与表格高保真读写、比对、套打、合成引擎 | MCP Server + Codex/WorkBuddy Skill + CLI | 统一调度 `OfficeCLI`、`excel-mcp-server`（46 子工具）、`Office-Word-MCP-Server`（58 子工具）、Windows COM 与原生批处理 |
| **1. 能力执行层**<br>*(External Tools & Memory)* | **`work-memory`**<br>*(4 工具)* | **「流程经验配方 + 通用事实记忆 + 材料台账」**双轨记忆中枢 | MCP Server + Codex/WorkBuddy Skill + Console | 一句话从真实调用轨迹提炼「零探索直调配方」（`work_exp`），并接入开源 `Engram` 持久化事实记忆与 Obsidian 导出 |
| **2. 行为规约层**<br>*(System Prompt & IDE Rules)* | **`instruct-unrestricted`** | **多端 AI IDE 规则编译器与破限控制中枢** | Python SDK + CLI + REST API (`:5055`) | 不是 MCP/Skill，而是直接编译与注入 7 大 IDE/Agent 的底层规则文件，降低无意义拒答并强制工程执行纪律 |
| **3. 模型可信层**<br>*(Black-box Verification)* | **`modeltrace-attribution`** | **黑盒 LLM 指纹归因与反代掺水测谎系统** | Python SDK + CLI + REST API (`:5055`) | 不依赖服务端坦白：通过整数采样分布、Hellinger 距离与抗伪造检测器，识别真实模型血统与第三方代理代码伪造/降智 |

> 另附：`packages/work-toolkit` 内含 **2 个全局状态工具**（`work_status` / `work_modules`，共计 **47 个 MCP 工具**）以及 **Codex 风格本地装配控制台**（`web/serve.py`，端口 `8765`，用于可视化浏览工具、查看记忆图谱并一键开关 Codex `config.toml` 中的各模块）。

---

## 二、仓库解构目录

```text
ai-agent-workbench/
├── README.md                          # 总览与快速导航
├── LICENSE                            # MIT 许可（含第三方组件说明）
├── docs/
│   ├── ARCHITECTURE.md                # 深度解构：三层五工具本质差异、协作模式与边界
│   ├── SETUP_GUIDE.md                 # 详细安装与配置教程（Codex / WorkBuddy / IDE / 微服务）
│   └── TOOLS_REFERENCE.md             # 47 个 MCP 工具、CLI 命令与 REST API 全量手册
├── packages/
│   ├── work-toolkit/                  # 【工具 1-3】comms / office / memory 统一包
│   │   ├── mcp/                       # 4 个 MCP 入口：work_{comms,office,memory,toolkit}_server.py
│   │   ├── wechat/ & wecom/           # 微信 4.x SQLCipher+WAL+DPAPI & 企微 wxSQLite3 链路
│   │   ├── experience/                # work_exp 轨迹提炼与参数化零探索重放
│   │   ├── skills/                    # work-comms / work-office / work-memory / work-office-toolkit
│   │   ├── scripts/                   # work.py / install_skills.py / setup.py / launch.py
│   │   ├── tools/                     # doctor.py / register_mcp.py / regress.py / check_*
│   │   ├── web/                       # Codex 风格可视化装配控制台 (port 8765)
│   │   └── bin/                       # 内置 OfficeCLI (v1.0.161) 与第三方说明
│   ├── instruct-unrestricted/         # 【工具 4】7 大 IDE 破限控制中枢与认知编译器
│   │   ├── manager.py & cli.py        # UnrestrictedManager + CLI (status/options/apply/verify/reset)
│   │   ├── engines/                   # Cursor / Windsurf / Claude / Codex / Antigravity / WorkBuddy / Trae
│   │   ├── composer/                  # 4 角色 × 3 严格度 × 7 平台动态规则组装器
│   │   ├── compilers/ & presets/      # 底层协议编译器与确定性规则包
│   │   └── tests/test_unrestricted.py # 自动化单测
│   └── modeltrace-attribution/        # 【工具 5】大模型路由归因与特征测谎系统
│       ├── cli.py                     # CLI (bank-info/check-spoof/analyze/test-api)
│       ├── engine/                    # fingerprint / spoof_detector / collector / challenge_suite
│       ├── data/                      # 18 款官方基准模型指纹库 + 648 份真实样本
│       └── tests/test_modeltrace.py   # 自动化单测
├── services/
│   └── core_api_server.py             # 工具 4 + 工具 5 联合 RESTful 微服务 (port 5055)
└── scripts/
    ├── bootstrap.py                   # 一键运行全部测试与静态校验
    ├── requirements-toolkit.txt       # 工具 1-3 Python 依赖
    └── requirements-core.txt          # 工具 4-5 Python 依赖
```

---

## 三、快速开始（5 分钟跑通）

### 1. 安装依赖

```powershell
# 工具 1-3（通讯 / Office / 记忆 MCP）依赖
python -m pip install -r scripts/requirements-toolkit.txt

# 工具 4-5（破限编译器 / 模型测谎）依赖
python -m pip install -r scripts/requirements-core.txt
```

### 2. 一键校验全仓库模块

```powershell
python -X utf8 scripts/bootstrap.py
```

### 3. 按场景启用对应层

- **接入 Codex / WorkBuddy（工具 1-3）**：
  ```powershell
  # 安装/同步模块化 Skills 到 ~/.workbuddy/skills 与 ~/.codex/skills
  python -X utf8 packages/work-toolkit/scripts/install_skills.py
  # 注册 WorkBuddy MCP 服务（~/.workbuddy/mcp.json）
  python -X utf8 packages/work-toolkit/tools/register_mcp.py
  # 打开 Codex 风格装配控制台（可选，http://127.0.0.1:8765）
  python -X utf8 packages/work-toolkit/web/serve.py
  ```
- **为 AI IDE 注入规则（工具 4：`instruct-unrestricted`）**：
  ```powershell
  python -X utf8 packages/instruct-unrestricted/cli.py status
  python -X utf8 packages/instruct-unrestricted/cli.py apply --app cursor --role fullstack --strictness hyperdrive --workspace /path/to/project
  ```
- **检测 API 端点真伪/是否掺水（工具 5：`modeltrace-attribution`）**：
  ```powershell
  python -X utf8 packages/modeltrace-attribution/cli.py bank-info
  python -X utf8 packages/modeltrace-attribution/cli.py test-api --base-url "https://api.openai.com/v1" --api-key "sk-..." --model "gpt-4o"
  ```
- **启动工具 4+5 联合 REST 微服务（端口 `5055`）**：
  ```powershell
  python -X utf8 services/core_api_server.py
  ```

---

## 四、深入文档

- [架构解构指南 (`docs/ARCHITECTURE.md`)](docs/ARCHITECTURE.md)：拆解 5 个工具的不同技术本质、内部数据流、安全边界与组合范式。
- [完整配置教程 (`docs/SETUP_GUIDE.md`)](docs/SETUP_GUIDE.md)：涵盖 Python 环境、Codex `config.toml`、WorkBuddy、开源 companion 项目（`wechat-chat-mcp`、`excel-mcp-server`、`Office-Word-MCP-Server`、`Engram`）与常见问题排查。
- [工具与接口全量参考 (`docs/TOOLS_REFERENCE.md`)](docs/TOOLS_REFERENCE.md)：47 个 MCP 工具签名、CLI 参数与 RESTful API 清单。
