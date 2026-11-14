#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""office_com.py — 直连本机 Office / WPS 的 COM 桥（走「看得见」的那条路）。

为什么单独做：officecli 是离线改 OpenXML，快且不依赖 Office；
但有三类活它干不了，必须用本机应用：
  1. 转 PDF（版面要与「另存为 PDF」完全一致，尤其公文红头与页码）
  2. 重算公式 / 刷新域和目录（Excel 重算、Word 的 TOC / 交叉引用）
  3. 用户正开着文档，要往里面写（所见即所得）

同时兼容 Microsoft Office 与 WPS：
  Word  : Word.Application        → KWPS.Application
  Excel : Excel.Application       → KET.Application
  PPT   : PowerPoint.Application  → KWPP.Application

用法::

    python office_com.py --action apps
    python office_com.py --action active
    python office_com.py --action run-python --payload-file script.json
    python office_com.py --action info --file a.xlsx
    python office_com.py --action recalc --file a.xlsx
    python office_com.py --action refresh-fields --file a.docx
    python office_com.py --action replace --file a.docx --payload '{"find":"甲","replace":"乙"}'
    python office_com.py --action read-cells --file a.xlsx --payload '{"sheet":"Sheet1","range":"A1:D10"}'
    python office_com.py --action set-cells --file a.xlsx --payload '{"sheet":"Sheet1","cells":{"A1":"x"}}'
    python office_com.py --action export-pdf --file a.docx --out a.pdf

`run-python` 的 payload::

    {"code": "ws = Officer.Excel.ActiveSheet\\nws.Cells(1,1).Value='甲'",
     "data": "可选，脚本里用 data 取",
     "keep_open": true}

统一输出 JSON：{"status": "success|error", ...}
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
import time
from pathlib import Path

WORD_APPS = [("ms-word", "Word.Application"), ("wps-word", "KWPS.Application")]
EXCEL_APPS = [("ms-excel", "Excel.Application"), ("wps-excel", "KET.Application")]
PPT_APPS = [("ms-ppt", "PowerPoint.Application"), ("wps-ppt", "KWPP.Application")]

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "workspace" / "06_运行状态"
PID_FILE = STATE_DIR / "office_com_pids.json"


