# -*- coding: utf-8 -*-
"""任务经验与可执行工具配方库（experience/library.py）。

核心设计目标（解决「泛泛而谈」与「二次调用还要重新探索」两大痛点）：
  1. 真实轨迹驱动（Trace-Driven Distillation）：
     工具箱执行任何 comms / office / memory 工具时，自动在 06_运行状态/tool_trace.jsonl
     记录轻量调用轨迹（不注入上下文）。当用户说「将本次工作整理为经验」时，
     action="distill" 自动从近期轨迹提取真实调用过的模块、MCP 工具、子工具（如
     work_excel_mcp:describe_workbook）、精确参数、输入输出文件及失败重试记录。
  2. 绑定模块与工具链（Tool-Bound Playbook）：
     每份经验文档不仅有人可读的说明，还强制生成「三、用到的模块与精确工具清单」
     和「八、Agent 零探索直调配方」（含直接发给 AI 的零探索指令块、参数变量表、
     run_tool.py --batch 命令及内嵌 JSON 流水线）。
  3. 零探索复用（Zero-Exploration Replay & Skill Export）：
     - 直接把经验文档发给 Codex/WorkBuddy，AI 照单直调，跳过 --list / schema / probe；
     - 调用 work_exp(action="replay", doc_id=..., vars={...}) 可一键参数化重放；
     - 调用 work_exp(action="to_skill", doc_id=...) 可一键导出为独立子 Skill。
  4. 纯标准库实现（SQLite FTS5 可重建索引），文件即真相（workspace/11_任务经验/*.md）。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
DOC_DIR = ROOT / "workspace" / "11_任务经验"
STATE_DIR = ROOT / "workspace" / "06_运行状态"
TRACE_FILE = STATE_DIR / "tool_trace.jsonl"
INDEX_DB = DOC_DIR / ".exp_index.db"
SKILLS_EXPORT_DIR = DOC_DIR / "skills"

SECTIONS = (
    ("summary", "一句话经验", "下次遇到同类任务，最该先想起的一句话（点明核心工具与关键参数）"),
    ("goal", "一、这次要交付什么", "目标、边界、验收标准；写清「做完长什么样」"),
    ("materials", "二、用到的材料与参数变量", "输入文件 / 数据来源 / 可替换参数变量（{{var}}）"),
    ("tools_table", "三、用到的模块与精确工具清单（拒绝泛泛而谈）", "精确到模块、MCP 服务、工具名、子工具/模式与直调参数"),
    ("steps", "四、完整流程（按步直调）", "按顺序列步骤，命令与工具参数可直接复制运行"),
    ("decisions", "五、关键口径与决策", "当时为什么这么定，有没有被否掉的备选"),
    ("pitfalls", "六、踩过的坑（工具级实测避坑）", "现象 → 根因 → 改法（含从失败调用轨迹自动提炼的避坑点）"),
    ("outputs", "七、产物与落点", "最终文件、路径、格式、校验状态"),
    ("snippets", "八、Agent 零探索直调配方（直接发给 AI 或一键重放）", "直接发给 AI 的免探索指令块 + 一键重放命令 + 结构化流水线"),
)

_PLACEHOLDER = {
    "summary": "> （一句话：点明本次流程用到的核心模块工具与最关键的参数口径）",
    "goal": "（目标、边界、验收标准）",
    "materials": "| 材料 / 变量 | 默认路径 / 示例值 | 说明 |\n| --- | --- | --- |\n|  |  |  |",
    "tools_table": "| 步骤 | 所属模块 | MCP 服务 | 精确工具名 (含子工具/模式) | 核心参数结构 | 零探索直调要点 |\n| --- | --- | --- | --- | --- | --- |\n| 1 | office | work_office | work_office_inspect | `{\"file\":\"...\",\"mode\":\"text\"}` | 直接指定 mode，支持工作区裸文件名 |",
    "steps": "1. （第一步：调用 `工具名` ...）\n2. （第二步：调用 `工具名` ...）",
    "decisions": "- （定了什么口径/预设，为什么）",
    "pitfalls": "- **现象**：\n  **根因**：\n  **改法**：",
    "outputs": "| 产物 | 路径 | 说明 |\n| --- | --- | --- |\n|  |  |  |",
    "snippets": "```text\n（可整段复制发给 AI 的零探索直调指令 / CLI 批处理命令）\n```",
}

TOOL_CATALOG: Dict[str, Dict[str, str]] = {
    "work_office_inspect": {
        "module": "office",
        "mcp_server": "work_office",
        "label": "Office 全能检视与渲染（12种模式）",
        "sig": '{"file": "文件名或路径", "mode": "text|outline|stats|issues|annotated|forms|comments|screenshot|html|svg|pdf|skills", "cell_range": "A1:G10(可选)"}',
        "tip": "支持工作区裸文件名；Excel 区域可直接传 A1:G10（自动补工作表名）；视觉审计用 mode='screenshot'。",
    },
    "work_office_compose": {
        "module": "office",
        "mcp_server": "work_office",
        "label": "Markdown+GFM表格+Mermaid流程图+LaTeX公式合成 Word/PPT",
        "sig": '{"content_or_path": "Markdown文本或路径", "format": "docx|pptx", "title": "标题", "output": "输出路径", "toc": false, "watermark": ""}',
        "tip": "一次调用即可将 Markdown、代码块、表格、```mermaid 流程图、$$公式$$ 合成为原生 .docx 或 .pptx。",
    },
    "work_office_diff": {
        "module": "office",
        "mcp_server": "work_office",
        "label": "Word/Excel 新旧两版结构化差异对比",
        "sig": '{"file_a": "旧版路径", "file_b": "新版路径", "sheet": "可选表名", "output": "可选报告.md路径"}',
        "tip": "支持 .docx 段落/表格对比与 .xlsx 单元格级数值差额(delta)对比，支持工作区裸文件名。",
    },
    "work_office_join_docx": {
        "module": "office",
        "mcp_server": "work_office",
        "label": "多份 Word 文档批量合并",
        "sig": '{"source_dir": "目录路径", "files": "或文件列表", "output": "输出.docx", "page_break": true, "include_title": false}',
        "tip": "保留原段落样式与表格，适合多章节或多科室周报合稿。",
    },
    "work_office_consolidate": {
        "module": "office",
        "mcp_server": "work_office",
        "label": "多源表格汇总合并与异常核验（支持无模板通用合并）",
        "sig": '{"source_dir": "源目录", "auto_schema": true, "template_path": "可选母表模板", "output_path": "输出.xlsx"}',
        "tip": "通用跨表合并必传 auto_schema=true（自动识别 .xlsx/.csv/.tsv/.docx 表头）；政务专项经费摸底可传 template_path。",
    },
    "work_office_format_gbt9704": {
        "module": "office",
        "mcp_server": "work_office",
        "label": "多预设一键专业排版（modern/academic/memo/gbt9704）",
        "sig": '{"content_or_path": "Markdown或草稿路径", "preset": "modern|academic|memo|gbt9704", "output_path": "输出.docx"}',
        "tip": "日常商务/技术报告传 preset='modern'，学术三线表传 preset='academic'，简报传 preset='memo'，红头公文传 preset='gbt9704'。",
    },
    "work_office_merge": {
        "module": "office",
        "mcp_server": "work_office",
        "label": "Word/Excel/PPT 模板占位符套打（支持单条或 CSV/Excel 批量套打）",
        "sig": '{"template": "模板路径", "output": "输出文件或目录", "data": "JSON对象/数组或.csv/.xlsx路径", "name_key": "可选命名字段", "force": true}',
        "tip": "data 传 JSON 对象生成单份；传 JSON 数组或 .csv/.tsv/.xlsx 路径自动批量生成多份文档。",
    },
    "work_excel_mcp": {
        "module": "office",
        "mcp_server": "work_office",
        "label": "高级 Excel 46 工具桥接（透视表/图表/公式/条件格式/数据验证等）",
        "sig": '{"action": "call", "tool": "<子工具名>", "args": {"path": "表格路径"}, "allow_write": false}',
        "tip": "直接传 action='call' 与具体 tool 名（如 describe_workbook / read_cells / create_chart / create_pivot_table），args 可传 dict 或 JSON 字符串，写操作必传 allow_write=true；禁止先调 action='list' 盲猜。",
    },
    "work_word_mcp": {
        "module": "office",
        "mcp_server": "work_office",
        "label": "高级 Word 58 工具桥接（批注/脚注/尾注/保护/锚点插入/精准替换等）",
        "sig": '{"action": "call", "tool": "<子工具名>", "args": {"filename": "文档路径"}, "allow_write": false}',
        "tip": "直接传 action='call' 与具体 tool 名（如 get_all_comments / add_table / find_and_replace / add_restricted_editing），写操作必传 allow_write=true；禁止先调 action='list'。",
    },
    "work_office_batch": {
        "module": "office",
        "mcp_server": "work_office",
        "label": "OfficeCLI 批量 DOM 写操作（单次开关文件）",
        "sig": '{"file": "目标文件路径", "ops": [{"command": "set|add|remove", "path": "/body/p[1]", "props": {}}]}',
        "tip": "3 步以上 DOM 修改优先用 batch 一次完成，写前建议先调 work_backup_file。",
    },
    "work_officecli_run": {
        "module": "office",
        "mcp_server": "work_office",
        "label": "OfficeCLI 单条命令执行（常驻进程毫秒级响应）",
        "sig": '{"file": "文件路径", "command": "validate|get|query|set|add|remove", "args": []}',
        "tip": "底层走常驻 OfficeCLI 管道（~15ms），校验结构用 command='validate'。",
    },
    "work_office_create": {
        "module": "office",
        "mcp_server": "work_office",
        "label": "新建空白 .docx/.xlsx/.pptx",
        "sig": '{"file": "输出文件路径", "force": true}',
        "tip": "新建空白 Office 文件后配合 work_office_batch 或 work_office_import 写入内容。",
    },
    "work_office_import": {
        "module": "office",
        "mcp_server": "work_office",
        "label": "将 CSV/TSV 导入 Excel 工作表（支持自动筛选与冻结首行）",
        "sig": '{"file": "目标.xlsx", "source": "源.csv路径", "sheet": "Sheet1", "header": true}',
        "tip": "header=true 会自动加上表头筛选并冻结首行。",
    },
    "work_office_to_pdf": {
        "module": "office",
        "mcp_server": "work_office",
        "label": "通过本机 Office/WPS COM 导出标准版面 PDF",
        "sig": '{"file": "源文件路径", "output": "可选输出.pdf", "engine": "auto"}',
        "tip": "支持单文件 file 或整目录 batch_dir 批量转 PDF。",
    },
    "work_office_com": {
        "module": "office",
        "mcp_server": "work_office",
        "label": "本机 Office/WPS COM 直连操控（重算公式/刷新目录域/查找替换等）",
        "sig": '{"action": "info|recalc|refresh_fields|replace|export_pdf|read_cells|set_cells", "file": "文件路径"}',
        "tip": "刷新 Word 目录域用 action='refresh_fields'，重算 Excel 公式用 action='recalc'。",
    },
    "work_comms_notices": {
        "module": "comms",
        "mcp_server": "work_comms",
        "label": "跨微信 + 企业微信近期通知/截止时间/待办聚合",
        "sig": '{"days": 3, "only_notices": false, "keyword": "可选关键词", "limit": 40}',
        "tip": "泛问「最近有什么通知/新消息」首选本工具，一次调用跨双通道合并并提取截止时间。",
    },
    "work_wechat_messages": {
        "module": "comms",
        "mcp_server": "work_comms",
        "label": "读取指定微信群/联系人聊天记录（含 WAL 实时增量）",
        "sig": '{"chat": "群名或备注名", "since": "可选起始时间", "limit": 50}',
        "tip": "自动执行毫秒级 WAL 增量同步，无需预先调 probe 或 sync。",
    },
    "work_wechat_search": {
        "module": "comms",
        "mcp_server": "work_comms",
        "label": "微信全库/单群多维检索（关键词 + 发言人 + 消息类型）",
        "sig": '{"keyword": "关键词", "chat": "可选群名", "sender": "可选发言人", "msg_type": "all|text|file|image|voice|link|card|quote|solitaire|notice", "since": ""}',
        "tip": "支持只按 sender 或只按 msg_type 检索（如查某人全部发言或某群全部文件/引用/接龙）。",
    },
    "work_wechat_files": {
        "module": "comms",
        "mcp_server": "work_comms",
        "label": "微信收件统一入口（scan统计 / pull筛选复制 / harvest归档+核对未交）",
        "sig": '{"mode": "scan|pull|harvest", "since": "起始时间", "keyword": "可选关键词", "exts": ["docx","xlsx"], "roster": "可选底册路径"}',
        "tip": "mode='scan' 只读统计；mode='pull' 按条件复制；mode='harvest' 一键归档并对照底册核出未交名单。",
    },
    "work_wechat_contacts": {
        "module": "comms",
        "mcp_server": "work_comms",
        "label": "微信通讯录、群成员列表、群公告汇总与共同群查询",
        "sig": '{"mode": "contacts|group|announcements|common_groups", "query": "姓名或群名", "chat": "群名"}',
        "tip": "查群成员传 mode='group', chat='群名'；查所有群公告传 mode='announcements'；查某人在哪些群传 mode='common_groups'。",
    },
    "work_wechat_solitaire": {
        "module": "comms",
        "mcp_server": "work_comms",
        "label": "微信群接龙自动解析去重与未接龙名单核对",
        "sig": '{"chat": "可选群名", "keyword": "可选主题词", "roster": "可选底册路径或名单"}',
        "tip": "自动取每个接龙主题的最新一轮序号，对照群成员或底册算出未接龙人员。",
    },
    "work_wechat_favorites": {
        "module": "comms",
        "mcp_server": "work_comms",
        "label": "微信收藏夹（favorite.db）搜索与提取",
        "sig": '{"keyword": "可选关键词", "fav_type": "all|text|link|file|image|chat_record", "limit": 20}',
        "tip": "直接检索本机微信收藏的笔记、网页链接、文档与打包聊天记录。",
    },
    "work_wechat_insights": {
        "module": "comms",
        "mcp_server": "work_comms",
        "label": "微信群聊统计画像、高频发言人、分享链接与待办提取",
        "sig": '{"chat": "群名", "days": 30, "limit": 500}',
        "tip": "一键生成群活跃度、消息类型占比、Top 发言人、分享链接清单与通知/待办事项。",
    },
    "work_wechat_media": {
        "module": "comms",
        "mcp_server": "work_comms",
        "label": "微信加密图片（.dat）解密导出为 PNG/JPG",
        "sig": '{"chat": "群名或联系人", "prefer": "thumb|orig", "ref": "可选单图32位hex", "limit": 20}',
        "tip": "看截图小字必传 prefer='orig'；单图定位传消息里的 media_ref。",
    },
    "work_wechat_digest": {
        "module": "comms",
        "mcp_server": "work_comms",
        "label": "将群聊/私聊记录（含解密图片）整理为归档 Word/Markdown",
        "sig": '{"chat": "群名", "format": "docx|md", "include_media": true, "since": "可选起始时间"}',
        "tip": "include_media=true 会将聊天图片解密后直接嵌入文档正文。",
    },
    "work_wechat_remind": {
        "module": "comms",
        "mcp_server": "work_comms",
        "label": "根据未交名单生成 @ 催办通知话术并写入待发催办通知.md",
        "sig": '{"missing_list": ["科室A", "科室B"], "chat_group": "群名", "deadline_text": "今天17:00前"}',
        "tip": "生成可直接复制粘贴到微信群的 @ 催办通知文本。",
    },
    "work_ledger": {
        "module": "memory",
        "mcp_server": "work_memory",
        "label": "材料收发可追溯台账（汇总/科室多版对比/未交名单/历史明细/记账）",
        "sig": '{"action": "summary|departments|missing|history|batches|record", "dept": "可选科室", "record_dir": "记账目录"}',
        "tip": "核对未交名单与电话用 action='missing'；识别重复修改提交用 action='departments'。",
    },
    "work_backup_file": {
        "module": "memory",
        "mcp_server": "work_memory",
        "label": "写前安全备份（带时间戳复制到 02_自动备份区/）",
        "sig": '{"file": "文件路径或工作区裸文件名"}',
        "tip": "任何覆盖式修改重要 Word/Excel 前先调本工具备份。",
    },
    "work_mem": {
        "module": "memory",
        "mcp_server": "work_memory",
        "label": "通用跨会话结构化记忆引擎（基于开源 Gentleman-Programming/engram）",
        "sig": '{"action": "save|search|context|get|timeline|pin|unpin|delete|stats|doctor|export_obsidian", "title": "...", "content": "...", "query": "..."}',
        "tip": "用于保存和检索跨会话的长期事实、口径、偏好、决策与工具规律（支持 topic_key 自动覆盖演进与 Obsidian 图谱导出）。",
    },
}
_ILLEGAL = re.compile(r'[\\/:*?"<>|\r\n\t]')
_PIPELINE_RE = re.compile(
    r"<!--\s*WORK_EXP_PIPELINE_BEGIN\s*\n(.*?)\n\s*WORK_EXP_PIPELINE_END\s*-->",
    re.DOTALL,
)


def _safe_name(s: str, limit: int = 36) -> str:
    s = _ILLEGAL.sub("", (s or "").strip())
    s = re.sub(r"\s+", " ", s).strip(" .")
    return (s[:limit] or "未命名任务")


def _parse_list_field(raw: str) -> List[str]:
    return [t.strip().strip('"').strip("'") for t in re.split(r"[,，]", raw.strip("[]")) if t.strip()]


def _parse_front(text: str) -> dict:
    """解析 Markdown 顶部 YAML front matter（支持 tags / modules / tools 数组）。"""
    meta: dict = {}
    if not text.startswith("---"):
        return meta
    end = text.find("\n---", 3)
    if end < 0:
        return meta
    for line in text[3:end].splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        key = k.strip()
        val = v.strip().strip('"').strip("'")
        if key in ("tags", "modules", "tools"):
            meta[key] = _parse_list_field(val)
        else:
            meta[key] = val
    return meta


def _build_front(
    title: str,
    day: str,
    tags=None,
    summary: str = "",
    modules=None,
    tools=None,
) -> str:
    tag_txt = ", ".join(tags or [])
    mod_txt = ", ".join(modules or [])
    tool_txt = ", ".join(tools or [])
    lines = [
        "---",
        f"title: {title}",
        f"date: {day}",
        f"tags: [{tag_txt}]",
    ]
    if mod_txt:
        lines.append(f"modules: [{mod_txt}]")
    if tool_txt:
        lines.append(f"tools: [{tool_txt}]")
    if summary:
        lines.append(f'summary: "{summary.replace(chr(34), chr(39))[:160]}"')
    lines += ["---", ""]
    return "\n".join(lines)


def _extract_pipeline(text: str) -> Optional[Dict[str, Any]]:
    """从经验文档中提取内嵌的结构化流水线 JSON。"""
    m = _PIPELINE_RE.search(text or "")
    if not m:
        return None
    try:
        data = json.loads(m.group(1).strip())
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _extract_agent_prompt(text: str) -> str:
    """提取文档中「复制发给 AI 的零探索指令」代码块。"""
    marker = "### 1. 直接发给 AI 的零探索指令"
    idx = (text or "").find(marker)
    if idx < 0:
        return ""
    sub = text[idx:]
    m = re.search(r"```(?:text|markdown)?\n(.*?)```", sub, re.DOTALL)
    return m.group(1).strip() if m else ""


def _tool_info(tool_name: str, sub_tool: str = "") -> Dict[str, str]:
    clean = (tool_name or "").strip()
    for prefix in ("mcp__work_office__", "mcp__work_comms__", "mcp__work_memory__", "mcp__work_toolkit__"):
        if clean.startswith(prefix):
            clean = clean[len(prefix):]
    if clean in TOOL_CATALOG:
        info = dict(TOOL_CATALOG[clean])
        info["canonical_tool"] = clean
        info["display_tool"] = f"{clean}:{sub_tool}" if sub_tool and not sub_tool.startswith("[") else clean
        return info
    if clean.startswith("mcp__wechat_chat__"):
        short = clean[len("mcp__wechat_chat__"):]
        return {
            "module": "comms",
            "mcp_server": "wechat_chat",
            "canonical_tool": clean,
            "display_tool": f"wechat_chat:{short}",
            "label": f"微信深度分析与多模态附件工具 ({short})",
            "sig": "{...}",
            "tip": "通过 wechat_chat MCP 直接调用，支持语音转写、图片OCR、PDF/Word附件分页读取与群成员关联发现。",
        }
    if clean.startswith("mcp__officecli__") or clean == "officecli":
        return {
            "module": "office",
            "mcp_server": "officecli",
            "canonical_tool": clean,
            "display_tool": f"officecli:{sub_tool}" if sub_tool else clean,
            "label": "OfficeCLI 原生文档树操作",
            "sig": '{"command": "view|get|set|add|validate ..."}',
            "tip": "优先通过 work_office_inspect 或 work_office_batch 调用常驻进程。",
        }
    mod = "other"
    if clean.startswith(("work_wechat_", "work_wecom_", "work_comms_")):
        mod = "comms"
    elif clean.startswith("work_office") or clean in ("work_excel_mcp", "work_word_mcp"):
        mod = "office"
    elif clean in ("work_ledger", "work_exp", "work_backup_file", "work_mem"):
        mod = "memory"
    return {
        "module": mod,
        "mcp_server": f"work_{mod}" if mod in ("comms", "office", "memory") else "work_toolkit",
        "canonical_tool": clean,
        "display_tool": f"{clean}:{sub_tool}" if sub_tool else clean,
        "label": clean,
        "sig": "{...}",
        "tip": "按记录的参数结构直接调用，无需前置探索。",
    }


def build_markdown(
    title: str,
    day: str | None = None,
    tags=None,
    fields=None,
    modules=None,
    tools=None,
    pipeline_payload: Optional[Dict[str, Any]] = None,
) -> str:
    """按固定九段式骨架拼一份完整经验 Markdown（包含精确工具清单与零探索直调配方）。"""
    day = day or date.today().isoformat()
    fields = fields or {}
    parts = [
        _build_front(title, day, tags, fields.get("summary", ""), modules=modules, tools=tools),
        f"# {title}",
        "",
    ]
    for key, heading, hint in SECTIONS:
        parts.append(f"## {heading}")
        parts.append("")
        if key == "summary":
            val = (fields.get("summary") or "").strip()
            parts.append(f"> {val}" if val else _PLACEHOLDER[key])
        else:
            val = (fields.get(key) or "").strip()
            parts.append(val if val else f"<!-- {hint} -->\n{_PLACEHOLDER[key]}")
        parts.append("")
    if pipeline_payload:
        parts.append("<!-- WORK_EXP_PIPELINE_BEGIN")
        parts.append(json.dumps(pipeline_payload, ensure_ascii=False, indent=2))
        parts.append("WORK_EXP_PIPELINE_END -->")
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def _read_codex_rollout_calls(since_cutoff: datetime, max_calls: int = 40) -> List[Dict[str, Any]]:
    """可选从当前机器最新的 Codex 会话 rollout-*.jsonl 中补充读取 MCP 工具调用。"""
    if os.environ.get("WORK_TRACE_DISABLE") == "1":
        return []
    codex_sessions = Path.home() / ".codex" / "sessions"
    if not codex_sessions.is_dir():
        return []
    try:
        candidates = sorted(
            codex_sessions.rglob("rollout-*.jsonl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:2]
    except Exception:
        return []

    out: List[Dict[str, Any]] = []
    for fpath in candidates:
        try:
            if datetime.fromtimestamp(fpath.stat().st_mtime) < since_cutoff:
                continue
            calls_by_id: Dict[str, Dict[str, Any]] = {}
            ordered_ids: List[str] = []
            with open(fpath, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if "function_call" not in line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    payload = obj.get("payload") or {}
                    ptype = payload.get("type")
                    ts_raw = str(obj.get("timestamp") or "")[:19]
                    if ptype == "function_call":
                        name = str(payload.get("name") or "")
                        if not name.startswith("mcp__"):
                            continue
                        if any(skip in name for skip in ("work_status", "work_modules", "work_exp")):
                            continue
                        cid = str(payload.get("call_id") or "")
                        raw_args = payload.get("arguments") or "{}"
                        try:
                            args_dict = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                        except Exception:
                            args_dict = {}
                        info = _tool_info(name)
                        canonical = info["canonical_tool"]
                        sub_tool = ""
                        if canonical in ("work_excel_mcp", "work_word_mcp"):
                            act = str(args_dict.get("action") or "list")
                            sub = str(args_dict.get("tool") or "")
                            sub_tool = sub if (act == "call" and sub) else f"[{act}]"
                        elif args_dict.get("mode"):
                            sub_tool = str(args_dict["mode"])
                        elif args_dict.get("action"):
                            sub_tool = str(args_dict["action"])
                        is_disc = (
                            canonical in ("work_office_engines", "work_office_schema", "work_wechat_probe", "work_wecom_probe")
                            or (canonical in ("work_excel_mcp", "work_word_mcp") and str(args_dict.get("action") or "list") in ("list", "schema"))
                        )
                        entry = {
                            "ts": ts_raw or datetime.now().isoformat(timespec="seconds"),
                            "module": info["module"],
                            "tool": canonical,
                            "sub_tool": sub_tool,
                            "is_discovery": is_disc,
                            "args": args_dict,
                            "ok": True,
                            "ms": 0.0,
                            "inputs": [],
                            "outputs": [],
                            "error": "",
                            "source": "codex_rollout",
                        }
                        if cid:
                            calls_by_id[cid] = entry
                            ordered_ids.append(cid)
                        else:
                            out.append(entry)
                    elif ptype == "function_call_output":
                        cid = str(payload.get("call_id") or "")
                        if cid in calls_by_id:
                            raw_out = str(payload.get("output") or "")
                            if '"ok": false' in raw_out.lower() or '"ok":false' in raw_out.lower():
                                calls_by_id[cid]["ok"] = False
                                try:
                                    parsed_out = json.loads(raw_out)
                                    for ek in ("error", "reason", "detail", "message"):
                                        if parsed_out.get(ek):
                                            calls_by_id[cid]["error"] = str(parsed_out[ek])[:200]
                                            break
                                except Exception:
                                    calls_by_id[cid]["error"] = raw_out[:200]
            for cid in ordered_ids:
                out.append(calls_by_id[cid])
        except Exception:
            continue
    return out[-max_calls:]


def read_trace(
    limit: int = 30,
    since_minutes: int = 240,
    include_codex_rollout: bool = True,
) -> Dict[str, Any]:
    """读取近期工具调用轨迹，自动拆分为「有效执行步骤」「可剔除探索调用」与「失败避坑记录」。"""
    cutoff = datetime.now() - timedelta(minutes=max(1, int(since_minutes or 240)))
    raw_entries: List[Dict[str, Any]] = []

    if TRACE_FILE.exists():
        try:
            lines = TRACE_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
            for line in lines[-max(limit * 4, 120):]:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                    if not isinstance(item, dict):
                        continue
                    ts_str = str(item.get("ts") or "")
                    if ts_str:
                        try:
                            dt = datetime.fromisoformat(ts_str.replace("Z", ""))
                            if dt < cutoff:
                                continue
                        except Exception:
                            pass
                    item["source"] = "tool_trace"
                    raw_entries.append(item)
                except Exception:
                    continue
        except Exception:
            pass

    if include_codex_rollout and not raw_entries:
        raw_entries.extend(_read_codex_rollout_calls(cutoff, max_calls=limit * 2))

    effective: List[Dict[str, Any]] = []
    discovery: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    seen_keys: set = set()

    for item in raw_entries:
        tname = str(item.get("tool") or "")
        sub = str(item.get("sub_tool") or "")
        args = item.get("args") if isinstance(item.get("args"), dict) else {}
        ok = bool(item.get("ok", True))
        is_disc = bool(item.get("is_discovery", False))
        info = _tool_info(tname, sub)
        enriched = {
            **item,
            "module": info["module"],
            "mcp_server": info["mcp_server"],
            "tool": info["canonical_tool"],
            "display_tool": info["display_tool"],
            "label": info["label"],
            "tip": info["tip"],
        }
        if not ok:
            failed.append(enriched)
            continue
        if is_disc:
            discovery.append(enriched)
            continue
        dedupe_key = f"{info['canonical_tool']}|{sub}|{json.dumps(args, ensure_ascii=False, sort_keys=True)}"
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)
        effective.append(enriched)

    if limit > 0:
        effective = effective[-int(limit):]

    modules_used = []
    tools_used = []
    for s in effective:
        if s["module"] not in modules_used:
            modules_used.append(s["module"])
        if s["display_tool"] not in tools_used:
            tools_used.append(s["display_tool"])

    return {
        "ok": True,
        "trace_file": str(TRACE_FILE),
        "raw_count": len(raw_entries),
        "effective_count": len(effective),
        "discovery_eliminated_count": len(discovery),
        "failed_count": len(failed),
        "modules": modules_used,
        "tools": tools_used,
        "effective_steps": effective,
        "discovery_calls": discovery[-10:],
        "failed_calls": failed[-10:],
    }


def clear_trace() -> Dict[str, Any]:
    """清空运行态工具调用轨迹（不影响已保存的经验文档）。"""
    if TRACE_FILE.exists():
        try:
            TRACE_FILE.unlink()
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
    return {"ok": True, "trace_file": str(TRACE_FILE), "cleared": True}
_PARAM_VAR_CANDIDATES = (
    ("file", "input_file", "输入文件路径或工作区文件名"),
    ("path", "input_path", "目标文件路径"),
    ("filename", "input_file", "目标文档路径"),
    ("file_path", "input_file", "目标表格路径"),
    ("file_a", "file_a", "基准文件（旧版）路径"),
    ("file_b", "file_b", "对比文件（新版）路径"),
    ("source_dir", "source_dir", "源材料目录路径"),
    ("record_dir", "record_dir", "台账记账目录路径"),
    ("template", "template_file", "模板文件路径"),
    ("template_path", "template_path", "汇总母表模板路径"),
    ("roster", "roster_file", "花名册/科室底册路径"),
    ("chat", "chat_name", "目标微信/企微群名或联系人"),
    ("chat_group", "chat_group", "目标微信群名"),
    ("since", "since_time", "起始时间过滤"),
    ("output", "output_path", "输出文件路径"),
    ("output_path", "output_path", "输出文件路径"),
)


def _parameterize_steps(steps: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, str]]]:
    """从有效工具调用步骤中自动提取可参数化变量（{{var}}），同时保留真实默认值。"""
    vars_schema: Dict[str, Dict[str, str]] = {}
    val_to_var: Dict[str, str] = {}

    for step in steps:
        args = step.get("args") if isinstance(step.get("args"), dict) else {}
        for arg_key, var_base, desc in _PARAM_VAR_CANDIDATES:
            val = args.get(arg_key)
            if isinstance(val, str) and val.strip() and len(val.strip()) <= 180:
                sval = val.strip()
                if sval not in val_to_var:
                    vname = var_base
                    idx = 2
                    while vname in vars_schema and vars_schema[vname]["default"] != sval:
                        vname = f"{var_base}_{idx}"
                        idx += 1
                    val_to_var[sval] = vname
                    vars_schema[vname] = {"default": sval, "description": desc, "arg_key": arg_key}
        if step.get("tool") in ("work_excel_mcp", "work_word_mcp") and args.get("args"):
            raw_inner = args.get("args")
            inner = raw_inner if isinstance(raw_inner, dict) else None
            if isinstance(raw_inner, str):
                try:
                    inner = json.loads(raw_inner)
                except Exception:
                    inner = None
            if isinstance(inner, dict):
                for arg_key, var_base, desc in _PARAM_VAR_CANDIDATES:
                    val = inner.get(arg_key)
                    if isinstance(val, str) and val.strip() and len(val.strip()) <= 180:
                        sval = val.strip()
                        if sval not in val_to_var:
                            vname = var_base
                            idx = 2
                            while vname in vars_schema and vars_schema[vname]["default"] != sval:
                                vname = f"{var_base}_{idx}"
                                idx += 1
                            val_to_var[sval] = vname
                            vars_schema[vname] = {"default": sval, "description": desc, "arg_key": arg_key}

    pipeline_steps: List[Dict[str, Any]] = []
    for idx, step in enumerate(steps, start=1):
        args = dict(step.get("args") or {})
        tmpl_args: Dict[str, Any] = {}
        for k, v in args.items():
            if isinstance(v, str) and v.strip() in val_to_var:
                tmpl_args[k] = "{{" + val_to_var[v.strip()] + "}}"
            elif k == "args" and step.get("tool") in ("work_excel_mcp", "work_word_mcp"):
                inner = v if isinstance(v, dict) else None
                if isinstance(v, str):
                    try:
                        inner = json.loads(v)
                    except Exception:
                        inner = None
                if isinstance(inner, dict):
                    inner_tmpl = {
                        ik: ("{{" + val_to_var[iv.strip()] + "}}" if isinstance(iv, str) and iv.strip() in val_to_var else iv)
                        for ik, iv in inner.items()
                    }
                    tmpl_args[k] = inner_tmpl
                    args[k] = inner
                else:
                    tmpl_args[k] = v
            else:
                tmpl_args[k] = v

        pipeline_steps.append({
            "step": idx,
            "module": step["module"],
            "mcp_server": step["mcp_server"],
            "tool": step["tool"],
            "sub_tool": step.get("sub_tool") or "",
            "display_tool": step["display_tool"],
            "label": step["label"],
            "args": args,
            "template_args": tmpl_args,
            "tip": step["tip"],
            "note": step.get("note") or step["label"],
        })
    return pipeline_steps, vars_schema


def distill_doc(
    title: str = "",
    summary: str = "",
    goal: str = "",
    decisions: str = "",
    pitfalls: str = "",
    tags: str | List[str] = "",
    day: str = "",
    overwrite: bool = True,
    since_minutes: int = 240,
    trace_limit: int = 20,
    steps_override: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """一键将本次工作自动整理为「绑定模块与精确工具链」的经验文档。"""
    trace_data = read_trace(limit=trace_limit, since_minutes=since_minutes, include_codex_rollout=True)
    raw_steps: List[Dict[str, Any]] = []

    if steps_override:
        for s in steps_override:
            if not isinstance(s, dict) or not s.get("tool"):
                continue
            tname = str(s["tool"]).strip()
            args = s.get("args") if isinstance(s.get("args"), dict) else {}
            sub = str(s.get("sub_tool") or "")
            if not sub:
                if tname in ("work_excel_mcp", "work_word_mcp"):
                    sub = str(args.get("tool") or "")
                elif args.get("mode"):
                    sub = str(args["mode"])
                elif args.get("action"):
                    sub = str(args["action"])
            info = _tool_info(tname, sub)
            raw_steps.append({
                "module": info["module"],
                "mcp_server": info["mcp_server"],
                "tool": info["canonical_tool"],
                "sub_tool": sub,
                "display_tool": info["display_tool"],
                "label": info["label"],
                "args": args,
                "tip": info["tip"],
                "note": str(s.get("note") or info["label"]),
                "inputs": s.get("inputs") or [],
                "outputs": s.get("outputs") or [],
            })
    else:
        raw_steps = trace_data.get("effective_steps") or []

    if not raw_steps:
        return {
            "ok": False,
            "error": "近期轨迹中没有检测到有效的工具调用步骤，无法自动提炼工具级经验",
            "hint": "请先使用 comms / office / memory 工具执行完任务后再调用 action='distill'，或通过 steps_json 显式传入工具步骤数组。",
            "trace_summary": {
                "raw_count": trace_data.get("raw_count", 0),
                "discovery_eliminated_count": trace_data.get("discovery_eliminated_count", 0),
                "failed_count": trace_data.get("failed_count", 0),
            },
        }

    pipeline_steps, vars_schema = _parameterize_steps(raw_steps)
    modules_used: List[str] = []
    tools_used: List[str] = []
    mcp_servers_used: List[str] = []
    all_outputs: List[str] = []

    for ps in pipeline_steps:
        if ps["module"] not in modules_used:
            modules_used.append(ps["module"])
        if ps["display_tool"] not in tools_used:
            tools_used.append(ps["display_tool"])
        if ps["mcp_server"] not in mcp_servers_used:
            mcp_servers_used.append(ps["mcp_server"])
    for rs in raw_steps:
        for out_p in (rs.get("outputs") or []):
            if out_p and out_p not in all_outputs:
                all_outputs.append(out_p)

    if not (title or "").strip():
        labels = [ps["label"].split("（")[0] for ps in pipeline_steps[:3]]
        title = " → ".join(dict.fromkeys(labels)) + " 标准工作流"
    title = title.strip()

    if isinstance(tags, str):
        tag_list = [t.strip() for t in re.split(r"[,，\s]+", tags) if t.strip()]
    else:
        tag_list = [str(t).strip() for t in (tags or []) if str(t).strip()]
    for m in modules_used:
        if m not in tag_list:
            tag_list.append(m)
    for t in tools_used[:4]:
        short_t = t.split(":")[0]
        if short_t not in tag_list:
            tag_list.append(short_t)

    disc_cnt = trace_data.get("discovery_eliminated_count", 0)
    if not (summary or "").strip():
        chain_str = " → ".join(f"`{t}`" for t in tools_used)
        elim_note = f"（已剔除 {disc_cnt} 次前置探索调用）" if disc_cnt > 0 else ""
        summary = (
            f"本流程横跨 {'/'.join(modules_used)} 模块，固定按 {chain_str} 顺序直调{elim_note}；"
            f"下次直接把本文档或第八节指令块发给 AI，即可跳过工具探索一步到位。"
        )

    if not (goal or "").strip():
        goal = (
            f"通过组合调用 `{', '.join(mcp_servers_used)}` MCP 服务中的 {len(pipeline_steps)} 个确定性工具步骤，"
            f"完成「{title}」全流程处理，确保参数零盲猜、一次执行成功并产出合规结果。"
        )

    mat_lines = [
        "| 参数变量名 | 默认值 / 本次实测取值 | 对应工具参数 | 说明 |",
        "| --- | --- | --- | --- |",
    ]
    if vars_schema:
        for vname, vmeta in vars_schema.items():
            mat_lines.append(
                f"| `{{{{{vname}}}}}` | `{vmeta['default']}` | `{vmeta['arg_key']}` | {vmeta['description']} |"
            )
    else:
        mat_lines.append("| （无动态路径变量） | — | — | 直接按默认参数调用即可 |")
    materials_md = "\n".join(mat_lines)

    tool_lines = [
        "| 步骤 | 所属模块 | MCP 服务 | 精确工具名 (含子工具/模式) | 核心参数结构 | 零探索直调要点 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for ps in pipeline_steps:
        compact_args = json.dumps(ps["template_args"], ensure_ascii=False)
        if len(compact_args) > 110:
            compact_args = compact_args[:107] + "..."
        tool_lines.append(
            f"| {ps['step']} | `{ps['module']}` | `{ps['mcp_server']}` | **`{ps['display_tool']}`** | `{compact_args}` | {ps['tip']} |"
        )
    tools_table_md = "\n".join(tool_lines)

    step_lines = []
    for ps in pipeline_steps:
        pretty_args = json.dumps(ps["args"], ensure_ascii=False)
        step_lines.append(
            f"{ps['step']}. **[{ps['module']} · {ps['note']}]** 直接调用 `{ps['mcp_server']}` 的 **`{ps['tool']}`**"
            + (f"（子工具/模式：`{ps['sub_tool']}`）" if ps["sub_tool"] else "")
            + f"：\n   - 实测参数：`{pretty_args}`\n   - 直调要点：{ps['tip']}"
        )
    steps_md = "\n".join(step_lines)

    if not (decisions or "").strip():
        dec_items = [
            f"- **模块解耦按需加载**：本流程仅需挂载 `{', '.join(mcp_servers_used)}`，无需走 `/work` 全量路由或预检。",
            "- **拒绝前置盲探**：已知确切工具名与参数结构，禁止先调 `action='list'`、`work_office_schema` 或 `work_wechat_probe`。",
        ]
        for ps in pipeline_steps:
            if ps["tool"] == "work_office_consolidate" and ps["args"].get("auto_schema"):
                dec_items.append("- **跨表汇总口径**：启用 `auto_schema=true` 自动识别并对齐各分表字段，不依赖固定母表模板。")
            elif ps["tool"] == "work_office_format_gbt9704":
                preset = ps["args"].get("preset", "gbt9704")
                dec_items.append(f"- **排版预设选择**：使用 `preset='{preset}'` 预设排版。")
        decisions_md = "\n".join(dec_items)
    else:
        decisions_md = decisions.strip()

    pit_items: List[str] = []
    if (pitfalls or "").strip():
        pit_items.append(pitfalls.strip())
    for fc in (trace_data.get("failed_calls") or []):
        err_txt = fc.get("error") or "返回 ok:false"
        pit_items.append(
            f"- **实测报错拦截（`{fc['display_tool']}`）**：\n"
            f"  - **现象**：调用 `{fc['tool']}` 传参 `{json.dumps(fc.get('args', {}), ensure_ascii=False)}` 时报错 `{err_txt}`\n"
            f"  - **根因**：参数名不匹配、缺少必填项或未传 `allow_write=true`\n"
            f"  - **改法**：严格按本文档第三节的参数模板直调（{fc['tip']}）。"
        )
    if disc_cnt > 0:
        disc_names = sorted(set(d["display_tool"] for d in (trace_data.get("discovery_calls") or [])))
        pit_items.append(
            f"- **消除无效探索开销**：\n"
            f"  - **现象**：首次探索时多调了 {disc_cnt} 次探测/列表工具（`{', '.join(disc_names)}`）\n"
            f"  - **根因**：未事先锁定子工具名与参数结构\n"
            f"  - **改法**：复用本经验时直接跳过上述探测步骤，直调最终工具链。"
        )
    if not pit_items:
        for ps in pipeline_steps[:3]:
            pit_items.append(
                f"- **`{ps['display_tool']}` 调用注意**：{ps['tip']}"
            )
    pitfalls_md = "\n".join(pit_items)

    out_lines = [
        "| 产物 / 阶段结果 | 路径 / 落点 | 说明 |",
        "| --- | --- | --- |",
    ]
    if all_outputs:
        for op in all_outputs:
            out_lines.append(f"| 流程产出文件 | `{op}` | 由工具链自动生成并校验 |")
    else:
        out_lines.append("| 终端结构化返回 | 标准 JSON 响应 | 只读分析/检视结果，不落盘污染工作区 |")
    outputs_md = "\n".join(out_lines)

    day_str = day or date.today().isoformat()
    doc_stem = f"{day_str.replace('-', '')}_{_safe_name(title)}"
    agent_prompt_lines = [
        f"请严格按以下已验证的【{title}】工具配方执行（零探索模式）：",
        f"- 所需模块：`{', '.join(modules_used)}`（对应 MCP 服务：`{', '.join(mcp_servers_used)}`）",
        "- 硬性约束：禁止先调 `action=\"list\"`、`work_office_schema`、`work_status` 或 `probe` 探索，直接按顺序调用以下工具：",
    ]
    for ps in pipeline_steps:
        agent_prompt_lines.append(
            f"  Step {ps['step']}: 调用 `{ps['mcp_server']}` 的 `{ps['tool']}`，参数传 `{json.dumps(ps['template_args'], ensure_ascii=False)}`"
        )
    if vars_schema:
        defaults_inline = ", ".join(f'{k}="{v["default"]}"' for k, v in vars_schema.items())
        agent_prompt_lines.append(f"- 变量默认值（若我未另外指定则用默认值）：{defaults_inline}")
    agent_prompt_txt = "\n".join(agent_prompt_lines)

    batch_cli_steps = [{"tool": ps["tool"], "args": ps["args"]} for ps in pipeline_steps if ps["tool"].startswith("work_")]
    batch_json_one_line = json.dumps(batch_cli_steps, ensure_ascii=False)

    snippets_md = "\n".join([
        "### 1. 直接发给 AI 的零探索指令（复制本框发给 Codex / WorkBuddy）",
        "",
        "```text",
        agent_prompt_txt,
        "```",
        "",
        "### 2. 一键参数化重放（通过 `work_memory` MCP）",
        "",
        "```json",
        json.dumps({
            "tool": "work_exp",
            "args": {
                "action": "replay",
                "doc_id": doc_stem,
                "dry_run": False,
                "vars": {k: v["default"] for k, v in vars_schema.items()},
            },
        }, ensure_ascii=False, indent=2),
        "```",
        "",
        "### 3. 本地 CLI 免 AI 一键批处理执行",
        "",
        "```powershell",
        f'python -X utf8 "{ROOT / "tools" / "run_tool.py"}" --batch \'{batch_json_one_line}\'',
        "```",
    ])

    pipeline_payload = {
        "doc_id": doc_stem,
        "title": title,
        "modules": modules_used,
        "mcp_servers": mcp_servers_used,
        "tools": tools_used,
        "vars": vars_schema,
        "steps": pipeline_steps,
    }

    fields = {
        "summary": summary,
        "goal": goal,
        "materials": materials_md,
        "tools_table": tools_table_md,
        "steps": steps_md,
        "decisions": decisions_md,
        "pitfalls": pitfalls_md,
        "outputs": outputs_md,
        "snippets": snippets_md,
    }

    save_res = save_doc(
        title=title,
        day=day_str,
        tags=tag_list,
        overwrite=overwrite,
        fields=fields,
        modules=modules_used,
        tools=tools_used,
        pipeline_payload=pipeline_payload,
    )
    if save_res.get("ok"):
        save_res["distilled"] = True
        save_res["modules"] = modules_used
        save_res["tools"] = tools_used
        save_res["steps_count"] = len(pipeline_steps)
        save_res["discovery_eliminated"] = disc_cnt
        save_res["vars"] = vars_schema
        save_res["agent_prompt"] = agent_prompt_txt
    return save_res
def list_docs() -> dict:
    """按日期倒序列出全部经验文档（附带绑定的 modules、tools 与是否含可执行配方）。"""
    DOC_DIR.mkdir(parents=True, exist_ok=True)
    items = []
    for p in sorted(DOC_DIR.glob("*.md"), reverse=True):
        if p.name.lower() in ("index.md", "readme.md"):
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        meta = _parse_front(text)
        m = re.match(r"^(\d{4})(\d{2})(\d{2})[_\-](.+)$", p.stem)
        day = meta.get("date") or (f"{m[1]}-{m[2]}-{m[3]}" if m else "")
        title = meta.get("title") or (m[4] if m else p.stem)
        pipe = _extract_pipeline(text)
        modules = meta.get("modules") or (pipe.get("modules") if pipe else []) or []
        tools = meta.get("tools") or (pipe.get("tools") if pipe else []) or []
        items.append({
            "id": p.stem,
            "title": title,
            "date": day,
            "tags": meta.get("tags") or [],
            "modules": modules,
            "tools": tools,
            "has_pipeline": bool(pipe),
            "steps_count": len(pipe.get("steps") or []) if pipe else 0,
            "summary": meta.get("summary") or "",
            "chars": len(text),
            "path": str(p),
        })
    items.sort(key=lambda x: (x["date"], x["id"]), reverse=True)
    return {"ok": True, "dir": str(DOC_DIR), "count": len(items), "items": items}


def get_doc(doc_id: str) -> dict:
    """按 id 或标题子串取回整篇原文，并附带解析出的工具配方（pipeline + agent_prompt）。"""
    DOC_DIR.mkdir(parents=True, exist_ok=True)
    key = (doc_id or "").strip().removesuffix(".md")
    if not key:
        return {"ok": False, "error": "需要 doc_id（用 list 或 search 查看全部 id）"}
    direct = DOC_DIR / f"{key}.md"
    if not direct.exists():
        hits = [p for p in DOC_DIR.glob("*.md")
                if p.name.lower() not in ("index.md", "readme.md") and key.lower() in p.stem.lower()]
        if len(hits) == 1:
            direct = hits[0]
        elif len(hits) > 1:
            return {"ok": False, "error": f"匹配到多份：{[p.stem for p in hits]}，请给完整 id"}
        else:
            return {"ok": False, "error": f"找不到经验文档：{doc_id}"}
    text = direct.read_text(encoding="utf-8", errors="replace")
    meta = _parse_front(text)
    pipe = _extract_pipeline(text)
    agent_prompt = _extract_agent_prompt(text)
    return {
        "ok": True,
        "id": direct.stem,
        "path": str(direct),
        "meta": meta,
        "modules": meta.get("modules") or (pipe.get("modules") if pipe else []),
        "tools": meta.get("tools") or (pipe.get("tools") if pipe else []),
        "has_pipeline": bool(pipe),
        "pipeline": pipe,
        "agent_prompt": agent_prompt,
        "chars": len(text),
        "content": text,
    }


def template_doc(title: str = "新任务名称") -> dict:
    """返回一份填好标题和日期的空白九段式模板，不写盘。"""
    md = build_markdown(title)
    return {
        "ok": True,
        "title": title,
        "chars": len(md),
        "content": md,
        "note": "空白模板，没写进目录。推荐使用 action='distill' 直接从真实工具轨迹生成经验。",
    }


def save_doc(
    title: str,
    content: str = "",
    day: str = "",
    tags=None,
    overwrite: bool = False,
    fields: dict | None = None,
    modules=None,
    tools=None,
    pipeline_payload: Optional[Dict[str, Any]] = None,
) -> dict:
    """显式写入一份经验文档，并同步刷新目录与本地 FTS5 索引。"""
    title = (title or "").strip()
    if not title:
        return {"ok": False, "error": "需要 title（任务名）"}
    DOC_DIR.mkdir(parents=True, exist_ok=True)
    if isinstance(tags, str):
        tags = [t.strip() for t in re.split(r"[,，\s]+", tags) if t.strip()]
    if isinstance(modules, str):
        modules = [t.strip() for t in re.split(r"[,，\s]+", modules) if t.strip()]
    if isinstance(tools, str):
        tools = [t.strip() for t in re.split(r"[,，\s]+", tools) if t.strip()]
    day = day or date.today().isoformat()

    if content.strip():
        md = content.strip()
        if not md.startswith("---"):
            md = _build_front(title, day, tags, modules=modules, tools=tools) + md
        md = md.rstrip() + "\n"
    else:
        md = build_markdown(
            title,
            day,
            tags,
            fields,
            modules=modules,
            tools=tools,
            pipeline_payload=pipeline_payload,
        )

    stem = f"{day.replace('-', '')}_{_safe_name(title)}"
    dst = DOC_DIR / f"{stem}.md"
    if dst.exists() and not overwrite:
        return {
            "ok": False,
            "error": "同名日期已有这份经验，没覆盖",
            "existing_id": dst.stem,
            "hint": "改一下 title，或显式传 overwrite=true 覆盖",
        }
    dst.write_text(md, encoding="utf-8")
    refresh_index()
    _sync_fts_index()
    return {
        "ok": True,
        "id": dst.stem,
        "path": str(dst),
        "chars": len(md),
        "note": "已存成独立 md（含模块与工具绑定元数据）。可直接复制给 AI 零探索执行，或用 action='replay' 重放。",
    }


def _tokenize_zh(text: str) -> str:
    """为 SQLite FTS5 提供中英文混合分词（中文双字切词 + 英文数字词元）。"""
    s = (text or "").lower()
    tokens: List[str] = []
    for chunk in re.findall(r"[a-z0-9_:\-]+|[\u4e00-\u9fff]+", s):
        if re.match(r"^[\u4e00-\u9fff]+$", chunk):
            tokens.append(chunk)
            if len(chunk) >= 2:
                tokens.extend(chunk[i : i + 2] for i in range(len(chunk) - 1))
        else:
            tokens.append(chunk)
    return " ".join(tokens)


def _sync_fts_index() -> None:
    """将 workspace/11_任务经验/*.md 同步到可随时重建的 SQLite FTS5 索引（.exp_index.db）。"""
    DOC_DIR.mkdir(parents=True, exist_ok=True)
    docs = list_docs().get("items") or []
    fp = hashlib.sha256(
        json.dumps([(d["id"], d["chars"]) for d in docs], ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]

    try:
        conn = sqlite3.connect(str(INDEX_DB))
        with conn:
            conn.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
            row = conn.execute("SELECT v FROM meta WHERE k='fp'").fetchone()
            if row and row[0] == fp:
                conn.close()
                return
            conn.execute("DROP TABLE IF EXISTS exp_fts")
            conn.execute(
                "CREATE VIRTUAL TABLE exp_fts USING fts5(doc_id UNINDEXED, title, summary, tags, modules, tools, body, tok)"
            )
            for d in docs:
                body = Path(d["path"]).read_text(encoding="utf-8", errors="replace")
                tok = _tokenize_zh(
                    " ".join([
                        d["title"],
                        d["summary"],
                        " ".join(d.get("tags") or []),
                        " ".join(d.get("modules") or []),
                        " ".join(d.get("tools") or []),
                        body,
                    ])
                )
                conn.execute(
                    "INSERT INTO exp_fts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        d["id"],
                        d["title"],
                        d["summary"],
                        ", ".join(d.get("tags") or []),
                        ", ".join(d.get("modules") or []),
                        ", ".join(d.get("tools") or []),
                        body,
                        tok,
                    ),
                )
            conn.execute("INSERT OR REPLACE INTO meta VALUES ('fp', ?)", (fp,))
        conn.close()
    except Exception:
        pass


def search_docs(
    query: str = "",
    module: str = "",
    tool: str = "",
    limit: int = 10,
) -> Dict[str, Any]:
    """按关键词、所属模块（comms/office/memory）或具体工具名检索经验文档与直调配方。"""
    _sync_fts_index()
    all_items = {d["id"]: d for d in (list_docs().get("items") or [])}
    q = (query or "").strip()
    mod_filter = (module or "").strip().lower()
    tool_filter = (tool or "").strip().lower()

    matched_ids: List[str] = []
    if q and INDEX_DB.exists():
        try:
            conn = sqlite3.connect(str(INDEX_DB))
            q_toks = [t for t in _tokenize_zh(q).split() if len(t) >= 2][:8]
            if q_toks:
                fts_q = " OR ".join(f'"{t}"' for t in q_toks)
                rows = conn.execute(
                    "SELECT doc_id FROM exp_fts WHERE exp_fts MATCH ? ORDER BY rank LIMIT 30",
                    (fts_q,),
                ).fetchall()
                matched_ids = [r[0] for r in rows if r[0] in all_items]
            conn.close()
        except Exception:
            matched_ids = []

    if not matched_ids:
        for did, d in all_items.items():
            hay = " ".join([
                did,
                d["title"],
                d["summary"],
                " ".join(d.get("tags") or []),
                " ".join(d.get("modules") or []),
                " ".join(d.get("tools") or []),
            ]).lower()
            if not q or q.lower() in hay or any(tok in hay for tok in q.lower().split()):
                matched_ids.append(did)

    results: List[Dict[str, Any]] = []
    for did in matched_ids:
        d = all_items.get(did)
        if not d:
            continue
        if mod_filter and mod_filter not in [m.lower() for m in (d.get("modules") or [])]:
            continue
        if tool_filter and not any(tool_filter in t.lower() for t in (d.get("tools") or [])):
            continue
        full = get_doc(did)
        results.append({
            **d,
            "agent_prompt": full.get("agent_prompt") or "",
            "vars": (full.get("pipeline") or {}).get("vars") or {},
        })
        if limit > 0 and len(results) >= int(limit):
            break

    return {
        "ok": True,
        "query": q,
        "module_filter": mod_filter,
        "tool_filter": tool_filter,
        "count": len(results),
        "items": results,
    }


def _substitute_vars(obj: Any, resolved_vars: Dict[str, str]) -> Any:
    if isinstance(obj, str):
        out = obj
        for k, v in resolved_vars.items():
            out = out.replace("{{" + k + "}}", str(v))
        return out
    if isinstance(obj, dict):
        return {k: _substitute_vars(v, resolved_vars) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_substitute_vars(v, resolved_vars) for v in obj]
    return obj


def replay_doc(
    doc_id: str,
    vars_dict: Optional[Dict[str, Any]] = None,
    dry_run: bool = True,
    executor: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
) -> Dict[str, Any]:
    """按经验文档内嵌的流水线配方进行参数化重放（dry_run=True 预览解析后的直调序列，False 实际执行）。"""
    doc = get_doc(doc_id)
    if not doc.get("ok"):
        return doc
    pipe = doc.get("pipeline")
    if not pipe or not pipe.get("steps"):
        return {
            "ok": False,
            "id": doc["id"],
            "error": "该经验文档未包含结构化工具流水线（可能是早期纯文本经验）",
            "hint": "可直接阅读 content 原文，或用 action='distill' 生成带工具配方的经验文档。",
        }

    schema_vars = pipe.get("vars") or {}
    resolved_vars: Dict[str, str] = {k: str(v.get("default") or "") for k, v in schema_vars.items()}
    if isinstance(vars_dict, dict):
        for k, v in vars_dict.items():
            if v is not None and str(v) != "":
                resolved_vars[str(k)] = str(v)

    resolved_steps: List[Dict[str, Any]] = []
    for s in pipe["steps"]:
        tmpl = s.get("template_args") if s.get("template_args") is not None else s.get("args", {})
        final_args = _substitute_vars(tmpl, resolved_vars)
        resolved_steps.append({
            "step": s.get("step"),
            "module": s.get("module"),
            "mcp_server": s.get("mcp_server"),
            "tool": s.get("tool"),
            "sub_tool": s.get("sub_tool") or "",
            "args": final_args,
            "note": s.get("note") or "",
        })

    if dry_run or executor is None:
        return {
            "ok": True,
            "dry_run": True,
            "doc_id": doc["id"],
            "title": doc["meta"].get("title") or doc["id"],
            "modules": pipe.get("modules") or [],
            "tools": pipe.get("tools") or [],
            "resolved_vars": resolved_vars,
            "steps_count": len(resolved_steps),
            "steps": resolved_steps,
            "agent_prompt": _substitute_vars(doc.get("agent_prompt") or "", resolved_vars),
            "note": "dry_run=true 仅展示代入变量后的精确工具调用序列；传 dry_run=false 可立即顺序执行。",
        }

    exec_results: List[Dict[str, Any]] = []
    all_ok = True
    for rs in resolved_steps:
        tname = str(rs["tool"])
        targs = dict(rs["args"] or {})
        try:
            raw_res = executor(tname, targs)
            parsed = json.loads(raw_res) if isinstance(raw_res, str) else raw_res
            step_ok = bool(parsed.get("ok", True)) if isinstance(parsed, dict) else True
        except Exception as exc:
            parsed = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            step_ok = False
        if not step_ok:
            all_ok = False
        exec_results.append({
            **rs,
            "ok": step_ok,
            "result": parsed,
        })

    return {
        "ok": all_ok,
        "dry_run": False,
        "doc_id": doc["id"],
        "title": doc["meta"].get("title") or doc["id"],
        "resolved_vars": resolved_vars,
        "steps_count": len(exec_results),
        "results": exec_results,
    }


def export_to_skill(
    doc_id: str,
    skill_name: str = "",
    install_codex: bool = False,
) -> Dict[str, Any]:
    """将一份高频经验文档一键导出为独立的零探索 Skill（SKILL.md）。"""
    doc = get_doc(doc_id)
    if not doc.get("ok"):
        return doc
    meta = doc.get("meta") or {}
    pipe = doc.get("pipeline") or {}
    title = meta.get("title") or doc["id"]
    summary = meta.get("summary") or f"按已验证的工具链执行「{title}」标准流程"
    modules = doc.get("modules") or []
    tools = doc.get("tools") or []

    slug = re.sub(r"[^a-z0-9\-]+", "-", (skill_name or "").strip().lower()).strip("-")
    if not slug:
        slug = "work-sop-" + hashlib.md5(doc["id"].encode("utf-8")).hexdigest()[:8]

    steps = pipe.get("steps") or []
    vars_schema = pipe.get("vars") or {}

    lines = [
        "---",
        f"name: {slug}",
        f"description: 【固化流程经验 Skill】{title}。涉及模块：{', '.join(modules) or '通用'}；核心工具：{', '.join(tools) or '见正文'}。{summary[:120]}",
        "---",
        "",
        f"# {title}（零探索直调 Skill）",
        "",
        f"> 来源经验文档：`{doc['id']}.md` · 绑定模块：`{', '.join(modules)}` · 绑定工具：`{', '.join(tools)}`",
        "",
        "## 1. 零探索调用纪律",
        "",
        "- **禁止盲探**：本流程工具名、子工具名与参数键名均已固化，**禁止**先调 `action=\"list\"`、`work_office_schema`、`work_status` 或 `probe`。",
        "- **按序直调**：严格按下方步骤顺序调用对应 MCP 工具。",
        "",
        "## 2. 精确工具调用链",
        "",
        "| 步骤 | 模块 | MCP 服务 | 工具名 (含子工具/模式) | 参数模板 | 要点 |",
        "| :--- | :--- | :--- | :--- | :--- | :--- |",
    ]
    for s in steps:
        lines.append(
            f"| {s.get('step')} | `{s.get('module')}` | `{s.get('mcp_server')}` | **`{s.get('display_tool')}`** | `{json.dumps(s.get('template_args', s.get('args', {})), ensure_ascii=False)}` | {s.get('tip', '')} |"
        )
    if vars_schema:
        lines += [
            "",
            "## 3. 可替换参数变量",
            "",
            "| 变量 | 默认值 | 说明 |",
            "| :--- | :--- | :--- |",
        ]
        for k, v in vars_schema.items():
            lines.append(f"| `{{{{{k}}}}}` | `{v.get('default', '')}` | {v.get('description', '')} |")

    lines += [
        "",
        "## 4. 一键重放命令",
        "",
        f"- MCP 重放：`work_exp(action=\"replay\", doc_id=\"{doc['id']}\", dry_run=False, vars={{...}})`",
        "",
    ]
    skill_md = "\n".join(lines)

    out_dir = SKILLS_EXPORT_DIR / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "SKILL.md"
    out_file.write_text(skill_md, encoding="utf-8")

    installed_paths = [str(out_file)]
    if install_codex:
        for base in (ROOT / "skills" / slug, Path.home() / ".codex" / "skills" / slug):
            try:
                base.mkdir(parents=True, exist_ok=True)
                dst = base / "SKILL.md"
                dst.write_text(skill_md, encoding="utf-8")
                installed_paths.append(str(dst))
            except Exception:
                pass

    return {
        "ok": True,
        "doc_id": doc["id"],
        "skill_name": slug,
        "path": str(out_file),
        "installed_paths": installed_paths,
        "chars": len(skill_md),
    }


def refresh_index() -> dict:
    """生成目录页 index.md：包含日期、任务、绑定模块、精确工具链、一句话经验与文档链接。"""
    docs = list_docs()["items"]
    lines = [
        "# 任务经验与可执行工具配方目录",
        "",
        "> 一份 md = 一整段已验证的任务流程 + 精确到模块/子工具的零探索直调配方。",
        "> 完成工作后说一句「将本次工作整理为经验」（`work_exp(action=\"distill\")`）即可根据真实工具调用轨迹自动生成。",
        "> 下次遇到同类任务，直接把对应 md 发给 AI，或调用 `work_exp(action=\"replay\", doc_id=\"...\")` 一键重放。",
        "",
    ]
    if not docs:
        lines += ["（还没有经验文档。完成一次任务后说「将本次工作整理为经验」即可自动生成第一份。）", ""]
    else:
        lines += [
            f"共 {len(docs)} 份：",
            "",
            "| 日期 | 任务 | 涉及模块 | 核心工具链 | 一句话经验 | 文档 |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for d in docs:
            mods = " / ".join(d.get("modules") or d.get("tags")[:2] or ["—"])
            tls = ", ".join(f"`{t}`" for t in (d.get("tools") or [])[:4]) or "—"
            lines.append(
                f"| {d['date']} | {d['title']} | {mods} | {tls} | "
                f"{d['summary'] or '—'} | [{d['id']}]({d['id']}.md) |"
            )
        lines.append("")
    DOC_DIR.mkdir(parents=True, exist_ok=True)
    (DOC_DIR / "index.md").write_text("\n".join(lines), encoding="utf-8")
    return {"ok": True, "count": len(docs), "path": str(DOC_DIR / "index.md")}


def export_all() -> dict:
    """把全部经验合成一份 md，方便整体带走/备份。"""
    docs = list_docs()["items"]
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    parts = [
        "# 任务经验与工具配方合集",
        "",
        f"> 导出于 {datetime.now().strftime('%Y-%m-%d %H:%M')} · 共 {len(docs)} 份",
        "",
        "> 每份文档自带精确工具链与零探索直调配方，可单独整篇复制发给 AI 或一键重放。",
        "",
    ]
    for d in docs:
        text = Path(d["path"]).read_text(encoding="utf-8", errors="replace")
        if text.startswith("---"):
            cut = text.find("\n---", 3)
            if cut >= 0:
                text = text[cut + 4 :].lstrip("\n")
        parts += ["---", "", text.rstrip(), ""]
    md = "\n".join(parts)
    dst = STATE_DIR / "任务经验合集.md"
    dst.write_text(md, encoding="utf-8")
    return {"ok": True, "output": str(dst), "docs": len(docs), "chars": len(md)}


def stats() -> dict:
    docs = list_docs()["items"]
    with_pipe = sum(1 for d in docs if d.get("has_pipeline"))
    return {
        "ok": True,
        "dir": str(DOC_DIR),
        "docs": len(docs),
        "executable_playbooks": with_pipe,
        "total_chars": sum(d["chars"] for d in docs),
        "latest": docs[0]["date"] if docs else None,
        "index": str(DOC_DIR / "index.md"),
        "trace_file": str(TRACE_FILE),
    }


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "list":
        out = list_docs()
    elif cmd == "get":
        out = get_doc(sys.argv[2] if len(sys.argv) > 2 else "")
    elif cmd == "search":
        out = search_docs(sys.argv[2] if len(sys.argv) > 2 else "")
    elif cmd == "trace":
        out = read_trace()
    elif cmd == "distill":
        out = distill_doc(title=sys.argv[2] if len(sys.argv) > 2 else "")
    elif cmd == "template":
        out = template_doc(sys.argv[2] if len(sys.argv) > 2 else "新任务名称")
    elif cmd == "index":
        out = refresh_index()
    elif cmd == "export":
        out = export_all()
    elif cmd == "stats":
        out = stats()
    else:
        out = {
            "ok": False,
            "error": f"未知命令 {cmd}",
            "supported": ["list", "get", "search", "trace", "distill", "template", "index", "export", "stats"],
        }
    if cmd == "get" and out.get("ok"):
        print(out["content"])
    else:
        print(json.dumps(out, ensure_ascii=False, indent=2))
