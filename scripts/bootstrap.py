#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Install dependencies (--install) and verify all 5 tools in ai-agent-workbench."""

from __future__ import annotations
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

STEPS = [
    ("1/5 work-toolkit check_intents", [sys.executable, "-X", "utf8", str(ROOT / "packages/work-toolkit/tools/check_intents.py")]),
    ("2/5 work-toolkit check_nodes", [sys.executable, "-X", "utf8", str(ROOT / "packages/work-toolkit/tools/check_nodes.py")]),
    ("3/5 work-toolkit test_config_manager", [sys.executable, "-X", "utf8", str(ROOT / "packages/work-toolkit/tools/test_config_manager.py")]),
    ("4/5 instruct-unrestricted tests", [sys.executable, "-X", "utf8", str(ROOT / "packages/instruct-unrestricted/tests/test_unrestricted.py")]),
    ("5/5 modeltrace-attribution tests", [sys.executable, "-X", "utf8", str(ROOT / "packages/modeltrace-attribution/tests/test_modeltrace.py")]),
]


def install_all(with_wechat_venv: bool = True) -> int:
    for req in ("requirements-toolkit.txt", "requirements-core.txt"):
        req_path = ROOT / "scripts" / req
        print(f"\n=== Installing {req} ===", flush=True)
        rc = subprocess.call([sys.executable, "-m", "pip", "install", "-r", str(req_path)], cwd=str(ROOT))
        if rc != 0:
            return rc

    if with_wechat_venv:
        wc_dir = ROOT / "packages" / "work-toolkit" / "mcp_servers" / "wechat-chat-mcp"
        venv_dir = wc_dir / ".venv"
        venv_py = venv_dir / ("Scripts" if sys.platform == "win32" else "bin") / ("python.exe" if sys.platform == "win32" else "python")
        if wc_dir.is_dir():
            if not venv_py.is_file():
                print("\n=== Creating wechat-chat-mcp .venv ===", flush=True)
                rc = subprocess.call([sys.executable, "-m", "venv", str(venv_dir)], cwd=str(wc_dir))
                if rc != 0:
                    return rc
            print("\n=== Installing wechat-chat-mcp[mcp,media] into .venv ===", flush=True)
            rc = subprocess.call([str(venv_py), "-m", "pip", "install", "-e", ".[mcp,media]"], cwd=str(wc_dir))
            if rc != 0:
                return rc
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Bootstrap and verify ai-agent-workbench")
    ap.add_argument("--install", action="store_true", help="Install Python dependencies and build wechat-chat-mcp .venv")
    ap.add_argument("--skip-wechat-venv", action="store_true", help="Skip creating isolated .venv for wechat-chat-mcp when using --install")
    args = ap.parse_args()

    if args.install:
        rc = install_all(with_wechat_venv=not args.skip_wechat_venv)
        if rc != 0:
            return rc

    for label, cmd in STEPS:
        print(f"\n=== {label} ===", flush=True)
        rc = subprocess.call(cmd, cwd=str(ROOT))
        if rc != 0:
            print(f"[FAIL] {label} exited with {rc}", file=sys.stderr)
            return rc
    print("\n[OK] All verification suites passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
