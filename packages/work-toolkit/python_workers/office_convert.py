#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用本机 Office（Microsoft Office / WPS）把文档转成 PDF。

为什么需要它：officecli 二进制不做 PDF 转换（命令面里没有 convert），
而「公文报送」这一步几乎一定要 PDF。COM 是最短路径，版面与人工「另存为 PDF」一致。

体制内不少机器只装 WPS，所以这里按 progid 逐个尝试：
  Word   : Word.Application   → KWPS.Application
  Excel  : Excel.Application  → KET.Application
  PPT    : PowerPoint.Application → KWPP.Application

用法::

    python office_convert.py --in a.docx --out a.pdf
    python office_convert.py --in a.xlsx --out a.pdf --sheet 1
    python office_convert.py --in a.docx --out a.pdf --engine wps
    python office_convert.py --in "dir" --out "dir_pdf"      # 批量

输出 JSON（stdout），字段：
    status / engine / output_pdf / pages / bytes / elapsed_ms / inputs

失败时 status=error，附 error 与 hint（比如「未安装 Office」「文件被占用」）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

WD_FORMAT_PDF = 17
PPT_PDF = 32

WORD_EXT = {".doc", ".docx", ".docm", ".rtf", ".wps", ".odt"}
EXCEL_EXT = {".xls", ".xlsx", ".xlsm", ".et", ".ods"}
PPT_EXT = {".ppt", ".pptx", ".pptm", ".dps"}

# (引擎标记, progid) —— 顺序即自动模式下的尝试顺序：先 MS，失败再 WPS
WORD_APPS = [("ms-word", "Word.Application"), ("wps-word", "KWPS.Application")]
EXCEL_APPS = [("ms-excel", "Excel.Application"), ("wps-excel", "KET.Application")]
PPT_APPS = [("ms-ppt", "PowerPoint.Application"), ("wps-ppt", "KWPP.Application")]


def _com():
    try:
        import win32com.client  # type: ignore
        import pythoncom  # type: ignore
        return win32com.client, pythoncom
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"缺少 pywin32，无法调用 Office COM：{exc}")


def _kill_quiet(app):
    try:
        app.Quit()
    except Exception:
        pass


def _pick_apps(candidates, engine: str):
    """engine=auto 全试；ms/wps 只试对应引擎。"""
    if engine in ("", "auto"):
        return candidates
    want = "ms" if engine.lower().startswith("ms") else "wps"
    picked = [c for c in candidates if c[0].startswith(want)]
    if not picked:
        raise RuntimeError(f"未知 engine：{engine}（可选 auto / ms / wps）")
    return picked


# --------------------------------------------------------------------------- #
# 三种文档：每个函数接收已确定的 progid，失败抛出异常交给上层换引擎
# --------------------------------------------------------------------------- #
def word_to_pdf(progid: str, src: str, dst: str) -> int:
    wc, pythoncom = _com()
    pythoncom.CoInitialize()
    app = None
    doc = None
    try:
        app = wc.DispatchEx(progid)
        try:
            app.Visible = False
        except Exception:
            pass
        try:
            app.DisplayAlerts = 0
        except Exception:
            pass
        doc = app.Documents.Open(
            os.path.abspath(src),
            ConfirmConversions=False,
            ReadOnly=True,
            AddToRecentFiles=False,
        )
        pages = 0
        try:
            pages = int(doc.ComputeStatistics(2))  # wdStatisticPages
        except Exception:
            pass
        try:
            doc.ExportAsFixedFormat(
                OutputFileName=os.path.abspath(dst),
                ExportFormat=WD_FORMAT_PDF,
                OpenAfterExport=False,
                OptimizeFor=0,          # wdExportOptimizeForPrint
                CreateBookmarks=1,      # wdExportCreateHeadingBookmarks
                DocStructureTags=True,
            )
        except Exception:
            # WPS 的部分版本不认 ExportAsFixedFormat，退回 SaveAs2/SaveAs
            try:
                doc.SaveAs2(os.path.abspath(dst), FileFormat=WD_FORMAT_PDF)
            except Exception:
                doc.SaveAs(os.path.abspath(dst), FileFormat=WD_FORMAT_PDF)
        return pages
    finally:
        if doc is not None:
            try:
                doc.Close(False)
            except Exception:
                pass
        if app is not None:
            _kill_quiet(app)
        pythoncom.CoUninitialize()


