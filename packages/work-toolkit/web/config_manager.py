# -*- coding: utf-8 -*-
"""config_manager.py - Safe reader/writer for Codex config.toml work modules."""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import tomllib
from datetime import datetime
from typing import Any, Dict

HOME = pathlib.Path(os.path.expanduser("~"))
CODEX_CONFIG = HOME / ".codex" / "config.toml"
CODEX_SKILLS = HOME / ".codex" / "skills"
ROOT = pathlib.Path(__file__).resolve().parent.parent

MANAGED_MODULES = [
    {
        "id": "work_comms",
        "name": "通讯模块",
        "module": "comms",
        "desc": "本机 work_comms MCP 与 work-comms Skill 的配置项",
        "entry": "mcp/work_comms_server.py",
        "skill_id": "work-comms",
    },
    {
        "id": "work_office",
        "name": "Office 模块",
        "module": "office",
        "desc": "本机 work_office MCP 与 work-office Skill 的配置项",
        "entry": "mcp/work_office_server.py",
        "skill_id": "work-office",
    },
    {
        "id": "work_memory",
        "name": "记忆与经验模块",
        "module": "memory",
        "desc": "本机 work_memory MCP 与 work-memory Skill 的配置项",
        "entry": "mcp/work_memory_server.py",
        "skill_id": "work-memory",
    },
    {
        "id": "engram",
        "name": "Engram 通用事实记忆",
        "module": "external",
        "desc": "外部 engram MCP 配置项",
        "entry": "bin/engram.exe",
        "skill_id": None,
    },
    {
        "id": "wechat_chat",
        "name": "微信多模态助理",
        "module": "external",
        "desc": "外部 wechat-chat MCP 配置项",
        "entry": "mcp_servers/wechat-chat-mcp",
        "skill_id": None,
    },
    {
        "id": "officecli",
        "name": "OfficeCLI 原生引擎",
        "module": "external",
        "desc": "外部 officecli MCP 配置项",
        "entry": "bin/officecli.exe",
        "skill_id": None,
    },
]


def _toml_string(value: Any) -> str:
    """Return a TOML-safe basic string.

    json.dumps produces a TOML-compatible basic string for ordinary strings:
    backslashes and quotes are escaped, and Unicode is kept readable when
    ensure_ascii=False. This avoids invalid Windows path Unicode escapes.
    """
    return json.dumps(str(value), ensure_ascii=False)


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return _toml_string(value)
    if isinstance(value, (int, float)):
        return json.dumps(value)
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return _toml_string(value)


def _inline_table(item: Dict[str, Any]) -> str:
    return "{ " + ", ".join(
        f"{_toml_string(k)} = {_toml_value(v)}" for k, v in item.items()
    ) + " }"


def _validate_toml(text: str) -> str | None:
    try:
        tomllib.loads(text)
        return None
    except Exception as exc:
        return f"TOML 校验失败，配置文件未被写入：{exc}"


def _atomic_write(new_text: str, operation: str) -> Dict[str, Any]:
    """Validate TOML before writing; never replace config.toml with invalid text."""
    err = _validate_toml(new_text)
    if err:
        return {"ok": False, "error": err, "operation": operation, "not_written": True}

    CODEX_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    bak = CODEX_CONFIG.with_name(CODEX_CONFIG.name + f".bak_{stamp}")
    tmp = CODEX_CONFIG.with_name(CODEX_CONFIG.name + ".tmp")
    old = CODEX_CONFIG.read_bytes() if CODEX_CONFIG.is_file() else b""
    try:
        bak.write_bytes(old)
        tmp.write_bytes(new_text.encode("utf-8"))
        os.replace(tmp, CODEX_CONFIG)
    except Exception as exc:
        try:
            if old:
                CODEX_CONFIG.write_bytes(old)
            else:
                CODEX_CONFIG.unlink(missing_ok=True)
        except Exception:
            pass
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        return {"ok": False, "error": f"写配置文件失败，已尝试恢复：{exc}", "operation": operation}
    return {"ok": True, "operation": operation, "backup": str(bak)}


def _load_config() -> tuple[dict, str, str | None]:
    text = CODEX_CONFIG.read_text(encoding="utf-8") if CODEX_CONFIG.is_file() else ""
    try:
        return tomllib.loads(text), text, None
    except Exception as exc:
        return {}, text, str(exc)


def _skill_key(raw: Any) -> str:
    """Stable key for a skill config entry.

    Uses the containing folder name so both normal and legacy .disabled paths
    collapse to the same managed skill, without ever keying on SKILL.md alone.
    """
    text = str(raw or "").replace("/", "\\")
    try:
        parent = pathlib.PureWindowsPath(text).parent.name.lower()
    except Exception:
        parent = ""
    if parent.endswith(".disabled"):
        parent = parent[:-len(".disabled")]
    return parent or text.lower()


def _dedupe_skill_config(items: Any) -> list[Dict[str, Any]]:
    merged: dict[str, Dict[str, Any]] = {}
    for raw in items or []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        key = _skill_key(item.get("path", ""))
        if not key:
            continue
        if key in merged:
            merged[key].update(item)
        else:
            merged[key] = item
    return list(merged.values())


