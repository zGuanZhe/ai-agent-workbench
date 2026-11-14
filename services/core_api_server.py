#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
独立微服务启动器 (Standalone Core Service Runner)
同时托管【破限控制中枢】与【模型路由归因测谎】两大核心能力
默认端点: http://127.0.0.1:5055
"""

from __future__ import annotations
import sys
import os
import json
from pathlib import Path
from flask import Flask, jsonify, request

# 确保控制台支持 UTF-8
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT_DIR = Path(__file__).resolve().parent.parent
INSTRUCT_CORE_DIR = ROOT_DIR / "packages" / "instruct-unrestricted"
MODELTRACE_CORE_DIR = ROOT_DIR / "packages" / "modeltrace-attribution"
DATA_DIR = ROOT_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# 注册动态模块路径
sys.path.insert(0, str(INSTRUCT_CORE_DIR / "controller"))
sys.path.insert(0, str(MODELTRACE_CORE_DIR / "controller"))
sys.path.insert(0, str(MODELTRACE_CORE_DIR / "engine"))

from unrestricted_controller import init_unrestricted_module
from modeltrace_controller import init_modeltrace_module

app = Flask(__name__)

# 1. 注册核心模块
init_unrestricted_module(app, ROOT_DIR=INSTRUCT_CORE_DIR, DATA_DIR=DATA_DIR)
init_modeltrace_module(app, MT_CORE_DIR=MODELTRACE_CORE_DIR, DATA_DIR=DATA_DIR)

# 2. 根与健康检查端点
@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "service": "AI Core Modules Standalone Service (Jailbreak & ModelTrace)",
        "version": "2.0.0-upgrade-pack",
        "status": "online",
        "endpoints": {
            "unrestricted": {
                "apps": "/api/unrestricted/apps",
                "workspaces": "/api/unrestricted/workspaces",
                "apply_app": "POST /api/unrestricted/apply_app",
                "reset_app": "POST /api/unrestricted/reset_app",
                "apply_all": "POST /api/unrestricted/apply_all",
                "reset_all": "POST /api/unrestricted/reset_all"
            },
            "modeltrace": {
                "info": "/api/modeltrace/info",
                "analyze": "POST /api/modeltrace/analyze",
                "test_api": "POST /api/modeltrace/test_api"
            }
        }
    })

@app.route("/health", methods=["GET"])
def health():
    bank_file = MODELTRACE_CORE_DIR / "data" / "unified_bank.json"
    bank_ok = bank_file.exists()
    return jsonify({
        "status": "healthy",
        "modules": {
            "instruct_unrestricted": "ready",
            "modeltrace_attribution": "ready" if bank_ok else "degraded (missing unified_bank.json)"
        }
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5055))
    print("=" * 72)
    print("  🚀 独立核心升级服务已启动 (Standalone Core Microservice)")
    print(f"  本地调试端口: http://127.0.0.1:{port}")
    print(f"  破限中枢接口: http://127.0.0.1:{port}/api/unrestricted/apps")
    print(f"  模型归因接口: http://127.0.0.1:{port}/api/modeltrace/info")
    print("=" * 72)
    app.run(host="127.0.0.1", port=port, debug=False)
