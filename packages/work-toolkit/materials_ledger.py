#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""材料台账：把每一次收件记成可追溯的流水。

解决的问题很具体——「收材料」这件事，光把文件拷过来不够，还要能回答：

    * 谁交了、谁没交（对着科室底册比）
    * 某个科室交了几次（上午交了一版，下午改了一版，哪一版算数）
    * 这份材料是什么时候、从哪个渠道进来的

没有台账时，每次都只能重扫目录、重算一遍，还回答不了「改了几次」。

存储：``workspace/08_材料台账/materials.db``（SQLite，单文件，可备份可删）

两张表：

    batches  一次收件动作（时间 / 来源 / 说明 / 份数 / 涉及科室）
    intake   每份材料的指纹（sha256 主键）+ 落点 + 科室 + 收发时间 + 被扫到次数

同一个文件重复扫到不会新增行，只把 ``seen_count`` 加一并刷新 ``last_seen``；
同一科室交了不同内容则是多行——这正是「改了几版」的证据。
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

ROOT = Path(__file__).resolve().parent
LEDGER_DIR = ROOT / "workspace" / "08_材料台账"
DB_PATH = LEDGER_DIR / "materials.db"
ROSTER = ROOT / "workspace" / "科室底册.xlsx"

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS batches (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    at        TEXT    NOT NULL,
    note      TEXT    DEFAULT '',
    source    TEXT    DEFAULT '',
    files     INTEGER DEFAULT 0,
    added     INTEGER DEFAULT 0,
    depts     TEXT    DEFAULT ''
);

CREATE TABLE IF NOT EXISTS intake (
    sha256      TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    src         TEXT DEFAULT '',
    dst         TEXT DEFAULT '',
    dept        TEXT DEFAULT '',
    sender      TEXT DEFAULT '',
    ext         TEXT DEFAULT '',
    size        INTEGER DEFAULT 0,
    received_at TEXT DEFAULT '',
    first_seen  TEXT DEFAULT '',
    last_seen   TEXT DEFAULT '',
    seen_count  INTEGER DEFAULT 1,
    batch_id    INTEGER
);

