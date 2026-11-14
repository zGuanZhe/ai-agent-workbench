#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gbt9704_formatter.py
严格按照《党政机关公文格式》(GB/T 9704-2012) 将 Markdown 草稿、纯文本或普通 Word 文档
一键排版并导出为标准公文 .docx 文件：
- 页边距：上 37mm，下 35mm，左 28mm，右 26mm
- 版头（可选红头）：发文机关标志（红色小标宋/宋体大字居中）+ 发文字号 + 红色分隔线（带五角星或双线）
- 标题：二号方正小标宋简体（22pt），居中，段前段后合理间距
- 一级标题（“一、”）：三号黑体（16pt），首行缩进2字符，固定行距28磅
- 二级标题（“（一）”）：三号楷体_GB2312（16pt，加粗），首行缩进2字符，固定行距28磅
- 三级标题（“1.”）：三号仿宋_GB2312（16pt，加粗），首行缩进2字符，固定行距28磅
- 正文段落：三号仿宋_GB2312（16pt），首行缩进2字符（32pt），固定行距28磅
- 落款与成文日期：右空4字，三号仿宋_GB2312
- 页脚页码：4号半角宋体“— 1 —”样式
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import nsdecls, qn
from docx.shared import Mm, Pt, RGBColor


H1_RE = re.compile(r"^(?:[一二三四五六七八九十百]+、|#{1}\s+)(.+)$")
H2_RE = re.compile(r"^(?:[（(][一二三四五六七八九十百]+[)）]|#{2}\s+)(.+)$")
H3_RE = re.compile(r"^(?:\d+[.．、]|#{3,}\s+)(.+)$")
SALUTATION_RE = re.compile(r"^[^\s，。；：！？]{2,35}[：:]$")
DATE_LINE_RE = re.compile(r"^\d{4}年\d{1,2}月\d{1,2}日$")


# --------------------------------------------------------------------------- #
# 字体降级：国标要求方正小标宋/仿宋_GB2312/楷体_GB2312，若本机未安装则按公文惯例降级，
# 避免 Word 渲染时整篇回退成默认宋体。
# --------------------------------------------------------------------------- #
FONT_CANDIDATES = {
    "title": ["方正小标宋简体", "方正小标宋_GBK", "华文中宋", "宋体"],
    "h1": ["黑体", "微软雅黑"],
    "h2": ["楷体_GB2312", "楷体", "华文楷体"],
    "body": ["仿宋_GB2312", "仿宋", "华文仿宋"],
    "page": ["宋体"],
}

FILE_STEM_MAP = {
    "simfang": "仿宋",
    "simhei": "黑体",
    "simkai": "楷体",
    "simsun": "宋体",
    "stzhongs": "华文中宋",
    "msyh": "微软雅黑",
    "fzxbsjw": "方正小标宋简体",
}


