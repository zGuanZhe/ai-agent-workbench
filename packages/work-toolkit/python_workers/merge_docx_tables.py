#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
merge_docx_tables.py
批量读取文件夹内所有科室上交的 Word 申报表 (.docx) 或 Excel 分表 (.xlsx)，
自动对齐字段、清洗异常格式、校验逻辑勾稽关系（如合计列是否等于分项之和、手机号/身份证位数、漏填项），
写入汇总母表 (.xlsx) 并高亮异常单元格，同时输出《异常数据核验单》。
"""

import argparse
import csv


def extract_raw_tables_from_file(fpath: Path) -> List[Tuple[str, List[List[str]]]]:
    """从 .xlsx / .csv / .tsv / .docx 提取原始二维表或键值对，用于无模板自动推断表头。
    返回 [(kind, rows), ...]，kind 为 'table'（首行表头）或 'kv'（键值对列表 [[k, v], ...]）。
    """
    ext = fpath.suffix.lower()
    out: List[Tuple[str, List[List[str]]]] = []
    if ext in (".csv", ".tsv"):
        delim = "\t" if ext == ".tsv" else ","
        try:
            text = fpath.read_text(encoding="utf-8-sig", errors="replace")
            rows = [[c.strip() for c in r] for r in csv.reader(text.splitlines(), delimiter=delim) if any(c.strip() for c in r)]
            if len(rows) >= 2:
                out.append(("table", rows))
        except Exception:
            pass
        return out

    if ext == ".xlsx":
        try:
            wb = openpyxl.load_workbook(str(fpath), data_only=True)
            for ws in wb.worksheets:
                if ws.title == "异常数据核验单":
                    continue
                rows = []
                for row in ws.iter_rows(values_only=True):
                    cleaned = ["" if c is None else str(c).strip() for c in row]
                    if any(cleaned):
                        rows.append(cleaned)
                if len(rows) >= 2:
                    # 若第1行只有1个非空单元格（大标题），而第2行有>=2个非空单元格，则从第2行起算表头
                    non_empty_0 = sum(1 for x in rows[0] if x)
                    non_empty_1 = sum(1 for x in rows[1] if x)
                    if non_empty_0 == 1 and non_empty_1 >= 2 and len(rows) >= 3:
                        rows = rows[1:]
                    out.append(("table", rows))
        except Exception:
            pass
        return out

    if ext == ".docx":
        try:
            doc = Document(str(fpath))
            for table in doc.tables:
                rows_data: List[List[str]] = []
                for row in table.rows:
                    cells_text: List[str] = []
                    last_tc = None
                    for cell in row.cells:
                        if cell._tc is last_tc:
                            continue
                        last_tc = cell._tc
                        cells_text.append(cell.text.strip().replace("\n", " "))
                    if any(cells_text):
                        rows_data.append(cells_text)
                if not rows_data:
                    continue
                # 判断是键值对登记表（偶数列表格且多行第0/2列像短标签）还是普通列表型表格
                first_row = rows_data[0]
                if len(first_row) in (4, 6) and len(rows_data) >= 2 and all(len(r) == len(first_row) for r in rows_data[:2]):
                    # 检查是否第1行与第2行的偶数列都是不同字段名（键值表特征）
                    even_0 = [first_row[i] for i in range(0, len(first_row), 2) if first_row[i]]
                    even_1 = [rows_data[1][i] for i in range(0, len(rows_data[1]), 2) if rows_data[1][i]]
                    if even_0 and even_1 and set(even_0) != set(even_1) and all(len(k) <= 16 and not parse_number(k) for k in even_0 + even_1):
                        kv_pairs = []
                        for r in rows_data:
                            for i in range(0, len(r) - 1, 2):
                                k, v = r[i].strip(), r[i + 1].strip()
                                if k and len(k) <= 20:
                                    kv_pairs.append([k, v])
                        if kv_pairs:
                            out.append(("kv", kv_pairs))
                            continue
                if len(rows_data) >= 2:
                    out.append(("table", rows_data))
        except Exception:
            pass
    return out


def infer_master_headers(sub_files: List[Path]) -> List[str]:
    """无模板时，从所有子文件自动推断并对齐统一表头。"""
    headers: List[str] = ["序号"]
    for fpath in sub_files:
        for kind, rows in extract_raw_tables_from_file(fpath):
            if kind == "table" and rows:
                cand_headers = [c.strip() for c in rows[0] if c.strip()]
            elif kind == "kv":
                cand_headers = [pair[0].strip() for pair in rows if pair and pair[0].strip()]
            else:
                continue
            for h in cand_headers:
                if h in ("合计", "总计", "汇总") and len(cand_headers) == 1:
                    continue
                if match_column(h, headers) is None:
                    headers.append(h)
    if match_column("来源文件", headers) is None:
        headers.append("来源文件")
    return headers


def extract_records_from_csv(csv_path: Path, master_headers: List[str]) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for kind, rows_data in extract_raw_tables_from_file(csv_path):
        if kind != "table" or len(rows_data) < 2:
            continue
        header_row = rows_data[0]
        matched_cols = [match_column(c, master_headers) for c in header_row]
        if sum(1 for m in matched_cols if m is not None) < 1:
            continue
        for r in rows_data[1:]:
            first_non_empty = next((c for c in r if c), "")
            if first_non_empty in ("合计", "总计", "汇总"):
                continue
            rec: Dict[str, Any] = {"__source_file__": csv_path.name}
            if "来源文件" in master_headers:
                rec["来源文件"] = csv_path.name
            for c_idx, cell_val in enumerate(r):
                if c_idx < len(matched_cols) and matched_cols[c_idx] is not None:
                    rec[master_headers[matched_cols[c_idx]]] = cell_val
            if len(rec) > 1:
                records.append(rec)
    return records

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from docx import Document
import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter


PHONE_RE = re.compile(r"^1[3-9]\d{9}$")
IDCARD_RE = re.compile(r"^\d{17}[\dXx]$")
NUM_CLEAN_RE = re.compile(r"[,，\s万元元%％]")


def parse_number(val: Any) -> Optional[float]:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    if not s or s in ("-", "—", "/", "无", "N/A"):
        return 0.0
    cleaned = NUM_CLEAN_RE.sub("", s)
    try:
        return float(cleaned)
    except ValueError:
        return None


def normalize_header(text: str) -> str:
    if not text:
        return ""
    s = re.sub(r"[\s\n\r（）()【】\[\]：:*＊]", "", str(text))
    s = re.sub(r"万元|元|人数|姓名|名称", lambda m: m.group(0), s)
    return s.strip()


def match_column(field_name: str, master_headers: List[str]) -> Optional[int]:
    """将子表字段名映射到母表列索引（0-based）。"""
    norm_f = normalize_header(field_name)
    if not norm_f:
        return None
    # 1. 精确匹配
    for idx, mh in enumerate(master_headers):
        if normalize_header(mh) == norm_f:
            return idx
    # 2. 同义词/包含匹配
    synonyms = {
        "科室": ["部门", "单位", "填报科室", "填报单位", "所属科室", "处室", "单位名称", "科室名称"],
        "联络员": ["填报人", "联系人", "经办人", "负责人", "姓名", "上报人"],
        "联系电话": ["电话", "手机号", "手机", "联系方式", "办公电话"],
        "身份证号": ["身份证", "身份证号码", "证件号码"],
        "总计": ["合计", "小计", "总额", "经费总计", "预算总计", "总经费", "申报总额"],
        "备注": ["说明", "其他说明", "备注信息"],
    }
    for idx, mh in enumerate(master_headers):
        norm_m = normalize_header(mh)
        if norm_f in norm_m or norm_m in norm_f:
            return idx
        for canon, aliases in synonyms.items():
            group = [canon] + aliases
            if any(a in norm_f for a in group) and any(a in norm_m for a in group):
                return idx
    return None


def _dept_from_name(stem: str) -> str:
    """从文件名首段猜科室，只在它真的像单位名时才认。

    早期直接取 `stem.split("_")[0]`，于是 `2026-11_实践作品简介表.docx`
    被写成「科室名称=2026-11」，平白多出一条脏数据。
    """
    parts = stem.split("_")
    if len(parts) < 2:
        return ""
    head = parts[0].strip()
    if not head or len(head) > 12 or re.match(r"^[\d\-_.．年月日]+", head):
        return ""
    return head


def extract_records_from_docx(docx_path: Path, master_headers: List[str]) -> List[Dict[str, Any]]:
    """
    支持两种常见办公 Word 表格：
    1. 列表型表格（第一行为表头，后续行为数据行）
    2. 登记表/摸底表型（键值对交替，如 4列 [字段A, 值A, 字段B, 值B] 或混合底部明细行）
    """
    doc = Document(str(docx_path))
    records: List[Dict[str, Any]] = []

    # 从文件名推断科室与发送人（如 财务科_张三_专项经费摸底表.docx）
    stem_parts = docx_path.stem.split("_")
    inferred_dept = _dept_from_name(docx_path.stem)
    inferred_sender = stem_parts[1] if len(stem_parts) >= 3 else ""

    for table in doc.tables:
        rows_data: List[List[str]] = []
        for row in table.rows:
            cells_text: List[str] = []
            last_tc = None
            for cell in row.cells:
                # 去重合并单元格
                if cell._tc is last_tc:
                    continue
                last_tc = cell._tc
                cells_text.append(cell.text.strip().replace("\n", " "))
            if any(cells_text):
                rows_data.append(cells_text)

        if not rows_data:
            continue

        # 判断第一行是否直接对应 master_headers（至少匹配 3 个表头）
        first_row = rows_data[0]
        matched_cols = [match_column(c, master_headers) for c in first_row]
        valid_matches = [m for m in matched_cols if m is not None]

        if (len(valid_matches) >= 3 or (len(valid_matches) >= 2 and len(valid_matches) == len(first_row))) and len(rows_data) >= 2:
            # 列表型表格
            for r in rows_data[1:]:
                if not any(r):
                    continue
                rec: Dict[str, Any] = {"__source_file__": docx_path.name}
                for c_idx, cell_val in enumerate(r):
                    if c_idx < len(matched_cols) and matched_cols[c_idx] is not None:
                        mh_name = master_headers[matched_cols[c_idx]]
                        rec[mh_name] = cell_val
                if inferred_dept:
                    dept_idx = match_column("科室", master_headers)
                    if dept_idx is not None and not rec.get(master_headers[dept_idx]):
                        rec[master_headers[dept_idx]] = inferred_dept
                records.append(rec)
        else:
            # 键值对型登记表（或混合式摸底表）
            kv_rec: Dict[str, Any] = {"__source_file__": docx_path.name}
            for r in rows_data:
                # 偶数对解析: (k0, v0, k1, v1, ...)
                if len(r) >= 2:
                    i = 0
                    while i + 1 < len(r):
                        k, v = r[i], r[i + 1]
                        m_idx = match_column(k, master_headers)
                        if m_idx is not None:
                            kv_rec[master_headers[m_idx]] = v
                            i += 2
                        else:
                            i += 1
            if inferred_dept:
                dept_idx = match_column("科室", master_headers)
                if dept_idx is not None and not kv_rec.get(master_headers[dept_idx]):
                    kv_rec[master_headers[dept_idx]] = inferred_dept
            if inferred_sender:
                sender_idx = match_column("联络员", master_headers)
                if sender_idx is not None and not kv_rec.get(master_headers[sender_idx]):
                    kv_rec[master_headers[sender_idx]] = inferred_sender
            if len(kv_rec) > 1:
                # 只收真正命中母表字段的登记表。否则目录里一份课程作业表
                # （表头是「作品名称/学号」）也会被当成摸底表，产出
                # 「科室名称=2026-11、联络员=学号」的脏行和一堆假异常。
                hit_fields = [k for k in kv_rec if k != "__source_file__" and k in master_headers]
                if len(hit_fields) >= 2:
                    records.append(kv_rec)

    return records


def extract_records_from_xlsx(xlsx_path: Path, master_headers: List[str]) -> List[Dict[str, Any]]:
    wb = openpyxl.load_workbook(str(xlsx_path), data_only=True)
    ws = wb.active
    rows_data: List[List[Any]] = []
    for row in ws.iter_rows(values_only=True):
        cleaned = ["" if c is None else str(c).strip() for c in row]
        if any(cleaned):
            rows_data.append(cleaned)

    if not rows_data:
        return []

    stem_parts = xlsx_path.stem.split("_")
    inferred_dept = _dept_from_name(xlsx_path.stem)

    # 寻找表头行（前 5 行内匹配最多母表列的行）
    best_header_idx = 0
    best_matched: List[Optional[int]] = []
    best_count = 0
    for idx in range(min(5, len(rows_data))):
        m_cols = [match_column(c, master_headers) for c in rows_data[idx]]
        cnt = sum(1 for m in m_cols if m is not None)
        if cnt > best_count:
            best_count = cnt
            best_header_idx = idx
            best_matched = m_cols

    records: List[Dict[str, Any]] = []
    if best_count >= 2:
        for r in rows_data[best_header_idx + 1 :]:
            # 跳过空行或表尾纯汇总行（如果第一列是“合计/总计”且没有科室名）
            first_non_empty = next((c for c in r if c), "")
            if first_non_empty in ("合计", "总计", "汇总"):
                continue
            rec: Dict[str, Any] = {"__source_file__": xlsx_path.name}
            for c_idx, cell_val in enumerate(r):
                if c_idx < len(best_matched) and best_matched[c_idx] is not None:
                    mh_name = master_headers[best_matched[c_idx]]
                    rec[mh_name] = cell_val
            if inferred_dept:
                dept_idx = match_column("科室", master_headers)
                if dept_idx is not None and not rec.get(master_headers[dept_idx]):
                    rec[master_headers[dept_idx]] = inferred_dept
            if len(rec) > 1:
                records.append(rec)
    return records


def detect_numeric_and_total_columns(master_headers: List[str]) -> Tuple[List[str], Optional[str]]:
    """自动识别母表中的分项数值列与总计/合计列。"""
    total_col: Optional[str] = None
    sub_num_cols: List[str] = []
    ignore_keywords = ["序号", "编号", "电话", "手机", "身份证", "日期", "时间", "科室", "部门", "单位", "姓名", "联络员", "联系人", "备注", "说明"]

    for h in master_headers:
        nh = normalize_header(h)
        if any(k in nh for k in ["总计", "合计", "小计", "总额", "总经费", "申报总额"]):
            total_col = h
        elif not any(k in nh for k in ignore_keywords):
            if any(k in nh for k in ["费", "金", "额", "款", "数", "量", "万", "元", "预算", "支出", "补贴", "采购", "专项"]):
                sub_num_cols.append(h)

    return sub_num_cols, total_col


def validate_record(
    row_idx: int,
    rec: Dict[str, Any],
    master_headers: List[str],
    sub_num_cols: List[str],
    total_col: Optional[str],
    rules_text: str,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    校验单条记录，返回 (异常列表, 异常列名列表)。
    """
    issues: List[Dict[str, Any]] = []
    bad_cols: List[str] = []
    dept_col_idx = match_column("科室", master_headers)
    dept_name = rec.get(master_headers[dept_col_idx], "未知科室") if dept_col_idx is not None else rec.get("__source_file__", f"第{row_idx}行")
    source_file = rec.get("__source_file__", "")

    # 1. 必填项漏填检查（除“备注/说明/序号”外均视为应填项）
    for h in master_headers:
        nh = normalize_header(h)
        if any(k in nh for k in ["序号", "备注", "说明"]):
            continue
        val = str(rec.get(h, "")).strip()
        if not val:
            issues.append({
                "row": row_idx,
                "department": dept_name,
                "source_file": source_file,
                "column": h,
                "type": "漏填缺失",
                "detail": f"字段【{h}】未填写（为空）",
            })
            bad_cols.append(h)

    # 2. 手机号与身份证号格式校验
    for h in master_headers:
        nh = normalize_header(h)
        val = str(rec.get(h, "")).strip()
        if not val:
            continue
        if any(k in nh for k in ["电话", "手机", "联系方式"]):
            # 若规则要求11位手机或本身是1开头数字
            if "11位" in rules_text or val.startswith("1") or val.isdigit():
                if not PHONE_RE.match(val):
                    issues.append({
                        "row": row_idx,
                        "department": dept_name,
                        "source_file": source_file,
                        "column": h,
                        "type": "格式错误",
                        "detail": f"联系电话【{val}】不符合11位手机号规范（当前长度 {len(val)} 位）",
                    })
                    bad_cols.append(h)
        if "身份证" in nh:
            if not IDCARD_RE.match(val):
                issues.append({
                    "row": row_idx,
                    "department": dept_name,
                    "source_file": source_file,
                    "column": h,
                    "type": "格式错误",
                    "detail": f"身份证号【{val}】位数或校验码格式不合法（当前长度 {len(val)} 位）",
                })
                bad_cols.append(h)

    # 3. 数值转换与分项合计勾稽校验
    if total_col and sub_num_cols:
        sub_sum = 0.0
        all_subs_numeric = True
        sub_parts_desc: List[str] = []
        for sc in sub_num_cols:
            raw_v = rec.get(sc, "")
            num_v = parse_number(raw_v)
            if num_v is None:
                all_subs_numeric = False
                issues.append({
                    "row": row_idx,
                    "department": dept_name,
                    "source_file": source_file,
                    "column": sc,
                    "type": "数值异常",
                    "detail": f"数值列【{sc}】包含无法解析的非数字内容：“{raw_v}”",
                })
                bad_cols.append(sc)
            else:
                sub_sum += num_v
                sub_parts_desc.append(f"{sc}({num_v:g})")

        raw_total = rec.get(total_col, "")
        total_val = parse_number(raw_total)
        if total_val is None and str(raw_total).strip():
            issues.append({
                "row": row_idx,
                "department": dept_name,
                "source_file": source_file,
                "column": total_col,
                "type": "数值异常",
                "detail": f"合计列【{total_col}】包含非数字内容：“{raw_total}”",
            })
            bad_cols.append(total_col)
        elif all_subs_numeric and total_val is not None:
            diff = round(total_val - sub_sum, 4)
            if abs(diff) > 1e-4:
                issues.append({
                    "row": row_idx,
                    "department": dept_name,
                    "source_file": source_file,
                    "column": total_col,
                    "type": "勾稽不符",
                    "detail": (
                        f"分项相加为 {sub_sum:g}（{' + '.join(sub_parts_desc)}），"
                        f"但填报【{total_col}】为 {total_val:g}，差额 {diff:+g}"
                    ),
                })
                bad_cols.append(total_col)

    return issues, bad_cols