def _record_pid(label: str, app) -> None:
    """把自己拉起的应用 PID 记到状态文件，供超时后清理。

    脚本卡在 C 层（time.sleep、COM 调用）时 settrace 拦不住，外层 subprocess 会
    直接强杀 worker——finally 不执行，Excel/WPS 就成了没人管的僵尸。
    只有这份「事先写好的记录」能让 MCP 层收尾。
    """
    try:
        import win32process  # type: ignore
        pid = int(win32process.GetWindowThreadProcessId(app.Hwnd)[1])
        if not pid:
            return
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        data = {}
        if PID_FILE.is_file():
            try:
                data = json.loads(PID_FILE.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        data[label] = {"pid": pid, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        PID_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

WORD_EXT = {".doc", ".docx", ".docm", ".rtf", ".wps", ".odt"}
EXCEL_EXT = {".xls", ".xlsx", ".xlsm", ".et", ".ods"}
PPT_EXT = {".ppt", ".pptx", ".pptm", ".dps"}

WD_REPLACE_ALL = 2
WD_FIND_CONTINUE = 1


def _com():
    try:
        import win32com.client  # type: ignore
        import pythoncom  # type: ignore
        return win32com.client, pythoncom
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"缺少 pywin32，无法调用 Office COM：{exc}") from exc


def _kind(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in WORD_EXT:
        return "word"
    if ext in EXCEL_EXT:
        return "excel"
    if ext in PPT_EXT:
        return "ppt"
    raise ValueError(f"不支持的格式：{ext}")


def _apps_for(kind: str):
    return {"word": WORD_APPS, "excel": EXCEL_APPS, "ppt": PPT_APPS}[kind]


def _pick(candidates, engine: str):
    if engine in ("", "auto"):
        return candidates
    want = "ms" if engine.lower().startswith("ms") else "wps"
    picked = [c for c in candidates if c[0].startswith(want)]
    if not picked:
        raise RuntimeError(f"未知 engine：{engine}（可选 auto / ms / wps）")
    return picked


def _registry_progids() -> set[str]:
    """快速判断本机注册了哪些 COM 组件，不去真的启动应用。"""
    found: set[str] = set()
    try:
        import winreg

        for progid in [p for _, p in WORD_APPS + EXCEL_APPS + PPT_APPS]:
            try:
                with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, progid + r"\CLSID"):
                    found.add(progid)
            except OSError:
                continue
    except Exception:
        pass
    return found


def _live_instances() -> dict:
    """哪些应用当前正在运行（能看到用户开着的文档）。"""
    out = {}
    try:
        wc, pythoncom = _com()
        pythoncom.CoInitialize()
        try:
            for label, progid in WORD_APPS + EXCEL_APPS + PPT_APPS:
                try:
                    obj = wc.GetActiveObject(progid)
                except Exception:
                    out[label] = {"running": False}
                    continue
                info = {"running": True}
                try:
                    if "excel" in label:
                        info["documents"] = int(obj.Workbooks.Count)
                    elif "ppt" in label:
                        info["documents"] = int(obj.Presentations.Count)
                    else:
                        info["documents"] = int(obj.Documents.Count)
                except Exception:
                    pass
                out[label] = info
        finally:
            pythoncom.CoUninitialize()
    except Exception as exc:
        out["error"] = str(exc)
    return out


# --------------------------------------------------------------------------- #
# 通用：按引擎顺序打开应用，失败换下一个
# --------------------------------------------------------------------------- #
def _with_app(kind: str, engine: str, opener, want_visible: bool = False):
    """opener(app) -> result；返回 (result, engine_used)。"""
    wc, pythoncom = _com()
    errors = []
    for label, progid in _pick(_apps_for(kind), engine):
        pythoncom.CoInitialize()
        app = None
        try:
            app = wc.DispatchEx(progid)
            try:
                app.Visible = want_visible
            except Exception:
                pass
            try:
                app.DisplayAlerts = 0 if kind != "excel" else False
            except Exception:
                pass
            return opener(app), label
        except Exception as exc:
            errors.append(f"{label}: {exc}")
        finally:
            if app is not None:
                try:
                    app.Quit()
                except Exception:
                    pass
            pythoncom.CoUninitialize()
    raise RuntimeError("；".join(errors) or "没有可用的 Office 引擎")


# --------------------------------------------------------------------------- #
# 具体动作
# --------------------------------------------------------------------------- #
def act_info(path: Path, engine: str) -> dict:
    kind = _kind(path)
    src = str(path.resolve())

    def opener(app):
        if kind == "word":
            doc = app.Documents.Open(src, ReadOnly=True, AddToRecentFiles=False)
            try:
                data = {
                    "paragraphs": int(doc.Paragraphs.Count),
                    "words": int(doc.ComputeStatistics(0)),
                    "pages": int(doc.ComputeStatistics(2)),
                    "tables": int(doc.Tables.Count),
                    "sections": int(doc.Sections.Count),
                }
                try:
                    data["title"] = str(doc.BuiltInDocumentProperties("Title").Value or "")
                except Exception:
                    pass
            finally:
                doc.Close(False)
            return data
        if kind == "excel":
            wb = app.Workbooks.Open(src, ReadOnly=True, UpdateLinks=0)
            try:
                sheets = []
                for i in range(1, wb.Worksheets.Count + 1):
                    ws = wb.Worksheets(i)
                    # 晚绑定下 ws.UsedRange 是 Range 对象（打印时才显示成值数组），
                    # 优先走 Range.Rows/Columns；取不到再退回按值数组维数数。
                    vals = ws.UsedRange
                    rows = cols = 0
                    try:
                        rows = int(vals.Rows.Count)
                        cols = int(vals.Columns.Count)
                    except Exception:
                        if isinstance(vals, tuple):
                            rows = len(vals)
                            cols = len(vals[0]) if isinstance(vals[0], tuple) else 1
                        elif vals is not None:
                            rows = cols = 1
                    sheets.append({"name": str(ws.Name), "rows": rows, "cols": cols})
                return {"sheets": sheets, "sheet_count": len(sheets)}
            finally:
                wb.Close(False)
        pres = app.Presentations.Open(src, WithWindow=False, ReadOnly=True)
        try:
            slides = []
            for i in range(1, pres.Slides.Count + 1):
                s = pres.Slides(i)
                slides.append({"index": i, "shapes": int(s.Shapes.Count)})
            return {"slides": pres.Slides.Count, "detail": slides[:50]}
        finally:
            pres.Close()

    data, used = _with_app(kind, engine, opener)
    return {"status": "success", "file": src, "kind": kind, "engine_used": used, **data}


def act_recalc(path: Path, engine: str) -> dict:
    if _kind(path) != "excel":
        raise ValueError("recalc 只适用于 Excel 文件")
    src = str(path.resolve())

    def opener(app):
        wb = app.Workbooks.Open(src, UpdateLinks=3)  # xlUpdateLinksAlways
        try:
            before = None
            try:
                before = str(wb.Worksheets(1).Range("A1").Value)
            except Exception:
                pass
            try:
                app.CalculateFullRebuild()
            except Exception:
                app.CalculateFull()
            wb.Save()
            return {"calculated": True, "probe_a1": before,
                    "sheets": int(wb.Worksheets.Count)}
        finally:
            wb.Close(True)

    data, used = _with_app("excel", engine, opener)
    return {"status": "success", "file": src, "engine_used": used, **data}


def act_refresh_fields(path: Path, engine: str) -> dict:
    if _kind(path) != "word":
        raise ValueError("refresh-fields 只适用于 Word 文件")
    src = str(path.resolve())

    def opener(app):
        doc = app.Documents.Open(src, AddToRecentFiles=False)
        try:
            fields = int(doc.Fields.Count)
            try:
                doc.Fields.Update()
            except Exception:
                pass
            tocs = 0
            try:
                tocs = int(doc.TablesOfContents.Count)
                for i in range(1, tocs + 1):
                    doc.TablesOfContents(i).Update()
            except Exception:
                pass
            pages_before = 0
            try:
                pages_before = int(doc.ComputeStatistics(2))
            except Exception:
                pass
            doc.Save()
            return {"fields": fields, "toc": tocs, "pages": pages_before}
        finally:
            doc.Close(True)

    data, used = _with_app("word", engine, opener)
    return {"status": "success", "file": src, "engine_used": used, **data}


def act_replace(path: Path, engine: str, payload: dict) -> dict:
    find = payload.get("find") or ""
    repl = payload.get("replace", "")
    if not find:
        raise ValueError("replace 需要 payload.find（要查找的原文）")
    if _kind(path) != "word":
        raise ValueError("replace 只支持 Word；Excel 请用 set-cells")
    src = str(path.resolve())
    match_case = bool(payload.get("match_case", False))
    whole_word = bool(payload.get("whole_word", False))

    def opener(app):
        doc = app.Documents.Open(src, AddToRecentFiles=False)
        try:
            rng = doc.Content
            f = rng.Find
            f.ClearFormatting()
            f.Replacement.ClearFormatting()
            f.Text = find
            f.Replacement.Text = repl
            f.Forward = True
            f.Wrap = WD_FIND_CONTINUE
            f.MatchCase = match_case
            f.MatchWholeWord = whole_word
            # 必须按位置传全 15 个参数：晚绑定下 `Execute(Replace=WD_REPLACE_ALL)`
            # 里的关键字会被 IDispatch 按名字匹配，Word 不认这个名字就静默按 0 处理——
            # 结果 replace 报 success:true，文档一个字没改。这是本轮最危险的一个假成功。
            hit = bool(f.Execute(find, match_case, whole_word, False, False, False,
                                 True, WD_FIND_CONTINUE, False, repl, WD_REPLACE_ALL,
                                 False, False, False, False))
            doc.Save()
            # 返回值同样不可靠，回读一次：搜不到原文才算真替换成功。
            verify = doc.Content.Find
            verify.ClearFormatting()
            verify.Text = find
            verify.Forward = True
            verify.Wrap = WD_FIND_CONTINUE
            verify.MatchCase = match_case
            verify.MatchWholeWord = whole_word
            left = bool(verify.Execute())
            return {"replaced": hit and not left, "find": find, "replace": repl,
                    "still_found": left,
                    "note": None if not left
                    else "替换后仍能搜到原文：可能只改了一部分，或参数/换行导致未命中"}
        finally:
            doc.Close(True)

    data, used = _with_app("word", engine, opener)
    return {"status": "success", "file": src, "engine_used": used, **data}


def act_read_cells(path: Path, engine: str, payload: dict) -> dict:
    if _kind(path) != "excel":
        raise ValueError("read-cells 只适用于 Excel 文件")
    src = str(path.resolve())
    sheet = payload.get("sheet") or ""
    rng = payload.get("range") or "A1:D20"

    def opener(app):
        wb = app.Workbooks.Open(src, ReadOnly=True, UpdateLinks=0)
        try:
            ws = wb.Worksheets(sheet) if sheet else wb.Worksheets(1)
            values = ws.Range(rng).Value
            if values is None:
                rows = []
            elif not isinstance(values, tuple):
                rows = [[values]]
            else:
                rows = [list(r) if isinstance(r, tuple) else [r] for r in values]
            return {"sheet": str(ws.Name), "range": rng, "rows": rows}
        finally:
            wb.Close(False)

    data, used = _with_app("excel", engine, opener)
    return {"status": "success", "file": src, "engine_used": used, **data}


def act_set_cells(path: Path, engine: str, payload: dict) -> dict:
    if _kind(path) != "excel":
        raise ValueError("set-cells 只适用于 Excel 文件")
    cells = payload.get("cells") or {}
    if not isinstance(cells, dict) or not cells:
        raise ValueError('set-cells 需要 payload.cells，形如 {"A1": "值", "B2": 12}')
    src = str(path.resolve())
    sheet = payload.get("sheet") or ""

    def opener(app):
        wb = app.Workbooks.Open(src, UpdateLinks=0)
        try:
            ws = wb.Worksheets(sheet) if sheet else wb.Worksheets(1)
            written = []
            for addr, val in cells.items():
                ws.Range(str(addr)).Value = val
                written.append(str(addr))
            wb.Save()
            return {"sheet": str(ws.Name), "written": written}
        finally:
            wb.Close(True)

    data, used = _with_app("excel", engine, opener)
    return {"status": "success", "file": src, "engine_used": used, **data}


def act_export_pdf(path: Path, engine: str, payload: dict) -> dict:
    out = payload.get("output") or str(path.with_suffix(".pdf"))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import office_convert  # type: ignore

    info = office_convert.convert(str(path), out, payload.get("sheet", ""), engine)
    return {"status": "success", "output_pdf": out, **info}


# --------------------------------------------------------------------------- #
# Officer：OfficeMCP 式的 COM 门面
# --------------------------------------------------------------------------- #
class Officer:
    """`Officer.Word` / `Officer.Excel` / `Officer.PPT` 直接拿到可操控的应用对象。

    取对象的顺序刻意是「先接管、再新建」：用户多半已经开着文档，
    这时候 GetActiveObject 拿到的就是**他眼前那个窗口**，改一下立刻能看见，
    和后台另开一个不可见的实例改文件是两回事。
    新建的实例默认可见，不用再教用户「怎么让 Word 显示出来」。
    """

    def __init__(self, engine: str = "auto", visible: bool = True):
        self.engine = engine or "auto"
        self.visible = visible
        self._apps: dict[str, tuple[str, object]] = {}
        self.started: list[str] = []      # 本次自己拉起来的，收尾要关掉
        self.attached: list[str] = []     # 接管的是用户已经开着的，绝不替用户关

    def _resolve(self, kind: str):
        if kind in self._apps:
            return self._apps[kind][1]
        wc, pythoncom = _com()
        errors = []
        for label, progid in _pick(_apps_for(kind), self.engine):
            try:
                app = wc.GetActiveObject(progid)
                self._apps[kind] = (label, app)
                self.attached.append(label)
                return app
            except Exception:
                pass
            try:
                app = wc.DispatchEx(progid)
                try:
                    app.Visible = self.visible
                except Exception:
                    pass
                try:
                    app.DisplayAlerts = 0 if kind != "excel" else False
                except Exception:
                    pass
                self._apps[kind] = (label, app)
                self.started.append(label)
                # 立刻把 PID 记到状态文件：万一脚本卡在 C 层（time.sleep / COM 阻塞），
                # settrace 拦不住，只能等外层超时强杀 worker——那时 finally 不会执行，
                # 只有这份记录能让 MCP 层把残留的 Excel/WPS 进程收掉。
                _record_pid(label, app)
                return app
            except Exception as exc:
                errors.append(f"{label}: {exc}")
        raise RuntimeError("；".join(errors) or f"没有可用的 {kind} 引擎")

    @property
    def Word(self):
        return self._resolve("word")

    @property
    def Excel(self):
        return self._resolve("excel")

    @property
    def PPT(self):
        return self._resolve("ppt")

    # OfficeMCP 里叫 PowerPoint / PPT，两个都留，免得智能体猜
    @property
    def PowerPoint(self):
        return self._resolve("ppt")

    def open(self, path: str):
        """按扩展名打开文件，返回对应的 Document / Workbook / Presentation。"""
        p = Path(path).resolve()
        kind = _kind(p)
        app = self._resolve(kind)
        if kind == "word":
            return app.Documents.Open(str(p), AddToRecentFiles=False)
        if kind == "excel":
            return app.Workbooks.Open(str(p))
        return app.Presentations.Open(str(p))

    def status(self) -> dict:
        return {
            "engine": self.engine,
            "visible": self.visible,
            "attached_to_running": self.attached,
            "started_by_me": self.started,
            "open_apps": {k: v[0] for k, v in self._apps.items()},
        }

    def quit_started(self) -> list[str]:
        """只关自己拉起来的；用户开着的应用一概不动。"""
        closed = []
        for kind, (label, app) in list(self._apps.items()):
            if label not in self.started:
                continue
            try:
                app.Quit()
                closed.append(label)
            except Exception:
                pass
            self._apps.pop(kind, None)
        self.started = [s for s in self.started if s not in closed]
        return closed


def act_active(engine: str) -> dict:
    """当前每个应用里正开着/激活的是哪个文件——接管窗口前先看清现场。"""
    wc, pythoncom = _com()
    pythoncom.CoInitialize()
    try:
        apps = {}
        for kind, candidates in (("word", WORD_APPS), ("excel", EXCEL_APPS), ("ppt", PPT_APPS)):
            for label, progid in _pick(candidates, engine):
                try:
                    app = wc.GetActiveObject(progid)
                except Exception:
                    continue
                entry: dict = {"engine": label, "running": True}
                try:
                    if kind == "excel":
                        entry["documents"] = int(app.Workbooks.Count)
                        active = app.ActiveWorkbook
                    elif kind == "ppt":
                        entry["documents"] = int(app.Presentations.Count)
                        active = app.ActivePresentation
                    else:
                        entry["documents"] = int(app.Documents.Count)
                        active = app.ActiveDocument
                    entry["active"] = str(getattr(active, "FullName", "") or getattr(active, "Name", ""))
                    try:
                        entry["saved"] = bool(active.Saved)
                    except Exception:
                        pass
                except Exception:
                    entry["active"] = ""
                apps[kind] = entry
                break
        return {
            "status": "success",
            "apps": apps,
            "note": "active=该应用里当前激活的文件；saved=false 表示有未保存改动",
        }
    finally:
        pythoncom.CoUninitialize()


def _jsonable(value, depth: int = 0):
    """把 output 尽量变成能进 JSON 的东西，别让返回体因为一个 COM 对象整体失败。"""
    if depth > 4:
        return str(value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v, depth + 1) for k, v in list(value.items())[:200]}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v, depth + 1) for v in list(value)[:200]]
    try:
        return str(value)
    except Exception:
        return f"<{type(value).__name__}>"


