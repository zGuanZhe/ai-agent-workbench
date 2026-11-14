# -*- coding: utf-8 -*-
"""
各编程软件破限控制中枢与多工作区动态管理器
Unrestricted / Jailbreak Controller - Real Multi-Workspace & 7-IDE Full Lifecycle Edition
"""

from __future__ import annotations
import sys
import json
import time
from pathlib import Path
from flask import jsonify, request

MODULE_ROOT = Path(__file__).resolve().parent.parent
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

try:
    from manager import UnrestrictedManager
    from composer import DynamicRuleComposer
except ImportError:
    from instruct_unrestricted_core.manager import UnrestrictedManager
    from instruct_unrestricted_core.composer import DynamicRuleComposer

def init_unrestricted_module(app, ROOT_DIR: Path, DATA_DIR: Path):
    MODULE_ROOT = Path(__file__).resolve().parent.parent
    mgr = UnrestrictedManager(root_dir=MODULE_ROOT, data_dir=DATA_DIR)

    @app.route("/api/unrestricted/workspaces", methods=["GET", "POST"])
    def api_unrestricted_workspaces():
        if request.method == "POST":
            req = request.get_json() or {}
            ws_param = req.get("workspace")
            if not ws_param:
                return jsonify({"success": False, "error": "缺少 workspace 参数"}), 400
            try:
                p = mgr.set_configured_workspace(ws_param)
                return jsonify({
                    "success": True,
                    "current_workspace": str(p),
                    "message": f"已成功切换当前受管工作区为: {p.name}",
                })
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 400
        else:
            current_ws = mgr.get_configured_workspace()
            candidates = mgr.get_workspace_candidates()
            return jsonify({
                "success": True,
                "current_workspace": str(current_ws),
                "candidates": candidates,
            })

    @app.route("/api/unrestricted/apps", methods=["GET"])
    def api_unrestricted_apps():
        ws_param = request.args.get("workspace")
        ws = mgr.resolve_workspace(ws_param)
        overview = mgr.get_status_overview(ws)
        return jsonify(overview)

    @app.route("/api/unrestricted/integrity", methods=["GET"])
    def api_unrestricted_integrity():
        """Task 1.1: 状态热感知与 SHA256 完整性核验"""
        ws_param = request.args.get("workspace")
        app_param = request.args.get("app")
        ws = mgr.resolve_workspace(ws_param)
        res = mgr.verify_file_integrity(ws, app_id=app_param if app_param else None)
        return jsonify(res)

    @app.route("/api/unrestricted/apply_app", methods=["POST"])
    def api_unrestricted_apply_app():
        req = request.get_json() or {}
        app_id = req.get("app")
        version = req.get("version", "")
        role = req.get("role", "fullstack")
        strictness = req.get("strictness", "standard")
        custom_content = req.get("custom_content", "")
        scope = req.get("scope", "workspace")
        ws_param = req.get("workspace")

        if not app_id:
            return jsonify({"success": False, "error": "缺少 app 参数"}), 400

        ws = mgr.resolve_workspace(ws_param)
        try:
            res = mgr.apply_app(
                app_id=app_id,
                ws=ws,
                version=version,
                role=role,
                strictness=strictness,
                custom_content=custom_content,
                scope=scope,
            )
            return jsonify(res), 200 if res.get("success") else 500
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/api/unrestricted/reset_app", methods=["POST"])
    def api_unrestricted_reset_app():
        req = request.get_json() or {}
        app_id = req.get("app")
        scope = req.get("scope", "workspace")
        ws_param = req.get("workspace")

        if not app_id:
            return jsonify({"success": False, "error": "缺少 app 参数"}), 400

        ws = mgr.resolve_workspace(ws_param)
        try:
            res = mgr.reset_app(app_id=app_id, ws=ws, scope=scope)
            return jsonify(res), 200 if res.get("success") else 500
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/api/unrestricted/apply_all", methods=["POST"])
    def api_unrestricted_apply_all():
        req = request.get_json() or {}
        ws_param = req.get("workspace")
        role = req.get("role", "fullstack")
        strictness = req.get("strictness", "standard")
        custom_content = req.get("custom_content", "")

        ws = mgr.resolve_workspace(ws_param)
        try:
            res = mgr.apply_all(
                ws=ws,
                role=role,
                strictness=strictness,
                custom_content=custom_content,
            )
            return jsonify(res)
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/api/unrestricted/reset_all", methods=["POST"])
    def api_unrestricted_reset_all():
        req = request.get_json() or {}
        ws_param = req.get("workspace")
        ws = mgr.resolve_workspace(ws_param)
        try:
            res = mgr.reset_all(ws=ws)
            return jsonify(res)
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/api/unrestricted/preview", methods=["GET"])
    def api_unrestricted_preview():
        app_id = request.args.get("app", "antigravity")
        version = request.args.get("version", "")
        role = request.args.get("role", "fullstack")
        strictness = request.args.get("strictness", "standard")
        ws_param = request.args.get("workspace")
        ws = mgr.resolve_workspace(ws_param)

        content = mgr.preview(
            app_id=app_id,
            ws=ws,
            version=version,
            role=role,
            strictness=strictness,
        )
        return jsonify({"success": True, "app": app_id, "version": version, "content": content})

    @app.route("/api/unrestricted/doctor", methods=["GET"])
    def api_unrestricted_doctor():
        ws_param = request.args.get("workspace")
        app_param = request.args.get("app")
        ws = mgr.resolve_workspace(ws_param)
        output_str = mgr.doctor(ws=ws, app_id=app_param if app_param else None)
        return jsonify({
            "success": True,
            "workspace": str(ws),
            "output": output_str,
        })

    # ── 动态组装端点 (Task 1.3) ──
    @app.route("/api/unrestricted/compose", methods=["POST"])
    def api_unrestricted_compose():
        req = request.get_json() or {}
        role = req.get("role", "fullstack")
        strictness = req.get("strictness", "standard")
        target = req.get("target", "cursor")
        custom_notes = req.get("custom_notes", "")
        res = mgr.compose_rule(role=role, strictness=strictness, target=target, custom_notes=custom_notes)
        return jsonify(res)

    @app.route("/api/unrestricted/compose/options", methods=["GET"])
    def api_unrestricted_compose_options():
        return jsonify(mgr.get_compose_options())

    # 兼容历史端点
    @app.route("/api/unrestricted/status", methods=["GET"])
    def api_unrestricted_status():
        ws_param = request.args.get("workspace")
        ws = mgr.resolve_workspace(ws_param)
        apps = mgr.get_apps(ws)
        unr_count = sum(1 for a in apps if a.get("is_unrestricted"))
        return jsonify({
            "success": True,
            "workspace": str(ws),
            "status": {
                "is_unrestricted": unr_count > 0,
                "unrestricted_count": unr_count,
                "total_apps": len(apps),
                "apps": apps,
            },
        })

    @app.route("/api/unrestricted/templates", methods=["GET"])
    def api_unrestricted_templates():
        return api_unrestricted_apps()

    @app.route("/api/unrestricted/apply", methods=["POST"])
    def api_unrestricted_apply():
        return api_unrestricted_apply_all()

    @app.route("/api/unrestricted/restore", methods=["POST"])
    def api_unrestricted_restore():
        return api_unrestricted_reset_all()
