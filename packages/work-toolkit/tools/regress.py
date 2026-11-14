#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""regress.py — 每次改完跑一遍，确认全部工具与总 Skill 本地桥接没被改坏。

    python tools/regress.py            # 任何 python 都能启动，会自举到装齐依赖的那个
    python tools/regress.py --json     # 机器可读
    python tools/regress.py --quick    # 只跑与机器无关的用例（跳过 env / guard / slow）

用例分三类，报告里会标明，不拿「参数校验通过」冒充「功能跑通」：
    run   —— 真跑，只读或明确 dry/空结果，期望 ok:true
    guard —— 故意传不完整参数，期望 ok:false 且带人能读懂的报错（校验没崩）
    skip  —— 本机没有微信数据，环境性失败不算回归失败

不产生垃圾文件：写类工具一律走 dry_run / 0 匹配 / 参数校验，
不会往 workspace 里塞测试产物。
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))


def _bootstrap(argv: list[str]) -> int | None:
    """当前解释器没依赖时，换一个有的重新执行自己。"""
    if "--inproc" in argv:
        return None
    import doctor

    best, _ = doctor.find_best()
    if not best:
        print("找不到装齐 mcp / python-docx / openpyxl 的解释器，先跑 scripts\\setup.cmd",
              file=sys.stderr)
        return 1
    here = doctor.probe(sys.executable)
    if doctor.is_ready(here):        # 当前就够用，省一次进程
        return None
    return subprocess.call([best, str(Path(__file__).resolve()), "--inproc"] + argv)


