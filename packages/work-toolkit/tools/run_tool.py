#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_tool.py — 按需解耦直调单个工具或组合流水线（仅加载目标工具所属模块，不连带加载其他模块）。

用法：
    # 1) 单工具按需调用（调用 office 工具只加载 office.py，调用微信工具只加载 comms.py）
    python tools/run_tool.py work_office_diff "{\"file_a\":\"a.xlsx\",\"file_b\":\"b.xlsx\"}"
    python tools/run_tool.py work_wechat_messages "{\"chat\":\"老吴园区\"}"

    # 2) 查看单模块或组合模块工具列表
    python tools/run_tool.py --list --module office
    python tools/run_tool.py --list --module comms,memory

    # 3) 跨模块组合批量调用（--batch 传入 [{"tool":"...","args":{...}}, ...]）
    python tools/run_tool.py --batch "[{\"tool\":\"work_wechat_files\",\"args\":{\"mode\":\"scan\"}},{\"tool\":\"work_ledger\",\"args\":{\"action\":\"summary\"}}]"
"""

import json
import logging
import os
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(ROOT, "mcp"))
sys.path.append(os.path.join(ROOT, "wechat"))
os.chdir(ROOT)

os.environ.setdefault("WORK_TOOLKIT_LIGHT", "1")

logging.disable(logging.CRITICAL)
for _name in ("comtypes", "comtypes.client", "urllib3"):
    logging.getLogger(_name).setLevel(logging.CRITICAL)
try:
    from loguru import logger as _loguru  # type: ignore

    _loguru.remove()
except Exception:  # pragma: no cover
    pass

try:
    import work_mods  # noqa: E402
except ImportError as _exc:
    _mod = getattr(_exc, "name", "") or str(_exc)
    print(f"加载模块层失败：缺依赖 {_mod}", file=sys.stderr)
    print(f"  当前解释器：{sys.executable}", file=sys.stderr)
    print("  先体检：python tools/doctor.py", file=sys.stderr)
    sys.exit(3)


def _usage(module_spec: str = "") -> None:
    boxes = work_mods.boxes(module_spec, include_other=not bool(module_spec)) if module_spec else work_mods.boxes()
    names = sorted(n for b in boxes for n in b.names())
    print("用法: python tools/run_tool.py <工具名> [JSON参数]", file=sys.stderr)
    print("      python tools/run_tool.py --list [--module comms|office|memory|comms,office]", file=sys.stderr)
    print('      python tools/run_tool.py --batch "[{\"tool\":\"...\",\"args\":{}}]"', file=sys.stderr)
    print("可用工具:", ", ".join(names), file=sys.stderr)
    sys.exit(2)


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "gbk", "utf-16"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


def _read_stdin(timeout: float = 0.6) -> str:
    if sys.stdin is None or sys.stdin.isatty():
        return ""
    box: dict[str, bytes] = {}

    def reader():
        try:
            box["data"] = sys.stdin.buffer.read()
        except Exception:
            box["data"] = b""

    th = threading.Thread(target=reader, daemon=True)
    th.start()
    th.join(timeout)
    return _decode(box.get("data", b""))


def _parse_json_arg(raw: str) -> dict:
    raw = (raw or "").strip()
    if raw.startswith("﻿"):
        raw = raw.lstrip("﻿").strip()
    if not raw:
        return {}
    if raw.startswith("@") and len(raw) > 1:
        with open(raw[1:], "rb") as fh:
            raw = _decode(fh.read()).strip()
    try:
        kwargs = json.loads(raw)
    except Exception as exc:
        print(f"JSON 参数解析失败: {exc} | 收到: {raw!r}", file=sys.stderr)
        sys.exit(2)
    if not isinstance(kwargs, dict):
        print('参数必须是 JSON 对象，例如 "{\"limit\":10}"', file=sys.stderr)
        sys.exit(2)
    return kwargs


_ARG_ALIASES = {"id": ("obs_id", "doc_id"), "file_path": ("file",), "path": ("file",), "group": ("chat",), "chat_name": ("chat",), "vars": ("vars", "vars_json")}


def _fit_kwargs(fn, kwargs: dict):
    """按函数签名校正参数：常见别名自动映射，未知参数返回可读错误而不是 TypeError 栈。"""
    import inspect
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return kwargs, None
    if any(p.kind == p.VAR_KEYWORD for p in params.values()):
        return kwargs, None
    fixed, unknown = {}, []
    for k, v in (kwargs or {}).items():
        if k in params:
            fixed[k] = v
            continue
        target = next((t for t in _ARG_ALIASES.get(k, ()) if t in params and t not in kwargs), None)
        if target:
            fixed[target] = v
        else:
            unknown.append(k)
    if unknown:
        return fixed, {"ok": False, "error": f"未知参数: {unknown}", "valid_params": list(params)}
    return fixed, None


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    argv = sys.argv[1:]
    if not argv:
        _usage()

    # 模式 1：--list [--module comms|office|memory]
    if argv[0] == "--list":
        mod_spec = ""
        if "--module" in argv:
            idx = argv.index("--module")
            if idx + 1 < len(argv):
                mod_spec = argv[idx + 1]
        mods = work_mods.describe(mod_spec if mod_spec else None)
        print(json.dumps({
            "ok": True,
            "loaded_modules": work_mods.loaded_ids(),
            "modules": mods,
        }, ensure_ascii=False, indent=2))
        return 0

    # 模式 2：--batch 跨模块组合流水线顺序调用
    if argv[0] == "--batch":
        raw_batch = argv[1] if len(argv) > 1 else _read_stdin()
        try:
            steps = json.loads(raw_batch)
            if not isinstance(steps, list):
                raise ValueError("--batch 参数必须是 JSON 数组")
        except Exception as exc:
            print(f"--batch JSON 解析失败: {exc}", file=sys.stderr)
            return 2
        results = []
        all_ok = True
        for i, step in enumerate(steps, start=1):
            tname = str((step or {}).get("tool") or "").strip()
            targs = (step or {}).get("args") or {}
            if isinstance(targs, str):
                targs = _parse_json_arg(targs)
            fn, mid = work_mods.load_tool(tname)
            if not callable(fn):
                results.append({"step": i, "tool": tname, "ok": False, "error": f"未知工具: {tname}"})
                all_ok = False
                continue
            targs, _err = _fit_kwargs(fn, targs)
            out_raw = json.dumps(_err, ensure_ascii=False) if _err else fn(**targs)
            try:
                out_data = json.loads(out_raw) if isinstance(out_raw, str) else out_raw
            except Exception:
                out_data = {"raw": out_raw}
            step_ok = bool(out_data.get("ok", True)) if isinstance(out_data, dict) else True
            if not step_ok:
                all_ok = False
            results.append({"step": i, "module": mid, "tool": tname, "ok": step_ok, "result": out_data})
        print(json.dumps({
            "ok": all_ok,
            "steps_count": len(results),
            "loaded_modules": work_mods.loaded_ids(),
            "results": results,
        }, ensure_ascii=False, indent=2))
        return 0 if all_ok else 1

    # 模式 3：单工具按需解耦调用（只加载该工具所属的单个模块！）
    name = argv[0]
    fn, mid = work_mods.load_tool(name)
    if not callable(fn) or not name.startswith("work_"):
        print(f"未知工具: {name}", file=sys.stderr)
        _usage()

    raw = argv[1] if len(argv) > 1 else _read_stdin()
    kwargs = _parse_json_arg(raw)

    kwargs, _err = _fit_kwargs(fn, kwargs)
    if _err:
        print(json.dumps(_err, ensure_ascii=False, indent=2))
        return 1
    out = fn(**kwargs)
    try:
        data = json.loads(out)
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0 if data.get("ok", True) else 1
    except Exception:
        print(out)
        return 0


if __name__ == "__main__":
    _code = 1
    try:
        _code = main()
    except SystemExit as _se:
        _code = int(_se.code) if isinstance(_se.code, int) else (1 if _se.code else 0)
    except BaseException:
        import traceback

        traceback.print_exc()
        _code = 1
    finally:
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass
    os._exit(_code)
