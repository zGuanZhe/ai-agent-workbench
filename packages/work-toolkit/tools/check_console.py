#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Static checks for the generated console page."""

from __future__ import annotations

import pathlib
import sys
import unicodedata

ROOT = pathlib.Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
TEMPLATE = WEB / "_console_template.html"
OUTPUT = WEB / "console.html"

PLACEHOLDERS = ("__TOOLS__", "__TOOLS_META__", "__FACTS__", "__CUSTOM__")
TEMPLATE_FAKE_STRINGS = (
    "47 工具",
    "18 工具",
    "5 篇",
    "261 文件",
    "Local Python 3.14",
    "本地服务 8765 已连接",
    "完全访问",
    "已全部启用",
    "MCP/Skill 架构",
)
HARD_FAKE_STRINGS = (
    "47 工具",
    "18 工具",
    "5 篇",
    "261 文件",
    "Local Python 3.14",
    "本地服务 8765 已连接",
    "完全访问",
    "已全部启用",
    "MCP/Skill 架构",
)
BANNED_CODEPOINTS = set(range(0x1F300, 0x1FB00)) | set(range(0x2600, 0x27C0)) | {
    ord("↻"), ord("↑"), ord("→"), ord("←"), ord("✕"), ord("✓"), ord("✔"), ord("●"), ord("○")
}


def _glyphs(text: str) -> list[tuple[str, str, str]]:
    out = []
    for ch in text:
        if ord(ch) in BANNED_CODEPOINTS:
            out.append((ch, hex(ord(ch)), unicodedata.name(ch, "unknown")))
    return out


def main() -> int:
    errors: list[str] = []
    if not TEMPLATE.is_file():
        errors.append(f"模板不存在: {TEMPLATE}")
    if not OUTPUT.is_file():
        errors.append(f"生成页不存在: {OUTPUT}，先运行 web/build.py")

    if TEMPLATE.is_file():
        tpl = TEMPLATE.read_text(encoding="utf-8")
        for ph in PLACEHOLDERS:
            if ph not in tpl:
                errors.append(f"模板缺少占位符 {ph}")
        for needle in ("file1", "file2", "days:"):
            if needle in tpl:
                errors.append(f"模板仍含错误参数 {needle}")
        for cls in ("pill-blue", "pill-green", "pill-purple", "pill-amber", "pill-red"):
            if cls in tpl:
                errors.append(f"模板仍含装饰性彩色徽章类 {cls}")
        glyphs = _glyphs(tpl)
        if glyphs:
            errors.append(f"模板含表情或图标字符: {glyphs[:8]}")
        for fake in TEMPLATE_FAKE_STRINGS:
            if fake in tpl:
                errors.append(f"模板含静态文案: {fake}")

    if OUTPUT.is_file():
        page = OUTPUT.read_text(encoding="utf-8")
        for ph in PLACEHOLDERS:
            if ph in page:
                errors.append(f"生成页仍含未替换占位符 {ph}")
        glyphs = _glyphs(page)
        if glyphs:
            errors.append(f"生成页含表情或图标字符: {glyphs[:8]}")
        for fake in HARD_FAKE_STRINGS:
            if fake in page:
                errors.append(f"生成页含静态文案: {fake}")
        if "work_office_diff" in page and "file_a" not in page:
            errors.append("work_office_diff 未使用 file_a")
        if "work_office_diff" in page and "file_b" not in page:
            errors.append("work_office_diff 未使用 file_b")
        if "work_wechat_digest" in page and "days:" in page:
            errors.append("work_wechat_digest 仍使用 days 参数")

    if errors:
        for item in errors:
            print("[fail]", item)
        return 1
    print("console static checks: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