def excel_to_pdf(progid: str, src: str, dst: str, sheet: str = "") -> int:
    wc, pythoncom = _com()
    pythoncom.CoInitialize()
    app = None
    wb = None
    try:
        app = wc.DispatchEx(progid)
        try:
            app.Visible = False
        except Exception:
            pass
        try:
            app.DisplayAlerts = False
        except Exception:
            pass
        wb = app.Workbooks.Open(os.path.abspath(src), ReadOnly=True, UpdateLinks=0)

        if sheet:
            names = [str(wb.Worksheets(i).Name) for i in range(1, wb.Worksheets.Count + 1)]
            target = None
            for i, nm in enumerate(names, 1):
                if nm == sheet or str(i) == str(sheet):
                    target = wb.Worksheets(i)
                    break
            if target is None:
                raise RuntimeError(f"工作表不存在：{sheet}；可选：{'、'.join(names)}")
            target.Select()
            target.ExportAsFixedFormat(0, os.path.abspath(dst))
        else:
            wb.ExportAsFixedFormat(0, os.path.abspath(dst))  # xlTypePDF
        return 0
    finally:
        if wb is not None:
            try:
                wb.Close(False)
            except Exception:
                pass
        if app is not None:
            _kill_quiet(app)
        pythoncom.CoUninitialize()


def ppt_to_pdf(progid: str, src: str, dst: str) -> int:
    wc, pythoncom = _com()
    pythoncom.CoInitialize()
    app = None
    pres = None
    try:
        app = wc.DispatchEx(progid)
        # WPS 演示的 Presentations.Open 不接受 WithWindow
        try:
            pres = app.Presentations.Open(os.path.abspath(src), WithWindow=False, ReadOnly=True)
        except Exception:
            pres = app.Presentations.Open(os.path.abspath(src), ReadOnly=True)
        try:
            pres.SaveAs(os.path.abspath(dst), PPT_PDF)
        except Exception:
            pres.ExportAsFixedFormat(os.path.abspath(dst), PPT_PDF)
        try:
            return int(pres.Slides.Count)
        except Exception:
            return 0
    finally:
        if pres is not None:
            try:
                pres.Close()
            except Exception:
                pass
        if app is not None:
            _kill_quiet(app)
        pythoncom.CoUninitialize()


def _pdf_pages(path: str) -> int:
    """数 PDF 页数。

    Word 能自己报（ComputeStatistics），Excel/PowerPoint 的 COM 不回报页数，
    但报送单上常要填「共 X 页」，所以对产物再数一遍，别把这个活留给人工。
    """
    reader = None
    for mod in ("pypdf", "PyPDF2"):
        try:
            reader = __import__(mod, fromlist=["PdfReader"]).PdfReader(path)
            break
        except Exception:
            continue
    if reader is None:
        return 0
    try:
        return len(reader.pages)
    except Exception:
        return 0


