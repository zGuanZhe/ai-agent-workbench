#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把微信群/私聊的聊天记录整理成一份可存档的材料。

典型用途：群里发了一堆通知、要求、截屏，事后要把「这个群这周说了什么」变成一份
能进的材料——会议记录、通知汇总、督办台账。

输出格式：docx（默认，按公文习惯排版）或 md。
图片消息可以选择解密后嵌进正文（--include-media），因为工作群里大量关键信息是
截图，只留一行「[图片]」等于没记。

用法::

    python wx_digest.py --chat "老吴园区" --since 2026-11-01 --format docx
    python wx_digest.py --chat "软件工程2504（东山）" --keyword 值班 --limit 200
    python wx_digest.py --chat "某某工作群" --include-media --max-images 12
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "wechat"))

try:
    import wxread  # type: ignore
except Exception as exc:  # pragma: no cover
    print(json.dumps({"ok": False, "error": f"无法导入 wxread：{exc}"}, ensure_ascii=False))
    raise SystemExit(1)

try:
    import media  # type: ignore
except Exception:  # pragma: no cover
    media = None  # type: ignore

FONT_TITLE = "方正小标宋简体"
FONT_TITLE_FALLBACK = "华文中宋"
FONT_HEAD = "黑体"
FONT_BODY = "仿宋_GB2312"
FONT_BODY_FALLBACK = "仿宋"


def _set_font(run, name: str, size_pt: float, bold: bool = False):
    from docx.shared import Pt
    from docx.oxml.ns import qn

    run.font.name = name
    run.font.size = Pt(size_pt)
    run.font.bold = bold
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = rpr.makeelement(qn("w:rFonts"), {})
        rpr.append(rfonts)
    for attr in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        rfonts.set(qn(attr), name)


def _para(doc, text: str, font: str, size: float, bold=False,
          align=None, indent=True, space_after=0, line_pt=28):
    from docx.shared import Pt
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.space_before = Pt(0)
    pf.space_after = Pt(space_after)
    pf.line_spacing = Pt(line_pt)
    if indent:
        pf.first_line_indent = Pt(size * 2)
    if align is not None:
        p.alignment = align
    run = p.add_run(text)
    _set_font(run, font, size, bold)
    return p