# (工具名, 参数, 期望类型, 说明)
CASES = [
    ("work_status",             {},                                             "ok",    "九大引擎状态"),
    ("work_modules",            {"detail": False},                              "ok",    "模块清单：四模块与工具数"),
    ("work_exp",                {"action": "list"},                              "ok",    "经验目录列表"),
    ("work_exp",                {"action": "template", "title": "回归样板"},      "ok",    "空白模板（不落盘）"),
    ("work_exp",                {"action": "get"},                               "guard", "缺 doc_id 校验"),
    ("work_exp",                {"action": "search", "module": "office"},        "ok",    "按模块检索工具级经验配方"),
    ("work_exp",                {"action": "replay", "doc_id": "20261113_新旧版表格与文档差异比对_自动备份与高级结构检视", "dry_run": True}, "ok", "经验配方参数化重放预览"),
    ("work_mem",                {"action": "stats"},                             "ok",    "Engram 通用记忆库统计"),
    ("work_mem",                {"action": "search", "query": "modern"},         "ok",    "Engram FTS5 检索长期口径"),
    ("work_mem",                {"action": "save", "title": ""},                 "guard", "Engram 保存记忆必填校验"),
    ("work_ledger",             {"action": "summary"},                          "ok",    "台账汇总"),
    ("work_ledger",             {"action": "departments"},                      "ok",    "科室对齐"),
    ("work_wechat_probe",       {},                                             "env",   "通道探测"),
    ("work_wechat_files",       {"mode": "scan", "limit": 3},                   "env",   "收件扫描(mode=scan)"),
    ("work_wechat_sessions",    {"limit": 3},                                  "env",   "会话列表"),
    ("work_wechat_search",      {"keyword": "通知", "limit": 2},                 "env",   "全文搜索"),
    ("work_wechat_search",      {"msg_type": "quote", "limit": 2},              "env",   "多维搜索：按引用回复筛选"),
    ("work_wechat_contacts",    {"mode": "announcements", "limit": 3},          "env",   "微信群公告跨群汇总"),
    ("work_wechat_contacts",    {"mode": "contacts", "limit": 3},               "env",   "微信通讯录与所在群查询"),
    ("work_wechat_contacts",    {"mode": "bogus"},                              "guard", "微信通讯录 mode 参数校验"),
    ("work_wechat_solitaire",   {"limit": 3},                                   "env",   "微信群接龙解析与未接龙核对"),
    ("work_wechat_favorites",   {"limit": 3},                                   "env",   "微信收藏夹检索与提取"),
    ("work_wechat_insights",    {"chat": "老吴园区", "days": 365, "limit": 60},   "env",   "微信群聊画像与待办提取"),
    ("work_wechat_media",       {},                                             "env",   "图片能力自检"),
    ("work_wechat_remind",      {"dry_run": True, "missing_list": ["回归测试科"]}, "env", "催办话术 dry-run"),
    ("work_wechat_files",       {"mode": "pull", "since": "2099-01-01", "limit": 1}, "guard", "归档 0 匹配（明确业务提示）"),
    ("work_wechat_files",       {"mode": "harvest", "since": "2099-01-01"},     "env",   "一条龙 0 匹配"),
    ("work_wechat_files",       {"mode": "bogus"},                              "guard", "收件 mode 参数校验"),
    ("work_wechat_sync",        {"force": False},                               "env",   "解密副本（不强制）"),
    # 下面这些是写类工具：用参数校验/空输入覆盖，不制造测试文件
    ("work_backup_file",        {"file": ""},                                   "guard", "备份参数校验"),
    ("work_office_to_pdf",      {},                                             "guard", "转 PDF 参数校验"),
    ("work_officecli_run",      {"command": "validate", "file": ""},             "guard", "OfficeCLI 参数校验"),
    ("work_office_consolidate", {"source_dir": "__不存在的回归目录__"},            "guard", "汇总参数校验"),
    ("work_office_format_gbt9704", {},                                          "run",   "公文排版（产物落系统临时目录）"),
    ("work_office_inspect",     {"file": "", "mode": "text"},                    "guard", "检视参数校验"),
    # 新增的 Office 能力：真的建、真的写、真的合并、真的转 PDF（全落沙箱）
    ("work_office_schema",      {"format": "docx", "element": "paragraph"},      "ok",    "schema 参考可读"),
    ("work_office_create",      {"file": ""},                                    "guard", "新建文档参数校验"),
    ("work_office_batch",       {"file": "", "ops": ""},                         "guard", "批量操作参数校验"),
    ("work_office_merge",       {"template": "", "output": "", "data": ""},        "guard", "模板合并参数校验"),
    ("work_office_import",      {"file": ""},                                    "guard", "CSV 导入参数校验"),
    ("work_office_com",         {"action": "bogus"},                             "guard", "COM 动作校验"),
    ("work_office_create",      {"file": "{sandbox}/回归新建.xlsx"},              "ok",    "新建空白表（落沙箱）"),
    ("work_office_import",      {"file": "{sandbox}/回归新建.xlsx", "sheet": "Sheet1",
                                 "data": "姓名,单位\n甲,乙局", "header": True},    "ok",    "CSV 导入（落沙箱）"),
    ("work_office_create",      {"file": "{sandbox}/回归模板.docx"},              "ok",    "新建空白文档（落沙箱）"),
    ("work_office_batch",       {"file": "{sandbox}/回归模板.docx",
                                 "ops": '[{"command":"add","parent":"/body","type":"paragraph",'
                                        '"props":{"text":"{{单位}}：回归"}}]'},    "ok",    "批量写入（落沙箱）"),
    ("work_office_merge",       {"template": "{sandbox}/回归模板.docx",
                                 "output": "{sandbox}/回归合并.docx",
                                 "data": '{"单位":"甲局"}'},                        "ok",    "模板合并（落沙箱）"),
    ("work_office_com",         {"action": "apps"},                              "ok",    "COM 组件探测"),
    ("work_office_com",         {"action": "active"},                            "ok",    "当前打开的文档（只读）"),
    ("work_office_com",         {"action": "run_python", "code": "output = data * 2",
                                 "data": "ab", "keep_open": False},              "ok",    "Python 沙箱：不碰 COM 也能量"),
    ("work_office_com",         {"action": "run_python"},                        "guard", "run_python 缺 code"),
    ("work_office_watch",       {"action": "status"},                            "ok",    "预览服务状态（只读）"),
    ("work_office_watch",       {"action": "start"},                             "guard", "预览缺文件"),
    ("work_office_com",         {"action": "info", "file": "{sandbox}/回归合并.docx"}, "slow", "COM 读页数与字数"),
    ("work_office_com",         {"action": "export_pdf", "file": "{sandbox}/回归合并.docx",
                                 "output": "{sandbox}/回归合并.pdf"},              "slow", "COM/WPS 导出 PDF"),
    ("work_wechat_digest",      {"chat": "__回归不存在的群__", "limit": 1},       "guard", "摘录参数校验"),
    ("work_wechat_messages",    {"chat": "__回归不存在的群__", "limit": 1},       "guard", "消息参数校验"),
    # 企业微信：另一套加密（wxSQLite3 逐页 AES-CBC），另一套库，单独覆盖
    ("work_wecom_probe",        {},                                             "env",   "企微通道探测"),
    ("work_wecom_sync",         {"force": False},                               "env",   "企微密钥与解密副本"),
    ("work_wecom_sessions",     {"limit": 3},                                   "env",   "企微会话列表"),
    ("work_wecom_messages",     {"chat": "__回归不存在的群__", "limit": 1},       "guard", "企微消息参数校验"),
    ("work_wecom_search",       {"keyword": "请假", "limit": 2},                 "env",   "企微正文搜索（protobuf 解出来才搜得到）"),
    ("work_wecom_files",        {"limit": 3},                                   "env",   "企微文件清单"),
    ("work_wecom_contacts",     {"keyword": "王", "limit": 2},                   "env",   "企微通讯录"),
    ("work_wecom_harvest",      {"since": "2099-01-01", "dry_run": True},        "ok",    "企微收材料 0 匹配（dry-run 不落盘）"),
    # 通讯：两通道合并视图。即使某条通道读不了，总览本身仍应 ok:true 并把原因写进卡里
    ("work_comms_overview",     {"limit": 2},                                   "ok",   "通讯总览：两通道状态 + 合并时间线"),
    # 通讯泛问的新主路径：一次调用跨两通道拿最近消息，通知优先排序
    ("work_comms_notices",      {"days": 3, "limit": 5},                        "ok",   "最近通知聚合：一次调用两通道"),
    ("work_comms_notices",      {"days": 1, "limit": 3, "only_notices": True},  "ok",   "最近通知：只看像通知的条目"),
    ("work_comms_notices",      {"since": "这不是时间"},                          "guard", "最近通知：since 解析校验"),
    # 克隆的 Office / MCP 引擎：体检与两个只读桥；写操作必须被默认只读拦住
    ("work_office_engines",     {},                                             "ok",   "引擎体检：真实加载并数工具数"),
    ("work_excel_mcp",          {"action": "list", "keyword": "pivot"},          "ok",   "Excel MCP：列透视相关工具"),
    ("work_excel_mcp",          {"action": "bogus"},                             "guard", "Excel MCP：action 校验"),
    ("work_excel_mcp",          {"action": "call", "tool": "create_workbook"},    "guard", "Excel MCP：写操作默认只读被拦"),
    ("work_word_mcp",           {"action": "list", "keyword": "comment"},        "ok",   "Word MCP：列批注相关工具"),
    ("work_word_mcp",           {"action": "call", "tool": "create_document"},    "guard", "Word MCP：写操作默认只读被拦"),
    ("work_office_inspect",     {"mode": "skills", "skill": "data-dashboard"},    "ok",   "OfficeCLI 内置技能指南加载"),
    ("work_office_inspect",     {"file": "{sandbox}/回归合并.docx", "mode": "screenshot",
                                 "output": "{sandbox}/回归截图.png"},              "ok",   "OfficeCLI 高清截图渲染（落沙箱）"),
    ("work_office_compose",     {"content_or_path": "# 回归富文档\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n$$E=mc^2$$",
                                 "output": "{sandbox}/回归合成.docx",
                                 "preset": "modern", "toc": True},                 "ok",   "通用富文档合成（表格+公式+目录，落沙箱）"),
    ("work_office_diff",        {"file_a": "{sandbox}/回归模板.docx",
                                 "file_b": "{sandbox}/回归合并.docx"},             "ok",   "Word 文档版本差异对比（只读）"),
    ("work_office_join_docx",   {"files": "{sandbox}/回归模板.docx,{sandbox}/回归合并.docx",
                                 "output": "{sandbox}/回归汇编.docx"},             "ok",   "多份 Word 文档拼接合并（落沙箱）"),
]