def get_config_status() -> Dict[str, Any]:
    cfg, _text, err = _load_config()
    if err:
        return {
            "ok": False,
            "error": f"config.toml 当前无法解析：{err}",
            "config_path": str(CODEX_CONFIG),
            "mcps": {},
        }

    skills_cfg = _dedupe_skill_config((cfg.get("skills") or {}).get("config") or [])
    disabled_ids = {
        _skill_key(item.get("path", ""))
        for item in skills_cfg
        if item.get("enabled") is False
    }

    mcp_servers = cfg.get("mcp_servers") or {}
    status: Dict[str, Any] = {}
    for item in MANAGED_MODULES:
        sid = item["id"]
        server = mcp_servers.get(sid)
        mcp_registered = isinstance(server, dict)
        mcp_enabled = bool(server and server.get("enabled", True))

        sk_id = item.get("skill_id")
        skill_registered = False
        skill_enabled = False
        if sk_id:
            sk_file = CODEX_SKILLS / sk_id / "SKILL.md"
            skill_registered = sk_file.is_file()
            skill_enabled = skill_registered and sk_id.lower() not in disabled_ids

        status[sid] = {
            **item,
            "mcp_registered": mcp_registered,
            "mcp_enabled": mcp_enabled,
            "skill_registered": skill_registered,
            "skill_enabled": skill_enabled,
        }

    return {
        "ok": True,
        "config_path": str(CODEX_CONFIG),
        "mcps": status,
        "config_valid": True,
    }


def _set_mcp_enabled(text: str, server_id: str, enabled: bool) -> tuple[str, str | None]:
    pattern = rf'(\[mcp_servers\.{re.escape(server_id)}\])([\s\S]*?)(?=\n\[|$)'
    m = re.search(pattern, text)
    if not m:
        return text, f"配置文件中未登记 [mcp_servers.{server_id}]"
    header, body = m.group(1), m.group(2)
    val = str(enabled).lower()
    if re.search(r"^\s*enabled\s*=\s*(?:true|false)", body, re.M):
        new_body = re.sub(
            r"(^\s*enabled\s*=\s*)(?:true|false)",
            rf"\g<1>{val}",
            body,
            flags=re.M,
        )
    else:
        new_body = f"\nenabled = {val}" + body
    return text[:m.start()] + header + new_body + text[m.end():], None


def toggle_mcp(server_id: str, enabled: bool) -> Dict[str, Any]:
    cfg, text, err = _load_config()
    if err:
        return {"ok": False, "error": f"config.toml 当前无法解析，拒绝写入：{err}"}
    if server_id not in (cfg.get("mcp_servers") or {}):
        return {"ok": False, "error": f"配置文件中未登记 [mcp_servers.{server_id}]"}
    new_text, err2 = _set_mcp_enabled(text, server_id, enabled)
    if err2:
        return {"ok": False, "error": err2}
    result = _atomic_write(new_text, f"toggle_mcp:{server_id}:{enabled}")
    if not result.get("ok"):
        return result
    return {"ok": True, "server_id": server_id, "enabled": enabled, **result}


def _set_skill_enabled(text: str, cfg: dict, skill_id: str, enabled: bool, target_path: str) -> str:
    skills_cfg = _dedupe_skill_config((cfg.get("skills") or {}).get("config") or [])
    key = _skill_key(target_path) or skill_id.lower()
    found = False
    for item in skills_cfg:
        if _skill_key(item.get("path", "")) == key:
            item["path"] = target_path
            item["enabled"] = bool(enabled)
            found = True
            break
    if not found:
        skills_cfg.append({"path": target_path, "enabled": bool(enabled)})
    skills_cfg = _dedupe_skill_config(skills_cfg)

    config_line = "config = [" + ", ".join(_inline_table(x) for x in skills_cfg) + "]\n"

    m_sec = re.search(r"(\[skills\]\s*\n)([\s\S]*?)(?=\n\[|\Z)", text)
    if not m_sec:
        return text.rstrip() + "\n\n[skills]\n" + config_line

    body = m_sec.group(2)
    m_cfg = re.search(r"(?ms)^(\s*config\s*=)([\s\S]*?)(?=^\s*[A-Za-z0-9_\-]+\s*=|\Z)", body)
    if m_cfg:
        new_body = body[:m_cfg.start()] + config_line + body[m_cfg.end():]
    else:
        new_body = body.rstrip() + "\n" + config_line
    return text[:m_sec.start(2)] + new_body + text[m_sec.end(2):]


def toggle_skill(skill_id: str, enabled: bool) -> Dict[str, Any]:
    cfg, text, err = _load_config()
    if err:
        return {"ok": False, "error": f"config.toml 当前无法解析，拒绝写入：{err}"}

    sk_dir = CODEX_SKILLS / skill_id
    sk_file = sk_dir / "SKILL.md"
    src_dir = ROOT / "skills" / skill_id
    if not sk_file.is_file():
        if src_dir.is_dir():
            sk_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src_dir, sk_dir, dirs_exist_ok=True)
        else:
            return {"ok": False, "error": f"Skill 源目录不存在: {src_dir}"}

    target_path = str(sk_file).replace("/", "\\")
    new_text = _set_skill_enabled(text, cfg, skill_id, enabled, target_path)
    result = _atomic_write(new_text, f"toggle_skill:{skill_id}:{enabled}")
    if not result.get("ok"):
        return result
    return {"ok": True, "skill_id": skill_id, "enabled": enabled, "path": target_path, **result}
