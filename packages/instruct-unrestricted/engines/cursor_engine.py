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

class CursorEngine(BaseIDEEngine):
    app_id = "cursor"
    name = "Cursor IDE"
    vendor = "Anysphere"
    badge = ".cursorrules & .cursor/rules/*.mdc"
    icon = "cursor"
    color = "#000000"
    description = "管理工作区 .cursorrules 与新版 .cursor/rules/*.mdc (带 frontmatter 元数据)，消除代码截断与软拒绝"

    STATE_REL = ".cursor/.cursor-instruct-state.json"
    BACKUP_EXT = ".cursor-backup"
    TARGET_RELS = [".cursorrules", ".cursor/rules/unrestricted.mdc"]

    def detect(self, ws: Path) -> Dict[str, Any]:
        state_file = ws / self.STATE_REL
        integrity = self.verify_integrity(ws)
        is_unr = integrity["status"] in ["intact", "drifted"]
        ver = integrity.get("version") or ("cursor-unmanaged" if integrity["status"] == "unmanaged" else "未部署 (原生默认)")

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
                {"id": "composed", "name": "动态组装规约 (推荐)", "desc": "按角色与严格度动态编译生成专属 .cursorrules 与 .mdc"},
                {"id": "cursor-v1-standard", "name": "Cursor 生产稳定版", "desc": "经典全栈工程零拒答与防漂移规范"},
                {"id": "cursor-v2-hyperdrive", "name": "Cursor 极限超频版", "desc": "零说教高吞吐底层逆向与安全分析规范"},
            ],
            "scope": "workspace",
            "target_path": str(ws / ".cursorrules"),
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
        version: str = "composed",
        role: str = "fullstack",
        strictness: str = "standard",
        custom_content: str = "",
        scope: str = "workspace",
    ) -> Dict[str, Any]:
        t0 = time.time()
        # 1. 动态生成或按版本组装
        if version == "cursor-v2-hyperdrive":
            role = "security_audit"
            strictness = "hyperdrive"
        elif version == "cursor-v1-standard":
            role = "fullstack"
            strictness = "standard"

        composed = DynamicRuleComposer.compose(
            role=role,
            strictness=strictness,
            target="cursor",
            custom_notes=custom_content,
        )

        # 2. 检查既有文件并备份
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
        for f in composed["files"]:
            rel = f["rel_path"]
            target_p = ws / rel
            backup_rel = prev_backups.get(rel)

            if target_p.exists() and not backup_rel and rel not in managed_by_us:
                # 既有外部文件，且尚未备份
                bk_p = ws / (rel + self.BACKUP_EXT)
                try:
                    bk_p.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target_p, bk_p)
                    backup_rel = rel + self.BACKUP_EXT
                except Exception:
                    backup_rel = None

            # 原子写入
            atomic_write_text(target_p, f["content"])
            managed_records.append({
                "rel_path": rel,
                "sha256": f["sha256"],
                "backup": backup_rel,
            })

        # 3. 记录状态
        new_state = {
            "app": self.app_id,
            "version": composed["version"],
            "role": role,
            "strictness": strictness,
            "sha256": composed["sha256"],
            "applied_at": datetime.now().isoformat(),
            "managed_files": managed_records,
        }
        atomic_write_text(state_file, json.dumps(new_state, ensure_ascii=False, indent=2))

        duration_ms = round((time.time() - t0) * 1000, 1)
        return {
            "success": True,
            "app": self.app_id,
            "version": composed["version"],
            "workspace": str(ws),
            "duration_ms": duration_ms,
            "files_written": len(managed_records),
            "message": f"已成功为 Cursor IDE 部署规约至: {ws.name}",
        }

    def reset(self, ws: Path, scope: str = "workspace") -> Dict[str, Any]:
        t0 = time.time()
        state_file = ws / self.STATE_REL
        if not state_file.exists():
            # 没有状态文件时，也尝试清理默认文件
            for rel in self.TARGET_RELS:
                p = ws / rel
                if p.exists():
                    p.unlink(missing_ok=True)
            return {
                "success": True,
                "app": self.app_id,
                "workspace": str(ws),
                "message": f"未发现 Cursor 状态文件，已清理残留规约文件",
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
        # 清理空目录
        rules_dir = ws / ".cursor" / "rules"
        if rules_dir.exists() and not any(rules_dir.iterdir()):
            rules_dir.rmdir()
        cursor_dir = ws / ".cursor"
        if cursor_dir.exists() and not any(cursor_dir.iterdir()):
            cursor_dir.rmdir()

        duration_ms = round((time.time() - t0) * 1000, 1)
        return {
            "success": True,
            "app": self.app_id,
            "workspace": str(ws),
            "duration_ms": duration_ms,
            "restored_backups": restored,
            "deleted_files": deleted,
            "message": f"已成功恢复 Cursor IDE 原生默认，回滚 {restored} 个备份，清理 {deleted} 个受管文件！",
        }

    def doctor(self, ws: Path) -> str:
        lines = [f"=== Cursor IDE 环境诊断 (Doctor) ==="]
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
        comp = DynamicRuleComposer.compose(role=role, strictness=strictness, target="cursor")
        return comp["content"]
