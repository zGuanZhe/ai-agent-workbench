"""微信 4.x 本地收件箱（只读）。

微信把**聊天中收到的文件**以明文存放在：
    <账号目录>\\msg\\file\\YYYY-MM\\原文件名

这条路径不需要数据库密钥、不需要读取进程内存、也不需要操作微信窗口 —— 纯文件系统只读。
（消息正文、群名、发送人昵称存在 db_storage 的加密库里，本模块不触碰。）

本模块提供：
  scan()   按时间/关键字/扩展名筛选收到的文件
  stats()  按月份、类型、来源维度统计
  pull()   复制到工作区并按规则重命名，附 sha256 去重
  match_roster()  用底册名单去匹配文件名，得出「已交/未交」
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Iterable, Sequence

BODY_EXTS = {".docx", ".doc", ".xlsx", ".xls", ".pdf", ".pptx", ".ppt", ".wps", ".et"}
ARCHIVE_EXTS = {".zip", ".rar", ".7z"}
MEDIA_EXTS = {".jpg", ".jpeg", ".png", ".mp4", ".mov", ".amr", ".mp3", ".wav"}
SKIP_PREFIX = (".~", "~$", ".")


@dataclass
class InboxFile:
    name: str
    path: str
    size: int
    mtime: float
    ext: str
    month: str
    kind: str

    @property
    def mtime_text(self) -> str:
        return datetime.fromtimestamp(self.mtime).strftime("%Y-%m-%d %H:%M")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["mtime_text"] = self.mtime_text
        d.pop("mtime", None)
        return d


def _kind(ext: str) -> str:
    e = ext.lower()
    if e in BODY_EXTS:
        return "document"
    if e in ARCHIVE_EXTS:
        return "archive"
    if e in MEDIA_EXTS:
        return "media"
    return "other"


def file_root(account_dir: str) -> str:
    return os.path.join(account_dir, "msg", "file")


def scan(
    account_dir: str,
    since: str = "",
    until: str = "",
    keyword: str = "",
    exts: Sequence[str] | None = None,
    kinds: Sequence[str] | None = None,
    include_hidden: bool = False,
    limit: int = 200,
    order: str = "desc",
) -> list[InboxFile]:
    """扫描微信收到的文件。

    since/until 支持 "YYYY-MM-DD" 或 "YYYY-MM-DDTHH:MM"；空字符串表示不限。
    """
    root = file_root(account_dir)
    if not os.path.isdir(root):
        return []

    t_since = _parse_ts(since, end=False)
    t_until = _parse_ts(until, end=True)
    exts_norm = {e.lower() if e.startswith(".") else "." + e.lower() for e in exts} if exts else None
    kinds_norm = set(kinds) if kinds else None
    kw = keyword.strip().lower()

    out: list[InboxFile] = []
    for entry in os.scandir(root):
        if not entry.is_dir():
            continue
        for f in os.scandir(entry.path):
            if not f.is_file():
                continue
            name = f.name
            if not include_hidden and name.startswith(SKIP_PREFIX):
                continue
            ext = os.path.splitext(name)[1].lower()
            kind = _kind(ext)
            if exts_norm and ext not in exts_norm:
                continue
            if kinds_norm and kind not in kinds_norm:
                continue
            if kw and kw not in name.lower():
                continue
            try:
                st = f.stat()
            except OSError:
                continue
            if t_since and st.st_mtime < t_since:
                continue
            if t_until and st.st_mtime > t_until:
                continue
            out.append(InboxFile(name, f.path, st.st_size, st.st_mtime, ext, entry.name, kind))

    out.sort(key=lambda x: x.mtime, reverse=(order != "asc"))
    return out[: max(1, int(limit))] if limit else out


def _parse_ts(text: str, end: bool) -> float | None:
    text = (text or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(text, fmt)
            if end and fmt == "%Y-%m-%d":
                dt = dt.replace(hour=23, minute=59, second=59)
            return dt.timestamp()
        except ValueError:
            continue
    return None


def stats(files: Iterable[InboxFile]) -> dict:
    by_month: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    by_ext: dict[str, int] = {}
    total = 0
    for f in files:
        total += f.size
        by_month[f.month] = by_month.get(f.month, 0) + 1
        by_kind[f.kind] = by_kind.get(f.kind, 0) + 1
        by_ext[f.ext or "(无扩展名)"] = by_ext.get(f.ext or "(无扩展名)", 0) + 1
    return {
        "count": sum(by_month.values()),
        "total_mb": round(total / 1048576, 2),
        "by_month": dict(sorted(by_month.items())),
        "by_kind": by_kind,
        "by_ext": dict(sorted(by_ext.items(), key=lambda kv: -kv[1])[:15]),
    }


def sha256_of(path: str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def safe_name(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", name).strip()
    return name[:120] or "unnamed"


def _existing_fingerprints(dest_dir: str) -> dict[str, str]:
    """扫目标目录已有文件，建 sha256 → 路径 索引。

    只按「单次调用内」去重是不够的：同一条命令跑第二遍仍会生成 `xxx(2).docx`
    副本，几次下来目录里全是同名副本。真幂等必须看落盘目录现状。
    """
    index: dict[str, str] = {}
    try:
        entries = [e for e in os.scandir(dest_dir) if e.is_file()]
    except OSError:
        return index
    for e in entries:
        if e.name.startswith("~$"):
            continue
        try:
            if e.stat().st_size == 0:
                continue
            index[sha256_of(e.path)] = e.path
        except OSError:
            continue
    return index


def pull(
    files: Sequence[InboxFile],
    dest_dir: str,
    prefix_rule: str = "{month}",
    dedupe: bool = True,
) -> list[dict]:
    """复制到工作区。prefix_rule 可用占位符 {month} {kind}。

    同名冲突时自动追加 (2)(3)；dedupe 时同 sha256 只拷一次——**包括目标目录里
    已经存在的同内容文件**，因此重复执行不会产生副本。
    """
    os.makedirs(dest_dir, exist_ok=True)
    seen_sha: dict[str, str] = _existing_fingerprints(dest_dir) if dedupe else {}
    results: list[dict] = []
    for f in files:
        target_name = safe_name(f.name)
        prefix = prefix_rule.format(month=f.month, kind=f.kind, ext=f.ext.lstrip(".")) if prefix_rule else ""
        if prefix:
            target_name = safe_name(f"{prefix}_{target_name}")
        dst = os.path.join(dest_dir, target_name)

        sha = sha256_of(f.path) if dedupe else ""
        if dedupe and sha in seen_sha:
            results.append({"src": f.path, "skipped": "duplicate", "same_as": seen_sha[sha]})
            continue

        base, ext = os.path.splitext(target_name)
        i = 2
        while os.path.exists(dst):
            dst = os.path.join(dest_dir, f"{base}({i}){ext}")
            i += 1
        try:
            shutil.copy2(f.path, dst)
            try:  # 微信原件多为只读，copy2 会把只读属性带到档案；解除以便后续编辑
                os.chmod(dst, 0o644)
            except OSError:
                pass
        except OSError as exc:
            results.append({"src": f.path, "error": str(exc)})
            continue
        if sha:
            seen_sha[sha] = dst
        results.append({"src": f.path, "dst": dst, "sha256": sha})
    return results


def match_roster(
    files: Sequence[InboxFile],
    roster: Sequence[str],
    extra_keywords: dict[str, Sequence[str]] | None = None,
) -> dict:
    """用底册名单匹配文件名，判断谁交了、谁没交。

    匹配规则：文件名包含名单中的名称即算已交；extra_keywords 可给某个名称追加别名。
    """
    hit: dict[str, list[str]] = {}
    unmatched: list[str] = []
    for f in files:
        owner = None
        for name in roster:
            keys = [name, *(extra_keywords or {}).get(name, [])]
            if any(k and k in f.name for k in keys):
                owner = name
                break
        if owner:
            hit.setdefault(owner, []).append(f.name)
        else:
            unmatched.append(f.name)
    missing = [n for n in roster if n not in hit]
    dup = {k: v for k, v in hit.items() if len(v) > 1}
    return {
        "submitted": sorted(hit),
        "missing": missing,
        "submitted_count": len(hit),
        "missing_count": len(missing),
        "duplicates": dup,
        "unmatched_files": unmatched,
        "detail": {k: v for k, v in sorted(hit.items())},
    }