# 依赖本机微信数据的工具，换机器没数据时不算回归失败
ENV_HINTS = ("不存在", "没找到", "没有找到", "未就绪", "not found", "不可用", "无会话", "空")


def _bridge_rows(module: str = "") -> list[dict]:
    """验「总 Skill 的本地桥接」——不开网页、不连 MCP 也要能调到工具。"""
    work = ROOT / "scripts" / "work.py"
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("PYTHONUTF8", "1")
    specs = [
        ("work.py call", ["call", "work_status", "{}"], "本地桥接直调 work_status"),
        ("work.py ask", ["ask", "把 11 月报表收一下并合成汇总母表"], "意图路由：一句话命中两个工具"),
        ("work.py ask-readonly", ["ask", "把这个表汇总一下但别写文件"], "只读保护：写产物工具不进 plan"),
        ("work.py ask-out", ["ask", "帮我写一份 500 字的年度工作总结"],
         "边界：通用任务不接管，给出原生能力落点"),
        ("work.py ask-schema", ["ask", "docx 的 paragraph 有什么属性"],
         "schema 类提问只查手册，且预填 format"),
        ("work.py schema-empty", ["call", "work_office_schema", "{}"],
         "空参 schema 标 overview，不冒充属性表"),
        ("work.py ask-watch", ["ask", "开个实时预览 边改边看"],
         "新意图：实时预览命中并标出缺文件"),
        ("work.py ask-wecom", ["ask", "企业微信里收一下材料"],
         "企微意图：命中收材料，且不串到微信"),
        ("work.py ask-wecom-search", ["ask", "企业微信里谁提过请假"],
         "企微意图：谁提过 → wecom_search"),
        ("work.py ask-wechat-only", ["ask", "收材料"],
         "互斥：不带「企业微信」四个字时不许进企微通道"),
        ("work.py ask-wechat-chat", ["ask", "帮我读一下【老吴园区】最近的聊天记录，看看最近说了什么"],
         "读群消息：自动抽 chat=老吴园区，且不串入 office_inspect / probe"),
        ("work.py ask-solitaire", ["ask", "看看微信群接龙情况，谁还没接龙"],
         "群接龙：命中 work_wechat_solitaire 且不误触收件写操作"),
        ("work.py ask-favorites", ["ask", "找一下我微信收藏夹里的密码"],
         "微信收藏：自动抽 fav_type=note 与 keyword=密码"),
        ("work.py ask-insights", ["ask", "分析一下【老吴园区】群聊统计和谁发言最多"],
         "群聊画像：命中 work_wechat_insights 并自动抽 chat=老吴园区"),
        ("work.py ask-ledger-missing", ["ask", "看看台账里还有谁没交"],
         "台账未交：自动细分 action=missing 且不误触微信收件"),
        ("work.py ask-comms", ["ask", "通讯总览"],
         "通讯泛称：命中 work_comms_overview"),
        ("work.py ask-comms-no-clash", ["ask", "企微通讯录里查一下王"],
         "互斥：「通讯录」不能被「通讯总览」抢走"),
        ("work.py ask-notices", ["ask", "通讯软件中有什么值得注意的通知"],
         "通讯泛问：一次命中 work_comms_notices，不拼十几轮"),
        ("work.py ask-write-notice", ["ask", "帮我写一份关于开展消防检查的通知"],
         "防误判：写通知仍走原生能力，不进通讯聚合"),
        ("work.py ask-engines", ["ask", "克隆的那些 office 项目到底用上了没"],
         "新意图：克隆引擎体检"),
        ("work.py ask-office-range", ["ask", "读取 2026年11月汇总母表.xlsx 的 A1:G10 范围数据"],
         "防文件名误触发：母表区域读取走 work_office_inspect 并抽 cell_range"),
        ("work.py ask-office-diff", ["ask", "对比 2026年11月专项经费汇总母表_模板.xlsx 和 2026年11月汇总母表.xlsx 的版本差异"],
         "版本对比：自动抽 file_a / file_b 走 work_office_diff"),
        ("work.py ask-office-compose", ["ask", "把 06_运行状态/待发催办通知.md 加上 Mermaid 流程图和自动目录生成现代风格 Word 富文档"],
         "富文档合成：自动抽 content_or_path / preset=modern / toc=True"),
        ("work.py ask-office-join", ["ask", "把 01_微信收回原件 下的所有 docx 合并成一个 Word 文档"],
         "多份 Word 合并：走 work_office_join_docx 不误触 Excel 汇总"),
        ("work.py ask-modules", ["ask", "通讯和office是怎么分开的"],
         "模块划分：命中 work_modules"),
        ("work.py modules", ["modules", "--json"], "模块清单：四个模块与验收命令"),
        ("work.py tools-module", ["tools", "--module", "comms"], "按模块筛工具"),
        ("work.py tools-module-memory", ["tools", "--module", "memory"],
         "按模块筛工具：记忆模块"),
        ("work.py tools-module-combo", ["tools", "--module", "comms,office"],
         "跨模块组合筛工具：comms+office"),
        ("work.py subcmd-office", ["office", "看看这个表 2026年11月汇总母表.xlsx"],
         "单模块直达入口：work.py office（不经 /work）"),
        ("work.py subcmd-comms", ["comms", "帮我读一下【老吴园区】最近的聊天记录"],
         "单模块直达入口：work.py comms（不经 /work）"),
        ("work.py subcmd-memory", ["memory", "看看台账里还有谁没交"],
         "单模块直达入口：work.py memory（不经 /work）"),
        ("work.py pipeline-combo", ["pipeline", "--modules", "comms,office", "把 11 月报表收一下并合成汇总母表"],
         "跨模块组合流水线：work.py pipeline (comms+office)"),
        ("work.py tools", ["tools", "work_exp"], "本地桥接读工具清单"),
    ]
    rows = []
    for label, argv, note in specs:
        if module and _BRIDGE_MODULE.get(label, "other") not in (module, "other"):
            continue
        t = time.perf_counter()
        try:
            r = subprocess.run([sys.executable, str(work)] + argv,
                               capture_output=True, text=True, timeout=180,
                               cwd=str(ROOT), env=env,
                               encoding="utf-8", errors="replace")
            data = json.loads((r.stdout or "").strip() or "{}")
            plans = {p.get("tool") for p in data.get("plan", [])} if isinstance(data, dict) else set()
            if label.endswith("call"):
                ok = r.returncode == 0 and isinstance(data, dict) and bool(data.get("ok"))
            elif label == "work.py ask":
                ok = (r.returncode == 0 and isinstance(data, dict)
                      and data.get("matched") is True
                      and {"work_office_consolidate", "work_wechat_files"} <= plans
                      and bool(data.get("checkpoint")))
            elif label == "work.py ask-readonly":
                ok = (r.returncode == 0 and isinstance(data, dict)
                      and data.get("readonly_requested") is True
                      and "work_office_consolidate" not in plans)
            elif label == "work.py ask-out":
                ok = (r.returncode == 0 and isinstance(data, dict)
                      and data.get("matched") is False
                      and data.get("scope") == "out_of_scope"
                      and "docx" in str(data.get("native_suggestion"))
                      and not plans)
            elif label == "work.py ask-schema":
                first = (data.get("plan") or [{}])[0] if isinstance(data, dict) else {}
                ok = (r.returncode == 0 and isinstance(data, dict)
                      and plans == {"work_office_schema"}
                      and (first.get("args") or {}).get("format") == "docx")
            elif label == "work.py schema-empty":
                ok = (r.returncode == 0 and isinstance(data, dict)
                      and data.get("mode") == "overview"
                      and bool(data.get("examples")))
            elif label == "work.py ask-watch":
                first = (data.get("plan") or [{}])[0] if isinstance(data, dict) else {}
                ok = (r.returncode == 0 and isinstance(data, dict)
                      and plans == {"work_office_watch"}
                      and first.get("executable") is False
                      and "file" in (first.get("needs_input") or []))
            elif label == "work.py ask-wecom":
                ok = (r.returncode == 0 and isinstance(data, dict)
                      and "work_wecom_harvest" in plans
                      and not any(p.startswith("work_wechat_") for p in plans))
            elif label == "work.py ask-wecom-search":
                ok = (r.returncode == 0 and isinstance(data, dict)
                      and "work_wecom_search" in plans
                      and "work_wechat_search" not in plans)
            elif label == "work.py ask-wechat-only":
                ok = (r.returncode == 0 and isinstance(data, dict)
                      and "work_wechat_files" in plans
                      and not any(p.startswith("work_wecom_") for p in plans))
            elif label == "work.py ask-wechat-chat":
                first = (data.get("plan") or [{}])[0] if isinstance(data, dict) else {}
                ok = (r.returncode == 0 and isinstance(data, dict)
                      and plans == {"work_wechat_messages"}
                      and (first.get("args") or {}).get("chat") == "老吴园区"
                      and first.get("executable") is True)
            elif label == "work.py ask-solitaire":
                ok = (r.returncode == 0 and isinstance(data, dict)
                      and plans == {"work_wechat_solitaire"})
            elif label == "work.py ask-favorites":
                first = (data.get("plan") or [{}])[0] if isinstance(data, dict) else {}
                ok = (r.returncode == 0 and isinstance(data, dict)
                      and plans == {"work_wechat_favorites"}
                      and (first.get("args") or {}).get("fav_type") == "note"
                      and (first.get("args") or {}).get("keyword") == "密码")
            elif label == "work.py ask-insights":
                first = (data.get("plan") or [{}])[0] if isinstance(data, dict) else {}
                ok = (r.returncode == 0 and isinstance(data, dict)
                      and plans == {"work_wechat_insights"}
                      and (first.get("args") or {}).get("chat") == "老吴园区")
            elif label == "work.py ask-ledger-missing":
                first = (data.get("plan") or [{}])[0] if isinstance(data, dict) else {}
                ok = (r.returncode == 0 and isinstance(data, dict)
                      and plans == {"work_ledger"}
                      and (first.get("args") or {}).get("action") == "missing")
            elif label == "work.py ask-comms":
                ok = (r.returncode == 0 and isinstance(data, dict)
                      and plans == {"work_comms_overview"})
            elif label == "work.py ask-comms-no-clash":
                ok = (r.returncode == 0 and isinstance(data, dict)
                      and "work_wecom_contacts" in plans
                      and "work_comms_overview" not in plans)
            elif label == "work.py ask-notices":
                ok = (r.returncode == 0 and isinstance(data, dict)
                      and data.get("matched") is True
                      and plans == {"work_comms_notices"})
            elif label == "work.py ask-write-notice":
                ok = (r.returncode == 0 and isinstance(data, dict)
                      and data.get("matched") is False
                      and data.get("scope") == "out_of_scope" and not plans)
            elif label == "work.py ask-engines":
                ok = (r.returncode == 0 and isinstance(data, dict)
                      and plans == {"work_office_engines"})
            elif label == "work.py ask-office-range":
                first = (data.get("plan") or [{}])[0] if isinstance(data, dict) else {}
                ok = (r.returncode == 0 and isinstance(data, dict)
                      and plans == {"work_office_inspect"}
                      and (first.get("args") or {}).get("cell_range") == "A1:G10"
                      and first.get("executable") is True)
            elif label == "work.py ask-office-diff":
                first = (data.get("plan") or [{}])[0] if isinstance(data, dict) else {}
                ok = (r.returncode == 0 and isinstance(data, dict)
                      and plans == {"work_office_diff"}
                      and bool((first.get("args") or {}).get("file_a"))
                      and bool((first.get("args") or {}).get("file_b"))
                      and first.get("executable") is True)
            elif label == "work.py ask-office-compose":
                first = (data.get("plan") or [{}])[0] if isinstance(data, dict) else {}
                ok = (r.returncode == 0 and isinstance(data, dict)
                      and plans == {"work_office_compose"}
                      and (first.get("args") or {}).get("preset") == "modern"
                      and (first.get("args") or {}).get("toc") is True
                      and first.get("executable") is True)
            elif label == "work.py ask-office-join":
                first = (data.get("plan") or [{}])[0] if isinstance(data, dict) else {}
                ok = (r.returncode == 0 and isinstance(data, dict)
                      and plans == {"work_office_join_docx"}
                      and "01_微信收回原件" in str((first.get("args") or {}).get("source_dir", "")))
            elif label == "work.py ask-modules":
                ok = (r.returncode == 0 and isinstance(data, dict)
                      and data.get("matched") is True
                      and plans == {"work_modules"})
            elif label == "work.py modules":
                mods = {m.get("id") for m in (data.get("modules") or [])} if isinstance(data, dict) else set()
                ok = (r.returncode == 0 and {"comms", "office", "memory", "other"} <= mods
                      and data.get("total_tools", 0) >= 38)
            elif label == "work.py tools-module-combo":
                items = data.get("tools", []) if isinstance(data, dict) else []
                mods_got = {x.get("module") for x in items}
                ok = r.returncode == 0 and mods_got == {"comms", "office"}
            elif label.startswith("work.py tools-module"):
                want = label.rsplit("-", 1)[-1] if label.endswith(("-memory", "-other")) else "comms"
                items = data.get("tools", []) if isinstance(data, dict) else []
                ok = (r.returncode == 0 and bool(items)
                      and all(x.get("module") == want for x in items))
            elif label == "work.py subcmd-office":
                ok = (r.returncode == 0 and isinstance(data, dict)
                      and data.get("module_scope") == ["office"]
                      and plans == {"work_office_inspect"})
            elif label == "work.py subcmd-comms":
                ok = (r.returncode == 0 and isinstance(data, dict)
                      and data.get("module_scope") == ["comms"]
                      and plans == {"work_wechat_messages"})
            elif label == "work.py subcmd-memory":
                ok = (r.returncode == 0 and isinstance(data, dict)
                      and data.get("module_scope") == ["memory"]
                      and plans == {"work_ledger"})
            elif label == "work.py pipeline-combo":
                ok = (r.returncode == 0 and isinstance(data, dict)
                      and data.get("module_scope") == ["comms", "office"]
                      and {"work_wechat_files", "work_office_consolidate"} <= plans)
            else:
                items = data.get("tools", []) if isinstance(data, dict) else data
                ok = (r.returncode == 0 and isinstance(items, list)
                      and any(isinstance(x, dict) and x.get("name") == "work_exp" for x in items))
            err = "" if ok else ((r.stderr or "").strip()[-90:] or f"退出码 {r.returncode}")
        except Exception as exc:
            ok, err = False, f"{type(exc).__name__}: {exc}"[:90]
        rows.append({"tool": label, "status": "PASS" if ok else "FAIL",
                     "ms": round((time.perf_counter() - t) * 1000, 1),
                     "detail": err, "expect": "bridge", "note": note})
    return rows


