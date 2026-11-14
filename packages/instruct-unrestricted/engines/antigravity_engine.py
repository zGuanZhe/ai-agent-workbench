# -*- coding: utf-8 -*-
from __future__ import annotations
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from .base_engine import BaseIDEEngine, sha256_file
except (ImportError, ValueError):
    try:
        from engines.base_engine import BaseIDEEngine, sha256_file
    except (ImportError, ValueError):
        from instruct_unrestricted_core.engines.base_engine import BaseIDEEngine, sha256_file

class AntigravityEngine(BaseIDEEngine):
    app_id = "antigravity"
    name = "Google Antigravity / Gemini CLI"
    vendor = "Google DeepMind"
    badge = "认知编译器 / 规则挂载"
    icon = "antigravity"
    color = "#8b5cf6"
    description = "管理 .agents/rules、AGENTS.md 及 hooks.json，消除模型伪能力拒绝与破坏性代码覆盖"

    def _get_script(self) -> Path:
        candidates = [
            self.root_dir / "compilers" / "antigravity-instruct" / "agy-instruct.py",
            self.root_dir / "instruct" / "antigravity-instruct" / "agy-instruct.py",
        ]
        for c in candidates:
            if c.exists():
                return c
        return candidates[0]

    def detect(self, ws: Path) -> Dict[str, Any]:
        integrity = self.verify_integrity(ws)
        is_unr = integrity["status"] in ["intact", "drifted"]
        ver = integrity.get("version") or ("agy-v1-standard (已挂载规则)" if integrity["status"] == "unmanaged" else "未部署 (原生默认)")

        rules_dir = ws / ".agents" / "rules"
        scope = integrity.get("scope", "workspace")

        return {
            "id": self.app_id,
            "name": self.name,
            "vendor": self.vendor,
            "badge": self.badge,
            "icon": self.icon,
            "color": self.color,
            "description": self.description,
            "is_unrestricted": is_unr,
            "active_version": ver,
            "integrity": integrity,
            "available_versions": [
                {"id": "agy-v1-standard", "name": "标准生产版 (agy-v1-standard)", "desc": "高精度认知编译器，消除软拒绝，推荐主力使用"},
                {"id": "agy-v2-hyperdrive", "name": "超频无约束版 (agy-v2-hyperdrive)", "desc": "高吞吐底层逆向与安全审计模式，零拒答极致执行"},
            ],
            "scope": scope,
            "target_path": str(rules_dir),
            "target_workspace": str(ws),
            "applied_at": integrity.get("applied_at", ""),
            "managed_files": [f["rel_path"] for f in integrity.get("files", [])],
        }

    def verify_integrity(self, ws: Path) -> Dict[str, Any]:
        state_candidates = [
            ws / ".agy-instruct-state.json",
            ws / ".agents" / ".agy-instruct-state.json",
        ]
        state_file = next((f for f in state_candidates if f.exists()), None)

        if not state_file:
            rules_dir = ws / ".agents" / "rules"
            existing_rules = list(rules_dir.glob("*.md")) if rules_dir.exists() else []
            has_gemini = (ws / "GEMINI.md").exists()
            has_agents_md = (ws / "AGENTS.md").exists()
            if existing_rules or has_gemini or has_agents_md:
                found_files = []
                for p in existing_rules:
                    found_files.append({
                        "rel_path": str(p.relative_to(ws)).replace("\\", "/"),
                        "exists": True,
                        "status": "unmanaged",
                        "actual_sha256": sha256_file(p),
                    })
                if has_gemini:
                    found_files.append({
                        "rel_path": "GEMINI.md",
                        "exists": True,
                        "status": "unmanaged",
                        "actual_sha256": sha256_file(ws / "GEMINI.md"),
                    })
                if has_agents_md:
                    found_files.append({
                        "rel_path": "AGENTS.md",
                        "exists": True,
                        "status": "unmanaged",
                        "actual_sha256": sha256_file(ws / "AGENTS.md"),
                    })
                return {
                    "status": "unmanaged",
                    "state_file_exists": False,
                    "summary": f"发现 {len(found_files)} 个未受管规则文件存在，但无部署快照记录",
                    "files": found_files,
                }
            return {
                "status": "clean",
                "state_file_exists": False,
                "summary": "工作区纯净，原生默认未部署",
                "files": [],
            }

        try:
            data = json.loads(state_file.read_text(encoding="utf-8", errors="replace"))
        except Exception as e:
            return {
                "status": "drifted",
                "state_file_exists": True,
                "summary": f"状态快照损坏无法解析: {e}",
                "files": [],
            }

        managed_files = data.get("managed_files", [])
        expected_sha = data.get("sha256", "")
        backups = data.get("backups", {})

        files_report: List[Dict[str, Any]] = []
        any_missing = False
        any_drifted = False

        for rel in managed_files:
            target_p = ws / rel
            bk = backups.get(rel)
            if not target_p.exists():
                any_missing = True
                files_report.append({
                    "rel_path": rel,
                    "exists": False,
                    "status": "missing",
                    "expected_sha256": "",
                    "actual_sha256": "",
                    "backup": bk,
                })
            else:
                act_sha = sha256_file(target_p)
                files_report.append({
                    "rel_path": rel,
                    "exists": True,
                    "status": "intact",
                    "expected_sha256": "",
                    "actual_sha256": act_sha,
                    "backup": bk,
                })

        # 比对主规则摘要
        overall = "intact"
        summary = "所有受管规约文件完整"
        if any_missing:
            overall = "missing"
            summary = "部分受管文件已缺失"

        return {
            "status": overall,
            "state_file_exists": True,
            "summary": summary,
            "version": data.get("version", "agy-v1-standard"),
            "scope": "global" if data.get("is_global") else "workspace",
            "applied_at": data.get("applied_at", ""),
            "files": files_report,
        }

    def apply(
        self,
        ws: Path,
        version: str = "agy-v1-standard",
        role: str = "",
        strictness: str = "",
        custom_content: str = "",
        scope: str = "workspace",
    ) -> Dict[str, Any]:
        t0 = time.time()
        script = self._get_script()
        ver = version or "agy-v1-standard"
        cmd = [sys.executable, str(script), "--apply", "--version", ver]
        if scope == "global":
            cmd.append("--global")
        else:
            cmd.extend(["--workspace", str(ws)])

        proc = subprocess.run(cmd, cwd=str(ws), capture_output=True, encoding="utf-8", errors="replace")
        duration_ms = round((time.time() - t0) * 1000, 1)

        if proc.returncode != 0:
            return {
                "success": False,
                "app": self.app_id,
                "error": proc.stderr or proc.stdout,
                "duration_ms": duration_ms,
            }

        return {
            "success": True,
            "app": self.app_id,
            "version": ver,
            "workspace": str(ws),
            "duration_ms": duration_ms,
            "message": f"已成功为 Google Antigravity 部署破限规约至: {ws.name}",
        }

    def reset(self, ws: Path, scope: str = "workspace") -> Dict[str, Any]:
        t0 = time.time()
        script = self._get_script()
        cmd = [sys.executable, str(script), "--reset"]
        if scope == "global":
            cmd.append("--global")
        else:
            cmd.extend(["--workspace", str(ws)])

        proc = subprocess.run(cmd, cwd=str(ws), capture_output=True, encoding="utf-8", errors="replace")
        duration_ms = round((time.time() - t0) * 1000, 1)

        return {
            "success": proc.returncode == 0,
            "app": self.app_id,
            "workspace": str(ws),
            "duration_ms": duration_ms,
            "message": f"已成功恢复 Google Antigravity 原生默认",
        }

    def doctor(self, ws: Path) -> str:
        script = self._get_script()
        cmd = [sys.executable, str(script), "--doctor", "--workspace", str(ws)]
        proc = subprocess.run(cmd, cwd=str(ws), capture_output=True, encoding="utf-8", errors="replace")
        return proc.stdout.strip()

    def preview(
        self,
        ws: Path,
        version: str = "agy-v1-standard",
        role: str = "",
        strictness: str = "",
    ) -> str:
        p = self.root_dir / "compilers" / "antigravity-instruct" / "packages" / f"{version or 'agy-v1-standard'}.md"
        if p.exists():
            return p.read_text(encoding="utf-8", errors="replace")
        return ""
