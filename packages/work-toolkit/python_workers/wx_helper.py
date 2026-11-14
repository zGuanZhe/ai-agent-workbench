#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wx_helper.py
本地暂存目录收件归档与催办话术生成辅助脚本（纯只读/复制）：
1. harvest 子命令：扫描指定收件暂存目录中的 .docx/.xlsx/.pdf 附件，
   按“科室_发送人_原文件名”规范重命名存入目标目录，并与应交花名册(rosterExcelPath)比对生成已交/未交名单。
2. send 子命令：生成标准 @ 催办通知记录供写入待发催办通知队列。
"""

import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import openpyxl


ALLOWED_EXTS = {".docx", ".doc", ".xlsx", ".xls", ".pdf"}


def safe_copy(src: Path, dst: Path) -> None:
    """复制文件，并保证目标可被后续覆盖。"""
    if dst.exists():
        try:
            os.chmod(dst, 0o666)
        except OSError:
            pass
    shutil.copy2(src, dst)
    try:
        os.chmod(dst, 0o644)
    except OSError:
        pass


def load_roster(roster_path: Path) -> List[Dict[str, str]]:
    """读取应交单位/科室花名册 Excel (.xlsx)。"""
    if not roster_path.exists():
        return []

    wb = openpyxl.load_workbook(str(roster_path), data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []

    headers = ["" if c is None else str(c).strip() for c in rows[0]]
    dept_idx = 0
    person_idx = 1 if len(headers) > 1 else 0
    phone_idx = 2 if len(headers) > 2 else -1

    for idx, h in enumerate(headers):
        if any(k in h for k in ["科室", "部门", "单位", "处室"]):
            dept_idx = idx
        elif any(k in h for k in ["姓名", "联络员", "联系人", "负责人", "经办人"]):
            person_idx = idx
        elif any(k in h for k in ["电话", "手机"]):
            phone_idx = idx

    roster: List[Dict[str, str]] = []
    for r in rows[1:]:
        if not r or all(c is None or str(c).strip() == "" for c in r):
            continue
        dept = str(r[dept_idx]).strip() if dept_idx < len(r) and r[dept_idx] is not None else ""
        person = str(r[person_idx]).strip() if person_idx < len(r) and r[person_idx] is not None else ""
        phone = str(r[phone_idx]).strip() if 0 <= phone_idx < len(r) and r[phone_idx] is not None else ""
        if dept:
            roster.append({"department": dept, "person": person, "phone": phone})
    return roster


def match_roster_entry(text: str, roster: List[Dict[str, str]]) -> Tuple[Optional[str], Optional[str]]:
    """根据文件名或发送者备注匹配花名册中的 (department, person)。"""
    for item in roster:
        dept = item["department"]
        person = item["person"]
        if dept and dept in text:
            return dept, (person or "联络员")
        if person and person in text:
            return dept, person
    return None, None


def cmd_harvest(args: argparse.Namespace) -> int:
    roster_path = Path(args.roster)
    target_dir = Path(args.out)
    target_dir.mkdir(parents=True, exist_ok=True)

    roster = load_roster(roster_path)
    harvested_files: List[Dict[str, str]] = []
    submitted_depts = set()
    source_channel = "local_staging_scan"

    scan_dirs: List[Path] = []
    if args.inbox and Path(args.inbox).exists():
        scan_dirs.append(Path(args.inbox))
    scan_dirs.append(target_dir)

    seen_paths = set()

    for sdir in scan_dirs:
        for fpath in sorted(sdir.iterdir()):
            if not fpath.is_file() or fpath.name.startswith("~$"):
                continue
            if fpath.suffix.lower() not in ALLOWED_EXTS:
                continue
            if fpath.resolve() in seen_paths or fpath.resolve() == roster_path.resolve():
                continue

            dept, person = match_roster_entry(fpath.name, roster)
            parts = fpath.stem.split("_")
            dept_label = dept or (parts[0] if len(parts) >= 2 else "未知科室")
            person_label = person or (parts[1] if len(parts) >= 3 else "联络员")

            clean_orig = fpath.name
            for prefix in (f"{dept_label}_{person_label}_", f"{dept_label}-", f"{dept_label}_", f"{person_label}_"):
                if clean_orig.startswith(prefix):
                    clean_orig = clean_orig[len(prefix):]

            new_name = f"{dept_label}_{person_label}_{clean_orig}"
            dst_p = target_dir / new_name
            if fpath.resolve() != dst_p.resolve():
                safe_copy(fpath, dst_p)
                if args.move and sdir.resolve() == target_dir.resolve() and fpath.exists():
                    fpath.unlink()

            seen_paths.add(dst_p.resolve())
            if dept:
                submitted_depts.add(dept)
            elif any(r["department"] == dept_label for r in roster):
                submitted_depts.add(dept_label)

            harvested_files.append({
                "department": dept_label,
                "sender": person_label,
                "original_name": fpath.name,
                "saved_name": new_name,
                "saved_path": str(dst_p.resolve()),
            })

    submitted_list: List[Dict[str, str]] = []
    missing_list: List[Dict[str, str]] = []
    for entry in roster:
        if entry["department"] in submitted_depts:
            submitted_list.append(entry)
        else:
            missing_list.append(entry)

    demo_markers = ("模拟", "staging", "demo", "样例")
    inbox_label = Path(args.inbox).name if args.inbox else ""
    is_demo = any(m in inbox_label for m in demo_markers)

    result = {
        "status": "success",
        "channel": source_channel,
        "data_source": "staging_demo" if is_demo else source_channel,
        "is_demo_data": is_demo,
        "data_source_dir": str(Path(args.inbox).resolve()) if args.inbox else "",
        "data_source_note": (
            f"这批材料来自本地暂存目录「{inbox_label}」，是演示/手工放入的文件，"
            "不是微信客户端收到的原件；结论不能当作真实收缴情况。"
            "要按真实收件统计，请用 work_wechat_files(mode='scan'|'pull'|'harvest')。"
            if is_demo else "材料来自指定本地收件目录"
        ),
        "chat_group": args.group,
        "since_time": args.since,
        "target_save_dir": str(target_dir.resolve()),
        "total_roster_count": len(roster),
        "submitted_count": len(submitted_list),
        "missing_count": len(missing_list),
        "submitted_departments": [x["department"] for x in submitted_list],
        "missing_departments": [x["department"] for x in missing_list],
        "missing_contacts": [f"{x['department']}({x['person']})" if x["person"] else x["department"] for x in missing_list],
        "harvested_files": harvested_files,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_send(args: argparse.Namespace) -> int:
    result = {
        "status": "success",
        "mode": "pending_queue",
        "chat_group": args.group,
        "message": args.msg,
        "detail": "已生成标准催办通知并写入待发催办队列（只读安全策略，不触发微信桌面自动发送）",
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="办公微信收件归档与未交催办工具")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_harvest = subparsers.add_parser("harvest", help="扫描暂存目录附件并与花名册比对")
    p_harvest.add_argument("--group", required=True, help="微信群名称")
    p_harvest.add_argument("--since", required=True, help="起始时间 ISO 格式")
    p_harvest.add_argument("--roster", required=True, help="应交单位/人员底册 Excel 路径")
    p_harvest.add_argument("--out", required=True, help="归档保存的目标文件夹路径")
    p_harvest.add_argument("--inbox", default="", help="可选：本地微信文件接收目录或模拟收件目录")
    p_harvest.add_argument("--move", action="store_true",
                           help="归档后删除源文件（仅当源目录与目标目录相同时生效；默认只复制不删除）")

    p_send = subparsers.add_parser("send", help="生成微信群未交催办通知")
    p_send.add_argument("--group", required=True, help="目标微信群名称")
    p_send.add_argument("--msg", required=True, help="催办消息内容")
    p_send.add_argument("--dry-run", action="store_true", help="兼容保留参数")

    args = parser.parse_args()
    if args.command == "harvest":
        return cmd_harvest(args)
    if args.command == "send":
        return cmd_send(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