def _fmt_day(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y年%m月%d日")


def _fmt_hm(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M")


def collect(chat: str, since: str, until: str, keyword: str, limit: int) -> dict:
    if keyword:
        res = wxread.search(keyword, limit=limit, chat=chat)
        if not isinstance(res, dict):
            res = json.loads(res)
        if not res.get("ok"):
            return res
        hits = res.get("results") or []
        msgs = [{
            "ts": h.get("ts") or wxread._parse_time(h.get("time", "")),
            "time": h.get("time", ""),
            "sender": h.get("sender", ""),
            "type": 1,
            "type_name": "文本",
            "text": h.get("text", ""),
            "raw": "",
            "local_id": h.get("local_id"),
        } for h in hits]
        msgs.sort(key=lambda m: m.get("ts") or 0)
        return {"ok": True, "chat": res.get("chat") or {"name": chat}, "messages": msgs}

    res = wxread.messages(chat, limit=limit, since=since, until=until)
    if not isinstance(res, dict):
        res = json.loads(res)
    return res


def render_docx(data: dict, title: str, out_path: Path, want_media: bool,
                max_images: int, meta: dict) -> dict:
    from docx import Document
    from docx.shared import Cm, Pt
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    doc = Document()
    sec = doc.sections[0]
    sec.top_margin = Cm(3.7)
    sec.bottom_margin = Cm(3.5)
    sec.left_margin = Cm(2.8)
    sec.right_margin = Cm(2.6)
    style = doc.styles["Normal"]
    style.font.name = FONT_BODY
    style.font.size = Pt(16)

    _para(doc, title, FONT_TITLE, 22, align=WD_ALIGN_PARAGRAPH.CENTER,
          indent=False, space_after=12, line_pt=32)

    chat = data.get("chat") or {}
    chat_name = chat.get("name") or meta.get("chat") or ""
    intro = f"资料来源：微信会话「{chat_name}」；共 {len(data.get('messages') or [])} 条消息"
    if meta.get("span"):
        intro += f"；时间跨度 {meta['span']}"
    _para(doc, intro, FONT_BODY, 14, indent=False, space_after=10)

    images = 0
    media_errors = []
    acc = meta.get("account") or ""
    media_dir = Path(meta.get("media_dir") or (out_path.parent / (out_path.stem + "_图片")))

    day = None
    for m in data.get("messages") or []:
        ts = m.get("ts") or 0
        this_day = _fmt_day(ts) if ts else "未知日期"
        if this_day != day:
            day = this_day
            _para(doc, day, FONT_HEAD, 16, indent=False, space_after=6, line_pt=28)

        who = m.get("sender") or "（未知）"
        head = f"{_fmt_hm(ts)}　{who}" if ts else who
        p = doc.add_paragraph()
        pf = p.paragraph_format
        pf.space_before = Pt(0)
        pf.space_after = Pt(0)
        pf.line_spacing = Pt(28)
        pf.first_line_indent = Pt(32)
        r1 = p.add_run(head + "：")
        _set_font(r1, FONT_HEAD, 14, bold=False)
        body = (m.get("text") or "").strip()
        r2 = p.add_run(body)
        _set_font(r2, FONT_BODY, 16)

        # 图片消息：解出来嵌进正文，否则一行「[图片]」等于没记
        if want_media and media is not None and acc and (m.get("type") == 3) and images < max_images:
            ref = m.get("media_ref") or ""
            if ref:
                try:
                    got = media.export_one(acc, ref, str(media_dir), prefer="thumb")
                    if got.get("ok"):
                        ip = doc.add_paragraph()
                        ip.alignment = WD_ALIGN_PARAGRAPH.CENTER
                        ip.paragraph_format.space_after = Pt(6)
                        ip.add_run().add_picture(got["path"], width=Cm(8))
                        images += 1
                except Exception as exc:
                    media_errors.append(f"{ref}: {exc}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path))
    return {"images": images, "media_errors": media_errors}


def render_md(data: dict, title: str, out_path: Path) -> str:
    lines = [f"# {title}", ""]
    chat = data.get("chat") or {}
    lines.append(f"> 来源：微信会话「{chat.get('name') or ''}」")
    lines.append("")
    day = None
    for m in data.get("messages") or []:
        ts = m.get("ts") or 0
        this_day = _fmt_day(ts) if ts else "未知日期"
        if this_day != day:
            day = this_day
            lines.append(f"## {day}")
            lines.append("")
        who = m.get("sender") or "（未知）"
        stamp = _fmt_hm(ts) if ts else ""
        lines.append(f"- **{stamp} {who}**：{(m.get('text') or '').strip()}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(out_path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="把微信聊天记录整理成存档材料")
    ap.add_argument("--chat", required=True)
    ap.add_argument("--since", default="")
    ap.add_argument("--until", default="")
    ap.add_argument("--keyword", default="")
    ap.add_argument("--limit", type=int, default=300)
    ap.add_argument("--title", default="")
    ap.add_argument("--format", default="docx", choices=["docx", "md"])
    ap.add_argument("--output", default="")
    ap.add_argument("--include-media", action="store_true")
    ap.add_argument("--max-images", type=int, default=12)
    ap.add_argument("--account", default="")
    args = ap.parse_args(argv)

    data = collect(args.chat, args.since, args.until, args.keyword, args.limit)
    if not data.get("ok"):
        print(json.dumps({"ok": False, "error": data.get("error", "读取失败")},
                         ensure_ascii=False, indent=2))
        return 1

    msgs = data.get("messages") or []
    chat_name = (data.get("chat") or {}).get("name") or args.chat
    safe_chat = re.sub(r'[\\/:*?"<>|]', "_", chat_name)[:40]
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    title = args.title or f"{safe_chat}群消息摘录"
    span = ""
    stamps = [m.get("ts") for m in msgs if m.get("ts")]
    if stamps:
        span = f"{_fmt_day(min(stamps))} 至 {_fmt_day(max(stamps))}"

    if args.output:
        out = Path(args.output)
        if not out.is_absolute():
            out = (ROOT / args.output).resolve()
    else:
        out = ROOT / "workspace" / "03_汇总与成稿输出" / f"{title}_{stamp}.{args.format}"

    meta = {
        "chat": chat_name,
        "span": span,
        "account": args.account or (media.account_dir() if media else "") or "",
        "media_dir": str(out.parent / (out.stem + "_图片")),
    }
    extra = {"images": 0, "media_errors": []}
    try:
        if args.format == "md":
            render_md(data, title, out)
        else:
            extra = render_docx(data, title, out, args.include_media,
                                args.max_images, meta)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"渲染失败：{exc}"}, ensure_ascii=False, indent=2))
        return 1

    senders = sorted({m.get("sender") for m in msgs if m.get("sender")})
    types: dict = {}
    for m in msgs:
        k = m.get("type_name") or "其他"
        types[k] = types.get(k, 0) + 1

    print(json.dumps({
        "ok": True,
        "chat": chat_name,
        "messages": len(msgs),
        "senders": len(senders),
        "sender_names": senders[:12],
        "type_counts": types,
        "span": span,
        "format": args.format,
        "output_path": str(out.resolve()),
        "bytes": os.path.getsize(out) if out.exists() else 0,
        "images_embedded": extra.get("images", 0),
        "media_errors": extra.get("media_errors", [])[:5],
        "keyword": args.keyword or None,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