def convert(src: str, dst: str, sheet: str = "", engine: str = "auto") -> dict:
    ext = Path(src).suffix.lower()
    if not os.path.isfile(src):
        raise FileNotFoundError(f"源文件不存在：{src}")
    os.makedirs(os.path.dirname(os.path.abspath(dst)) or ".", exist_ok=True)

    if ext in WORD_EXT:
        apps, kind = _pick_apps(WORD_APPS, engine), "word"
    elif ext in EXCEL_EXT:
        apps, kind = _pick_apps(EXCEL_APPS, engine), "excel"
    elif ext in PPT_EXT:
        apps, kind = _pick_apps(PPT_APPS, engine), "powerpoint"
    else:
        raise ValueError(f"不支持的格式：{ext}（支持 {sorted(WORD_EXT | EXCEL_EXT | PPT_EXT)}）")

    errors, pages, used = [], 0, ""
    for label, progid in apps:
        try:
            if kind == "word":
                pages = word_to_pdf(progid, src, dst)
            elif kind == "excel":
                pages = excel_to_pdf(progid, src, dst, sheet)
            else:
                pages = ppt_to_pdf(progid, src, dst)
            if os.path.exists(dst):
                used = label
                break
            errors.append(f"{label}: 命令成功但没生成 PDF")
        except Exception as exc:
            errors.append(f"{label}: {exc}")
            if os.path.exists(dst):  # 半成品不能留着骗人
                try:
                    os.remove(dst)
                except Exception:
                    pass
    if not used:
        raise RuntimeError("；".join(errors) or "没有可用的 Office 引擎")
    if pages <= 0:
        pages = _pdf_pages(dst)
    return {
        "engine": kind,
        "engine_used": used,
        "pages": pages,
        "bytes": os.path.getsize(dst),
        "attempts": errors or None,
    }


def _hint_for(msg: str) -> str:
    if "Invalid class string" in msg or "无效的类字符串" in msg or "800401F3" in msg or "没有注册类" in msg:
        return "本机未安装对应 Office 组件（MS Office / WPS），或 COM 未注册"
    if "拒绝访问" in msg or "being used" in msg or "0x800A175D" in msg:
        return "文件被其他程序占用，请先关闭再试"
    if "工作表不存在" in msg:
        return "sheet 传工作表名称或序号（从 1 开始）"
    return ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Office 文档转 PDF（本机 COM，支持 MS Office / WPS）")
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--out", dest="dst", required=True)
    ap.add_argument("--sheet", default="", help="只导某一张工作表（名称或序号）")
    ap.add_argument("--engine", default="auto", choices=["auto", "ms", "wps"],
                    help="auto=先 MS 再 WPS；ms=只用 Microsoft Office；wps=只用 WPS")
    args = ap.parse_args(argv)

    t0 = time.time()
    src = str(Path(args.src).resolve()) if not os.path.isabs(args.src) else args.src
    dst = str(Path(args.dst).resolve()) if not os.path.isabs(args.dst) else args.dst

    # 目录 → 批量
    if os.path.isdir(src):
        results = []
        outdir = dst if dst.lower().endswith((".pdf",)) is False else os.path.dirname(dst)
        os.makedirs(outdir, exist_ok=True)
        for name in sorted(os.listdir(src)):
            p = os.path.join(src, name)
            if not os.path.isfile(p):
                continue
            if Path(name).suffix.lower() not in (WORD_EXT | EXCEL_EXT | PPT_EXT):
                continue
            target = os.path.join(outdir, Path(name).stem + ".pdf")
            try:
                info = convert(p, target, engine=args.engine)
                results.append({"input": name, "output": target, "ok": True, **info})
            except Exception as exc:
                results.append({"input": name, "output": target, "ok": False,
                                "error": str(exc), "hint": _hint_for(str(exc))})
        ok = sum(1 for r in results if r["ok"])
        print(json.dumps({
            "status": "success" if ok == len(results) else ("partial" if ok else "error"),
            "mode": "batch",
            "converted": ok,
            "failed": len(results) - ok,
            "out_dir": outdir,
            "elapsed_ms": int((time.time() - t0) * 1000),
            "results": results,
        }, ensure_ascii=False, indent=2))
        return 0 if ok else 1

    try:
        info = convert(src, dst, args.sheet, args.engine)
    except Exception as exc:
        print(json.dumps({
            "status": "error",
            "input": src,
            "output": dst,
            "error": str(exc),
            "hint": _hint_for(str(exc)),
            "elapsed_ms": int((time.time() - t0) * 1000),
        }, ensure_ascii=False, indent=2))
        return 1

    print(json.dumps({
        "status": "success",
        "input": src,
        "output_pdf": dst,
        "pages": info["pages"],
        "bytes": info["bytes"],
        "engine": info["engine"],
        "engine_used": info["engine_used"],
        "attempts": info["attempts"],
        "elapsed_ms": int((time.time() - t0) * 1000),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
