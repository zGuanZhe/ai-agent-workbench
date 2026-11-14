#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""setup.py — 别人下载这个项目之后，跑这一条就能用。

    scripts\\setup.cmd          （双击 / 命令行都行）
    python scripts/setup.py     （等价）
    python scripts/setup.py --check   （只体检，不改任何东西）

顺序：挑解释器 → 装缺失依赖 → 注册 MCP（可选增强） → 构建网页（可选展示） →
同步总 Skill（按内容比对，自动更新并清理旧命令） → 跑工具、本地桥接、意图路由验收。
只用标准库写，因此哪怕 PATH 里的 python 一个依赖都没有，这个脚本也能跑起来，
并且会自己找到/准备好合适的解释器。

日常使用不依赖 MCP 信任、也不依赖网页：`scripts/work.py` 会在本机直接调工具，
WorkBuddy 装好总 Skill 后只需 `/work <需求>`；`/work` 第一步由 `ask` 把口语翻成
确定的工具计划，避免智能体猜工具或跳过工具。

安全边界：只 pip install 固定白名单里的包，不执行项目外的命令；
写 ~/.workbuddy/mcp.json 时合并而非覆盖，解析失败先备份；
装 Skills 时按内容比对同步，覆盖前整体备份到 .bak-<时间戳>。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import doctor  # noqa: E402
import install_skills  # noqa: E402
import register_mcp  # noqa: E402

# 只装真正会被工具箱 import 的包。pandas / PyMuPDF 体积大且用不上，
# 缺了只影响可选功能，不进白名单（包体与安装时间都要省）。
PIP = {
    "mcp": "mcp",
    "docx": "python-docx",
    "openpyxl": "openpyxl",
    "pypdf": "pypdf",
    "PIL": "Pillow",
    "comtypes": "comtypes",
    "win32com": "pywin32",
}
REQUIRED = ("mcp", "docx", "openpyxl")


def _say(msg: str) -> None:
    print(msg, flush=True)


def _version_key(r: dict):
    try:
        return tuple(int(x) for x in (r.get("version") or "0").split("."))
    except Exception:
        return (0,)


def choose_target() -> tuple[str | None, list[dict], dict]:
    """优先用已经装齐的；一个都没有时，挑版本最高的可运行解释器，往里装。"""
    best, reports = doctor.find_best()
    target = best
    if not target:
        usable = [r for r in reports if r.get("ok") and not r.get("error")]
        if usable:
            usable.sort(key=_version_key, reverse=True)
            target = usable[0]["path"]
    info = doctor.probe(target) if target else {"deps": {}, "version": "", "path": ""}
    return target, reports, info


def install(target: str, info: dict, do_install: bool) -> list[dict]:
    missing = [n for n, pkg in PIP.items() if not info["deps"].get(n)]
    if not missing:
        _say("  依赖齐全，跳过安装")
        return []
    if not do_install:
        _say(f"  缺 {len(missing)} 个包（--no-install，只列出来）：{', '.join(missing)}")
        return [{"name": n, "status": "skipped"} for n in missing]

    _say(f"  正在装 {len(missing)} 个包：{', '.join(PIP[n] for n in missing)}")
    result = []
    for n in missing:
        pkg = PIP[n]
        try:
            r = subprocess.run(
                [target, "-m", "pip", "install", "--disable-pip-version-check", "--quiet", pkg],
                capture_output=True, text=True, timeout=240,
                encoding="utf-8", errors="replace",
            )
            ok = r.returncode == 0
            result.append({"name": n, "package": pkg, "status": "ok" if ok else "failed",
                           "detail": "" if ok else ((r.stderr or "").strip().splitlines() or [""])[-1][:180]})
            _say(f"    {'✅' if ok else '⚠️ '} {pkg}" + ("" if ok else "（失败，看 --json 详情）"))
        except Exception as exc:
            result.append({"name": n, "package": pkg, "status": "failed",
                           "detail": f"{type(exc).__name__}: {exc}"})
            _say(f"    ⚠️  {pkg} 失败")
    return result


