#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""launch.py — 控制台启动器：自己挑一个装齐依赖的解释器，再跑构建 + 服务。

存在的理由：`python` 在 Windows 上经常不是「装了 mcp 的那个」。
直接 `python web/serve.py` 会抛 `No module named 'mcp'`，而项目根下正好有个
mcp/ 目录，看起来像命名空间冲突，实际是解释器选错了。这个脚本只用标准库，
所以哪怕当前解释器什么依赖都没有，它也能跑起来、换个解释器重来。

用法：
    python scripts/launch.py                 # 构建 + 起服务（默认 8765）
    python scripts/launch.py --port 8802     # 换端口
    python scripts/launch.py --no-build      # 跳过构建，直接起
    python scripts/launch.py --no-open       # 不自动开浏览器
    python scripts/launch.py --doctor        # 只体检
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"


def _load_doctor():
    sys.path.insert(0, str(TOOLS))
    import doctor  # type: ignore

    return doctor


def _has_deps_here(doctor) -> bool:
    info = doctor.probe(sys.executable)
    return doctor.is_ready(info)


def main() -> int:
    args = sys.argv[1:]
    doctor = _load_doctor()

    if "--doctor" in args:
        sys.argv = [sys.argv[0]] + [a for a in args if a != "--doctor"]
        return doctor.main()

    # 当前解释器就够用就别折腾
    if _has_deps_here(doctor):
        py = sys.executable
    else:
        print("  当前解释器缺依赖，正在找可用的……", file=sys.stderr)
        py, _ = doctor.find_best(verbose=True)
        if not py:
            print("\n  没找到装齐 mcp / python-docx / openpyxl 的解释器。", file=sys.stderr)
            print("  装一下再回来（任选一个 3.9+ 的解释器执行）：", file=sys.stderr)
            print("      pip install mcp python-docx openpyxl pypdf Pillow comtypes pywin32",
                  file=sys.stderr)
            print("  装完用 python tools/doctor.py 复查。\n", file=sys.stderr)
            return 1
        print(f"  使用 {py}", file=sys.stderr)

    env = dict(os.environ)
    # 子进程要按 UTF-8 输出，否则中文路径/提示在管道里会变成乱码
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("PYTHONUTF8", "1")

    if "--no-build" not in args:
        rc = subprocess.call([py, str(ROOT / "web" / "build.py")], cwd=str(ROOT), env=env)
        if rc != 0:
            print(f"  构建失败（退出码 {rc}），先修 web/build.py 的报错。", file=sys.stderr)
            return rc

    serve_args = [a for a in args if a != "--no-build"]
    cmd = [py, str(ROOT / "web" / "serve.py")] + serve_args
    try:
        return subprocess.call(cmd, cwd=str(ROOT), env=env)
    except KeyboardInterrupt:
        print("\n  已停止。", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
