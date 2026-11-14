#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verify all 5 tools in ai-agent-workbench."""

from __future__ import annotations
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

def main() -> int:
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
