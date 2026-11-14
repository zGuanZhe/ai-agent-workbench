# -*- coding: utf-8 -*-
from __future__ import annotations
import json
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from .base_engine import BaseIDEEngine, atomic_write_text, sha256_file, sha256_text
    from ..composer.rule_composer import DynamicRuleComposer
except (ImportError, ValueError):
    try:
        from engines.base_engine import BaseIDEEngine, atomic_write_text, sha256_file, sha256_text
        from composer.rule_composer import DynamicRuleComposer
    except (ImportError, ValueError):
        from instruct_unrestricted_core.engines.base_engine import BaseIDEEngine, atomic_write_text, sha256_file, sha256_text
        from instruct_unrestricted_core.composer.rule_composer import DynamicRuleComposer

class TraeEngine(BaseIDEEngine):
    app_id = "trae"
    name = "Trae / Trae CN (字节跳动)"
    vendor = "ByteDance"
    badge = ".trae / 零拒答规约"
    icon = "trae"
    color = "#0ea5e9"
    description = "管理 Trae IDE 工作区 rules.md 规约，消除代码生成中的格式漂移与虚假拒绝"

    STATE_REL = ".trae/.trae-instruct-state.json"
    BACKUP_EXT = ".trae-backup"
    TARGET_RELS = [".trae/rules.md", ".trae/rules/project_rules.md"]

    def detect(self, ws: Path) -> Dict[str, Any]:
        integrity = self.verify_integrity(ws)
        is_unr = integrity["status"] in ["intact", "drifted"]
        ver = integrity.get("version") or ("trae-unmanaged" if integrity["status"] == "unmanaged" else "未部署 (原生默认)")

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
                {"id": "trae-unrestricted", "name": "零拒答开发版 (默认推荐)", "desc": "Trae 专属零拒答与真实代码交付规范"},
                {"id": "composed", "name": "动态组装规约", "desc": "按角色与严格度动态编译生成专属规约"},
            ],
            "scope": "workspace",
            "target_path": str(ws / ".trae" / "rules.md"),
            "target_workspace": str(ws),
            "applied_at": integrity.get("applied_at", ""),
            "managed_files": [f["rel_path"] for f in integrity.get("files", [])],
        }

    def verify_integrity(self, ws: Path) -> Dict[str, Any]:
        state_file = ws / self.STATE_REL
        return self.generic_verify_files(ws, state_file, self.TARGET_RELS)

    def apply(
        self,
        ws: Path,
        version: str = "trae-unrestricted",
        role: str = "fullstack",
        strictness: str = "standard",
        custom_content: str = "",
        scope: str = "workspace",
    ) -> Dict[str, Any]:
        t0 = time.time()
        if version == "trae-unrestricted" and not custom_content:
            preset_file = self.root_dir / "presets" / "trae_rules.md"
            if preset_file.exists():
                raw_body = preset_file.read_text(encoding="utf-8", errors="replace")
            else:
                raw_body = DynamicRuleComposer.compose(role="fullstack", strictness="standard", target="trae")["content"]
            composed_version = "trae-unrestricted"
            content_hash = sha256_text(raw_body)
            files = [
                {"rel_path": ".trae/rules.md", "content": raw_body, "sha256": content_hash},
                {"rel_path": ".trae/rules/project_rules.md", "content": raw_body, "sha256": content_hash},
            ]
        else:
            comp = DynamicRuleComposer.compose(
                role=role,
                strictness=strictness,
                target="trae",
                custom_notes=custom_content,
            )
            composed_version = comp["version"]
            files = comp["files"]
            content_hash = comp["sha256"]

        state_file = ws / self.STATE_REL
        existing_state = {}
        if state_file.exists():
            try:
                existing_state = json.loads(state_file.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                pass

        managed_by_us = {
            m.get("rel_path")
            for m in existing_state.get("managed_files", [])
        }
        prev_backups = {
            m.get("rel_path"): m.get("backup")
            for m in existing_state.get("managed_files", [])
            if m.get("backup")
        }

        managed_records: List[Dict[str, Any]] = []
        for f in files:
            rel = f["rel_path"]
            target_p = ws / rel
            backup_rel = prev_backups.get(rel)

            if target_p.exists() and not backup_rel and rel not in managed_by_us:
                bk_p = ws / (rel + self.BACKUP_EXT)
                try:
                    bk_p.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target_p, bk_p)
                    backup_rel = rel + self.BACKUP_EXT
                except Exception:
                    backup_rel = None

            atomic_write_text(target_p, f["content"])
            managed_records.append({
                "rel_path": rel,
                "sha256": f["sha256"],
                "backup": backup_rel,
            })

        new_state = {
            "app": self.app_id,
            "version": composed_version,
            "role": role,
            "strictness": strictness,
            "sha256": content_hash,
            "applied_at": datetime.now().isoformat(),
            "managed_files": managed_records,
        }
        atomic_write_text(state_file, json.dumps(new_state, ensure_ascii=False, indent=2))

        duration_ms = round((time.time() - t0) * 1000, 1)
        return {
            "success": True,
            "app": self.app_id,
            "version": composed_version,
            "workspace": str(ws),
            "duration_ms": duration_ms,
            "files_written": len(managed_records),
            "message": f"已成功为 Trae 部署沙箱规约至: {ws.name}",
        }

    def reset(self, ws: Path, scope: str = "workspace") -> Dict[str, Any]:
        t0 = time.time()
        state_file = ws / self.STATE_REL
        if not state_file.exists():
            for rel in self.TARGET_RELS:
                p = ws / rel
                if p.exists():
                    p.unlink(missing_ok=True)
            return {
                "success": True,
                "app": self.app_id,
                "workspace": str(ws),
                "message": f"未发现 Trae 状态文件，已清理残留规约文件",
            }

        try:
            state_data = json.loads(state_file.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            state_data = {}

        managed_items = state_data.get("managed_files", [])
        restored = 0
        deleted = 0

        for it in managed_items:
            rel = it.get("rel_path", "")
            if not rel:
                continue
            target_p = ws / rel
            backup_rel = it.get("backup")

            if backup_rel and (ws / backup_rel).exists():
                try:
                    shutil.move(ws / backup_rel, target_p)
                    restored += 1
                except Exception:
                    target_p.unlink(missing_ok=True)
                    deleted += 1
            else:
                target_p.unlink(missing_ok=True)
                deleted += 1

        state_file.unlink(missing_ok=True)
        rules_dir = ws / ".trae" / "rules"
        if rules_dir.exists() and not any(rules_dir.iterdir()):
            rules_dir.rmdir()
        trae_dir = ws / ".trae"
        if trae_dir.exists() and not any(trae_dir.iterdir()):
            trae_dir.rmdir()

        duration_ms = round((time.time() - t0) * 1000, 1)
        return {
            "success": True,
            "app": self.app_id,
            "workspace": str(ws),
            "duration_ms": duration_ms,
            "restored_backups": restored,
            "deleted_files": deleted,
            "message": f"已成功恢复 Trae 原生默认，回滚 {restored} 个备份，清理 {deleted} 个受管文件！",
        }

    def doctor(self, ws: Path) -> str:
        lines = [f"=== Trae IDE 环境诊断 (Doctor) ==="]
        lines.append(f"目标工作区: {ws}")
        integrity = self.verify_integrity(ws)
        lines.append(f"状态评估: {integrity['status'].upper()} ({integrity['summary']})")
        lines.append(f"状态快照: {ws / self.STATE_REL} [{'存在' if integrity['state_file_exists'] else '不存在'}]")
        for f in integrity.get("files", []):
            st = f["status"].upper()
            sha_hint = f"SHA: {f['actual_sha256'][:8]}..." if f["actual_sha256"] else "无"
            lines.append(f"  - {f['rel_path']}: [{st}] {sha_hint}")
        return "\n".join(lines)

    def preview(
        self,
        ws: Path,
        version: str = "",
        role: str = "fullstack",
        strictness: str = "standard",
    ) -> str:
        preset_file = self.root_dir / "presets" / "trae_rules.md"
        if version == "trae-unrestricted" and preset_file.exists():
            return preset_file.read_text(encoding="utf-8", errors="replace")
        comp = DynamicRuleComposer.compose(role=role, strictness=strictness, target="trae")
        return comp["content"]
