#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""install_skills.py — 把仓库里的 Skill 同步到 WorkBuddy，并回填本机路径。

    python scripts/install_skills.py            # 安装 / 自动更新（已装且内容一致则跳过）
    python scripts/install_skills.py --check   # 只看会做什么，不写文件
    python scripts/install_skills.py --force   # 内容一致也强制重写（先整体备份）

和早期「已存在就跳过」不同：仓库里的 Skill 更新后，再次运行会按内容比对自动同步，
并清掉目标目录里已经废弃的旧命令文件（例如 /exp、/exp-save、/work-setup），
保证用户看到的始终只有一个 /work 入口。

更新前的备份落在 `~/.workbuddy/skills.backups/`（skills 扫描根**之外**）。
早期版本把备份写成 `skills/<名字>.bak-<时间戳>/`，WorkBuddy 会把它们当独立 Skill
重复加载；本脚本会把这类遗留目录整体挪到 skills.backups/ 下。

仓库里的 SKILL.md 用 `<WORK_ROOT>` 占位，不含任何机器特定的绝对路径；
安装时按当前仓库位置替换，所以同一个仓库 clone 到哪台机器都能用。

只用标准库。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "skills"
DEFAULT_TARGET = Path.home() / ".workbuddy" / "skills"
PLACEHOLDER = "<WORK_ROOT>"
TEXT_SUFFIXES = {".md", ".json", ".txt", ".yaml", ".yml"}
RETIRED_SKILLS = ("codex-comms",)
CODEX_MODULE_SKILLS = ("work-comms", "work-office", "work-memory")


def _say(msg: str) -> None:
    print(msg, flush=True)


def render(text: str) -> str:
    """占位符回填：正斜杠上下文给 POSIX 路径，其余给 Windows 路径。"""
    text = text.replace(PLACEHOLDER + "/", ROOT.as_posix() + "/")
    return text.replace(PLACEHOLDER, str(ROOT))


def _files(base: Path) -> list[Path]:
    return sorted(
        p for p in base.rglob("*")
        if p.is_file() and not p.name.startswith(".") and "__pycache__" not in p.parts
    )


def _wanted(src_dir: Path) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for f in _files(src_dir):
        rel = f.relative_to(src_dir).as_posix()
        if f.suffix.lower() in TEXT_SUFFIXES:
            out[rel] = render(f.read_text(encoding="utf-8")).encode("utf-8")
        else:
            out[rel] = f.read_bytes()
    return out


def _have(dst_dir: Path) -> dict[str, bytes]:
    return {f.relative_to(dst_dir).as_posix(): f.read_bytes() for f in _files(dst_dir)}