def _installed_font_names() -> set:
    """枚举本机已安装字体名（注册表 + Fonts 目录文件名双通道）。"""
    names = set()
    try:
        import winreg

        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(root, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts") as key:
                    idx = 0
                    while True:
                        try:
                            value_name, value_data, _ = winreg.EnumValue(key, idx)
                        except OSError:
                            break
                        idx += 1
                        names.add(re.sub(r"\s*\(.*?\)\s*$", "", str(value_name)).strip())
                        names.add(Path(str(value_data)).stem.lower())
            except OSError:
                continue
    except Exception:
        pass

    fonts_dir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    if fonts_dir.exists():
        for f in fonts_dir.iterdir():
            stem = f.stem.lower()
            names.add(stem)
            if stem in FILE_STEM_MAP:
                names.add(FILE_STEM_MAP[stem])
    return names


_INSTALLED = _installed_font_names()


def resolve_font(role: str) -> Tuple[str, bool]:
    """返回 (实际使用字体名, 是否为标准字体)。"""
    candidates = FONT_CANDIDATES.get(role, [])
    if not candidates:
        return "宋体", False
    for i, name in enumerate(candidates):
        if name in _INSTALLED or any(name == n or name in str(n) for n in _INSTALLED):
            return name, i == 0
    return candidates[0], True


FONT_TITLE, TITLE_STD = resolve_font("title")
FONT_H1, H1_STD = resolve_font("h1")
FONT_H2, H2_STD = resolve_font("h2")
FONT_BODY, BODY_STD = resolve_font("body")
FONT_PAGE, _PAGE_STD = resolve_font("page")



def set_east_asia_font(run, font_name: str, size_pt: float, bold: bool = False, color: RGBColor = None):
    run.font.name = "Times New Roman"
    run.font.size = Pt(size_pt)
    run.font.bold = bold
    if color is not None:
        run.font.color.rgb = color
    rPr = run._element.get_or_add_rPr()
    rFonts = rPr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = OxmlElement("w:rFonts")
        rPr.append(rFonts)
    rFonts.set(qn("w:eastAsia"), font_name)
    rFonts.set(qn("w:ascii"), "Times New Roman")
    rFonts.set(qn("w:hAnsi"), "Times New Roman")


def format_paragraph_gbt(
    p,
    align=WD_ALIGN_PARAGRAPH.JUSTIFY,
    first_line_indent_pt: float = 32.0,
    line_spacing_pt: float = 28.0,
    space_before_pt: float = 0.0,
    space_after_pt: float = 0.0,
):
    p.alignment = align
    pf = p.paragraph_format
    pf.first_line_indent = Pt(first_line_indent_pt) if first_line_indent_pt > 0 else Pt(0)
    pf.line_spacing_rule = WD_LINE_SPACING.EXACTLY
    pf.line_spacing = Pt(line_spacing_pt)
    pf.space_before = Pt(space_before_pt)
    pf.space_after = Pt(space_after_pt)


def add_red_divider_line(doc: Document):
    """在公文版头下方插入标准红色分隔线段落。"""
    p = doc.add_paragraph()
    pPr = p._p.get_or_add_pPr()
    pBdr = parse_xml(
        f'<w:pBdr {nsdecls("w")}>'
        '<w:bottom w:val="single" w:sz="18" w:space="1" w:color="FF0000"/>'
        "</w:pBdr>"
    )
    pPr.insert(0, pBdr)
    format_paragraph_gbt(p, align=WD_ALIGN_PARAGRAPH.CENTER, first_line_indent_pt=0, line_spacing_pt=12)


def add_gbt_page_number_footer(section):
    """配置 GB/T 9704 页脚页码：— 1 — （4号半角宋体）。"""
    footer = section.footer
    footer.is_linked_to_previous = False
    p = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER

    r_pre = p.add_run("— ")
    set_east_asia_font(r_pre, FONT_PAGE, 14, bold=False)

    fldSimple = OxmlElement("w:fldSimple")
    fldSimple.set(qn("w:instr"), "PAGE")
    p._p.append(fldSimple)

    r_post = p.add_run(" —")
    set_east_asia_font(r_post, FONT_PAGE, 14, bold=False)


def load_source_lines(input_arg: str) -> List[str]:
    p = Path(input_arg)
    if p.exists() and p.is_file():
        if p.suffix.lower() == ".docx":
            src_doc = Document(str(p))
            return [para.text.strip() for para in src_doc.paragraphs if para.text.strip()]
        else:
            raw = p.read_text(encoding="utf-8", errors="ignore")
            return [line.strip() for line in raw.splitlines() if line.strip()]
    # 直接传入的文本内容
    text = input_arg.replace("\\n", "\n")
    return [line.strip() for line in text.splitlines() if line.strip()]


def clean_markdown_inline(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    return text.strip()


def _looks_like_body(line: str) -> bool:
    """像正文首句而不是标题：以「现将/根据/为…」开头，或以句号收尾。"""
    if line.rstrip().endswith(("。", "！", "？")):
        return True
    return bool(re.match(r"^(?:现将|根据|按照|依据|参照|为了?|经|兹|特此)", line))


def classify_and_extract_title(lines: List[str], doc_type: str) -> Tuple[str, List[str]]:
    """提取公文主标题与正文行列表。

    早先这里把逻辑写反了：第一行是称谓（「各单位：」）时反而去拼
    「关于各单位：的通知」——因为判断分支只在「不是称谓」时才取首行当标题。
    现在先跳过开头的称谓，再判断下一行像不像标题。
    """
    if not lines:
        return f"关于开展专项工作的{doc_type}", []

    # 称谓可能在最前面，标题被它挡在后面
    idx = 0
    while idx < len(lines) and SALUTATION_RE.match(lines[idx]):
        idx += 1
    if idx >= len(lines):
        return doc_type, lines

    first = lines[idx]
    if first.startswith("# "):
        return clean_markdown_inline(first[2:]), lines[:idx] + lines[idx + 1:]
    if len(first) <= 45 and not H1_RE.match(first) and not _looks_like_body(first):
        return clean_markdown_inline(first), lines[:idx] + lines[idx + 1:]

    # 通篇没有标题：不要拿正文首句硬凑「关于…的通知」，那比空着更糟。
    # 直接用文种当标题（如「通知」），并在返回里标 title_guessed，提醒用户补。
    return doc_type, lines



def _is_md_table_sep(line: str) -> bool:
    s = line.strip()
    if not (s.startswith("|") or "|" in s):
        return False
    cells = [c.strip() for c in s.strip("|").split("|")]
    return bool(cells) and all(bool(re.match(r"^:?-{2,}:?$", c)) for c in cells if c)


def _parse_md_table_row(line: str) -> List[str]:
    return [clean_markdown_inline(c.strip()) for c in line.strip().strip("|").split("|")]


def _add_formatted_table(doc: Document, rows_data: List[List[str]], font_name: str, size_pt: float, preset: str = "gbt9704"):
    if not rows_data:
        return
    cols = max(len(r) for r in rows_data)
    tbl = doc.add_table(rows=len(rows_data), cols=cols)
    tbl.autofit = True
    # 添加表格边框
    tblPr = tbl._tbl.tblPr
    borders_xml = (
        f'<w:tblBorders {nsdecls("w")}>'
        '<w:top w:val="single" w:sz="8" w:space="0" w:color="333333"/>'
        '<w:bottom w:val="single" w:sz="8" w:space="0" w:color="333333"/>'
        + (
            '<w:left w:val="none"/><w:right w:val="none"/><w:insideV w:val="none"/>'
            '<w:insideH w:val="single" w:sz="4" w:space="0" w:color="BFBFBF"/>'
            if preset == "academic"
            else '<w:left w:val="single" w:sz="4" w:space="0" w:color="BFBFBF"/>'
                 '<w:right w:val="single" w:sz="4" w:space="0" w:color="BFBFBF"/>'
                 '<w:insideH w:val="single" w:sz="4" w:space="0" w:color="BFBFBF"/>'
                 '<w:insideV w:val="single" w:sz="4" w:space="0" w:color="BFBFBF"/>'
        )
        + "</w:tblBorders>"
    )
    tblPr.append(parse_xml(borders_xml))

    for r_i, r_vals in enumerate(rows_data):
        row = tbl.rows[r_i]
        for c_i in range(cols):
            val = r_vals[c_i] if c_i < len(r_vals) else ""
            cell = row.cells[c_i]
            cp = cell.paragraphs[0]
            cp.alignment = WD_ALIGN_PARAGRAPH.CENTER if r_i == 0 else WD_ALIGN_PARAGRAPH.LEFT
            run = cp.add_run(val)
            is_hdr = r_i == 0
            hdr_color = RGBColor(255, 255, 255) if (is_hdr and preset == "modern") else None
            set_east_asia_font(run, font_name, max(10.5, size_pt - 2), bold=is_hdr, color=hdr_color)
            if is_hdr and preset in ("modern", "memo"):
                fill_hex = "1F4E78" if preset == "modern" else "D9E1F2"
                tcPr = cell._tc.get_or_add_tcPr()
                tcPr.append(parse_xml(f'<w:shd {nsdecls("w")} w:fill="{fill_hex}"/>'))


def render_gbt9704_docx(
    lines: List[str],
    doc_type: str,
    red_header_unit: str,
    doc_number: str,
    output_path: Path,
    preset: str = "gbt9704",
) -> Dict[str, any]:
    doc = Document()
    section = doc.sections[0]

    # A4 纸张与 GB/T 9704 页边距：上37mm 下35mm 左28mm 右26mm
    section.page_width = Mm(210)
    section.page_height = Mm(297)
    section.top_margin = Mm(37)
    section.bottom_margin = Mm(35)
    section.left_margin = Mm(28)
    section.right_margin = Mm(26)

    add_gbt_page_number_footer(section)

    # 1. 红头版头（若指定了发文机关标志）
    if red_header_unit:
        header_text = red_header_unit if red_header_unit.endswith("文件") or "纪要" in red_header_unit else f"{red_header_unit}文件"
        p_red = doc.add_paragraph()
        format_paragraph_gbt(p_red, align=WD_ALIGN_PARAGRAPH.CENTER, first_line_indent_pt=0, line_spacing_pt=46, space_after_pt=12)
        run_red = p_red.add_run(header_text)
        set_east_asia_font(run_red, FONT_TITLE, 36, bold=True, color=RGBColor(255, 0, 0))

        # 发文字号（三号仿宋居中）
        year_str = datetime.now().strftime("%Y")
        fa_wen_hao = doc_number or f"政发〔{year_str}〕1号"
        p_num = doc.add_paragraph()
        format_paragraph_gbt(p_num, align=WD_ALIGN_PARAGRAPH.CENTER, first_line_indent_pt=0, line_spacing_pt=28)
        run_num = p_num.add_run(fa_wen_hao)
        set_east_asia_font(run_num, FONT_BODY, 16, bold=False)

        # 红色分隔横线
        add_red_divider_line(doc)

    # 2. 公文主标题（二号方正小标宋简体 22pt，居中）
    title_text, body_lines = classify_and_extract_title(lines, doc_type)
    p_title = doc.add_paragraph()
    format_paragraph_gbt(
        p_title,
        align=WD_ALIGN_PARAGRAPH.CENTER,
        first_line_indent_pt=0,
        line_spacing_pt=32,
        space_before_pt=14,
        space_after_pt=14,
    )
    run_title = p_title.add_run(title_text)
    set_east_asia_font(run_title, FONT_TITLE, 22, bold=False)

    # 3. 逐段解析并应用一/二/三级标题、主送机关、正文、落款样式
    h1_counter = 0
    h2_counter = 0
    h3_counter = 0
    cn_nums = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十", "十一", "十二"]

    stats = {"h1": 0, "h2": 0, "h3": 0, "body": 0, "tables": 0}
    preset = (preset or "gbt9704").strip().lower()
    if preset in ("modern", "academic", "memo"):
        section.top_margin = Mm(25.4)
        section.bottom_margin = Mm(25.4)
        section.left_margin = Mm(25.4)
        section.right_margin = Mm(25.4)
    body_font_used = "微软雅黑" if preset in ("modern", "memo") else ("宋体" if preset == "academic" else FONT_BODY)
    body_size_pt = 11.0 if preset in ("modern", "memo") else (12.0 if preset == "academic" else 16.0)
    body_line_pt = 20.0 if preset in ("modern", "memo") else (22.0 if preset == "academic" else 28.0)
    body_indent_pt = 0.0 if preset in ("modern", "memo") else (24.0 if preset == "academic" else 32.0)

    idx = 0
    while idx < len(body_lines):
        raw_line = body_lines[idx]
        # Markdown 表格块识别
        if "|" in raw_line and idx + 1 < len(body_lines) and _is_md_table_sep(body_lines[idx + 1]):
            tbl_rows = [_parse_md_table_row(raw_line)]
            idx += 2
            while idx < len(body_lines) and "|" in body_lines[idx] and not _is_md_table_sep(body_lines[idx]):
                tbl_rows.append(_parse_md_table_row(body_lines[idx]))
                idx += 1
            _add_formatted_table(doc, tbl_rows, body_font_used, body_size_pt, preset=preset)
            stats["tables"] += 1
            continue

        cur_idx = idx
        line = clean_markdown_inline(raw_line)
        idx += 1
        if not line or line.startswith("```"):
            continue

        # 主送机关（第一段且以冒号结尾）
        if cur_idx == 0 and SALUTATION_RE.match(line):
            p = doc.add_paragraph()
            format_paragraph_gbt(p, align=WD_ALIGN_PARAGRAPH.LEFT, first_line_indent_pt=0, line_spacing_pt=28)
            r = p.add_run(line)
            set_east_asia_font(r, FONT_BODY, 16, bold=False)
            continue

        # 落款日期或发文机关署名（位于末尾2行内）
        if cur_idx >= len(body_lines) - 2 and (DATE_LINE_RE.match(line) or (len(line) <= 18 and any(k in line for k in ["局", "委", "办", "部", "处", "科", "中心", "政府"]))):
            p = doc.add_paragraph()
            format_paragraph_gbt(p, align=WD_ALIGN_PARAGRAPH.RIGHT, first_line_indent_pt=0, line_spacing_pt=28)
            p.paragraph_format.right_indent = Pt(64)  # 右空4字
            r = p.add_run(line)
            set_east_asia_font(r, FONT_BODY, 16, bold=False)
            continue

        # 一级标题：“一、...” 或 Markdown “# ...”
        heading_body = line.lstrip("#").strip()
        if (
            re.match(r"^[一二三四五六七八九十百]+、", heading_body)
            or re.match(r"^#{1,2}\s", raw_line)
        ) and not re.match(r"^[（(][一二三四五六七八九十百]+[)）]", heading_body):
            if re.match(r"^[一二三四五六七八九十百]+、", heading_body):
                # 正文已自带“一、”序号，直接沿用，避免出现“一、一、”重复编号
                formatted_h1 = heading_body
            else:
                num_label = cn_nums[h1_counter] if h1_counter < len(cn_nums) else str(h1_counter + 1)
                formatted_h1 = f"{num_label}、{heading_body}"

            if formatted_h1:
                h1_counter += 1
                h2_counter = 0
                h3_counter = 0
                stats["h1"] += 1
                p = doc.add_paragraph()
                format_paragraph_gbt(p, align=WD_ALIGN_PARAGRAPH.JUSTIFY, first_line_indent_pt=32, line_spacing_pt=28)
                r = p.add_run(formatted_h1)
                set_east_asia_font(r, FONT_H1, 16, bold=False)
                continue

        # 二级标题：“（一）...” 或 Markdown “### ...”
        if re.match(r"^[（(][一二三四五六七八九十百]+[)）]", line) or raw_line.startswith("### "):
            if re.match(r"^[（(][一二三四五六七八九十百]+[)）]", line):
                formatted_h2 = re.sub(r"^\(([一二三四五六七八九十百]+)\)", r"（\1）", line)
            else:
                num_label = cn_nums[h2_counter] if h2_counter < len(cn_nums) else str(h2_counter + 1)
                formatted_h2 = f"（{num_label}）{line.lstrip('#').strip()}"
            h2_counter += 1
            h3_counter = 0
            stats["h2"] += 1
            p = doc.add_paragraph()
            format_paragraph_gbt(p, align=WD_ALIGN_PARAGRAPH.JUSTIFY, first_line_indent_pt=32, line_spacing_pt=28)
            r = p.add_run(formatted_h2)
            set_east_asia_font(r, FONT_H2, 16, bold=True)
            continue

        # 三级标题：“1. ...” 或 “1．...”
        m_h3 = re.match(r"^(\d+)[.．、]\s*(.+)$", line)
        if m_h3:
            h3_counter += 1
            stats["h3"] += 1
            p = doc.add_paragraph()
            format_paragraph_gbt(p, align=WD_ALIGN_PARAGRAPH.JUSTIFY, first_line_indent_pt=32, line_spacing_pt=28)
            # 若带有句号分界，前半句加粗，后半句正常仿宋
            rest_text = m_h3.group(2)
            split_pos = -1
            for sep in ("。", "：", ":"):
                pos = rest_text.find(sep)
                if 0 < pos <= 28:
                    split_pos = pos
                    break
            if split_pos != -1:
                lead = f"{m_h3.group(1)}.{rest_text[: split_pos + 1]}"
                tail = rest_text[split_pos + 1 :]
                r_lead = p.add_run(lead)
                set_east_asia_font(r_lead, FONT_BODY, 16, bold=True)
                if tail:
                    r_tail = p.add_run(tail)
                    set_east_asia_font(r_tail, FONT_BODY, 16, bold=False)
            else:
                r = p.add_run(f"{m_h3.group(1)}.{rest_text}")
                set_east_asia_font(r, FONT_BODY, 16, bold=True)
            continue

        # 普通正文段落（公文三号仿宋28磅 / 通用现代11pt或学术12pt）
        stats["body"] += 1
        p = doc.add_paragraph()
        is_bullet = raw_line.lstrip().startswith(("- ", "* ", "• "))
        if is_bullet:
            line = "• " + clean_markdown_inline(raw_line.lstrip()[2:])
        format_paragraph_gbt(
            p,
            align=WD_ALIGN_PARAGRAPH.JUSTIFY,
            first_line_indent_pt=(0.0 if is_bullet else body_indent_pt),
            line_spacing_pt=body_line_pt,
        )
        r = p.add_run(line)
        set_east_asia_font(r, body_font_used, body_size_pt, bold=False)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_path))

    return {
        "status": "success",
        "standard": "GB/T 9704-2012",
        "doc_type": doc_type,
        "red_header_unit": red_header_unit or "白头公文",
        "title": title_text,
        # true 表示原稿里没找到像标题的行，标题只是文种占位，必须让用户补
        "title_guessed": title_text == doc_type,
        "margins_mm": {"top": 37, "bottom": 35, "left": 28, "right": 26},
        "typography": {
            "title": f"二号{FONT_TITLE} (22pt)",
            "h1": f"三号{FONT_H1} (16pt)",
            "h2": f"三号{FONT_H2}加粗 (16pt)",
            "h3": f"三号{FONT_BODY}加粗 (16pt)",
            "body": f"三号{FONT_BODY} (16pt), 行距固定值28磅, 首行缩进2字符",
            "page_number": f"四号{FONT_PAGE}",
        },
        "font_fallbacks": [
            f"{role}: {std} → {used}"
            for role, std, used in (
                ("标题(方正小标宋简体)", "方正小标宋简体", FONT_TITLE),
                ("一级标题(黑体)", "黑体", FONT_H1),
                ("二级标题(楷体_GB2312)", "楷体_GB2312", FONT_H2),
                ("正文(仿宋_GB2312)", "仿宋_GB2312", FONT_BODY),
            )
            if std != used
        ],
        "paragraph_counts": stats,
        "output_docx": str(output_path.resolve()),
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="GB/T 9704 国家标准公文一键排版工具")
    parser.add_argument("--input", required=True, help="文稿内容或源文件路径(.md/.txt/.docx)")
    parser.add_argument(
        "--type",
        default="通知",
        choices=["通知", "请示", "报告", "函", "会议纪要", "讲话稿", "总结"],
        help="公文文种",
    )
    parser.add_argument("--header", default="", help="版头发文机关标志（如“XX局文件”），为空则生成白头公文")
    parser.add_argument("--docnum", default="", help="发文字号（如“政发〔2026〕15号”）")
    parser.add_argument("--preset", default="gbt9704", choices=["gbt9704", "modern", "academic", "memo"], help="排版风格预设")
    parser.add_argument("--out", required=True, help="输出的正式 Word (.docx) 路径")
    args = parser.parse_args()

    lines = load_source_lines(args.input)
    result = render_gbt9704_docx(
        lines=lines,
        doc_type=args.type,
        red_header_unit=args.header,
        doc_number=args.docnum,
        output_path=Path(args.out),
        preset=args.preset,
    )
    result["preset"] = args.preset
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
