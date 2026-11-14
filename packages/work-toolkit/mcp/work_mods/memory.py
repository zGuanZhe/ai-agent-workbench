#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""work_mods/memory.py — 记忆模块（流程经验与工具配方库 + Engram 通用记忆引擎 + 材料台账 + 文件备份）。

本模块采用「三层协同、按需调用、零上下文污染」架构：
  1. 流程经验与零探索工具配方库（work_exp，底层 experience/library.py）：
     - 解决「每次都是那几套确定流程，但之前写经验泛泛而谈、下次用还得重新探索工具」的核心痛点；
     - 完成工作后说一句「将本次工作整理为经验」，调用 action="distill"（或 save）即可自动从
       近期工具调用轨迹（tool_trace.jsonl + 会话 MCP 记录）提取真实用过的模块、工具、子工具、
       参数模板与报错避坑，生成含「Agent 零探索直调配方」的 Markdown 经验文档；
     - 支持 action="search"（按模块/工具/关键词检索）、action="replay"（一键参数化重放流水线）、
       action="to_skill"（一键导出为独立子 Skill）。
  2. 通用跨会话结构化记忆引擎（work_mem，底层接入开源 Gentleman-Programming/engram v3.7.1）：
     - 单文件 Go 原生引擎（bin/engram.exe，SQLite + FTS5，常驻 stdio 管道 ~30ms 响应）；
     - 负责跨会话保存与检索通用事实、口径、偏好、决策、架构约定与踩坑记录（支持 topic_key 自动覆盖演进）；
     - 支持一键联合导出 Engram 记忆与 11_任务经验 为 Obsidian 双链知识图谱（action="export_obsidian"）。
  3. 材料收发可追溯台账（work_ledger）与写前安全备份（work_backup_file）。

与其他模块完全解耦：不 import comms / office 顶层代码；僅在 replay 实际执行时按需通过 work_mods.load_tool 调度。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from work_core import (
    ALLOWED_EXTS, BIN, DIR_BACKUP, DIR_HARVESTED, ROOT, ROSTER, STATE_DIR, WORKSPACE,
    ToolBox, _dump, _ensure_dirs, _file_arg, _resolve_path, exp_lib, ledger,
)

TOOLS = ToolBox("memory")

ENGRAM_BIN = BIN / "engram.exe"
ENGRAM_DATA_DIR = WORKSPACE / "10_工作记忆" / "engram"
OBSIDIAN_VAULT_DIR = WORKSPACE / "10_工作记忆" / "obsidian_vault"
DEFAULT_PROJECT = "work-super-agent"

MODULE = {
    "id": "memory",
    "name": "记忆（流程经验配方 + Engram通用记忆 + 台账）",
    "version": "2.0.0",
    "scope": "轨迹驱动的流程经验与零探索工具配方库（work_exp）、开源 Engram 跨会话通用记忆引擎（work_mem）、材料收发台账（work_ledger）、写前备份（work_backup_file）",
    "entry": "mcp/work_memory_server.py",
    "docs": "docs/EXPERIENCE.md",
}


