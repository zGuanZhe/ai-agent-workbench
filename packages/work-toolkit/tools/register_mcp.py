#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""register_mcp.py — 把 work-toolkit 注册进 WorkBuddy 的 ~/.workbuddy/mcp.json。

只用标准库，所以「PATH 里的 python 什么依赖都没装」也能跑；它会自己挑一个
装齐依赖的解释器写进 command。这是唯一的 MCP 注册实现，setup.py 与
register-workbuddy-mcp.ps1 都调它，避免两份逻辑漂移。

    python tools/register_mcp.py               # 注册
    python tools/register_mcp.py --dry-run     # 只看会写成什么
    python tools/register_mcp.py --check       # 只看当前配置对不对
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import doctor  # noqa: E402

NAME = "work-toolkit"
SERVER = ROOT / "mcp" / "work_toolkit_server.py"
MODULAR_SERVERS = {
    "work-comms": ROOT / "mcp" / "work_comms_server.py",
    "work-office": ROOT / "mcp" / "work_office_server.py",
    "work-memory": ROOT / "mcp" / "work_memory_server.py",
}
CONFIG = Path(os.path.expanduser("~")) / ".workbuddy" / "mcp.json"


def entry(python: str, script: Path = SERVER) -> dict:
    return {
        "command": python,
        "args": ["-X", "utf8", str(script)],
        "env": {"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
    }


def pick_python() -> str | None:
    best, _ = doctor.find_best()
    return best


def _read_config() -> tuple[dict, str]:
    """返回 (配置, 状态说明)。解析失败自动备份，绝不覆盖原文件。"""
    if not CONFIG.is_file():
        return {"mcpServers": {}}, "新建配置"
    raw = ""
    try:
        raw = CONFIG.read_text(encoding="utf-8-sig")
        cfg = json.loads(raw)
        if not isinstance(cfg, dict):
            raise ValueError("顶层不是对象")
        cfg.setdefault("mcpServers", {})
        if not isinstance(cfg["mcpServers"], dict):
            raise ValueError("mcpServers 不是对象")
        return cfg, "合并已有配置"
    except Exception as exc:
        backup = CONFIG.with_name(CONFIG.name + ".bak_" + __import__("datetime").datetime.now().strftime("%Y%m%d_%H%M%S"))
        try:
            backup.write_bytes(CONFIG.read_bytes())
            note = f"原配置读不了（{exc}），已备份到 {backup.name}"
        except Exception:
            note = f"原配置读不了（{exc}），且备份失败"
        return {"mcpServers": {}}, note


def check() -> dict:
    if not CONFIG.is_file():
        return {"ok": False, "error": f"配置不存在：{CONFIG}", "fix": "跑 python tools/register_mcp.py"}
    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
        e = (cfg.get("mcpServers") or {}).get(NAME)
    except Exception as exc:
        return {"ok": False, "error": f"配置解析失败：{exc}"}
    if not e:
        return {"ok": False, "error": f"没有 {NAME} 这一项", "fix": "跑 python tools/register_mcp.py"}
    py = e.get("command", "")
    if not os.path.isfile(py):
        return {"ok": False, "error": f"command 指向的解释器不存在：{py}"}
    info = doctor.probe(py)
    return {
        "ok": doctor.is_ready(info) and Path(str((e.get("args") or [""])[-1])).is_file(),
        "config": str(CONFIG),
        "python": py,
        "version": info["version"],
        "deps_ready": doctor.is_ready(info),
        "server_exists": Path(str((e.get("args") or [""])[-1])).is_file(),
        "other_servers": [k for k in (cfg.get("mcpServers") or {}) if k != NAME],
    }


def register(python: str = "", dry_run: bool = False) -> dict:
    if not SERVER.is_file():
        return {"ok": False, "error": f"找不到 MCP 服务脚本：{SERVER}"}
    py = python or pick_python()
    if not py:
        return {"ok": False,
                "error": "找不到装齐 mcp / python-docx / openpyxl 的解释器，先装依赖",
                "fix": "pip install mcp python-docx openpyxl（或跑 scripts\\setup.cmd）"}
    info = doctor.probe(py)
    if not doctor.is_ready(info):
        return {"ok": False, "error": f"这个解释器缺必需依赖：{py}", "detail": info["deps"]}

    cfg, note = _read_config()
    existed = NAME in cfg["mcpServers"]
    before = dict(cfg["mcpServers"])
    cfg["mcpServers"][NAME] = entry(py, SERVER)
    for m_name, m_script in MODULAR_SERVERS.items():
        if m_script.is_file():
            cfg["mcpServers"][m_name] = entry(py, m_script)
    text = json.dumps(cfg, ensure_ascii=False, indent=4)

    if dry_run:
        return {"ok": True, "dry_run": True, "note": note, "would_write": str(CONFIG),
                "python": py, "content": text}

    try:
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        # 无 BOM 的 UTF-8：Node 的 JSON.parse 吃到 BOM 会直接失败
        CONFIG.write_bytes(text.encode("utf-8"))
    except OSError as exc:
        return {"ok": False, "error": f"写配置失败：{exc}"}

    changed = before != cfg["mcpServers"]
    return {
        "ok": True, "config": str(CONFIG), "python": py, "version": info["version"],
        "action": "updated" if existed else "added", "changed": changed,
        "note": note,
        "other_servers_kept": [k for k in cfg["mcpServers"] if k != NAME],
        "next": "可选：其他 MCP 客户端或想让工具列表直接出现 work_* 时，在 WorkBuddy 连接器/MCP 管理点信任；/work 本地桥接不需要这一步",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="注册 work-toolkit MCP 服务")
    ap.add_argument("--python", default="", help="指定解释器绝对路径")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--check", action="store_true", help="只检查，不写")
    args = ap.parse_args()

    r = check() if args.check else register(args.python, args.dry_run)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
