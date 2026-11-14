# -*- coding: utf-8 -*-
from __future__ import annotations
import json
import re
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

class CodexEngine(BaseIDEEngine):
    app_id = "codex"
    name = "OpenAI Codex / GPT CLI"
    vendor = "OpenAI"
    badge = "config.toml / 100% 免杀"
    icon = "codex"
    color = "#10a37f"
    description = "管理 ~/.codex/config.toml 与指令文件，消除安全警告误杀，重塑 Codex 输出准确度"

    def _get_script(self) -> Path:
        candidates = [
            self.root_dir / "compilers" / "gpt-instruct" / "codex-instruct-v2.py",
            self.root_dir / "instruct" / "gpt-instruct" / "codex-instruct-v2.py",
        ]
        for c in candidates:
            if c.exists():
                return c
        return candidates[0]

    def detect(self, ws: Path) -> Dict[str, Any]:
        integrity = self.verify_integrity(ws)
        is_unr = integrity["status"] in ["intact", "drifted"]
        ver = integrity.get("version") or "未部署 (原生默认)"
        codex_cfg = Path.home() / ".codex" / "config.toml"

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
                {"id": "contract", "name": "免杀交付契约 (contract - 推荐)", "desc": "100% 免疫杀软拦截，形式化工程交付契约，杜绝幻觉与破坏性覆写"},
                {"id": "astra", "name": "前沿分层契约 (astra)", "desc": "针对 GPT-6 / Astra 前沿模型的深度破限与分层任务执行包"},
            ],
            "scope": "global",
            "target_path": str(codex_cfg),
            "target_workspace": str(ws),
            "applied_at": integrity.get("applied_at", ""),
            "managed_files": [f["rel_path"] for f in integrity.get("files", [])],
        }

    def verify_integrity(self, ws: Path) -> Dict[str, Any]:
        codex_home = Path.home() / ".codex"
        config_file = codex_home / "config.toml"
        if not config_file.exists():
            return {
                "status": "clean",
                "state_file_exists": False,
                "summary": "未发现 ~/.codex/config.toml 配置文件",
                "files": [],
            }

        txt = config_file.read_text(encoding="utf-8", errors="replace")
        m = re.search(r'model_instructions_file\s*=\s*["\']([^"\']+)["\']', txt)
        if not m:
            return {
                "status": "clean",
                "state_file_exists": False,
                "summary": "config.toml 未配置 model_instructions_file，原生默认状态",
                "files": [{"rel_path": str(config_file), "status": "clean", "exists": True}],
            }

        rel_val = m.group(1).lstrip("./")
        target_md = codex_home / rel_val
        if not target_md.exists():
            return {
                "status": "missing",
                "state_file_exists": True,
                "summary": f"config.toml 指向的指令文件缺失: {rel_val}",
                "files": [{"rel_path": str(target_md), "status": "missing", "exists": False}],
            }

        act_sha = sha256_file(target_md)
        ver = "astra (GPT-6 前沿推理)" if "astra" in rel_val.lower() else "contract (高精度免杀交付契约)"

        return {
            "status": "intact",
            "state_file_exists": True,
            "version": ver,
            "summary": f"Codex 指令契约有效挂载: {rel_val}",
            "files": [
                {
                    "rel_path": str(target_md),
                    "status": "intact",
                    "exists": True,
                    "actual_sha256": act_sha,
                }
            ],
        }

    def apply(
        self,
        ws: Path,
        version: str = "contract",
        role: str = "",
        strictness: str = "",
        custom_content: str = "",
        scope: str = "workspace",
    ) -> Dict[str, Any]:
        t0 = time.time()
        script = self._get_script()
        ver = version or "contract"
        cmd = [sys.executable, str(script), "--apply", "--pkg-version", ver, "--yes"]

        proc = subprocess.run(cmd, cwd=str(script.parent), capture_output=True, encoding="utf-8", errors="replace")
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
            "message": f"已成功为 OpenAI Codex 挂载指令契约 ({ver})",
        }

    def reset(self, ws: Path, scope: str = "workspace") -> Dict[str, Any]:
        t0 = time.time()
        script = self._get_script()
        cmd = [sys.executable, str(script), "--reset", "--yes"]

        proc = subprocess.run(cmd, cwd=str(script.parent), capture_output=True, encoding="utf-8", errors="replace")
        duration_ms = round((time.time() - t0) * 1000, 1)

        return {
            "success": proc.returncode == 0,
            "app": self.app_id,
            "workspace": str(ws),
            "duration_ms": duration_ms,
            "message": f"已成功从 Codex 卸载指令文件，恢复原生默认配置",
        }

    def doctor(self, ws: Path) -> str:
        script = self._get_script()
        cmd = [sys.executable, str(script), "--status"]
        proc = subprocess.run(cmd, cwd=str(script.parent), capture_output=True, encoding="utf-8", errors="replace")
        return proc.stdout.strip()

    def preview(
        self,
        ws: Path,
        version: str = "contract",
        role: str = "",
        strictness: str = "",
    ) -> str:
        pkg_dir = self.root_dir / "compilers" / "gpt-instruct" / "packages"
        if not pkg_dir.exists():
            pkg_dir = self.root_dir / "instruct" / "gpt-instruct" / "packages"
        p = pkg_dir / f"{version or 'contract'}.md"
        if p.exists():
            return p.read_text(encoding="utf-8", errors="replace")
        return ""
