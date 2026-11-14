#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unrestricted CLI - 破限控制中枢统一命令行工具
Unified CLI for AI Agent in-process or out-of-process zero-latency invocation.
"""

from __future__ import annotations
import argparse
import json
import os
import sys
from pathlib import Path

# 确保 Windows 终端支持 UTF-8
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

MODULE_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(MODULE_ROOT))

from manager import UnrestrictedManager
from composer import DynamicRuleComposer

def main() -> int:
    # 支持全局与子命令复用 --json 与 --workspace
    common_parser = argparse.ArgumentParser(add_help=False)
    common_parser.add_argument("--workspace", "-w", type=str, default="", help="指定目标工作区路径")
    common_parser.add_argument("--json", action="store_true", help="以结构化 JSON 输出结果")

    parser = argparse.ArgumentParser(
        prog="instruct-unrestricted",
        description="AI 编程环境破限控制中枢与沙箱认知编译器 (7 大 IDE 全托管)",
        parents=[common_parser],
    )

    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # 1. status
    p_status = subparsers.add_parser("status", help="查看所有受控 IDE 破限状态与工作区元数据", parents=[common_parser])

    # 2. verify (Task 1.1)
    p_verify = subparsers.add_parser("verify", help="核验工作区受管规约文件完整性与哈希一致性 (SHA256)", parents=[common_parser])
    p_verify.add_argument("--app", "-a", type=str, default="", help="指定待核验应用 ID (缺省则核验全平台)")

    # 3. apply
    p_apply = subparsers.add_parser("apply", help="在目标工作区部署破限规约", parents=[common_parser])
    p_apply.add_argument("--app", "-a", type=str, default="all", help="目标应用 (cursor/windsurf/antigravity/claude/codex/workbuddy/trae/all)")
    p_apply.add_argument("--version", "-v", type=str, default="", help="指定规约版本 (如 composed, agy-v1-standard 等)")
    p_apply.add_argument("--role", "-r", type=str, default="fullstack", help="专业角色 (fullstack/security_audit/automation_data/minimal_fix)")
    p_apply.add_argument("--strictness", "-s", type=str, default="standard", help="严格度 (standard/hyperdrive/strict_safe)")
    p_apply.add_argument("--custom", "-c", type=str, default="", help="开发者自定义规约文本")

    # 4. reset
    p_reset = subparsers.add_parser("reset", help="一键无损回滚规约，恢复原生默认状态", parents=[common_parser])
    p_reset.add_argument("--app", "-a", type=str, default="all", help="目标应用 (或 all)")

    # 5. doctor
    p_doctor = subparsers.add_parser("doctor", help="运行环境健康度与规约挂载诊断", parents=[common_parser])
    p_doctor.add_argument("--app", "-a", type=str, default="", help="指定诊断应用 ID")

    # 6. compose (Task 1.3)
    p_compose = subparsers.add_parser("compose", help="动态组装规约片段生成预览", parents=[common_parser])
    p_compose.add_argument("--role", "-r", type=str, default="fullstack", help="专业角色")
    p_compose.add_argument("--strictness", "-s", type=str, default="standard", help="严格度")
    p_compose.add_argument("--target", "-t", type=str, default="cursor", help="目标 IDE (cursor/windsurf/claude/antigravity/codex/workbuddy/trae)")
    p_compose.add_argument("--custom", "-c", type=str, default="", help="自定义规约附则")
    p_compose.add_argument("--out", "-o", type=str, default="", help="将组装结果写入指定文件")

    # 7. options
    p_options = subparsers.add_parser("options", help="列出动态组装支持的所有角色、严格度与目标平台", parents=[common_parser])

    args = parser.parse_args()
    mgr = UnrestrictedManager(root_dir=MODULE_ROOT)

    ws = mgr.resolve_workspace(args.workspace)

    if not args.command or args.command == "status":
        overview = mgr.get_status_overview(ws)
        if args.json:
            print(json.dumps(overview, ensure_ascii=False, indent=2))
        else:
            print("=" * 72)
            print("  ⚡ 破限控制中枢与认知编译器 (Unrestricted Controller)")
            print(f"  当前工作区: {overview['workspace']}")
            print(f"  受控应用数: {overview['total_apps']} | 破限已激活: {overview['unrestricted_count']} | 原生基线: {overview['clean_count']}")
            print(f"  完整性统计: 完整(Intact)={overview['integrity_summary']['intact']} | 漂移(Drifted)={overview['integrity_summary']['drifted']} | 缺失(Missing)={overview['integrity_summary']['missing']}")
            print("=" * 72)
            for app in overview["apps"]:
                st_icon = "[✓ 激活]" if app["is_unrestricted"] else "[○ 原生]"
                int_st = app.get("integrity", {}).get("status", "clean").upper()
                print(f"  {st_icon} {app['name']:<30} 版本: {app['active_version']:<25} 状态: [{int_st}]")
        return 0

    elif args.command == "verify":
        res = mgr.verify_file_integrity(ws, app_id=args.app if args.app else None)
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=2))
        else:
            print(f"=== 规约完整性核验 (Workspace: {res['workspace']}) ===")
            if args.app:
                print(f"应用: {args.app} | 状态: {res.get('status', '').upper()} | {res.get('summary', '')}")
                for f in res.get("files", []):
                    print(f"  - {f['rel_path']}: [{f['status'].upper()}] SHA256={f.get('actual_sha256', '')[:12]}...")
            else:
                for aid, rep in res.get("apps", {}).items():
                    print(f"[{aid}] 状态: {rep.get('status', '').upper()} - {rep.get('summary', '')}")
        return 0

    elif args.command == "apply":
        if args.app == "all":
            res = mgr.apply_all(ws, role=args.role, strictness=args.strictness, custom_content=args.custom)
        else:
            res = mgr.apply_app(
                args.app,
                ws,
                version=args.version,
                role=args.role,
                strictness=args.strictness,
                custom_content=args.custom,
            )
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=2))
        else:
            print(res.get("message") or f"已完成 {args.app} 部署")
        return 0 if res.get("success") else 1

    elif args.command == "reset":
        if args.app == "all":
            res = mgr.reset_all(ws)
        else:
            res = mgr.reset_app(args.app, ws)
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=2))
        else:
            print(res.get("message") or f"已完成 {args.app} 恢复原生默认")
        return 0 if res.get("success") else 1

    elif args.command == "doctor":
        doc_str = mgr.doctor(ws, app_id=args.app if args.app else None)
        if args.json:
            print(json.dumps({"success": True, "workspace": str(ws), "output": doc_str}, ensure_ascii=False))
        else:
            print(doc_str)
        return 0

    elif args.command == "compose":
        comp = mgr.compose_rule(role=args.role, strictness=args.strictness, target=args.target, custom_notes=args.custom)
        if args.out:
            Path(args.out).write_text(comp["content"], encoding="utf-8")
            print(f"[✓] 组装结果已写入: {args.out}")
        elif args.json:
            print(json.dumps(comp, ensure_ascii=False, indent=2))
        else:
            print(comp["content"])
        return 0

    elif args.command == "options":
        opts = mgr.get_compose_options()
        print(json.dumps(opts, ensure_ascii=False, indent=2))
        return 0

    return 0

if __name__ == "__main__":
    sys.exit(main())