class _ScriptTimeout(Exception):
    """run_python 执行超时（与脚本自身的异常区分开）。"""


def _exec_with_timeout(code_obj, ns: dict, seconds: float) -> None:
    """在单进程内给 exec 加截止时间。

    exec 不像 subprocess 那样能 kill，死循环只能靠 sys.settrace 在字节码层面打断。
    局限：纯 C 层阻塞（某个 COM 调用卡死、time.sleep(600)）不触发 line 事件，
    trace 拦不住——那种情况交给外层 subprocess 超时兜底，进程会被强杀。
    """
    if not seconds or seconds <= 0:
        exec(code_obj, ns)
        return
    deadline = time.time() + seconds
    tick = [0]

    def tracer(frame, event, arg):
        tick[0] += 1
        # 每 500 行查一次时间：time.time() 有开销，逐行查会把大循环拖慢近一倍
        if tick[0] >= 500:
            tick[0] = 0
            if time.time() > deadline:
                raise _ScriptTimeout(
                    f"脚本执行超过 {seconds:g} 秒，已中断（可能死循环，或卡在某个 COM 调用上）")
        return tracer

    old = sys.gettrace()
    sys.settrace(tracer)
    try:
        exec(code_obj, ns)
    finally:
        sys.settrace(old)


def act_run_python(engine: str, payload: dict) -> dict:
    """注入 Officer + data 执行 Python，返回 stdout 与 output。

    这是 OfficeMCP 的招牌能力，也是「实时填表、画图、调格式」唯一做得干净的路子：
    固定动作永远覆盖不全，而用户要的往往就是「把这一页按我说的排一下」。
    代价是任意代码执行——调用方必须先获得用户同意，这一点写在工具描述里。
    """
    code = (payload.get("code") or "").strip()
    if not code:
        return {"status": "error", "error": "code 不能为空"}
    data = payload.get("data", "")
    keep_open = bool(payload.get("keep_open", True))

    wc, pythoncom = _com()          # noqa: F841  (确认 pywin32 在，再往下走)
    pythoncom.CoInitialize()
    off = Officer(engine=engine, visible=bool(payload.get("visible", True)))
    buf = io.StringIO()
    ns = {
        "Officer": off,
        "office": off,              # 别名，减少「Officer 大写」这种低级错误
        "data": data,
        "output": None,
        "json": json,
        "os": os,
        "Path": Path,
        "__name__": "__office_script__",
    }
    t0 = time.time()
    failed = False
    try:
        with contextlib.redirect_stdout(buf):
            _exec_with_timeout(compile(code, "<run_python>", "exec"), ns,
                               float(payload.get("timeout") or 120))
        return {
            "status": "success",
            "stdout": buf.getvalue()[-8000:],
            "output": _jsonable(ns.get("output")),
            "officer": off.status(),
            "kept_open": keep_open,
            "elapsed_ms": int((time.time() - t0) * 1000),
        }
    except Exception as exc:
        failed = True
        # hint 要按异常类型分流：语法错误却教「VBA 属性写法」，等于让调用方
        # 在完全错误的方向上改三轮。
        if isinstance(exc, _ScriptTimeout):
            hint = ("脚本超时被中断。检查死循环；确实需要更久就调大 timeout"
                    "（纯 C 层阻塞如 COM 卡死，trace 拦不住，只能靠进程超时）")
        elif isinstance(exc, SyntaxError):
            where = f"第 {exc.lineno} 行" if getattr(exc, "lineno", None) else "代码里"
            line = (getattr(exc, "text", "") or "").strip()
            hint = f"语法错误在{where}：{line[:80]}。先修语法，别改 COM 调用"
        elif isinstance(exc, NameError):
            hint = ("用了没定义的变量。脚本里可直接用：Officer（别名 office）、data、output、"
                    "json、os、Path；其余模块要自己 import")
        elif isinstance(exc, (AttributeError, TypeError)):
            hint = ("属性或方法名不对。VBA 写法：Cells(1,1).Value（集合从 1 开始）、"
                    "UsedRange 先判空、改完必须 Save() 才落盘")
        else:
            hint = ("COM 调用失败。常见原因：文件被别的程序占用、本机没装对应应用、"
                    "权限不足；先手动打开一次确认能正常编辑")
        return {
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
            "stdout": buf.getvalue()[-4000:],
            "officer": off.status(),
            "hint": hint,
            "elapsed_ms": int((time.time() - t0) * 1000),
        }
    finally:
        # 脚本失败时不保留自己拉起的实例：用户根本看不到窗口（visible 可能为 false），
        # 进程却占着内存和文件锁，下次再调还会被占用的文件卡住。
        # 只关「我拉起的」，用户自己开着的窗口一律不碰。
        if (not keep_open) or failed:
            off.quit_started()
        pythoncom.CoUninitialize()