# --------------------------------------------------------------------------- #
# 常驻 Engram MCP 守护管道（_EngramDaemon）：将通用记忆读写压到 15~50ms
# --------------------------------------------------------------------------- #
class _EngramDaemon:
    """常驻 engram.exe mcp 进程管理，通过 JSON-RPC stdio 毫秒级调用 27 个原生记忆工具。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None
        self._req_id = 10

    def _env(self) -> Dict[str, str]:
        env = dict(os.environ)
        ENGRAM_DATA_DIR.mkdir(parents=True, exist_ok=True)
        env["ENGRAM_DATA_DIR"] = str(ENGRAM_DATA_DIR)
        env["ENGRAM_PROJECT"] = DEFAULT_PROJECT
        env["ENGRAM_NO_UPDATE_CHECK"] = "1"
        return env

    def _ensure_started(self) -> bool:
        if not ENGRAM_BIN.exists():
            return False
        if self._proc is not None and self._proc.poll() is None:
            return True
        try:
            self._proc = subprocess.Popen(
                [str(ENGRAM_BIN), "mcp", "--project", DEFAULT_PROJECT],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=self._env(),
                bufsize=1,
            )
            init_msg = json.dumps({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "work-memory", "version": "2.0.0"},
                },
            })
            assert self._proc.stdin is not None and self._proc.stdout is not None
            self._proc.stdin.write(init_msg + "\n")
            self._proc.stdin.flush()
            _ = self._proc.stdout.readline()
            notif = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
            self._proc.stdin.write(notif + "\n")
            self._proc.stdin.flush()
            return True
        except Exception:
            self._proc = None
            return False

    def call(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            if not self._ensure_started():
                return {"ok": False, "error": f"Engram 引擎不可用（未找到 {ENGRAM_BIN}）"}
            self._req_id += 1
            req = json.dumps({
                "jsonrpc": "2.0",
                "id": self._req_id,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
            }, ensure_ascii=False)
            try:
                assert self._proc is not None and self._proc.stdin is not None and self._proc.stdout is not None
                self._proc.stdin.write(req + "\n")
                self._proc.stdin.flush()
                line = self._proc.stdout.readline()
                if not line:
                    self._proc = None
                    return {"ok": False, "error": "Engram 守护进程无响应"}
                resp = json.loads(line)
            except Exception as exc:
                self._proc = None
                return {"ok": False, "error": f"Engram 调用异常: {exc}"}

            if "error" in resp:
                return {"ok": False, "error": resp["error"].get("message", str(resp["error"]))}
            res = resp.get("result") or {}
            is_err = bool(res.get("isError", False))
            contents = res.get("content") or []
            text_out = "\n".join(c.get("text", "") for c in contents if isinstance(c, dict) and c.get("text")).strip()
            parsed = None
            if text_out.startswith("{"):
                try:
                    parsed = json.loads(text_out)
                except Exception:
                    parsed = None
            if is_err:
                return {"ok": False, "error": text_out or "Engram 返回错误"}
            if isinstance(parsed, dict):
                return {"ok": True, **parsed}
            return {"ok": True, "result": text_out}


_ENGRAM = _EngramDaemon()


def _format_engram_content(content: str, where: str = "", learned: str = "") -> str:
    txt = (content or "").strip()
    if "**What**" in txt:
        return txt
    lines = [f"**What**: {txt}"]
    if where:
        lines.append(f"**Where**: {where}")
    if learned:
        lines.append(f"**Learned**: {learned}")
    return "\n".join(lines)


def status_snapshot() -> Dict[str, Any]:
    """供 other.work_status 汇总：返回台账、经验配方库与开源 Engram 通用记忆引擎的真实状态。"""
    ledger_stat: Dict[str, Any] = {"ok": False}
    if ledger is not None:
        try:
            ledger_stat = ledger.summary()
        except Exception as exc:
            ledger_stat = {"ok": False, "error": str(exc)}
    exp_stat: Dict[str, Any] = {"ok": False}
    if exp_lib is not None:
        try:
            exp_stat = exp_lib.stats()
        except Exception as exc:
            exp_stat = {"ok": False, "error": str(exc)}
    engram_ready = ENGRAM_BIN.exists()
    engram_db = ENGRAM_DATA_DIR / "engram.db"
    return {
        "ledger": ledger_stat,
        "experience": exp_stat,
        "engram": {
            "ok": engram_ready,
            "binary": str(ENGRAM_BIN),
            "db": str(engram_db),
            "db_exists": engram_db.exists(),
        },
        "engines": [
            {
                "name": "Materials Ledger (SQLite)",
                "module": "memory",
                "path": str(ROOT / "materials_ledger.py"),
                "ready": bool(ledger_stat.get("ok")),
                "bridge": "work_ledger（收发台账流水 / 改了几版 / 对底册算未交）",
            },
            {
                "name": "Experience Playbook & Trace Distiller",
                "module": "memory",
                "path": str(ROOT / "experience" / "library.py"),
                "ready": bool(exp_stat.get("ok")),
                "bridge": "work_exp（轨迹自动提炼经验 / 绑定模块工具链 / 零探索重放 / 导出子Skill）",
            },
            {
                "name": "Engram Memory Engine (Gentleman-Programming/engram v3.7.1)",
                "module": "memory",
                "path": str(ENGRAM_BIN),
                "ready": engram_ready,
                "bridge": "work_mem（跨会话通用事实/口径/偏好/决策记忆 + FTS5检索 + Obsidian知识图谱导出）",
            },
        ],
    }
# --------------------------------------------------------------------------- #
# 工具 1：材料台账（谁交了 / 谁没交 / 交了几次）
# --------------------------------------------------------------------------- #
@TOOLS.tool(
    name="work_ledger",
    description=(
        "【台账】材料收发的可追溯流水，回答「谁交了、谁没交、交了几次」。"
        "action=summary 看总量与最近批次；departments 按科室汇总（含重复提交标记）；"
        "missing 对着《科室底册.xlsx》算未交名单（带联络员与电话）；"
        "history 看某科室或全部的收件明细；batches 看每次收件动作；"
        "record 把一批文件记进台账（配合 record_dir 使用）；state 看增量水位。"
        "同一文件重复扫到不会新增行，只累加 seen_count——这是「同一版被扫了几次」；"
        "同一科室出现多行才是「改了几版」。"
    ),
)
def work_ledger(
    action: str = "summary",
    dept: str = "",
    record_dir: str = "",
    note: str = "",
    source: str = "",
    since: str = "",
    limit: int = 60,
    roster: str = "",
) -> str:
    if ledger is None:
        return _dump({"ok": False, "error": "台账模块不可用（materials_ledger.py 导入失败）"})

    if action == "summary":
        return _dump(ledger.summary())
    if action == "departments":
        rows = ledger.departments()
        s = ledger.summary()
        return _dump({"ok": True, "count": len(rows), "departments": rows,
                      "unassigned_files": s.get("unassigned_files", 0),
                      "note": ("" if rows else
                               "台账里的文件名没有「科室_姓名_事由」前缀，归不到科室；"
                               "让联络员按这个命名交材料，或改用 action=missing 对底册核对")})
    if action == "missing":
        return _dump(ledger.missing(roster or str(ROSTER), since=since))
    if action == "history":
        return _dump(ledger.history(dept, limit=limit))
    if action == "batches":
        return _dump(ledger.batches(limit=limit))
    if action == "state":
        return _dump(ledger.state())
    if action == "record":
        if not record_dir:
            return _dump({"ok": False, "error": "action=record 需要 record_dir（要记账的目录）"})
        base = _resolve_path(record_dir, DIR_HARVESTED)
        if not base.is_dir():
            return _dump({"ok": False, "error": f"目录不存在: {base}"})
        items = [{"path": str(p), "dst": str(p)}
                 for p in sorted(base.iterdir())
                 if p.is_file() and not p.name.startswith("~$")
                 and p.suffix.lower() in ALLOWED_EXTS]
        if not items:
            return _dump({"ok": False, "error": f"{base} 下没有可记账的文档"})
        result = ledger.record(items[:500], note=note, source=source or "manual")
        result["dir"] = str(base)
        return _dump(result)
    return _dump({"ok": False, "error": f"未知 action: {action}",
                  "supported": ["summary", "departments", "missing", "history",
                                "batches", "state", "record"]})


# --------------------------------------------------------------------------- #
# 工具 2：任务经验与可执行工具配方库（支持一句话从真实调用轨迹沉淀经验 + 零探索重放）
# --------------------------------------------------------------------------- #
def _exec_single_tool(tool_name: str, args: Dict[str, Any]) -> Any:
    import work_mods

    fn, _ = work_mods.load_tool(tool_name)
    if not callable(fn):
        return {"ok": False, "error": f"未知或无法加载工具: {tool_name}"}
    return fn(**args)


@TOOLS.tool(
    name="work_exp",
    description=(
        "【任务经验与零探索工具配方库】把每次固定的工作流程沉淀为「绑定模块与精确工具链（含子工具、参数模板、避坑点与一键重放配方）」的独立 Markdown。"
        "当用户说「将本次工作整理为经验 / 保存为SOP」时，优先调用 action='distill'（或 action='save'），"
        "会自动从近期工具调用轨迹（tool_trace.jsonl）提取本次实际调用过的 comms/office/memory 工具链、参数模板与失败避坑，"
        "生成可直接发给 AI 免探索直调的经验文档，并自动同步摘要到 Engram 通用记忆库。"
        "支持动作：distill（从真实调用轨迹一键提炼工具级经验） / list（列全部经验及绑定工具） / "
        "search（按关键词、module 或 tool 检索经验配方） / get（读取整篇原文与直调指令块） / "
        "replay（代入变量一键预览或重放执行经验中的工具链） / to_skill（一键将经验导出为独立子 Skill） / "
        "trace（查看近期工具调用轨迹） / save（显式保存经验） / template（空白模板） / export（导出合集）。"
    ),
)
def work_exp(
    action: str = "list",
    doc_id: str = "",
    title: str = "",
    content: str = "",
    tags: str = "",
    day: str = "",
    overwrite: bool = False,
    summary: str = "",
    goal: str = "",
    materials: str = "",
    steps: str = "",
    decisions: str = "",
    pitfalls: str = "",
    outputs: str = "",
    snippets: str = "",
    query: str = "",
    module: str = "",
    tool: str = "",
    steps_json: str = "",
    vars: Any = None,
    vars_json: str = "",
    dry_run: bool = True,
    skill_name: str = "",
    install_codex: bool = False,
    auto_from_trace: bool = True,
    since_minutes: int = 240,
    limit: int = 0,
) -> str:
    if exp_lib is None:
        return _dump({
            "ok": False,
            "error": "经验文档库不可用（experience/ 导入失败）",
            "hint": f"确认 {ROOT / 'experience' / 'library.py'} 存在",
        })

    act = (action or "list").strip().lower()

    if act == "list":
        r = exp_lib.list_docs()
        if r.get("ok") and limit > 0:
            r["items"] = r["items"][: int(limit)]
            r["truncated"] = len(r["items"]) < r["count"]
        return _dump(r)

    if act == "search":
        return _dump(exp_lib.search_docs(query=query or title, module=module, tool=tool, limit=limit or 10))

    if act == "trace":
        return _dump(exp_lib.read_trace(limit=limit or 30, since_minutes=since_minutes))

    if act == "get":
        if not doc_id and title:
            doc_id = title
        if not doc_id:
            return _dump({"ok": False, "error": "action=get 需要 doc_id（先用 action=list 或 search 取 id）"})
        return _dump(exp_lib.get_doc(doc_id))

    if act == "template":
        return _dump(exp_lib.template_doc(title or "新任务名称"))

    if act in ("distill", "save"):
        parsed_steps = None
        if steps_json:
            try:
                parsed_steps = json.loads(steps_json) if isinstance(steps_json, str) else steps_json
            except Exception as exc:
                return _dump({"ok": False, "error": f"steps_json 解析失败: {exc}"})

        # 当用户说「将本次工作整理为经验」时，无论调 distill 还是调 save（且未手写整篇 content），
        # 都优先尝试结合近期真实工具调用轨迹生成精确到工具与参数的经验配方！
        use_distill = (act == "distill") or (auto_from_trace and not content.strip() and (parsed_steps is not None or not steps.strip()))
        if use_distill:
            res = exp_lib.distill_doc(
                title=title,
                summary=summary,
                goal=goal,
                decisions=decisions,
                pitfalls=pitfalls,
                tags=tags,
                day=day,
                overwrite=(True if act == "distill" else overwrite),
                since_minutes=since_minutes,
                trace_limit=limit or 20,
                steps_override=parsed_steps,
            )
            if res.get("ok"):
                # 自动同步一条精炼工具链模式到 Engram 通用记忆库
                try:
                    if os.environ.get("WORK_TRACE_DISABLE") != "1" and ENGRAM_BIN.exists():
                        _ENGRAM.call("mem_save", {
                            "title": f"流程配方：{res.get('id')}",
                            "type": "pattern",
                            "topic_key": f"workflow/{res.get('id')}",
                            "content": _format_engram_content(
                                res.get("agent_prompt") or summary or title,
                                where=f"11_任务经验/{res.get('id')}.md",
                                learned="工具链: " + " -> ".join(res.get("tools") or []),
                            ),
                        })
                except Exception:
                    pass
                return _dump(res)
            if act == "distill":
                return _dump(res)

        if not title:
            return _dump({"ok": False, "error": "action=save 需要 title（任务名）"})
        fields = {
            k: v
            for k, v in {
                "summary": summary,
                "goal": goal,
                "materials": materials,
                "steps": steps,
                "decisions": decisions,
                "pitfalls": pitfalls,
                "outputs": outputs,
                "snippets": snippets,
            }.items()
            if v
        }
        r = exp_lib.save_doc(
            title=title,
            content=content,
            day=day,
            tags=tags,
            overwrite=overwrite,
            fields=fields,
            modules=module,
            tools=tool,
        )
        return _dump(r)

    if act == "replay":
        target_id = doc_id or title
        if not target_id:
            return _dump({"ok": False, "error": "action=replay 需要 doc_id（经验文档 id）"})
        vars_dict: Dict[str, Any] = {}
        v_in = vars if vars is not None else vars_json
        if v_in:
            try:
                vars_dict = json.loads(v_in) if isinstance(v_in, str) else dict(v_in)
            except Exception as exc:
                return _dump({"ok": False, "error": f"vars 解析失败: {exc}"})
        return _dump(
            exp_lib.replay_doc(
                doc_id=target_id,
                vars_dict=vars_dict,
                dry_run=dry_run,
                executor=None if dry_run else _exec_single_tool,
            )
        )

    if act == "to_skill":
        target_id = doc_id or title
        if not target_id:
            return _dump({"ok": False, "error": "action=to_skill 需要 doc_id（经验文档 id）"})
        return _dump(exp_lib.export_to_skill(doc_id=target_id, skill_name=skill_name, install_codex=install_codex))

    if act == "export":
        return _dump(exp_lib.export_all())

    return _dump({
        "ok": False,
        "error": f"未知 action: {action}",
        "supported": ["distill", "list", "search", "get", "replay", "to_skill", "trace", "save", "template", "export"],
    })


# --------------------------------------------------------------------------- #
# 工具 3：通用跨会话结构化记忆引擎（接入开源 Gentleman-Programming/engram）
# --------------------------------------------------------------------------- #
@TOOLS.tool(
    name="work_mem",
    description=(
        "【通用跨会话记忆引擎（基于开源 Gentleman-Programming/engram v3.7.1）】"
        "与管「整段任务工具配方」的 work_exp 互补：work_mem 专门存/查跨会话的长期事实、口径、个人偏好、"
        "关键决策、科室联络人与工具规律（底层 SQLite + FTS5 + 常驻管道 ~30ms 响应）。"
        "支持动作：save（保存结构化记忆，支持 topic_key 同主题自动演进更新与冲突检测） / "
        "search（FTS5 全文检索，支持按 type/scope 过滤） / context（获取近期会话记忆与置顶口径摘要） / "
        "get（按 obs_id 查看完整记忆及修订历史） / timeline（查看某条记忆前后的时间线） / "
        "pin / unpin（置顶或取消置顶核心长期口径） / delete（删除过时记忆） / "
        "stats（查看记忆库统计） / doctor（健康诊断） / "
        "export_obsidian（一键将 Engram 记忆与 11_任务经验 联合导出为 Obsidian 知识图谱库）。"
    ),
)
def work_mem(
    action: str = "search",
    query: str = "",
    title: str = "",
    content: str = "",
    type: str = "decision",
    topic_key: str = "",
    scope: str = "project",
    project: str = DEFAULT_PROJECT,
    obs_id: int = 0,
    all_projects: bool = False,
    hard_delete: bool = False,
    vault_path: str = "",
    limit: int = 10,
) -> str:
    act = (action or "search").strip().lower()
    proj = (project or DEFAULT_PROJECT).strip()

    if act == "save":
        if not (title or "").strip() or not (content or "").strip():
            return _dump({"ok": False, "error": "action=save 需要同时提供 title 和 content"})
        valid_types = {"decision", "architecture", "bugfix", "pattern", "config", "discovery", "learning"}
        mtype = (type or "decision").strip().lower()
        if mtype not in valid_types:
            mtype = "learning"
        args: Dict[str, Any] = {
            "title": title.strip(),
            "type": mtype,
            "scope": (scope or "project").strip(),
            "project": proj,
            "content": _format_engram_content(content),
        }
        if (topic_key or "").strip():
            args["topic_key"] = topic_key.strip()
        return _dump(_ENGRAM.call("mem_save", args))

    if act == "search":
        q = (query or title or "").strip()
        if not q:
            return _dump({"ok": False, "error": "action=search 需要 query（检索关键词）"})
        args = {"query": q, "limit": max(1, min(int(limit or 10), 50))}
        if all_projects:
            args["all_projects"] = True
        else:
            args["project"] = proj
        if (type or "").strip() and type != "all":
            # 调用时不强制限制 type，除非用户显式要求特定类别且不是默认占位的 decision
            pass
        if (scope or "").strip() and scope != "project":
            args["scope"] = scope.strip()
        return _dump(_ENGRAM.call("mem_search", args))

    if act == "context":
        args = {"project": proj, "limit": max(1, min(int(limit or 20), 50))}
        if (scope or "").strip():
            args["scope"] = scope.strip()
        return _dump(_ENGRAM.call("mem_context", args))

    if act == "get":
        if int(obs_id or 0) <= 0:
            return _dump({"ok": False, "error": "action=get 需要正整数 obs_id"})
        return _dump(_ENGRAM.call("mem_get_observation", {"id": int(obs_id)}))

    if act == "timeline":
        if int(obs_id or 0) <= 0:
            return _dump({"ok": False, "error": "action=timeline 需要正整数 obs_id"})
        return _dump(_ENGRAM.call("mem_timeline", {"observation_id": int(obs_id), "before": 5, "after": 5}))

    if act == "pin":
        if int(obs_id or 0) <= 0:
            return _dump({"ok": False, "error": "action=pin 需要正整数 obs_id"})
        return _dump(_ENGRAM.call("mem_pin", {"id": int(obs_id)}))

    if act == "unpin":
        if int(obs_id or 0) <= 0:
            return _dump({"ok": False, "error": "action=unpin 需要正整数 obs_id"})
        return _dump(_ENGRAM.call("mem_unpin", {"id": int(obs_id)}))

    if act == "delete":
        if int(obs_id or 0) <= 0:
            return _dump({"ok": False, "error": "action=delete 需要正整数 obs_id"})
        return _dump(_ENGRAM.call("mem_delete", {"id": int(obs_id), "hard_delete": bool(hard_delete), "expected_project": proj}))

    if act == "stats":
        return _dump(_ENGRAM.call("mem_stats", {}))

    if act == "doctor":
        return _dump(_ENGRAM.call("mem_doctor", {}))

    if act == "export_obsidian":
        target_vault = Path(vault_path).resolve() if (vault_path or "").strip() else OBSIDIAN_VAULT_DIR
        target_vault.mkdir(parents=True, exist_ok=True)
        env = _ENGRAM._env()
        try:
            proc = subprocess.run(
                [str(ENGRAM_BIN), "obsidian-export", "--vault", str(target_vault), "--all", "--force"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=30,
            )
            wf_dir = target_vault / "workflows"
            wf_dir.mkdir(parents=True, exist_ok=True)
            copied_sops = 0
            if exp_lib is not None:
                for d in (exp_lib.list_docs().get("items") or []):
                    src_md = Path(d["path"])
                    if src_md.is_file():
                        shutil.copy2(src_md, wf_dir / src_md.name)
                        copied_sops += 1
            return _dump({
                "ok": proc.returncode == 0,
                "vault_path": str(target_vault),
                "workflows_dir": str(wf_dir),
                "copied_experience_docs": copied_sops,
                "engram_output": proc.stdout.strip(),
                "error": proc.stderr.strip() if proc.returncode != 0 else "",
            })
        except Exception as exc:
            return _dump({"ok": False, "error": f"Obsidian 导出失败: {exc}"})

    return _dump({
        "ok": False,
        "error": f"未知 action: {action}",
        "supported": [
            "save", "search", "context", "get", "timeline",
            "pin", "unpin", "delete", "stats", "doctor", "export_obsidian",
        ],
    })


# --------------------------------------------------------------------------- #
# 工具 4：文件备份
# --------------------------------------------------------------------------- #
@TOOLS.tool(
    name="work_backup_file",
    description=(
        "【只读复制】把任意本机文件（docx / xlsx / pdf / md / 图片等）复制一份到 "
        "02_自动备份区/，文件名带时间戳，返回备份路径；绝不修改或覆盖源文件，"
        "可以直接单独调用，不必先有「要改文档」的前提。覆盖式写操作前建议先调用。"
    ),
)
def work_backup_file(file: str) -> str:
    _ensure_dirs()
    src, err = _file_arg(file)
    if err:
        return _dump({"ok": False, "error": err})
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = DIR_BACKUP / f"{src.stem}_{stamp}{src.suffix}"
    shutil.copy2(src, dst)
    return _dump({"ok": True, "source": str(src), "backup": str(dst), "size_kb": round(dst.stat().st_size / 1024, 1)})
