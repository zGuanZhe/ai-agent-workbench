#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_intents.py — 校验 web/intents.json 的意图路由表。

意图表是 /work 第一步 ask 的依据，写错一个工具名就会让智能体调到一个
根本不存在的工具（表现为「功能触发不了」），所以改完必须跑这个：

    python tools/check_intents.py            # 人读结论
    python tools/check_intents.py --json     # 给 setup / doctor 用
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INTENTS = ROOT / "web" / "intents.json"
TOOLS = ROOT / "web" / "tools.json"
VALID_CONTROL = {"setup", "doctor", "web", "tools", "skill"}


def run() -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    if not INTENTS.is_file():
        return {"ok": False, "intents": 0, "errors": ["缺 web/intents.json"], "warnings": []}
    if not TOOLS.is_file():
        return {"ok": False, "intents": 0, "errors": ["缺 web/tools.json，先跑 python scripts/work.py tools"],
                "warnings": []}
    rules = json.loads(INTENTS.read_text(encoding="utf-8-sig"))
    known = {t["name"] for t in json.loads(TOOLS.read_text(encoding="utf-8-sig"))}
    items = rules.get("intents") or []
    if not items:
        errors.append("intents 是空的")

    ids = set()
    for it in items:
        iid = it.get("id") or "<缺 id>"
        if iid in ids:
            errors.append(f"意图 id 重复：{iid}")
        ids.add(iid)
        kws = it.get("keywords") or {}
        if not kws:
            errors.append(f"{iid}: keywords 为空，永远不会命中")
        for kw, w in kws.items():
            if not str(kw).strip():
                errors.append(f"{iid}: 有空关键词")
            if not isinstance(w, (int, float)) or w <= 0:
                errors.append(f"{iid}: 关键词「{kw}」权重必须为正数")
        ctrl = it.get("control")
        if ctrl and ctrl not in VALID_CONTROL:
            errors.append(f"{iid}: 未知 control={ctrl}")
        if not ctrl and not it.get("primary"):
            errors.append(f"{iid}: 既不是 control 也没有 primary")
        primary = it.get("primary")
        if primary:
            if not str(primary).startswith("work_") or primary not in known:
                errors.append(f"{iid}: primary={primary} 不是已注册工具")
            step_names = {str(s).split(":", 1)[0] for s in (it.get("steps") or [])}
            if primary not in step_names:
                warnings.append(f"{iid}: primary 没出现在 steps 里，plan 会漏掉首选工具")
        for step in it.get("steps") or []:
            tool = str(step).split(":", 1)[0]
            if tool not in known:
                errors.append(f"{iid}: step 引用了不存在的工具 {tool}")
        if it.get("writes") and not ctrl and not it.get("out_dir"):
            warnings.append(f"{iid}: 会写文件但没标 out_dir，智能体无法转述产物路径")
        if it.get("confirm") is False and it.get("writes") and primary in ("work_officecli_run",):
            warnings.append(f"{iid}: 写工具未标 confirm，确认是否真的可以直接跑")

    # 每条意图至少能被自己的某个关键词命中（防关键词写错导致永远不触发）
    for it in items:
        q = " ".join(str(k) for k in (it.get("keywords") or {})).lower()
        score = sum(w for kw, w in (it.get("keywords") or {}).items()
                    if str(kw).lower() in q)
        if score <= 0:
            errors.append(f"{it.get('id')}: 自测无法命中")

    if not (rules.get("channel_map") or {}):
        warnings.append("缺 channel_map，微信通道判据会没有统一口径")

    return {"ok": not errors, "intents": len(items), "tools": len(known),
            "errors": errors, "warnings": warnings}


def main() -> int:
    ap = argparse.ArgumentParser(description="校验意图路由表")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()
    rep = run()
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print(f"意图路由表：{rep['intents']} 条意图 / {rep.get('tools', 0)} 个工具")
        for e in rep["errors"]:
            print("  ✗ " + e)
        for w in rep["warnings"]:
            print("  ! " + w)
        print("  ✅ 全部合法" if rep["ok"] and not rep["warnings"] else
              ("  ✅ 无致命错误，有提示项" if rep["ok"] else "  ❌ 有问题，修完再装 Skill"))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