CREATE INDEX IF NOT EXISTS idx_intake_dept ON intake(dept);
CREATE INDEX IF NOT EXISTS idx_intake_last ON intake(last_seen);
"""

_DATE_HEAD = re.compile(r"^[\d\-_.．年月日]+")
NON_DEPT = {"", "新建", "未命名", "文档", "表格", "副本", "扫描", "图片", "附件", "微信图片"}


# --------------------------------------------------------------------------- #
# 基础设施
# --------------------------------------------------------------------------- #
def sha256_of(path: str | Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def dept_from_name(stem: str) -> str:
    """从文件名首段猜科室，只在它真的像单位名时才认。

    与 merge_docx_tables 保持同一套规则，避免台账和汇总对不上：
    首段是日期、太长、或是「新建/副本」这类泛称时，一律不认。
    """
    parts = re.split(r"[_＿]", stem)
    if len(parts) < 2:
        return ""
    head = parts[0].strip()
    if not head or len(head) > 12 or _DATE_HEAD.match(head) or head in NON_DEPT:
        return ""
    return head


def sender_from_name(stem: str) -> str:
    parts = re.split(r"[_＿]", stem)
    return parts[1].strip() if len(parts) >= 3 else ""


def connect(path: str | Path = DB_PATH) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# 科室底册
# --------------------------------------------------------------------------- #
def roster(path: str | Path = ROSTER) -> List[Dict[str, str]]:
    """读科室底册。返回 [{dept, contact, phone}]，读不到就返回空表。"""
    p = Path(path)
    if not p.exists():
        return []
    try:
        import openpyxl  # type: ignore
    except Exception:
        return []
    try:
        wb = openpyxl.load_workbook(str(p), data_only=True)
    except Exception:
        return []
    out: List[Dict[str, str]] = []
    for ws in wb.worksheets:
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            continue
        header = [str(c or "").strip() for c in rows[0]]
        idx_dept = next((i for i, h in enumerate(header) if "科室" in h or "单位" in h or "部门" in h), None)
        if idx_dept is None:
            continue
        idx_contact = next((i for i, h in enumerate(header) if "联络" in h or "负责" in h or "联系人" in h), None)
        idx_phone = next((i for i, h in enumerate(header) if "电话" in h or "手机" in h), None)
        for row in rows[1:]:
            if idx_dept >= len(row):
                continue
            dept = str(row[idx_dept] or "").strip()
            if not dept:
                continue
            out.append({
                "dept": dept,
                "contact": str(row[idx_contact] or "").strip() if idx_contact is not None and idx_contact < len(row) else "",
                "phone": str(row[idx_phone] or "").strip() if idx_phone is not None and idx_phone < len(row) else "",
            })
    return out


# --------------------------------------------------------------------------- #
# 记账
# --------------------------------------------------------------------------- #
def record(
    items: Sequence[Dict[str, Any]],
    note: str = "",
    source: str = "",
    dedupe_by: str = "sha256",
) -> Dict[str, Any]:
    """把一批收件写进台账。

    items 每项至少要有 ``path``；``name/dept/sender/ext/size/received_at`` 可由本函数补。
    返回 {batch_id, at, files, added, repeated, depts}。
    """
    con = connect()
    try:
        at = _now()
        added = repeated = 0
        depts: List[str] = []
        cur = con.execute(
            "INSERT INTO batches (at, note, source, files, added, depts) VALUES (?,?,?,?,?,?)",
            (at, note, source, len(items), 0, ""),
        )
        batch_id = int(cur.lastrowid)

        for it in items:
            path = str(it.get("path") or it.get("src") or "")
            name = str(it.get("name") or os.path.basename(path))
            stem = os.path.splitext(name)[0]
            dept = str(it.get("dept") or dept_from_name(stem))
            sender = str(it.get("sender") or sender_from_name(stem))
            ext = str(it.get("ext") or os.path.splitext(name)[1]).lower()
            size = int(it.get("size") or 0)
            if not size and path and os.path.isfile(path):
                try:
                    size = os.path.getsize(path)
                except OSError:
                    size = 0

            if dedupe_by == "sha256":
                try:
                    digest = it.get("sha256") or (sha256_of(path) if path and os.path.isfile(path) else "")
                except OSError:
                    digest = ""
            else:
                digest = ""
            if not digest:  # 文件读不到时退化成「名字+大小」，至少不丢记录
                digest = "name:" + hashlib.md5(f"{name}|{size}".encode()).hexdigest()

            received = str(it.get("received_at") or "")
            if not received and path and os.path.isfile(path):
                try:
                    received = datetime.fromtimestamp(os.path.getmtime(path)).isoformat(timespec="seconds")
                except OSError:
                    received = ""

            row = con.execute("SELECT sha256, seen_count, dept, name FROM intake WHERE sha256=?", (digest,)).fetchone()
            if row:
                # 同一个文件第一次是以「2026-11_xxx.docx」这种没前缀的名字进来的，
                # 后来按「财务科_张三_xxx.docx」再记一次时，dept 必须补上——
                # 否则 sha256 命中去重会把更好的信息丢掉，台账永远学不会归科室。
                fill_dept = dept if (dept and not row["dept"]) else ""
                con.execute(
                    "UPDATE intake SET seen_count=seen_count+1, last_seen=?,"
                    " dst=COALESCE(NULLIF(?,''), dst),"
                    " dept=CASE WHEN ?<>'' THEN ? ELSE dept END,"
                    " sender=CASE WHEN sender='' AND ?<>'' THEN ? ELSE sender END"
                    " WHERE sha256=?",
                    (at, str(it.get("dst") or ""),
                     fill_dept, fill_dept, sender, sender, digest),
                )
                repeated += 1
                if fill_dept or row["dept"]:
                    depts.append(fill_dept or row["dept"])
            else:
                con.execute(
                    "INSERT INTO intake (sha256, name, src, dst, dept, sender, ext, size,"
                    " received_at, first_seen, last_seen, seen_count, batch_id)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,1,?)",
                    (digest, name, path, str(it.get("dst") or ""), dept, sender, ext,
                     size, received, at, at, batch_id),
                )
                added += 1
                if dept:
                    depts.append(dept)

        uniq = list(dict.fromkeys(d for d in depts if d))
        con.execute(
            "UPDATE batches SET added=?, depts=? WHERE id=?",
            (added, ",".join(uniq), batch_id),
        )
        con.commit()
        return {
            "ok": True,
            "batch_id": batch_id,
            "at": at,
            "files": len(items),
            "added": added,
            "repeated": repeated,
            "depts": uniq,
        }
    finally:
        con.close()


# --------------------------------------------------------------------------- #
# 查询
# --------------------------------------------------------------------------- #
def summary(con: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    own = con is None
    con = con or connect()
    try:
        total = con.execute("SELECT COUNT(*) c FROM intake").fetchone()["c"]
        depts = con.execute(
            "SELECT COUNT(DISTINCT dept) c FROM intake WHERE dept<>''"
        ).fetchone()["c"]
        batches = con.execute("SELECT COUNT(*) c FROM batches").fetchone()["c"]
        last = con.execute(
            "SELECT * FROM batches ORDER BY id DESC LIMIT 1"
        ).fetchone()
        size = con.execute("SELECT COALESCE(SUM(size),0) s FROM intake").fetchone()["s"]
        multi = con.execute(
            "SELECT COUNT(*) c FROM (SELECT dept FROM intake WHERE dept<>''"
            " GROUP BY dept HAVING COUNT(*)>1)"
        ).fetchone()["c"]
        # 真实微信文件名多半是「2026-11_xxx.pdf」，没有科室前缀，dept 会是空。
        # 只报「0 个科室」会让人以为台账坏了，把归不到科室的数量单独说出来。
        unassigned = con.execute(
            "SELECT COUNT(*) c FROM intake WHERE dept='' OR dept IS NULL"
        ).fetchone()["c"]
        return {
            "ok": True,
            "total_files": total,
            "departments": depts,
            "unassigned_files": unassigned,
            "batches": batches,
            "multi_submit_depts": multi,
            "total_bytes": size,
            "last_batch": dict(last) if last else None,
            "dept_rule": "按文件名里的「科室_姓名_事由」前缀识别；没有前缀就归不到科室",
            "db": str(DB_PATH),
        }
    finally:
        if own:
            con.close()


def departments(con: Optional[sqlite3.Connection] = None) -> List[Dict[str, Any]]:
    """按科室汇总：交了几份、最近一次什么时候、经手人。"""
    own = con is None
    con = con or connect()
    try:
        rows = con.execute(
            "SELECT dept, COUNT(*) files, SUM(size) bytes, MAX(last_seen) last_seen,"
            " MAX(received_at) received_at, GROUP_CONCAT(DISTINCT sender) senders"
            " FROM intake WHERE dept<>'' GROUP BY dept ORDER BY last_seen DESC"
        ).fetchall()
        return [
            {
                "dept": r["dept"],
                "files": r["files"],
                "bytes": r["bytes"] or 0,
                "last_seen": r["last_seen"],
                "received_at": r["received_at"],
                "senders": [s for s in (r["senders"] or "").split(",") if s],
                "resubmitted": (r["files"] or 0) > 1,
            }
            for r in rows
        ]
    finally:
        if own:
            con.close()


def missing(roster_path: str | Path = ROSTER,
            since: str = "",
            con: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    """对着底册算未交名单。``since`` 只统计该时间之后收到的材料。"""
    own = con is None
    con = con or connect()
    try:
        book = roster(roster_path)
        got = {d["dept"]: d for d in departments(con)}
        if since:
            got = {d["dept"]: d for d in departments(con) if (d["last_seen"] or "") >= since}
        out = []
        for person in book:
            dept = person["dept"]
            hit = got.get(dept)
            out.append({
                "dept": dept,
                "contact": person.get("contact", ""),
                "phone": person.get("phone", ""),
                "submitted": bool(hit),
                "files": hit["files"] if hit else 0,
                "last_seen": hit["last_seen"] if hit else "",
                "resubmitted": bool(hit and hit["resubmitted"]),
            })
        missing_list = [x for x in out if not x["submitted"]]
        extra = [d for d in got if book and d not in {b["dept"] for b in book}]
        return {
            "ok": True,
            "roster_count": len(book),
            "submitted_count": len(out) - len(missing_list),
            "missing_count": len(missing_list),
            "missing": missing_list,
            "unexpected": extra,
            "all": out,
            "since": since or None,
            "roster_source": str(roster_path),
        }
    finally:
        if own:
            con.close()


def history(dept: str = "", limit: int = 60,
            con: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    """某科室（或全部）的收件明细，按收到时间倒序。"""
    own = con is None
    con = con or connect()
    try:
        sql = "SELECT * FROM intake"
        args: List[Any] = []
        if dept:
            sql += " WHERE dept=?"
            args.append(dept)
        sql += " ORDER BY COALESCE(received_at,'') DESC, last_seen DESC LIMIT ?"
        args.append(max(1, int(limit)))
        rows = con.execute(sql, args).fetchall()
        return {
            "ok": True,
            "dept": dept or None,
            "count": len(rows),
            "items": [
                {
                    "name": r["name"],
                    "dept": r["dept"],
                    "sender": r["sender"],
                    "ext": r["ext"],
                    "size": r["size"],
                    "received_at": r["received_at"],
                    "first_seen": r["first_seen"],
                    "last_seen": r["last_seen"],
                    "seen_count": r["seen_count"],
                    "path": r["dst"] or r["src"],
                    "sha256": (r["sha256"] or "")[:16],
                }
                for r in rows
            ],
        }
    finally:
        if own:
            con.close()


def batches(limit: int = 20, con: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    own = con is None
    con = con or connect()
    try:
        rows = con.execute("SELECT * FROM batches ORDER BY id DESC LIMIT ?",
                           (max(1, int(limit)),)).fetchall()
        return {"ok": True, "count": len(rows), "items": [dict(r) for r in rows]}
    finally:
        if own:
            con.close()


def state(watermark: str = "") -> Dict[str, Any]:
    """增量水位：返回上次记到的时间，供 harvest 只处理新文件。"""
    con = connect()
    try:
        row = con.execute("SELECT MAX(last_seen) t, COUNT(*) c FROM intake").fetchone()
        return {"ok": True, "last_seen": row["t"], "files": row["c"],
                "requested": watermark or None, "db": str(DB_PATH)}
    finally:
        con.close()
