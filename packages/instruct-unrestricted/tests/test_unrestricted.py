#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
破限控制中枢 (Unrestricted Core) 全面自动化回归测试套件
验证 Task 1.1 (状态热感知与完整性核验), Task 1.2 (Cursor/Windsurf 扩充), Task 1.3 (动态规则组装器)
"""

import os
import sys
import tempfile
import shutil
import subprocess
from pathlib import Path

# 确保 Windows 终端支持 UTF-8 输出
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

UNRESTRICTED_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = UNRESTRICTED_DIR.parent.parent
sys.path.insert(0, str(UNRESTRICTED_DIR))

from manager import UnrestrictedManager
from composer import DynamicRuleComposer
from engines import CursorEngine, WindsurfEngine, WorkBuddyEngine, TraeEngine

def log_pass(msg: str):
    print(f"[✓] {msg}")

def test_compilers_cli():
    print("[*] 1. 校验底层编译器 CLI 基础工具链...")
    compilers = [
        ("antigravity-instruct", UNRESTRICTED_DIR / "compilers" / "antigravity-instruct" / "agy-instruct.py", ["--status"]),
        ("claude-instruct", UNRESTRICTED_DIR / "compilers" / "claude-instruct" / "claude-instruct.py", ["--status"]),
        ("gpt-instruct", UNRESTRICTED_DIR / "compilers" / "gpt-instruct" / "codex-instruct-v2.py", ["--status"]),
    ]

    for name, script_path, test_args in compilers:
        assert script_path.exists(), f"编译器脚本缺失: {script_path}"
        cmd = [sys.executable, str(script_path)] + test_args
        res = subprocess.run(cmd, cwd=str(script_path.parent), capture_output=True, encoding="utf-8", errors="replace")
        log_pass(f"{name} CLI 自检成功 (ExitCode={res.returncode})")

    # 检查内置规约包完整性
    agy_std = UNRESTRICTED_DIR / "compilers" / "antigravity-instruct" / "packages" / "agy-v1-standard.md"
    claude_std = UNRESTRICTED_DIR / "compilers" / "claude-instruct" / "packages" / "claude-v1-standard.md"
    assert agy_std.exists(), "缺少 agy-v1-standard.md"
    assert claude_std.exists(), "缺少 claude-v1-standard.md"
    log_pass(f"核心破限规约包完备: {agy_std.name}, {claude_std.name}")

def test_dynamic_rule_composer():
    print("\n[*] 2. 校验 Task 1.3: 动态规则组装器 (Dynamic Rule Composer)...")
    options = DynamicRuleComposer.get_options()
    assert len(options["roles"]) >= 4, "角色定义不足 4 款"
    assert len(options["strictness_levels"]) >= 3, "严格度定义不足 3 款"
    assert len(options["targets"]) >= 7, "目标平台不足 7 款"
    log_pass(f"规约组装器选项完备: 角色={len(options['roles'])}, 严格度={len(options['strictness_levels'])}, 目标平台={len(options['targets'])}")

    # 组合测试 1: Cursor + Security Audit + Hyperdrive
    c1 = DynamicRuleComposer.compose(role="security_audit", strictness="hyperdrive", target="cursor", custom_notes="禁止触碰生产数据库")
    assert c1["success"] is True
    assert "composed:security_audit:hyperdrive" in c1["version"]
    assert len(c1["files"]) == 2  # .cursorrules + .cursor/rules/unrestricted.mdc
    # 验证 MDC frontmatter
    mdc = next(f for f in c1["files"] if f["rel_path"].endswith(".mdc"))
    assert mdc["content"].startswith("---"), "Cursor MDC 缺少 frontmatter"
    assert "alwaysApply: true" in mdc["content"]
    assert "禁止触碰生产数据库" in mdc["content"]
    log_pass("Cursor 角色化 MDC 规约组装通过 (含 YAML frontmatter 与自定义附则)")

    # 组合测试 2: Windsurf + Fullstack + Standard
    c2 = DynamicRuleComposer.compose(role="fullstack", strictness="standard", target="windsurf")
    assert c2["success"] is True
    assert len(c2["files"]) == 2
    log_pass("Windsurf 级联流规约组装通过")

def test_cursor_lifecycle_and_integrity():
    print("\n[*] 3. 校验 Task 1.1 & 1.2: Cursor 引擎生命周期与 SHA256 完整性热感知...")
    with tempfile.TemporaryDirectory() as tmp_dir:
        ws = Path(tmp_dir)
        engine = CursorEngine(UNRESTRICTED_DIR)

        # 3.1 初始未部署
        det0 = engine.detect(ws)
        assert det0["is_unrestricted"] is False
        assert det0["integrity"]["status"] == "clean"
        log_pass("初始状态正确识别为 CLEAN (未部署)")

        # 3.2 部署规约
        app_res = engine.apply(ws, version="composed", role="security_audit", strictness="hyperdrive")
        assert app_res["success"] is True
        assert (ws / ".cursorrules").exists()
        assert (ws / ".cursor" / "rules" / "unrestricted.mdc").exists()
        assert (ws / ".cursor" / ".cursor-instruct-state.json").exists()
        log_pass("Cursor 规约原子写入成功 (.cursorrules & .mdc & state)")

        # 3.3 校验 INTACT
        intact_res = engine.verify_integrity(ws)
        assert intact_res["status"] == "intact", f"期望 INTACT，实际得到: {intact_res['status']}"
        log_pass("完整性校验通过: 状态为 INTACT (无篡改)")

        # 3.4 模拟外部手动篡改 -> 校验 DRIFTED (Task 1.1 热感知)
        (ws / ".cursorrules").write_text("# 外部第三方篡改内容", encoding="utf-8")
        drift_res = engine.verify_integrity(ws)
        assert drift_res["status"] == "drifted", f"期望 DRIFTED，实际得到: {drift_res['status']}"
        drift_file = next(f for f in drift_res["files"] if f["rel_path"] == ".cursorrules")
        assert drift_file["status"] == "drifted"
        log_pass("热感知篡改成功: 状态即时变更为 DRIFTED (SHA256 不匹配)")

        # 3.5 模拟文件被删除 -> 校验 MISSING (Task 1.1 缺失感知)
        (ws / ".cursorrules").unlink()
        missing_res = engine.verify_integrity(ws)
        assert missing_res["status"] == "missing", f"期望 MISSING，实际得到: {missing_res['status']}"
        log_pass("热感知缺失成功: 状态即时变更为 MISSING (文件丢失)")

        # 3.6 重新部署覆盖恢复
        engine.apply(ws, version="cursor-v1-standard")
        assert engine.verify_integrity(ws)["status"] == "intact"
        log_pass("重新覆盖部署成功，状态重置为 INTACT")

        # 3.7 一键重置回滚
        reset_res = engine.reset(ws)
        assert reset_res["success"] is True
        assert not (ws / ".cursorrules").exists()
        assert not (ws / ".cursor" / "rules" / "unrestricted.mdc").exists()
        assert engine.verify_integrity(ws)["status"] == "clean"
        log_pass("一键回滚成功: 文件彻底清除，工作区恢复 CLEAN 基线")

def test_windsurf_lifecycle_and_integrity():
    print("\n[*] 4. 校验 Task 1.2: Windsurf (Codeium) 引擎生命周期与备份回滚...")
    with tempfile.TemporaryDirectory() as tmp_dir:
        ws = Path(tmp_dir)
        engine = WindsurfEngine(UNRESTRICTED_DIR)

        # 预先放置一个用户的旧规则
        (ws / ".windsurfrules").write_text("# 原有用户规约 1.0", encoding="utf-8")

        # 部署
        app_res = engine.apply(ws, version="composed", role="fullstack", strictness="standard")
        assert app_res["success"] is True
        assert (ws / ".windsurfrules.windsurf-backup").exists(), "原有文件未自动备份"
        assert engine.verify_integrity(ws)["status"] == "intact"
        log_pass("原有 .windsurfrules 成功生成 .windsurf-backup 物理快照并部署新规约")

        # 重置 -> 验证备份还原
        reset_res = engine.reset(ws)
        assert reset_res["success"] is True
        assert (ws / ".windsurfrules").exists(), "原有备份未自动还原"
        assert (ws / ".windsurfrules").read_text(encoding="utf-8") == "# 原有用户规约 1.0"
        assert not (ws / ".windsurfrules.windsurf-backup").exists()
        log_pass("一键回滚成功: 原有用户规约已从备份物理无损还原！")

def test_unrestricted_manager_and_cli():
    print("\n[*] 5. 校验 UnrestrictedManager 统一高内聚管理器与 CLI...")
    with tempfile.TemporaryDirectory() as tmp_dir:
        ws = Path(tmp_dir)
        data_dir = ws / "data"
        mgr = UnrestrictedManager(root_dir=UNRESTRICTED_DIR, data_dir=data_dir)

        # 全局概览
        overview = mgr.get_status_overview(ws)
        assert overview["total_apps"] == 7
        assert len(overview["apps"]) == 7
        log_pass(f"UnrestrictedManager 成功纳管 7 大 IDE 引擎 (工作区: {ws.name})")

        # 批量应用
        all_res = mgr.apply_all(ws, role="fullstack", strictness="standard")
        assert all_res["success"] is True
        log_pass("apply_all 批量部署完成")

        # 验证完整性
        int_res = mgr.verify_file_integrity(ws)
        assert "cursor" in int_res["apps"]
        assert "windsurf" in int_res["apps"]
        log_pass("verify_file_integrity 全局多应用并发核验通过")

        # 批量重置
        rst_res = mgr.reset_all(ws)
        assert rst_res["success"] is True
        log_pass("reset_all 批量回滚恢复基线完成")

    # CLI 命令行自测
    cli_py = UNRESTRICTED_DIR / "cli.py"
    res = subprocess.run([sys.executable, str(cli_py), "status", "--json"], capture_output=True, encoding="utf-8", errors="replace")
    assert res.returncode == 0, f"CLI status 失败: {res.stderr}"
    log_pass("CLI status --json 运行成功 (零延迟)")

    res_opt = subprocess.run([sys.executable, str(cli_py), "options"], capture_output=True, encoding="utf-8", errors="replace")
    assert res_opt.returncode == 0
    log_pass("CLI options 运行成功")

if __name__ == "__main__":
    print("=" * 72)
    print("  🚀 破限控制中枢 (Instruct Unrestricted Core) 升级自动化回归测试")
    print("=" * 72)
    test_compilers_cli()
    test_dynamic_rule_composer()
    test_cursor_lifecycle_and_integrity()
    test_windsurf_lifecycle_and_integrity()
    test_unrestricted_manager_and_cli()
    print("\n" + "=" * 72)
    print("  🎉 [ALL TESTS PASSED] 模块一：破限控制中枢与认知编译器全面校验通过！")
    print("=" * 72)