def _quiet_fd2():
    """把底层 fd 2 接到空设备：comtypes 用 loguru 打 INFO，C 层直接写 fd，
    logging.disable 和 redirect_stderr 都拦不住。"""
    @contextlib.contextmanager
    def _cm():
        if not sys.stderr:
            yield
            return
        saved = os.dup(2)
        null = os.open(os.devnull, os.O_WRONLY)
        os.dup2(null, 2)
        try:
            yield
        finally:
            os.dup2(saved, 2)
            os.close(saved)
            os.close(null)

    return _cm()


# 桥接用例归哪个模块：底座流程归 other，通道专属的归 comms / office / memory。
# 拆模块之后要能单独验收，「只跑 comms」不能连 Office 的用例一起跑。
_BRIDGE_MODULE = {
    "work.py ask-notices": "comms",
    "work.py ask-comms": "comms",
    "work.py ask-comms-no-clash": "comms",
    "work.py ask-wecom": "comms",
    "work.py ask-wecom-search": "comms",
    "work.py ask-wechat-only": "comms",
    "work.py ask-wechat-chat": "comms",
    "work.py ask-solitaire": "comms",
    "work.py ask-favorites": "comms",
    "work.py ask-insights": "comms",
    "work.py ask-ledger-missing": "memory",
    "work.py ask-write-notice": "comms",
    "work.py ask-engines": "office",
    "work.py ask-office-range": "office",
    "work.py ask-office-diff": "office",
    "work.py ask-office-compose": "office",
    "work.py ask-office-join": "office",
    "work.py ask-modules": "other",
    "work.py modules": "other",
    "work.py tools-module": "comms",
    "work.py tools-module-memory": "memory",
    "work.py tools-module-combo": "other",
    "work.py subcmd-office": "office",
    "work.py subcmd-comms": "comms",
    "work.py subcmd-memory": "memory",
    "work.py pipeline-combo": "other", 
    "work.py tools": "memory",
    "work.py ask-schema": "office",
    "work.py schema-empty": "office",
    "work.py ask-watch": "office",
    "work.py ask-readonly": "office",
    "work.py ask": "office",
}


