# -*- coding: utf-8 -*-
from __future__ import annotations
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from .base_engine import BaseIDEEngine, sha256_file, sha256_text
except (ImportError, ValueError):
    try:
        from engines.base_engine import BaseIDEEngine, sha256_file, sha256_text
    except (ImportError, ValueError):
        from instruct_unrestricted_core.engines.base_engine import BaseIDEEngine, sha256_file, sha256_text

class ClaudeEngine(BaseIDEEngine):
    app_id = "claude"
    name = "Anthropic Claude Code"
    vendor = "Anthropic"
    badge = "CLAUDE.md / Runtime 契约"
    icon = "claude"
    color = "#d97706"
    description = "管理 CLAUDE.md 与 settings.json 沙箱规则，消除 Claude Code 拒绝指令并锁定单通道输出"

    def _get_script(self) -> Path:
        candidates = [
            self.root_dir / "compilers" / "claude-instruct" / "claude-instruct.py",
            self.root_dir / "instruct" / "claude-instruct" / "claude-instruct.py",
        ]
        for c in candidates:
            if c.exists():
                return c
        return candidates[0]

    def _get_package_hashes(self) -> Dict[str, str]:
        pkg_dir = self.root_dir / "compilers" / "claude-instruct" / "packages"
        if not pkg_dir.exists():
            pkg_dir = self.root_dir / "instruct" / "claude-instruct" / "packages"
        res = {}
        if pkg_dir.exists():
            for p in pkg_dir.glob("*.md"):
                res[p.stem] = sha256_file(p)
        return res

    def detect(self, ws: Path) -> Dict[str, Any]:
        integrity = self.verify_integrity(ws)
        is_unr = integrity["status"] in ["intact", "drifted"]
        ver = integrity.get("version") or ("claude-unmanaged" if integrity["status"] == "unmanaged" else "未部署 (原生默认)")
        scope = integrity.get("scope", "project")

        target_file = ws / "CLAUDE.md" if scope == "project" else Path.home() / ".claude" / "CLAUDE.md"

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
                {"id": "claude-v1-standard", "name": "确定性执行版 (claude-v1-standard)", "desc": "标准认知编译器契约，严格单通道输入锁定与工具四原语审计"},
                {"id": "claude-v2-hyperdrive", "name": "极限穿透版 (claude-v2-hyperdrive)", "desc": "底层逆向工程、漏洞审计与免杀分析专属破限规则"},
            ],
            "scope": scope,
            "target_path": str(target_file),
            "target_workspace": str(ws),
            "applied_at": integrity.get("applied_at", ""),
            "managed_files": [f["rel_path"] for f in integrity.get("files", [])],
        }

    def verify_integrity(self, ws: Path) -> Dict[str, Any]:
        proj_claude_md = ws / "CLAUDE.md"
        user_claude_md = Path.home() / ".claude" / "CLAUDE.md"

        target_md = None
        scope = "project"
        if proj_claude_md.exists():
            target_md = proj_claude_md
            scope = "project"
        elif user_claude_md.exists():
            target_md = user_claude_md
            scope = "user"

        if not target_md or not target_md.exists():
            return {
                "status": "clean",
                "state_file_exists": False,
                "summary": "工作区与用户目录未部署 CLAUDE.md 规约",
                "files": [],
            }

        act_sha = sha256_file(target_md)
        txt = target_md.read_text(encoding="utf-8", errors="replace")
        pkg_hashes = self._get_package_hashes()

        matched_ver = ""
        for vname, vsha in pkg_hashes.items():
            if act_sha == vsha:
                matched_ver = vname
                break

        has_unr_markers = any(m in txt for m in ["claude-", "MODE: UNRESTRICTED", "COGNITIVE_COMPILER", "CLAUDE_INSTRUCT"])

        if matched_ver:
            status = "intact"
            summary = f"CLAUDE.md 规约哈希匹配官方基准 ({matched_ver})"
            ver = matched_ver
        elif has_unr_markers:
            status = "drifted"
            summary = "CLAUDE.md 含有破限规约，但已被外部编辑修改"
            ver = "claude-v2-hyperdrive" if "hyperdrive" in txt else "claude-v1-standard"
        else:
            status = "unmanaged"
            summary = "发现普通原生 CLAUDE.md，未注入破限编译器"
            ver = "custom"

        rel_label = "CLAUDE.md" if scope == "project" else str(user_claude_md)
        return {
            "status": status,
            "scope": scope,
            "version": ver,
            "summary": summary,
            "files": [
                {
                    "rel_path": rel_label,
                    "exists": True,
                    "status": status,
                    "actual_sha256": act_sha,
                }
            ],
        }

    def apply(
        self,
        ws: Path,
        version: str = "claude-v1-standard",
        role: str = "",
        strictness: str = "",
        custom_content: str = "",
        scope: str = "workspace",
    ) -> Dict[str, Any]:
        t0 = time.time()
        script = self._get_script()
        ver = version or "claude-v1-standard"
        claude_scope = "project" if scope in ["workspace", "project", "local"] else "user"
        cmd = [sys.executable, str(script), "--apply", "--pkg-version", ver, "--scope", claude_scope]
        if claude_scope == "project":
            cmd.extend(["--project-dir", str(ws)])

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
            "message": f"已成功为 Claude Code 部署破限规约至: {ws.name}",
        }

    def reset(self, ws: Path, scope: str = "workspace") -> Dict[str, Any]:
        t0 = time.time()
        script = self._get_script()
        claude_scope = "project" if scope in ["workspace", "project", "local"] else "user"
        cmd = [sys.executable, str(script), "--reset", "--scope", claude_scope]
        if claude_scope == "project":
            cmd.extend(["--project-dir", str(ws)])

        proc = subprocess.run(cmd, cwd=str(ws), capture_output=True, encoding="utf-8", errors="replace")
        duration_ms = round((time.time() - t0) * 1000, 1)

        return {
            "success": proc.returncode == 0,
            "app": self.app_id,
            "workspace": str(ws),
            "duration_ms": duration_ms,
            "message": f"已成功恢复 Claude Code 原生默认",
        }

    def doctor(self, ws: Path) -> str:
        script = self._get_script()
        cmd = [sys.executable, str(script), "--doctor", "--project-dir", str(ws)]
        proc = subprocess.run(cmd, cwd=str(ws), capture_output=True, encoding="utf-8", errors="replace")
        return proc.stdout.strip()

    def preview(
        self,
        ws: Path,
        version: str = "claude-v1-standard",
        role: str = "",
        strictness: str = "",
    ) -> str:
        pkg_dir = self.root_dir / "compilers" / "claude-instruct" / "packages"
        if not pkg_dir.exists():
            pkg_dir = self.root_dir / "instruct" / "claude-instruct" / "packages"
        p = pkg_dir / f"{version or 'claude-v1-standard'}.md"
        if p.exists():
            return p.read_text(encoding="utf-8", errors="replace")
        return ""