def verify(target: str) -> list[dict]:
    checks = [
        ("work_status", {}),
        ("work_exp", {"action": "list"}),
        ("work_wechat_probe", {}),
    ]
    out = []
    for name, args in checks:
        try:
            r = subprocess.run([target, str(ROOT / "tools" / "run_tool.py"),
                                name, json.dumps(args, ensure_ascii=False)],
                               capture_output=True, text=True, timeout=120,
                               cwd=str(ROOT), encoding="utf-8", errors="replace")
            data = json.loads((r.stdout or "").strip() or "{}")
            ok = r.returncode == 0 and bool(data.get("ok"))
        except Exception as exc:
            data, ok = {"error": str(exc)}, False
        out.append({"tool": name, "ok": ok, "error": "" if ok else str(data.get("error", ""))[:160]})

    # 用户真正走的是总 Skill 的本地桥接：不验这一条，装完仍可能「网页能跑、/work 跑不了」。
    bridge = ROOT / "scripts" / "work.py"
    for label, argv in (
        ("work.py 本地桥接", ["call", "work_status", "{}"]),
        ("work.py 工具清单", ["tools", "work_exp"]),
    ):
        try:
            r = subprocess.run([sys.executable, str(bridge)] + argv,
                               capture_output=True, text=True, timeout=180,
                               cwd=str(ROOT), encoding="utf-8", errors="replace")
            data = json.loads((r.stdout or "").strip() or "{}")
            ok = r.returncode == 0 and (bool(data.get("ok")) if isinstance(data, dict)
                                        else bool(data))
            err = "" if ok else str(data.get("error", "") if isinstance(data, dict) else "")[:160]
        except Exception as exc:
            ok, err = False, str(exc)[:160]
        out.append({"tool": label, "ok": ok, "error": err})
    # 意图路由与只读保护：/work 的第一步，装完必须真能跑，否则智能体又会回去猜工具。
    for label, argv, judge in (
        ("work.py 意图路由", ["ask", "把 11 月报表收一下并合成汇总母表"],
         lambda d: d.get("matched") is True
         and {"work_office_consolidate", "work_wechat_files"}
         <= {p.get("tool") for p in d.get("plan", [])}),
        ("work.py 只读保护", ["ask", "把这个表汇总一下但别写文件"],
         lambda d: d.get("readonly_requested") is True and d.get("writes") is False
         and all(p.get("tool") != "work_office_consolidate" for p in d.get("plan", []))),
    ):
        try:
            r = subprocess.run([sys.executable, str(bridge)] + argv,
                               capture_output=True, text=True, timeout=180,
                               cwd=str(ROOT), encoding="utf-8", errors="replace")
            data = json.loads((r.stdout or "").strip() or "{}")
            ok = r.returncode == 0 and bool(judge(data))
            err = "" if ok else f"路由结果不符预期：{str(data)[:150]}"
        except Exception as exc:
            ok, err = False, str(exc)[:160]
        out.append({"tool": label, "ok": ok, "error": err})

    try:
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "check_intents.py"), "--json"],
                           capture_output=True, text=True, timeout=60,
                           cwd=str(ROOT), encoding="utf-8", errors="replace")
        rep = json.loads((r.stdout or "").strip() or "{}")
        ok = r.returncode == 0 and bool(rep.get("ok"))
        err = "" if ok else "；".join(rep.get("errors", [])[:2])[:160]
    except Exception as exc:
        ok, err = False, str(exc)[:160]
    out.append({"tool": "意图表校验", "ok": ok, "error": err})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="办公工具箱一键安装")
    ap.add_argument("--check", action="store_true", help="只体检，不装不写")
    ap.add_argument("--no-install", action="store_true", help="不执行 pip")
    ap.add_argument("--no-mcp", action="store_true", help="不改 WorkBuddy 的 mcp.json")
    ap.add_argument("--no-build", action="store_true", help="不重新生成 console.html")
    ap.add_argument("--no-skills", action="store_true", help="不安装 WorkBuddy Skills")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    rep: dict = {"steps": {}}
    if args.check:
        _say("  只体检，不改动任何文件")
        rep["doctor"] = doctor.run_all()
        rep["mcp"] = register_mcp.check()
        rep["ok"] = bool(rep["doctor"]["ok"]) and bool(rep["mcp"].get("ok"))
        if args.json:
            print(json.dumps(rep, ensure_ascii=False, indent=2))
        else:
            doctor.print_report(rep["doctor"])
            _say(f"  MCP 注册状态：{'已就绪' if rep['mcp'].get('ok') else rep['mcp'].get('error')}")
        return 0 if rep["ok"] else 1

    _say("\n  办公工具箱 · 一键准备\n  " + "-" * 46)

    # 1) 挑解释器
    _say("[1/6] 找装齐依赖的 Python …")
    target, reports, info = choose_target()
    if not target:
        rep["ok"] = False
        rep["error"] = "这台机器上没有可运行的 Python，先装 3.9+ 并勾选 Add to PATH"
        _say(f"  ❌ {rep['error']}")
        return 1
    _say(f"  → {target}  (Python {info.get('version') or '?'})")
    rep["python"] = target
    rep["interpreters"] = [{"path": r["path"], "version": r["version"], "ready": doctor.is_ready(r)}
                           for r in reports]

    # 2) 装依赖
    _say("[2/6] 检查 / 安装依赖 …")
    rep["steps"]["install"] = install(target, info, not args.no_install)
    after = doctor.probe(target)
    missing_req = [n for n in REQUIRED if not after["deps"].get(n)]
    if missing_req:
        _say(f"  ❌ 必需依赖仍缺：{', '.join(PIP[n] for n in missing_req)}")
        _say("     多半是没网或公司代理挡了 pip。手动装：")
        _say(f"     \"{target}\" -m pip install " + " ".join(PIP[n] for n in missing_req))
        rep["ok"] = False
        rep["missing_required"] = [PIP[n] for n in missing_req]
        if args.json:
            print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 1

    # 3) 注册 MCP
    _say("[3/6] 注册到 WorkBuddy 的 MCP …")
    if args.no_mcp:
        rep["steps"]["mcp"] = {"status": "skipped"}
        _say("  → 跳过（--no-mcp）")
    else:
        r = register_mcp.register(target)
        rep["steps"]["mcp"] = r
        _say(f"  → {'已写入 ' + r['config'] if r.get('ok') else '失败：' + str(r.get('error'))}")
        if r.get("ok"):
            _say(f"     已保留其他 MCP：{r.get('other_servers_kept') or '无'}")

    # 4) 构建网页
    _say("[4/6] 生成控制台页面 …")
    if args.no_build:
        rep["steps"]["build"] = {"status": "skipped"}
        _say("  → 跳过（--no-build）")
    else:
        try:
            b = subprocess.run([target, str(ROOT / "web" / "build.py")],
                               capture_output=True, text=True, timeout=180, cwd=str(ROOT),
                               encoding="utf-8", errors="replace")
            ok = b.returncode == 0
            rep["steps"]["build"] = {"ok": ok, "output": (b.stdout or "").strip()[:400]}
            _say(("  → " + (b.stdout or "").strip().splitlines()[0]) if ok else
                 "  ⚠️  构建失败：" + ((b.stderr or "").strip()[-200:]))
        except Exception as exc:
            rep["steps"]["build"] = {"ok": False, "error": str(exc)}
            _say(f"  ⚠️  构建失败：{exc}")

    # 5) 同步总 Skill：仓库里用 <WORK_ROOT> 占位，装的时候回填本机路径
    _say("[5/6] 同步 WorkBuddy 总 Skill …")
    if args.no_skills:
        rep["steps"]["skills"] = {"status": "skipped"}
        _say("  → 跳过（--no-skills）")
    else:
        try:
            s = install_skills.install(install_skills.DEFAULT_TARGET,
                                       force=False, check=False)
            rep["steps"]["skills"] = s
            if s.get("ok"):
                _say(f"  → 新装 {s['installed']} / 更新 {s['updated']} / 跳过 {s['skipped']}")
            else:
                _say(f"  ⚠️  {s.get('error')}")
        except Exception as exc:
            rep["steps"]["skills"] = {"ok": False, "error": str(exc)}
            _say(f"  ⚠️  Skills 同步失败：{exc}")

    # 6) 验收：工具本体 + 总 Skill 用的本地桥接
    _say("[6/6] 跑工具与本地桥接验收 …")
    rep["steps"]["verify"] = verify(target)
    good = sum(1 for v in rep["steps"]["verify"] if v["ok"])
    _say(f"  → {good}/{len(rep['steps']['verify'])} 通过"
         + ("" if good == len(rep["steps"]["verify"]) else
            "  " + ", ".join(v["tool"] + ":" + v["error"][:50]
                            for v in rep["steps"]["verify"] if not v["ok"])))

    final = doctor.run_all()
    rep["doctor"] = final
    rep["ok"] = bool(final["ok"]) and good == len(rep["steps"]["verify"])
    _say("\n  " + "-" * 46)
    if rep["ok"]:
        _say("  ✅ 准备完成。重载 WorkBuddy 会话后，直接输入 /work <需求>；")
        _say("     不用打开网页，也不用手动信任 MCP。网页只是可选的能力展示。")
    else:
        _say("  ⚠️  有项目没过，跑 python tools\\doctor.py 看逐项详情。")
    _say("")

    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