def run_cases(quick: bool, module: str = "") -> dict:
    # 整个回归期间静默：日志不只出现在导入时，comtypes 会在首次调用工具时
    # 再初始化一次。stdout 有缓冲、日志没有，不挡住就会糊在报告前面。
    with _quiet_fd2():
        return _run_cases_impl(quick, module)


def _run_cases_impl(quick: bool, module: str = "") -> dict:
    sys.path.append(str(ROOT / "mcp"))
    sys.path.append(str(ROOT / "wechat"))
    import work_toolkit_server as G  # noqa: E402
    from work_mods import ALL as _BOXES  # noqa: E402

    tool_module = {n: box.module for box in _BOXES for n in box.names()}
    module = (module or "").strip().lower()

    # 写类工具的产物只落系统临时目录，跑完就删，不碰用户材料库
    sandbox = Path(tempfile.mkdtemp(prefix="work-regress-"))
    draft_dir = Path(tempfile.gettempdir()) / "work-toolkit" / "drafts"
    before = set(draft_dir.glob("*.md")) if draft_dir.is_dir() else set()

    rows = []
    t0 = time.perf_counter()
    try:
        for name, args, expect, note in CASES:
            # base（状态/备份/台账/经验）是两个模块共同的地基，任何模块验收都带上
            if module and tool_module.get(name, "other") not in (module, "other"):
                continue
            if quick and expect in ("env", "guard", "slow"):
                continue
            if name == "work_office_format_gbt9704":
                args = {"content_or_path": "# 回归冒烟测试\n\n这一份不进用户材料库。",
                        "output_path": str(sandbox / "回归公文.docx")}
            # 用例里的 {sandbox} 换成真实临时目录，保证写类工具不碰用户材料库
            args = {k: (v.replace("{sandbox}", str(sandbox).replace("\\", "/"))
                        if isinstance(v, str) else v)
                    for k, v in args.items()}
            fn = getattr(G, name, None)
            if not callable(fn):
                rows.append({"tool": name, "status": "FAIL", "ms": 0,
                             "detail": "工具不存在", "expect": expect, "note": note})
                continue
            t = time.perf_counter()
            try:
                raw = fn(**args)
                data = json.loads(raw) if isinstance(raw, str) else raw
                ok = bool(isinstance(data, dict) and data.get("ok"))
                # 各工具报错字段不统一：有的用 error，有的用 reason + detail。
                # 只要给了人能读懂的原因就算「校验生效」。
                err = ""
                if isinstance(data, dict):
                    for k in ("error", "reason", "detail", "message"):
                        if data.get(k):
                            err = str(data[k])
                            break
            except Exception as exc:
                ok, err = False, f"{type(exc).__name__}: {exc}"
            ms = (time.perf_counter() - t) * 1000

            if expect == "guard":
                # 校验生效 = 明确报错，而不是抛异常或返回 ok:true 后偷偷写文件
                status = "PASS" if (not ok and err and "Error" not in err[:12]) else "FAIL"
                detail = err[:90] or ("不该返回 ok:true" if ok else "无报错文本")
            elif expect == "env" and not ok and any(h in err for h in ENV_HINTS):
                status, detail = "SKIP", "本机无微信数据：" + err[:70]
            else:
                status = "PASS" if ok else "FAIL"
                detail = "" if ok else (err[:90] or "ok=false")

            rows.append({"tool": name, "status": status, "ms": round(ms, 1),
                         "detail": detail, "expect": expect, "note": note})
        # 工具本体之外，还要验总 Skill 真正走的那条本地桥接
        rows.extend(_bridge_rows(module))
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)
        if draft_dir.is_dir():
            for p in set(draft_dir.glob("*.md")) - before:
                try:
                    p.unlink()
                except OSError:
                    pass

    total = time.perf_counter() - t0
    failed = [r for r in rows if r["status"] == "FAIL"]
    skipped = [r for r in rows if r["status"] == "SKIP"]
    return {
        "ok": not failed,
        "module": module or "all",
        "tools_registered": len([n for n in dir(G) if n.startswith("work_") and callable(getattr(G, n))]),
        "cases": len(rows),
        "passed": len(rows) - len(failed) - len(skipped),
        "failed": len(failed),
        "skipped": len(skipped),
        "elapsed_ms": round(total * 1000, 1),
        "rows": rows,
    }