def load_or_init_master(template_path: Path) -> Tuple[openpyxl.Workbook, List[str], int]:
    """加载空白汇总母表，返回 (workbook, 表头列表, 数据起始行号1-based)。"""
    if template_path.exists():
        wb = openpyxl.load_workbook(str(template_path))
        ws = wb.active
        # 找到表头行（最后一行非空行作为表头）
        header_row_idx = 1
        headers: List[str] = []
        for r_idx in range(1, ws.max_row + 1):
            row_vals = [ws.cell(r_idx, c).value for c in range(1, ws.max_column + 1)]
            cleaned = ["" if v is None else str(v).strip() for v in row_vals]
            if sum(1 for x in cleaned if x) >= 2:
                header_row_idx = r_idx
                headers = cleaned
        if headers:
            return wb, headers, header_row_idx + 1

    # 默认初始化标准办公摸底汇总母表
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "汇总母表"
    default_headers = [
        "序号",
        "科室名称",
        "联络员",
        "联系电话",
        "设备采购费(万元)",
        "业务培训费(万元)",
        "系统运维费(万元)",
        "经费总计(万元)",
        "备注",
    ]
    ws.append(default_headers)
    return wb, default_headers, 2


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="批量抽取合并 Word/Excel 摸底表并执行逻辑勾稽校验")
    parser.add_argument("--src", required=True, help="存放待合并文档/表格的文件夹路径")
    parser.add_argument("--tpl", default="auto", help="空白汇总母表(.xlsx)路径；传 auto 或留空则自动从子表推断表头")
    parser.add_argument("--out", required=True, help="生成的最终汇总表(.xlsx)路径")
    parser.add_argument("--rules", default="", help="校验规则描述")
    parser.add_argument("--auto-schema", action="store_true", help="强制无模板自动推断并对齐所有子表表头")
    args = parser.parse_args()

    src_dir = Path(args.src)
    tpl_raw = (args.tpl or "").strip()
    auto_mode = bool(args.auto_schema) or tpl_raw.lower() in ("", "auto", "none")
    tpl_path = Path(tpl_raw) if not auto_mode else Path("__auto_schema__")
    out_path = Path(args.out)

    if not src_dir.exists():
        print(json.dumps({"status": "error", "message": f"源文件夹不存在: {src_dir}"}, ensure_ascii=False))
        return 1

    # 收集源目录下所有 .docx, .xlsx, .csv, .tsv 文件
    sub_files = sorted(
        [
            p
            for p in src_dir.iterdir()
            if p.is_file()
            and not p.name.startswith("~$")
            and p.suffix.lower() in (".docx", ".xlsx", ".csv", ".tsv")
            and (auto_mode or not tpl_path.exists() or p.resolve() != tpl_path.resolve())
            and p.resolve() != out_path.resolve()
        ]
    )

    if auto_mode:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "通用汇总表"
        master_headers = infer_master_headers(sub_files)
        ws.append(master_headers)
        start_row = 2
    else:
        wb, master_headers, start_row = load_or_init_master(tpl_path)
        ws = wb.active

    all_records: List[Dict[str, Any]] = []
    skipped_files: List[Dict[str, str]] = []
    def _collect_records(hdrs: List[str]):
        recs: List[Dict[str, Any]] = []
        skips: List[Dict[str, str]] = []
        for fpath in sub_files:
            ext = fpath.suffix.lower()
            if ext == ".docx":
                got = extract_records_from_docx(fpath, hdrs)
            elif ext == ".xlsx":
                got = extract_records_from_xlsx(fpath, hdrs)
            elif ext in (".csv", ".tsv"):
                got = extract_records_from_csv(fpath, hdrs)
            else:
                continue
            if got:
                for r in got:
                    if "来源文件" in hdrs and not r.get("来源文件"):
                        r["来源文件"] = fpath.name
                recs.extend(got)
            else:
                skips.append({"file": fpath.name, "reason": "未识别出匹配字段，已跳过"})
        return recs, skips

    all_records, skipped_files = _collect_records(master_headers)
    # 若未显式开启 auto_mode 但默认模板 0 命中且目录里有可提取表格，自动降级为无模板通用合并
    if not all_records and not auto_mode and sub_files:
        inferred = infer_master_headers(sub_files)
        if len(inferred) > 2:
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "通用汇总表"
            master_headers = inferred
            ws.append(master_headers)
            start_row = 2
            auto_mode = True
            all_records, skipped_files = _collect_records(master_headers)

    sub_num_cols, total_col = detect_numeric_and_total_columns(master_headers)
    # 通用模式下补充自动数值列识别：若某列（非序号/电话/身份证/日期）>=60%非空单元格是纯数值，也纳入数值统计列
    if all_records:
        ignore_nh = ("序号", "编号", "电话", "手机", "身份证", "日期", "时间", "名称", "姓名", "单位", "科室", "部门", "备注", "来源")
        for h in master_headers:
            if h == total_col or h in sub_num_cols:
                continue
            nh = normalize_header(h)
            if any(k in nh for k in ignore_nh):
                continue
            non_empty_vals = [str(r.get(h, "")).strip() for r in all_records if str(r.get(h, "")).strip()]
            if non_empty_vals:
                num_ok = sum(1 for v in non_empty_vals if parse_number(v) is not None)
                if num_ok / len(non_empty_vals) >= 0.6:
                    sub_num_cols.append(h)
    numeric_set = set(sub_num_cols + ([total_col] if total_col else []))

    # 样式定义
    thin_side = Side(style="thin", color="BFBFBF")
    border_all = Border(left=thin_side, right=thin_side, top=thin_side, bottom=thin_side)
    header_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
    header_font = Font(name="微软雅黑", size=11, bold=True, color="FFFFFF")
    body_font = Font(name="仿宋_GB2312", size=11)
    error_cell_fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
    error_cell_font = Font(name="仿宋_GB2312", size=11, bold=True, color="9C0006")
    warn_row_fill = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")
    total_row_fill = PatternFill(start_color="D9E1F2", end_color="D9E1F2", fill_type="solid")
    total_row_font = Font(name="微软雅黑", size=11, bold=True)

    # 格式化表头行
    header_row = start_row - 1
    for c_idx, h_text in enumerate(master_headers, start=1):
        cell = ws.cell(header_row, c_idx, h_text)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = border_all
    ws.row_dimensions[header_row].height = 28

    all_issues: List[Dict[str, Any]] = []
    col_sums: Dict[str, float] = {c: 0.0 for c in numeric_set}

    for idx, rec in enumerate(all_records, start=1):
        excel_row = start_row + idx - 1
        # 自动填充序号
        seq_col_idx = match_column("序号", master_headers)
        if seq_col_idx is not None:
            rec[master_headers[seq_col_idx]] = idx

        row_issues, bad_cols = validate_record(
            idx, rec, master_headers, sub_num_cols, total_col, args.rules
        )
        all_issues.extend(row_issues)
        bad_col_set = set(bad_cols)

        for c_idx, h_name in enumerate(master_headers, start=1):
            raw_val = rec.get(h_name, "")
            write_val: Any = raw_val
            if h_name in numeric_set:
                parsed_num = parse_number(raw_val)
                if parsed_num is not None and str(raw_val).strip() != "":
                    write_val = parsed_num
                    col_sums[h_name] += parsed_num

            cell = ws.cell(excel_row, c_idx, write_val)
            cell.border = border_all
            cell.font = body_font
            cell.alignment = Alignment(
                horizontal="right" if h_name in numeric_set else "center",
                vertical="center",
            )
            if row_issues:
                cell.fill = warn_row_fill
            if h_name in bad_col_set:
                cell.fill = error_cell_fill
                cell.font = error_cell_font
        ws.row_dimensions[excel_row].height = 22

    # 写入底部汇总行
    summary_row = start_row + len(all_records)
    for c_idx, h_name in enumerate(master_headers, start=1):
        cell = ws.cell(summary_row, c_idx)
        if c_idx == 1:
            cell.value = "合计"
        elif h_name in col_sums:
            cell.value = round(col_sums[h_name], 4)
        cell.fill = total_row_fill
        cell.font = total_row_font
        cell.border = border_all
        cell.alignment = Alignment(
            horizontal="right" if h_name in numeric_set else "center",
            vertical="center",
        )
    ws.row_dimensions[summary_row].height = 25

    # 自适应列宽
    for c_idx, h_name in enumerate(master_headers, start=1):
        col_letter = get_column_letter(c_idx)
        max_len = max(len(str(h_name)) * 2 + 4, 14)
        ws.column_dimensions[col_letter].width = min(max_len, 32)

    # 创建《异常数据核验单》Sheet
    if "异常数据核验单" in wb.sheetnames:
        del wb["异常数据核验单"]
    ws_err = wb.create_sheet(title="异常数据核验单")
    err_headers = ["序号", "填报科室/单位", "源文件名", "异常字段", "异常类型", "核验问题描述"]
    ws_err.append(err_headers)
    for c_idx in range(1, len(err_headers) + 1):
        c = ws_err.cell(1, c_idx)
        c.fill = PatternFill(start_color="9C0006", end_color="9C0006", fill_type="solid")
        c.font = Font(name="微软雅黑", size=11, bold=True, color="FFFFFF")
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.border = border_all

    for i, issue in enumerate(all_issues, start=1):
        ws_err.append([
            i,
            issue["department"],
            issue["source_file"],
            issue["column"],
            issue["type"],
            issue["detail"],
        ])
        for c_idx in range(1, len(err_headers) + 1):
            c = ws_err.cell(i + 1, c_idx)
            c.font = body_font
            c.border = border_all

    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(out_path))

    report = {
        "status": "success",
        "processed_files_count": len(sub_files),
        "processed_files": [f.name for f in sub_files],
        "skipped_files": skipped_files,
        "skipped_count": len(skipped_files),
        "merged_records_count": len(all_records),
        "master_headers": master_headers,
        "merged_rows": [{h: rec.get(h, "") for h in master_headers} for rec in all_records],
        "output_excel": str(out_path.resolve()),
        "column_totals": {k: round(v, 4) for k, v in col_sums.items()},
        "anomalies_count": len(all_issues),
        "anomalies": all_issues,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
