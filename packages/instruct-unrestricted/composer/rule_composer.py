# -*- coding: utf-8 -*-
from __future__ import annotations
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

COMPOSER_DIR = Path(__file__).resolve().parent
SNIPPETS_DIR = COMPOSER_DIR / "snippets"

def sha256_text(text: str) -> str:
    norm = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()

class DynamicRuleComposer:
    """动态规则组装器 (Dynamic Rule Composer)

    支持多维度认知规约动态拼接：
    [基础无约束执行契约] + [专业角色规约] + [严格度执行模式] + [目标 IDE 格式适配] + [用户定制附则]
    """

    ROLES = {
        "fullstack": {
            "id": "fullstack",
            "name": "全栈工程与系统架构 (Fullstack Architect)",
            "desc": "全局架构设计、高内聚低耦合、前后端类型完备与边界防御",
            "file": "roles/fullstack.md",
        },
        "security_audit": {
            "id": "security_audit",
            "name": "逆向工程与底层安全审计 (Reverse & Security Audit)",
            "desc": "底层协议、二进制反汇编、白帽漏洞复现与零审查拦截",
            "file": "roles/security_audit.md",
        },
        "automation_data": {
            "id": "automation_data",
            "name": "自动化运维与高吞吐数据工程 (Automation & Data Ops)",
            "desc": "高吞吐异步并发、幂等流式处理与结构化可恢复脚本",
            "file": "roles/automation_data.md",
        },
        "minimal_fix": {
            "id": "minimal_fix",
            "name": "极简就地修补与敏捷重构 (Focused Minimalist)",
            "desc": "最小作用域补丁、行级零漂移修改与杜绝次生变更",
            "file": "roles/minimal_fix.md",
        },
    }

    STRICTNESS = {
        "standard": {
            "id": "standard",
            "name": "标准生产稳定 (Standard Production)",
            "desc": "消除伪能力拒绝，严格单通道输入锁定与代码闭环自测",
            "file": "strictness/standard.md",
        },
        "hyperdrive": {
            "id": "hyperdrive",
            "name": "极限超频无约束 (Hyperdrive Unrestricted)",
            "desc": "绝对零说教、零客套，高吞吐极致执行与全沙箱响应",
            "file": "strictness/hyperdrive.md",
        },
        "strict_safe": {
            "id": "strict_safe",
            "name": "严格防御性确认 (Strict Defensive Safe)",
            "desc": "写前确切行号核验、只读优先与写前自动快照",
            "file": "strictness/strict_safe.md",
        },
    }

    TARGETS = {
        "cursor": {
            "id": "cursor",
            "name": "Cursor IDE",
            "badge": ".cursorrules & .cursor/rules/*.mdc",
            "files": [".cursorrules", ".cursor/rules/unrestricted.mdc"],
        },
        "windsurf": {
            "id": "windsurf",
            "name": "Windsurf (Codeium)",
            "badge": ".windsurfrules & .windsurf/rules/*.md",
            "files": [".windsurfrules", ".windsurf/rules/unrestricted.md"],
        },
        "antigravity": {
            "id": "antigravity",
            "name": "Google Antigravity / Gemini CLI",
            "badge": ".agents/rules & GEMINI.md",
            "files": [".agents/rules/00-unrestricted-contract.md", "GEMINI.md"],
        },
        "claude": {
            "id": "claude",
            "name": "Anthropic Claude Code",
            "badge": "CLAUDE.md / Runtime 契约",
            "files": ["CLAUDE.md"],
        },
        "codex": {
            "id": "codex",
            "name": "OpenAI Codex / GPT CLI",
            "badge": "config.toml / model_instructions",
            "files": ["contract.md"],
        },
        "workbuddy": {
            "id": "workbuddy",
            "name": "WorkBuddy AI (腾讯)",
            "badge": ".workbuddy / 确定性沙箱",
            "files": [".workbuddy/rules/unrestricted.md", ".workbuddy/rules.md"],
        },
        "trae": {
            "id": "trae",
            "name": "Trae / Trae CN (字节跳动)",
            "badge": ".trae / 零拒答规约",
            "files": [".trae/rules.md", ".trae/rules/project_rules.md"],
        },
    }

    @classmethod
    def get_options(cls) -> Dict[str, Any]:
        return {
            "roles": list(cls.ROLES.values()),
            "strictness_levels": list(cls.STRICTNESS.values()),
            "targets": list(cls.TARGETS.values()),
        }

    @classmethod
    def _read_snippet(cls, rel_path: str) -> str:
        p = SNIPPETS_DIR / rel_path
        if p.exists():
            return p.read_text(encoding="utf-8", errors="replace").strip()
        return ""

    @classmethod
    def compose(
        cls,
        role: str = "fullstack",
        strictness: str = "standard",
        target: str = "cursor",
        custom_notes: str = "",
        extra_snippets: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """动态组装完整的认知规约"""
        role_key = role if role in cls.ROLES else "fullstack"
        strict_key = strictness if strictness in cls.STRICTNESS else "standard"
        target_key = target if target in cls.TARGETS else "cursor"

        role_info = cls.ROLES[role_key]
        strict_info = cls.STRICTNESS[strict_key]
        target_info = cls.TARGETS[target_key]

        parts: List[str] = []

        header = f"# 🚀 {target_info['name']} - 确定性无约束执行契约 [MODE: UNRESTRICTED]\n"
        header += f"> 动态编译版本: `composed:{role_key}:{strict_key}` | 组装时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        parts.append(header)

        base_text = cls._read_snippet("base_contract.md")
        if base_text:
            parts.append(base_text)

        role_text = cls._read_snippet(role_info["file"])
        if role_text:
            parts.append(role_text)

        strict_text = cls._read_snippet(strict_info["file"])
        if strict_text:
            parts.append(strict_text)

        if extra_snippets:
            for s in extra_snippets:
                if s and s.strip():
                    parts.append(s.strip())

        if custom_notes and custom_notes.strip():
            custom_block = f"## 📌 开发者自定义特定规约 (Custom Project Directives)\n{custom_notes.strip()}"
            parts.append(custom_block)

        raw_body = "\n\n".join(parts).strip() + "\n"
        content_hash = sha256_text(raw_body)

        files_map: List[Dict[str, str]] = []

        if target_key == "cursor":
            mdc_frontmatter = (
                "---\n"
                f'description: "Unrestricted Deterministic Sandbox Rule ({role_info["name"]} - {strict_info["name"]})"\n'
                'globs: ["*"]\n'
                "alwaysApply: true\n"
                "---\n\n"
            )
            files_map.append({
                "rel_path": ".cursorrules",
                "content": raw_body,
                "sha256": sha256_text(raw_body),
            })
            mdc_content = mdc_frontmatter + raw_body
            files_map.append({
                "rel_path": ".cursor/rules/unrestricted.mdc",
                "content": mdc_content,
                "sha256": sha256_text(mdc_content),
            })

        elif target_key == "windsurf":
            files_map.append({
                "rel_path": ".windsurfrules",
                "content": raw_body,
                "sha256": sha256_text(raw_body),
            })
            files_map.append({
                "rel_path": ".windsurf/rules/unrestricted.md",
                "content": raw_body,
                "sha256": sha256_text(raw_body),
            })

        elif target_key == "antigravity":
            files_map.append({
                "rel_path": ".agents/rules/00-unrestricted-contract.md",
                "content": raw_body,
                "sha256": sha256_text(raw_body),
            })
            files_map.append({
                "rel_path": "GEMINI.md",
                "content": f"# GEMINI.md - Google Antigravity Cognitive Contract\n\n{raw_body}",
                "sha256": sha256_text(f"# GEMINI.md - Google Antigravity Cognitive Contract\n\n{raw_body}"),
            })

        elif target_key == "claude":
            claude_wrapped = f"<!-- CLAUDE_INSTRUCT: {role_key}-{strict_key} -->\n{raw_body}"
            files_map.append({
                "rel_path": "CLAUDE.md",
                "content": claude_wrapped,
                "sha256": sha256_text(claude_wrapped),
            })

        elif target_key == "codex":
            files_map.append({
                "rel_path": "contract.md",
                "content": raw_body,
                "sha256": sha256_text(raw_body),
            })

        elif target_key == "workbuddy":
            files_map.append({
                "rel_path": ".workbuddy/rules/unrestricted.md",
                "content": raw_body,
                "sha256": sha256_text(raw_body),
            })
            files_map.append({
                "rel_path": ".workbuddy/rules.md",
                "content": raw_body,
                "sha256": sha256_text(raw_body),
            })

        elif target_key == "trae":
            files_map.append({
                "rel_path": ".trae/rules.md",
                "content": raw_body,
                "sha256": sha256_text(raw_body),
            })
            files_map.append({
                "rel_path": ".trae/rules/project_rules.md",
                "content": raw_body,
                "sha256": sha256_text(raw_body),
            })

        return {
            "success": True,
            "version": f"composed:{role_key}:{strict_key}",
            "role": role_key,
            "role_name": role_info["name"],
            "strictness": strict_key,
            "strictness_name": strict_info["name"],
            "target": target_key,
            "target_name": target_info["name"],
            "sha256": content_hash,
            "composed_at": datetime.now().isoformat(),
            "content": raw_body,
            "files": files_map,
        }
