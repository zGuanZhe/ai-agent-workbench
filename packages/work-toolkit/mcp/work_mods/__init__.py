#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""work_mods — 工具箱的按需解耦模块层（支持单独加载、任意组合加载、全量加载）。

    comms   通讯：微信 4.x + 企业微信（只读）
    office  Office：通用富文档/PPT合成、版本对比、多文档合并、无模板汇总、排版、OfficeCLI、COM、高级 Excel/Word
    memory  记忆：材料台账 / 任务经验文档库 / 文件备份
    other   其他：状态总览 / 模块清单（公共底座）

解耦设计：
- 顶层 `import work_mods` **不主动导入任何业务子模块**；
- `load_module("office")` / `boxes(("other", "office"))` 仅加载指定模块，绝不触发微信/企微或台账模块导入；
- `boxes(("comms", "office"))` 可按任意组合挂载多个模块；
- `load_tool("work_office_diff")` 根据工具名前缀仅导入所属单模块。
"""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from typing import Any, Iterable, List, Optional

BUSINESS = ("comms", "office", "memory")
MOUNT_ORDER = ("other", "comms", "office", "memory")
_LOADED: dict[str, Any] = {}


def load_module(mid: str) -> Any:
    """按需懒加载单个子模块（comms / office / memory / other）。"""
    mid = (mid or "").strip().lower()
    if mid not in MOUNT_ORDER:
        raise KeyError(f"未知模块: {mid!r}（可选: {', '.join(MOUNT_ORDER)}）")
    if mid not in _LOADED:
        _LOADED[mid] = importlib.import_module(f".{mid}", __name__)
    return _LOADED[mid]


def loaded_ids() -> List[str]:
    """返回当前进程实际已加载的模块 id 列表（用于验证解耦隔离性）。"""
    return [m for m in MOUNT_ORDER if m in _LOADED]


def parse_module_spec(spec: str | Iterable[str] | None, include_other: bool = True) -> List[str]:
    """解析模块组合表达式（如 'comms'、'comms,office'、'all'），支持单独或任意组合。"""
    if spec is None:
        return list(MOUNT_ORDER)
    if isinstance(spec, str):
        raw = [x.strip().lower() for x in spec.replace("+", ",").replace(";", ",").split(",") if x.strip()]
    else:
        raw = [str(x).strip().lower() for x in spec if str(x).strip()]
    if not raw or "all" in raw or "*" in raw:
        return list(MOUNT_ORDER)
    picked: List[str] = []
    if include_other and "other" not in raw:
        picked.append("other")
    for m in raw:
        if m in MOUNT_ORDER and m not in picked:
            picked.append(m)
    return picked


class _LazyModuleMap(Mapping):
    """兼容旧代码 `work_mods.MODULES[mid]` / `.values()` / `.items()`，但只在访问时按需加载。"""

    def __getitem__(self, key: str) -> Any:
        return load_module(key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return load_module(key)
        except Exception:
            return default

    def __iter__(self):
        for k in MOUNT_ORDER:
            yield k

    def __len__(self) -> int:
        return len(MOUNT_ORDER)

    def keys(self):
        return MOUNT_ORDER

    def values(self):
        return [load_module(k) for k in MOUNT_ORDER]

    def items(self):
        return [(k, load_module(k)) for k in MOUNT_ORDER]


MODULES = _LazyModuleMap()


def boxes(ids: str | Iterable[str] | None = None, include_other: bool = False):
    """按模块 id（单个、列表或逗号分隔组合如 'comms,office'）按需加载并返回 ToolBox 列表。"""
    if ids is None:
        target_ids = list(MOUNT_ORDER)
    elif isinstance(ids, str):
        target_ids = parse_module_spec(ids, include_other=include_other)
    else:
        target_ids = [str(i).strip().lower() for i in ids if str(i).strip().lower() in MOUNT_ORDER]
        if include_other and "other" not in target_ids:
            target_ids.insert(0, "other")
    return [load_module(i).TOOLS for i in target_ids]


def infer_tool_module(tool_name: str) -> Optional[str]:
    """不导入全部模块，直接根据工具名前缀推导所属模块。"""
    t = (tool_name or "").strip()
    if t in ("work_status", "work_modules"):
        return "other"
    if t in ("work_ledger", "work_exp", "work_mem", "work_backup_file"):
        return "memory"
    if t.startswith(("work_wechat_", "work_wecom_", "work_comms_")):
        return "comms"
    if t.startswith("work_office") or t in ("work_excel_mcp", "work_word_mcp"):
        return "office"
    return None


def load_tool(tool_name: str):
    """仅加载目标工具所属的单个模块并返回可调用函数（实现单工具极速解耦调用）。"""
    mid = infer_tool_module(tool_name)
    if mid:
        mod = load_module(mid)
        fn = getattr(mod, tool_name, None)
        if callable(fn):
            return fn, mid
    for m in MOUNT_ORDER:
        mod = load_module(m)
        fn = getattr(mod, tool_name, None)
        if callable(fn):
            return fn, m
    return None, None


def describe(ids: str | Iterable[str] | None = None) -> list:
    """模块清单：支持只看指定模块或全部模块。"""
    target_ids = parse_module_spec(ids, include_other=True) if ids else list(MOUNT_ORDER)
    out = []
    for mid in target_ids:
        mod = load_module(mid)
        meta = dict(getattr(mod, "MODULE", {"id": mid, "name": mid}))
        names = mod.TOOLS.names()
        meta["tools"] = len(names)
        meta["names"] = names
        out.append(meta)
    return out


def __getattr__(name: str) -> Any:
    if name in MOUNT_ORDER:
        return load_module(name)
    if name == "ALL":
        return tuple(load_module(i).TOOLS for i in MOUNT_ORDER)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
