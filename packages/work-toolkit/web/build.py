#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build.py — 把工具清单与本机真实数据注入模板，生成单文件页面 web/console.html。

    python mcp/dump_tools.py > web/tools.json   # 工具增删后刷新一次
    python web/build.py                         # 重新生成页面

页面自包含（无外链、无字体、无图片），双击可开；
想点「在本机跑一遍」就改用 `python web/serve.py`（本地起服务 + 自动开浏览器）。
"""

import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import facts  # noqa: E402

TEMPLATE = HERE / "_console_template.html"
TOOLS = HERE / "tools.json"
CUSTOM = HERE / "custom_nodes.json"
OUTPUT = HERE / "console.html"


def _ascii_safe_text(value: object) -> str:
    """Keep injected JSON free of emoji and decorative Unicode icon glyphs."""
    s = str(value)
    for src, dst in (("→", "->"), ("←", "<-"), ("↑", "^"), ("↓", "v"), ("↻", ""), ("⚠", "[warn] ")):
        s = s.replace(src, dst)
    return "".join(
        ch for ch in s
        if not (0x1F300 <= ord(ch) <= 0x1FAFF or 0x2600 <= ord(ch) <= 0x27BF)
    )


def _ascii_safe(value: object) -> object:
    if isinstance(value, str):
        return _ascii_safe_text(value)
    if isinstance(value, list):
        return [_ascii_safe(x) for x in value]
    if isinstance(value, dict):
        return {_ascii_safe_text(k): _ascii_safe(v) for k, v in value.items()}
    return value


def load_custom() -> dict:
    """用户自定义节点。文件不存在或写坏了都不该让构建失败——
    退回空配置，把问题打出来就好。"""
    if not CUSTOM.is_file():
        return {"groups": []}
    try:
        raw = json.loads(CUSTOM.read_text(encoding="utf-8"))
        groups = [g for g in (raw.get("groups") or []) if isinstance(g, dict)]
        return {"groups": groups}
    except Exception as e:
        print(f"[warn] custom_nodes.json 读不了（{e}），本次按空配置构建")
        return {"groups": []}


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    tools = json.loads(TOOLS.read_text(encoding="utf-8"))
    tpl = TEMPLATE.read_text(encoding="utf-8")

    for ph in ("__TOOLS__", "__TOOLS_META__", "__FACTS__", "__CUSTOM__"):
        if ph not in tpl:
            raise SystemExit(f"模板里找不到占位符 {ph}")

    names = "[" + ",".join(json.dumps(_ascii_safe_text(t["name"]), ensure_ascii=False) for t in tools) + "]"
    meta = _ascii_safe([{"name": t["name"], "description": t.get("description", ""),
             "module": t.get("module", "")} for t in tools])
    f = _ascii_safe(facts.collect())
    custom = _ascii_safe(load_custom())
    html = tpl.replace("__TOOLS__", names)
    html = html.replace("__TOOLS_META__", json.dumps(meta, ensure_ascii=False))
    html = html.replace("__FACTS__", json.dumps(f, ensure_ascii=False, indent=2))
    html = html.replace("__CUSTOM__", json.dumps(custom, ensure_ascii=False))
    # newline="\n"：Windows 上 write_text 默认会把 \n 转成 \r\n，
    # 产物凭空多出两三千字节，还会让上面按 html 算的体积对不上文件。
    OUTPUT.write_text(html, encoding="utf-8", newline="\n")

    added = sum(len(g.get("nodes") or []) for g in custom["groups"])
    # 按字节算：len(str) 是字符数，中文页面会少报两成，看着像内容丢了
    kb = len(html.encode("utf-8")) / 1024
    print(f"已生成 {OUTPUT.name} · {kb:.0f} KB · {len(tools)} 个工具已登记"
          + (f" · 自定义节点 {added} 个" if added else ""))
    print("环境快照（web/facts.py 采集，10 分钟缓存）：")
    print(f"项目 {f.get('project') or '未知'} · 微信 {f.get('wechat_version') or '未知'} · 收件 {f.get('inbox_files')} 个文件 "
          f"{f.get('inbox_gb')} GB · 只读副本 {f.get('cache_dbs')} 个 · OfficeCLI {f.get('officecli')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