def _backup_dir(target: Path) -> Path:
    """备份必须放在 skills 扫描根之外。

    早先把备份写成 `<target>/<name>.bak-<时间戳>/`，WorkBuddy 会把它们当成
    独立 Skill 再装一遍——用户界面里出现 3 个 work-office-toolkit、
    2 个 work-console-upgrade，还会真的重复加载。备份一律落到
    `<target>.backups/`（与 skills 同级、不同目录）。
    """
    d = target.parent / (target.name + ".backups")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _backup_path(dst_dir: Path, target: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    root = _backup_dir(target)
    p = root / f"{dst_dir.name}.{stamp}"
    n = 1
    while p.exists():
        p = root / f"{dst_dir.name}.{stamp}-{n}"
        n += 1
    return p


def _stray_backups(target: Path) -> list[Path]:
    """找出 skills/ 下会被 WorkBuddy 当成「第二个同名 Skill」加载的副本。

    ``target`` 是 skills 根目录（不是某个 Skill 目录）。判据不是「名字里有没有
    .bak-」——WorkBuddy 把每个含 SKILL.md 的子目录都算一个 Skill，所以
    `work-office-toolkit-copy`、`work-office-toolkit (2)`、
    `work-office-toolkit.bak-20261110` 都会让列表里出现两份。
    早先只认 `.bak-`，用户手工复制一份就漏过去了，doctor 还照样报 ok。

    现在按「以仓库里某个 Skill 名开头 + 自己有 SKILL.md + 不是本体」判定。
    """
    if not target.is_dir() or not SRC.is_dir():
        return []
    known = {p.name for p in SRC.iterdir()
             if p.is_dir() and (p / "SKILL.md").is_file()}
    if not known:
        return []
    out: list[Path] = []
    for p in target.iterdir():
        if not p.is_dir() or p.name in known:
            continue
        if not (p / "SKILL.md").is_file():
            continue
        if any(p.name.startswith(k) for k in known):
            out.append(p)
    return sorted(out)


def _prune_backups(backup_root: Path, known_skills: set[str], keep_backups: int, check: bool) -> list[str]:
    if not backup_root.is_dir():
        return []
    entries = sorted(backup_root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    if keep_backups <= 0:
        removed = [str(p) for p in entries]
        if not check:
            for p in entries:
                if p.is_dir():
                    shutil.rmtree(p)
                else:
                    p.unlink(missing_ok=True)
        return removed

    grouped: dict[str, list[Path]] = {}
    pruned: list[str] = []
    for p in entries:
        matched = next((k for k in sorted(known_skills, key=len, reverse=True) if p.name.startswith(k + ".")), None)
        if matched is None:
            pruned.append(str(p))
            if not check:
                if p.is_dir():
                    shutil.rmtree(p)
                else:
                    p.unlink(missing_ok=True)
            continue
        grouped.setdefault(matched, []).append(p)

    for _k, paths in grouped.items():
        for p in paths[keep_backups:]:
            pruned.append(str(p))
            if not check:
                if p.is_dir():
                    shutil.rmtree(p)
                else:
                    p.unlink(missing_ok=True)
    return pruned


def install(
    target: Path,
    force: bool = False,
    check: bool = False,
    prune_backups: bool = True,
    keep_backups: int = 2,
    sync_codex: bool = True,
) -> dict:
    if not SRC.is_dir():
        return {"ok": False, "error": f"仓库里没有 skills/ 目录：{SRC}"}

    known_skills = {
        p.name for p in SRC.iterdir()
        if p.is_dir() and (p / "SKILL.md").is_file()
    }
    removed_retired: list[str] = []
    for r_name in RETIRED_SKILLS:
        r_dir = target / r_name
        if r_dir.exists():
            removed_retired.append(str(r_dir))
            if not check:
                if r_dir.is_dir():
                    shutil.rmtree(r_dir)
                else:
                    r_dir.unlink(missing_ok=True)

    moved: list[dict] = []
    for stray in _stray_backups(target):
        dest = _backup_dir(target) / stray.name
        n = 1
        while dest.exists():
            dest = _backup_dir(target) / f"{stray.name}-{n}"
            n += 1
        if not check:
            shutil.move(str(stray), str(dest))
        moved.append({"from": str(stray), "to": str(dest)})

    items = []
    for src_dir in sorted(p for p in SRC.iterdir() if p.is_dir()):
        dst_dir = target / src_dir.name
        wanted = _wanted(src_dir)
        have = _have(dst_dir) if dst_dir.is_dir() else {}
        changed = [k for k, v in wanted.items() if have.get(k) != v]
        extra = [k for k in have if k not in wanted]

        if not dst_dir.is_dir():
            action = "install"
        elif changed or extra or force:
            action = "update"
        else:
            action = "skip"

        item = {
            "skill": src_dir.name,
            "target": str(dst_dir),
            "files": len(wanted),
            "action": action,
            "changed": changed,
            "removed": extra,
        }
        items.append(item)

        if check or action == "skip":
            continue

        if action == "update":
            backup = _backup_path(dst_dir, target)
            shutil.copytree(dst_dir, backup)
            item["backup"] = str(backup)
            shutil.rmtree(dst_dir)

        dst_dir.mkdir(parents=True, exist_ok=True)
        for rel, data in wanted.items():
            out = dst_dir / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(data)

    # 同步独立模块 Skill（work-comms / work-office / work-memory）到 ~/.codex/skills（实现 Codex 按模块解耦触发）
    codex_target = Path.home() / ".codex" / "skills"
    codex_synced: list[str] = []
    if sync_codex and codex_target.parent.is_dir():
        if not check:
            codex_target.mkdir(parents=True, exist_ok=True)
        for r_name in RETIRED_SKILLS:
            rc_dir = codex_target / r_name
            if rc_dir.exists():
                removed_retired.append(str(rc_dir))
                if not check:
                    if rc_dir.is_dir():
                        shutil.rmtree(rc_dir)
                    else:
                        rc_dir.unlink(missing_ok=True)
        if not check:
            for mod_skill in CODEX_MODULE_SKILLS:
                sdir = SRC / mod_skill
                if sdir.is_dir():
                    ddir = codex_target / mod_skill
                    wanted = _wanted(sdir)
                    have = _have(ddir) if ddir.is_dir() else {}
                    for extra_rel in have:
                        if extra_rel not in wanted:
                            (ddir / extra_rel).unlink(missing_ok=True)
                    ddir.mkdir(parents=True, exist_ok=True)
                    for rel, data in wanted.items():
                        out_f = ddir / rel
                        out_f.parent.mkdir(parents=True, exist_ok=True)
                        out_f.write_bytes(data)
                    codex_synced.append(mod_skill)

    pruned_backups: list[str] = []
    if prune_backups:
        b_root = target.parent / (target.name + ".backups")
        pruned_backups = _prune_backups(b_root, known_skills, keep_backups, check)

    return {
        "ok": True,
        "root": str(ROOT),
        "target": str(target),
        "codex_skills_synced": codex_synced,
        "check_only": check,
        "moved_stray_backups": moved,
        "removed_retired_skills": removed_retired,
        "pruned_backups": pruned_backups,
        "installed": sum(1 for i in items if i["action"] == "install"),
        "updated": sum(1 for i in items if i["action"] == "update"),
        "skipped": sum(1 for i in items if i["action"] == "skip"),
        "items": items,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="同步 WorkBuddy Skills 并回填项目路径")
    ap.add_argument("--check", action="store_true", help="只预览，不写文件")
    ap.add_argument("--force", action="store_true", help="内容一致也覆盖（先备份）")
    ap.add_argument("--target", default=str(DEFAULT_TARGET), help="Skills 目标目录")
    ap.add_argument("--keep-backups", type=int, default=2, help="每个 Skill 最多保留几份历史备份（0 表示清空）")
    ap.add_argument("--no-prune-backups", action="store_true", help="不自动清理旧备份")
    ap.add_argument("--no-sync-codex", action="store_true", help="不同步独立模块 Skill 到 ~/.codex/skills")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    rep = install(
        Path(args.target),
        force=args.force,
        check=args.check,
        prune_backups=not args.no_prune_backups,
        keep_backups=args.keep_backups,
        sync_codex=not args.no_sync_codex,
    )
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0 if rep.get("ok") else 1

    if not rep.get("ok"):
        _say("  ❌ " + str(rep.get("error")))
        return 1

    verb = "将要" if args.check else "已"
    _say(f"  Skills 目标：{rep['target']}")
    for m in rep.get("moved_stray_backups") or []:
        _say(f"    {verb}移出重复备份  {Path(m['from']).name}  →  {Path(m['to']).parent}")
    for i in rep["items"]:
        word = {"install": "安装", "update": "同步更新", "skip": "跳过（内容一致）"}[i["action"]]
        _say(f"    {verb}{word}  {i['skill']}  ({i['files']} 个文件)")
        if i["changed"]:
            _say(f"      更新：{', '.join(i['changed'])}")
        if i["removed"]:
            _say(f"      清理旧文件：{', '.join(i['removed'])}")
        if i.get("backup"):
            _say(f"      备份：{i['backup']}")
    if not args.check:
        _say("  模块已解耦：可按模块单独调用（/comms · /office · /memory 或直调独立 Skill/MCP），也可组合调用（/work · pipeline）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