def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Office / WPS COM 桥")
    ap.add_argument("--action", required=True,
                    choices=["apps", "active", "run-python", "info", "recalc",
                             "refresh-fields", "replace", "read-cells", "set-cells",
                             "export-pdf"])
    ap.add_argument("--file", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--engine", default="auto", choices=["auto", "ms", "wps"])
    ap.add_argument("--payload", default="{}", help="动作附加参数的 JSON")
    # 32k 命令行上限：run_python 的代码很容易超，用文件传
    ap.add_argument("--payload-file", default="", help="从文件读 payload（优先于 --payload）")
    args = ap.parse_args(argv)

    t0 = time.time()
    try:
        raw = args.payload
        if args.payload_file:
            raw = Path(args.payload_file).read_text(encoding="utf-8")
        payload = json.loads(raw or "{}")
        if not isinstance(payload, dict):
            raise ValueError("payload 必须是 JSON 对象")
        if args.action == "apps":
            data = {
                "status": "success",
                "registered": sorted(_registry_progids()),
                "live": _live_instances(),
                "note": "registered=本机已注册的 COM 组件；live=当前正在运行的应用实例",
            }
        elif args.action == "active":
            data = act_active(args.engine)
        elif args.action == "run-python":
            data = act_run_python(args.engine, payload)
        else:
            if not args.file:
                raise ValueError("这个动作需要 --file")
            path = Path(args.file).resolve()
            if not path.is_file():
                raise FileNotFoundError(f"文件不存在：{path}")
            if args.action == "info":
                data = act_info(path, args.engine)
            elif args.action == "recalc":
                data = act_recalc(path, args.engine)
            elif args.action == "refresh-fields":
                data = act_refresh_fields(path, args.engine)
            elif args.action == "replace":
                data = act_replace(path, args.engine, payload)
            elif args.action == "read-cells":
                data = act_read_cells(path, args.engine, payload)
            elif args.action == "set-cells":
                data = act_set_cells(path, args.engine, payload)
            else:
                if args.out:
                    payload["output"] = args.out
                data = act_export_pdf(path, args.engine, payload)
        data["elapsed_ms"] = int((time.time() - t0) * 1000)
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
        return 0
    except Exception as exc:
        msg = str(exc)
        hint = ""
        if "没有注册类" in msg or "无效的类字符串" in msg or "800401F3" in msg:
            hint = "本机没装这个组件：用 action=apps 看可用应用，或改用 OfficeCLI 离线路径"
        elif "占用" in msg or "being used" in msg or "0x800A175D" in msg:
            hint = "文件被其他程序打开着，先关闭再试（或改用 OfficeCLI）"
        print(json.dumps({
            "status": "error", "action": args.action, "error": msg, "hint": hint,
            "elapsed_ms": int((time.time() - t0) * 1000),
        }, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    sys.exit(main())
