# -*- coding: utf-8 -*-
"""
UnrestrictedManager - 破限控制中枢统一核心调度器
Unified in-process engine manager, workspace integrity auditor & dynamic rule orchestrator.
"""

from __future__ import annotations
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from .engines import (
        BaseIDEEngine,
        CursorEngine,
        WindsurfEngine,
        WorkBuddyEngine,
        TraeEngine,
        AntigravityEngine,
        ClaudeEngine,
        CodexEngine,
    )
    from .composer import DynamicRuleComposer
except (ImportError, ValueError):
    try:
        from engines import (
            BaseIDEEngine,
            CursorEngine,
            WindsurfEngine,
            WorkBuddyEngine,
            TraeEngine,
            AntigravityEngine,
            ClaudeEngine,
            CodexEngine,
        )
        from composer import DynamicRuleComposer
    except (ImportError, ValueError):
        from instruct_unrestricted_core.engines import (
            BaseIDEEngine,
            CursorEngine,
            WindsurfEngine,
            WorkBuddyEngine,
            TraeEngine,
            AntigravityEngine,
            ClaudeEngine,
            CodexEngine,
        )
        from instruct_unrestricted_core.composer import DynamicRuleComposer

class UnrestrictedManager:
    """破限控制中枢核心管理器

    支持以纯 Python API / 零依赖方式秒级调用，无冷启动开销，
    全面托管 7 大主流 AI 编程环境（Antigravity, Claude Code, Codex, Cursor, Windsurf, WorkBuddy, Trae）。
    """

    def __init__(self, root_dir: Optional[Path] = None, data_dir: Optional[Path] = None):
        if root_dir:
            self.root_dir = Path(root_dir).resolve()
        else:
            self.root_dir = Path(__file__).resolve().parent

        if data_dir:
            self.data_dir = Path(data_dir).resolve()
        else:
            self.data_dir = self.root_dir.parent / "data"

        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.ws_config_file = self.data_dir / "unrestricted_workspace.json"

        # 注册 7 大 IDE 引擎实例
        self.engines: Dict[str, BaseIDEEngine] = {
            "antigravity": AntigravityEngine(self.root_dir),
            "claude": ClaudeEngine(self.root_dir),
            "codex": CodexEngine(self.root_dir),
            "cursor": CursorEngine(self.root_dir),
            "windsurf": WindsurfEngine(self.root_dir),
            "workbuddy": WorkBuddyEngine(self.root_dir),
            "trae": TraeEngine(self.root_dir),
        }

    # ── 工作区管理 ──
    def get_configured_workspace(self) -> Path:
        if self.ws_config_file.exists():
            try:
                d = json.loads(self.ws_config_file.read_text(encoding="utf-8", errors="replace"))
                saved = d.get("current_workspace")
                if saved:
                    p = Path(saved).resolve()
                    if p.exists() and p.is_dir():
                        return p
            except Exception:
                pass

        candidates = [
            self.root_dir.parent,
            self.root_dir,
        ]
        for c in candidates:
            if c.exists() and c.is_dir():
                return c.resolve()
        return self.root_dir.resolve()

    def set_configured_workspace(self, ws: Path | str) -> Path:
        p = Path(ws).resolve()
        if not p.exists() or not p.is_dir():
            raise ValueError(f"指定工作区路径不存在或不是目录: {ws}")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.ws_config_file.write_text(
            json.dumps({"current_workspace": str(p)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return p

    def resolve_workspace(self, param_ws: Optional[Path | str] = None) -> Path:
        if param_ws:
            p = Path(param_ws).resolve()
            if p.exists() and p.is_dir():
                self.set_configured_workspace(p)
                return p
        return self.get_configured_workspace()

    def get_workspace_candidates(self) -> List[str]:
        cands: List[str] = []
        def _add(p: Path):
            try:
                rp = p.resolve()
                if rp.exists() and rp.is_dir() and str(rp) not in cands:
                    cands.append(str(rp))
            except Exception:
                pass

        _add(self.get_configured_workspace())
        _add(self.root_dir.parent)
        _add(self.root_dir)

        try:
            for item in self.root_dir.parent.iterdir():
                if item.is_dir() and not item.name.startswith("."):
                    _add(item)
        except Exception:
            pass
        return cands

    # ── 状态与探测 ──
    def get_apps(self, ws: Optional[Path | str] = None) -> List[Dict[str, Any]]:
        target_ws = self.resolve_workspace(ws)
        return [engine.detect(target_ws) for engine in self.engines.values()]

    def get_app_info(self, app_id: str, ws: Optional[Path | str] = None) -> Dict[str, Any]:
        if app_id not in self.engines:
            raise KeyError(f"未知 IDE 引擎: {app_id}")
        target_ws = self.resolve_workspace(ws)
        return self.engines[app_id].detect(target_ws)

    def get_status_overview(self, ws: Optional[Path | str] = None) -> Dict[str, Any]:
        target_ws = self.resolve_workspace(ws)
        apps = self.get_apps(target_ws)
        unr_count = sum(1 for a in apps if a.get("is_unrestricted"))
        intact_count = sum(1 for a in apps if a.get("integrity", {}).get("status") == "intact")
        drifted_count = sum(1 for a in apps if a.get("integrity", {}).get("status") == "drifted")
        missing_count = sum(1 for a in apps if a.get("integrity", {}).get("status") == "missing")

        return {
            "success": True,
            "workspace": str(target_ws),
            "total_apps": len(apps),
            "unrestricted_count": unr_count,
            "clean_count": len(apps) - unr_count,
            "integrity_summary": {
                "intact": intact_count,
                "drifted": drifted_count,
                "missing": missing_count,
            },
            "apps": apps,
        }

    # ── 完整性比对 (Task 1.1) ──
    def verify_file_integrity(
        self,
        ws: Optional[Path | str] = None,
        app_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        target_ws = self.resolve_workspace(ws)
        if app_id:
            if app_id not in self.engines:
                raise KeyError(f"未知 IDE 引擎: {app_id}")
            report = self.engines[app_id].verify_integrity(target_ws)
            return {
                "success": True,
                "workspace": str(target_ws),
                "app": app_id,
                **report,
            }

        reports = {}
        for aid, engine in self.engines.items():
            reports[aid] = engine.verify_integrity(target_ws)

        return {
            "success": True,
            "workspace": str(target_ws),
            "apps": reports,
        }

    # ── 应用与部署 (Task 1.2) ──
    def apply_app(
        self,
        app_id: str,
        ws: Optional[Path | str] = None,
        version: str = "",
        role: str = "fullstack",
        strictness: str = "standard",
        custom_content: str = "",
        scope: str = "workspace",
    ) -> Dict[str, Any]:
        if app_id not in self.engines:
            raise KeyError(f"未知 IDE 引擎: {app_id}")
        target_ws = self.resolve_workspace(ws)
        engine = self.engines[app_id]
        return engine.apply(
            ws=target_ws,
            version=version,
            role=role,
            strictness=strictness,
            custom_content=custom_content,
            scope=scope,
        )

    def reset_app(
        self,
        app_id: str,
        ws: Optional[Path | str] = None,
        scope: str = "workspace",
    ) -> Dict[str, Any]:
        if app_id not in self.engines:
            raise KeyError(f"未知 IDE 引擎: {app_id}")
        target_ws = self.resolve_workspace(ws)
        engine = self.engines[app_id]
        return engine.reset(ws=target_ws, scope=scope)

    def apply_all(
        self,
        ws: Optional[Path | str] = None,
        role: str = "fullstack",
        strictness: str = "standard",
        custom_content: str = "",
    ) -> Dict[str, Any]:
        target_ws = self.resolve_workspace(ws)
        results = {}
        for aid, engine in self.engines.items():
            try:
                results[aid] = engine.apply(
                    ws=target_ws,
                    role=role,
                    strictness=strictness,
                    custom_content=custom_content,
                )
            except Exception as e:
                results[aid] = {"success": False, "error": str(e)}

        return {
            "success": True,
            "workspace": str(target_ws),
            "message": f"已完成全系编程软件破限沙箱批量部署 (工作区: {target_ws.name})！",
            "results": results,
        }

    def reset_all(self, ws: Optional[Path | str] = None) -> Dict[str, Any]:
        target_ws = self.resolve_workspace(ws)
        results = {}
        for aid, engine in self.engines.items():
            try:
                results[aid] = engine.reset(ws=target_ws)
            except Exception as e:
                results[aid] = {"success": False, "error": str(e)}

        return {
            "success": True,
            "workspace": str(target_ws),
            "message": f"已完成全系编程软件一键恢复原生默认 (工作区: {target_ws.name})！",
            "results": results,
        }

    # ── 诊断与预览 ──
    def doctor(self, ws: Optional[Path | str] = None, app_id: Optional[str] = None) -> str:
        target_ws = self.resolve_workspace(ws)
        lines = [f"=== 破限控制中枢环境体检诊断 (Workspace: {target_ws}) ==="]
        if app_id:
            if app_id in self.engines:
                lines.append(self.engines[app_id].doctor(target_ws))
            else:
                lines.append(f"未知应用: {app_id}")
        else:
            for aid, engine in self.engines.items():
                lines.append(f"\n[{aid.upper()}]")
                lines.append(engine.doctor(target_ws))
        return "\n".join(lines)

    def preview(
        self,
        app_id: str,
        ws: Optional[Path | str] = None,
        version: str = "",
        role: str = "fullstack",
        strictness: str = "standard",
    ) -> str:
        if app_id not in self.engines:
            return f"未知应用: {app_id}"
        target_ws = self.resolve_workspace(ws)
        return self.engines[app_id].preview(
            ws=target_ws,
            version=version,
            role=role,
            strictness=strictness,
        )

    # ── 动态组装 (Task 1.3) ──
    def compose_rule(
        self,
        role: str = "fullstack",
        strictness: str = "standard",
        target: str = "cursor",
        custom_notes: str = "",
    ) -> Dict[str, Any]:
        return DynamicRuleComposer.compose(
            role=role,
            strictness=strictness,
            target=target,
            custom_notes=custom_notes,
        )

    def get_compose_options(self) -> Dict[str, Any]:
        return DynamicRuleComposer.get_options()
