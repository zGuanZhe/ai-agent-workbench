# -*- coding: utf-8 -*-
from __future__ import annotations
import abc
import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

def sha256_text(text: str) -> str:
    norm = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()

def sha256_file(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
        return sha256_text(content)
    except Exception:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        return h.hexdigest()

def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    norm = content.replace("\r\n", "\n").replace("\r", "\n")
    tmp.write_text(norm, encoding="utf-8", newline="\n")
    os.replace(tmp, path)

class BaseIDEEngine(abc.ABC):
    """IDE 破限与沙箱生命周期管理抽象基类"""

    app_id: str = ""
    name: str = ""
    vendor: str = ""
    badge: str = ""
    icon: str = ""
    color: str = "#6366f1"
    description: str = ""

    def __init__(self, root_dir: Path):
        self.root_dir = root_dir

    @abc.abstractmethod
    def detect(self, ws: Path) -> Dict[str, Any]:
        """探测工作区内该 IDE 的部署状态与元数据"""
        pass

    @abc.abstractmethod
    def verify_integrity(self, ws: Path) -> Dict[str, Any]:
        """核验受管文件的 SHA256 完整性 (intact, drifted, missing, unmanaged, clean)"""
        pass

    @abc.abstractmethod
    def apply(
        self,
        ws: Path,
        version: str = "",
        role: str = "",
        strictness: str = "",
        custom_content: str = "",
        scope: str = "workspace",
    ) -> Dict[str, Any]:
        """在目标工作区部署破限规约与配置"""
        pass

    @abc.abstractmethod
    def reset(self, ws: Path, scope: str = "workspace") -> Dict[str, Any]:
        """一键无损回滚该 IDE 的所有受管规约，恢复原生基线"""
        pass

    @abc.abstractmethod
    def doctor(self, ws: Path) -> str:
        """运行环境诊断自检"""
        pass

    def preview(
        self,
        ws: Path,
        version: str = "",
        role: str = "",
        strictness: str = "",
    ) -> str:
        """获取规则预览内容"""
        return ""

    @staticmethod
    def generic_verify_files(
        ws: Path,
        state_file: Path,
        candidate_rel_paths: List[str],
    ) -> Dict[str, Any]:
        """通用状态文件比对逻辑"""
        if not state_file.exists():
            existing = [rel for rel in candidate_rel_paths if (ws / rel).exists()]
            if existing:
                return {
                    "status": "unmanaged",
                    "state_file_exists": False,
                    "summary": f"发现 {len(existing)} 个未受管规则文件存在，但无部署快照记录",
                    "files": [
                        {
                            "rel_path": rel,
                            "exists": True,
                            "status": "unmanaged",
                            "expected_sha256": "",
                            "actual_sha256": sha256_file(ws / rel),
                        }
                        for rel in existing
                    ],
                }
            return {
                "status": "clean",
                "state_file_exists": False,
                "summary": "工作区纯净，原生默认未部署",
                "files": [],
            }

        try:
            state_data = json.loads(state_file.read_text(encoding="utf-8", errors="replace"))
        except Exception as e:
            return {
                "status": "drifted",
                "state_file_exists": True,
                "summary": f"状态记录文件已损坏无法解析: {e}",
                "files": [],
            }

        managed_items = state_data.get("managed_files", [])
        norm_items: List[Dict[str, Any]] = []
        for item in managed_items:
            if isinstance(item, str):
                norm_items.append({"rel_path": item, "sha256": ""})
            elif isinstance(item, dict):
                norm_items.append(item)

        files_report: List[Dict[str, Any]] = []
        any_missing = False
        any_drifted = False

        for it in norm_items:
            rel = it.get("rel_path", "")
            if not rel:
                continue
            target_path = ws / rel
            exp_sha = it.get("sha256", "")
            backup_file = it.get("backup")

            if not target_path.exists():
                any_missing = True
                files_report.append({
                    "rel_path": rel,
                    "exists": False,
                    "status": "missing",
                    "expected_sha256": exp_sha,
                    "actual_sha256": "",
                    "backup": backup_file,
                })
            else:
                act_sha = sha256_file(target_path)
                if exp_sha and act_sha != exp_sha:
                    any_drifted = True
                    files_report.append({
                        "rel_path": rel,
                        "exists": True,
                        "status": "drifted",
                        "expected_sha256": exp_sha,
                        "actual_sha256": act_sha,
                        "backup": backup_file,
                    })
                else:
                    files_report.append({
                        "rel_path": rel,
                        "exists": True,
                        "status": "intact",
                        "expected_sha256": exp_sha,
                        "actual_sha256": act_sha,
                        "backup": backup_file,
                    })

        if any_missing:
            overall = "missing"
            summary = "部分受管文件已缺失"
        elif any_drifted:
            overall = "drifted"
            summary = "受管文件被外部手动修改，哈希已漂移"
        else:
            overall = "intact"
            summary = "所有受管规约文件哈希完整匹配，无篡改"

        return {
            "status": overall,
            "state_file_exists": True,
            "summary": summary,
            "version": state_data.get("version", ""),
            "applied_at": state_data.get("applied_at", ""),
            "files": files_report,
        }