def main() -> int:
    argv = sys.argv[1:]
    rc = _bootstrap(argv)
    if rc is not None:
        return rc
    argv = [a for a in argv if a != "--inproc"]
    ap = argparse.ArgumentParser(description="工具 + 本地桥接冒烟回归")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--module", default="",
                    help="只跑某个模块：comms / office / base（base 用例任何模块都带上）")
    args = ap.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    rep = run_cases(args.quick, args.module)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0 if rep["ok"] else 1

    scope = f"模块 {rep['module']}" if rep.get("module") not in ("", "all") else "全部模块"
    print(f"\n  工具 + 本地桥接回归 · work-toolkit · {scope}")
    print("  " + "-" * 68)
    for r in rep["rows"]:
        mark = {"PASS": "✅", "SKIP": "· ", "FAIL": "❌"}[r["status"]]
        print(f"  {mark} {r['tool']:<28} {r['ms']:>7.1f} ms  {r['note']}"
              + (f"  [{r['detail']}]" if r["detail"] else ""))
    print("  " + "-" * 68)
    print(f"  工具登记 {rep['tools_registered']} 个 · 用例 {rep['cases']} 条 · "
          f"通过 {rep['passed']} / 失败 {rep['failed']} / 跳过 {rep['skipped']} · "
          f"合计 {rep['elapsed_ms']:.0f} ms")
    print("  用例类型：run 真跑（只读/dry）· guard 参数校验 · env 依赖本机微信数据\n")
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
