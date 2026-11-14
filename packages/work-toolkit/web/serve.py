#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""serve.py — 本地起能力可视化网页（可选，不是 /work 的前提）。

    python web/serve.py            # 起服务并自动打开浏览器
    python web/serve.py --no-open  # 只起服务
    python web/serve.py --port 8770

日常总 Skill 走 scripts/work.py 本地桥接，不开这个服务也能调用全部工具。
接上这个服务之后：
  · 顶部显示接口状态；
  · 会话列表 / 群消息 / 收件文件 在右侧直接可视化；
  · 「直接调用」按钮真的执行工具。

只监听 127.0.0.1；只允许 tools.json 里登记过的工具名。
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "mcp"))
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))
import config_manager
from experience import library as exp_lib

TOOLS = HERE / "tools.json"
ALLOWED = {t["name"] for t in json.loads(TOOLS.read_text(encoding="utf-8"))}
RUNNER = ROOT / "tools" / "run_tool.py"
WS = (ROOT / "workspace").resolve()
TIMEOUT = 300

_PY_CANDIDATES = [
    os.environ.get("WORK_PY"),
    # 不写死某个用户的目录：用 LOCALAPPDATA 展开 installer 版 Python 的常见位置
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python314\python.exe"),
    sys.executable,
    "python",
]


def _pick_python() -> str:
    for cand in _PY_CANDIDATES:
        if not cand:
            continue
        if cand != "python" and not os.path.exists(cand):
            continue
        try:
            r = subprocess.run([cand, "-c", "import mcp, docx"], capture_output=True, timeout=40)
            if r.returncode == 0:
                return cand
        except Exception:
            continue
    return sys.executable


PY = _pick_python()


def _ensure_runtime() -> None:
    """当前解释器缺依赖时，换挑出来的解释器重跑（同一个控制台，Ctrl+C 能一起停）。

    Windows 没有真正的 exec，os.execv 会变成「起子进程 + 自己退出」，
    后台跑的时候容易丢输出和信号，所以这里用阻塞的 subprocess.call。
    """
    if os.environ.get("WORK_REEXEC") == "1":
        return
    try:
        import docx  # noqa: F401
        import mcp  # noqa: F401

        return
    except Exception:
        pass
    if os.path.realpath(PY) == os.path.realpath(sys.executable):
        return
    os.environ["WORK_REEXEC"] = "1"
    print(f"切换到 {PY} 重新启动…", flush=True)
    code = subprocess.call([PY, str(pathlib.Path(__file__).resolve()), *sys.argv[1:]])
    raise SystemExit(code)


# --------------------------------------------------------------------------- #
# 工具箱（进程内调用，读接口才快；写操作仍走子进程拿超时隔离）
# --------------------------------------------------------------------------- #
_G = None
_GLOCK = threading.Lock()
_CACHE: dict[str, tuple[float, object]] = {}
CACHE_TTL = 20

# 写类工具跑完要连带失效的读缓存，否则界面上还是旧数据
_INVALIDATE = {
    "work_wechat_sync": ("work_wechat_sessions", "work_wechat_messages", "work_wechat_search",
                         "work_comms_overview"),
    "work_wechat_files": ("work_wechat_files", "work_ledger"),
    "work_wechat_media": ("work_wechat_media",),
    "work_wecom_sync": ("work_wecom_sessions", "work_wecom_messages", "work_wecom_search",
                        "work_wecom_files", "work_wecom_contacts", "work_comms_overview"),
    "work_wecom_harvest": ("work_wecom_files",),
    "work_ledger": ("work_ledger",),
    "work_exp": ("work_exp",),
}


def toolkit():
    global _G
    if _G is None:
        sys.path.insert(0, str(ROOT / "mcp"))
        sys.path.insert(0, str(ROOT / "wechat"))
        os.chdir(ROOT)
        import logging

        logging.disable(logging.CRITICAL)
        try:
            from loguru import logger as _lg

            _lg.remove()
        except Exception:
            pass
        import work_toolkit_server as mod  # type: ignore

        _G = mod
    return _G


def call_tool(name: str, args: dict, cache: bool = True) -> dict:
    """进程内调工具，结果解析成 dict。带短期缓存，避免反复点同一个节点重复解密。

    cache=False 用于任务经验：那些是普通 md 文件，人会用编辑器随时改，
    缓存一秒都可能让「刚存完却看不到」。
    """
    key = name + json.dumps(args, ensure_ascii=False, sort_keys=True)
    now = time.time()
    if cache:
        hit = _CACHE.get(key)
        if hit and now - hit[0] < CACHE_TTL:
            return hit[1]  # type: ignore[return-value]

    with _GLOCK:
        raw = getattr(toolkit(), name)(**args)
    data = json.loads(raw) if isinstance(raw, str) else raw
    if cache:
        _CACHE[key] = (now, data)
        if len(_CACHE) > 40:
            for k in list(_CACHE)[:10]:
                _CACHE.pop(k, None)
    return data


# --------------------------------------------------------------------------- #
# 工作区文件（只读目录浏览，路径必须落在 workspace 之内）
# --------------------------------------------------------------------------- #
def _under_ws(rel: str) -> pathlib.Path | None:
    try:
        p = (WS / rel).resolve() if rel else WS
        p.relative_to(WS)
    except (ValueError, OSError):
        return None
    return p if p.is_dir() else None


def _fs_status(rel: str) -> int:
    """区分「越权」与「不存在」：前者 403，后者 404。"""
    try:
        p = (WS / rel).resolve() if rel else WS
        p.relative_to(WS)
    except (ValueError, OSError):
        return 403
    return 200 if p.is_dir() else 404


def _ent(p: pathlib.Path) -> dict:
    try:
        st = p.stat()
    except OSError:
        return {}
    return {
        "name": p.name,
        "path": str(p),
        "ext": p.suffix.lower().lstrip("."),
        "size": st.st_size if p.is_file() else 0,
        "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)),
        "is_dir": p.is_dir(),
    }


def list_dir(rel: str, limit: int = 80) -> dict:
    p = _under_ws(rel)
    if p is None:
        return {"ok": False, "error": f"目录不可读: {rel}"}
    try:
        kids = [c for c in p.iterdir() if c.is_file() and not c.name.startswith(".")]
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    kids.sort(key=lambda x: x.stat().st_mtime if x.exists() else 0, reverse=True)
    entries = [e for e in (_ent(c) for c in kids[:limit]) if e]
    return {
        "ok": True, "dir": rel or ".", "total": len(kids), "entries": entries,
        "size": sum(c.stat().st_size for c in kids if c.exists()),
    }


def list_tree() -> dict:
    dirs = []
    if WS.is_dir():
        for d in sorted(WS.iterdir()):
            if not d.is_dir() or d.name.startswith("."):
                continue
            n = size = 0
            latest = 0.0
            for dp, _dn, fs in os.walk(d):
                for f in fs:
                    try:
                        st = os.stat(os.path.join(dp, f))
                    except OSError:
                        continue
                    n += 1
                    size += st.st_size
                    latest = max(latest, st.st_mtime)
            dirs.append({"name": d.name, "count": n, "size": size,
                         "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(latest)) if latest else ""})
        loose = [f for f in WS.iterdir() if f.is_file() and not f.name.startswith(".")]
        if loose:
            dirs.append({"name": ".", "count": len(loose),
                         "size": sum(f.stat().st_size for f in loose if f.exists()),
                         "mtime": "", "loose": True})
    return {"ok": True, "dirs": dirs}


def wc_sessions(limit: int = 30, only_unread: bool = False) -> dict:
    """企业微信会话列表，顺手补齐前端要用的两个字段。

    企业微信这边只给 kind（群聊/单聊），没有微信那种 is_group + 摘要；
    会话行是同一个组件渲染的，这里补成一样的形状，省得前端两套判断。
    """
    res = call_tool("work_wecom_sessions", {"limit": limit, "only_unread": only_unread})
    for it in res.get("sessions") or []:
        it["is_group"] = it.get("kind") != "单聊"
        it["summary"] = it.get("kind") or ""
    return res


class Handler(BaseHTTPRequestHandler):
    server_version = "work-toolkit/3.1"

    def log_message(self, *_args):
        pass

    # ── 路由 ──
    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path in ("/", "/console.html"):
                return self._file(HERE / "console.html", "text/html; charset=utf-8")
            if u.path == "/favicon.ico":
                # 页面没有站点图标；不接这个路由的话每开一次都在控制台留一条 404
                self.send_response(204)
                self.end_headers()
                return None
            if u.path == "/api/config/status":
                return self._ok(config_manager.get_config_status())
            if u.path == "/api/memory/data":
                docs_res = exp_lib.list_docs()
                trace_res = exp_lib.read_trace(limit=40)
                # Engram facts
                engram_items = []
                try:
                    import work_mods
                    fn_mem, _ = work_mods.load_tool("work_mem")
                    res_mem = json.loads(fn_mem(action="search", query="*"))
                    if res_mem.get("ok"):
                        engram_items = res_mem.get("results", [])
                except Exception:
                    pass
                # Ledger summary
                ledger_sum = {}
                try:
                    import work_mods
                    fn_led, _ = work_mods.load_tool("work_ledger")
                    ledger_sum = json.loads(fn_led(action="summary"))
                except Exception:
                    pass
                return self._ok({
                    "docs": docs_res.get("items", []),
                    "docs_count": docs_res.get("count", 0),
                    "trace": trace_res,
                    "engram_memories": engram_items,
                    "ledger_summary": ledger_sum,
                })
            if u.path == "/api/trace":
                limit = int(q.get("limit") or 50)
                return self._ok(exp_lib.read_trace(limit=limit))
            if u.path == "/api/status":
                f = facts_collect()
                cfg_state = config_manager.get_config_status()
                port = getattr(self.server, "server_address", (None, None))[1]
                return self._ok({
                    "tools": len(ALLOWED),
                    "facts": f,
                    "root": str(ROOT),
                    "project": ROOT.name,
                    "python": sys.executable,
                    "config_path": str(config_manager.CODEX_CONFIG),
                    "config_valid": bool(cfg_state.get("ok")),
                    "host": "127.0.0.1",
                    "port": port,
                })
            if u.path == "/api/comms/overview":
                return self._ok(call_tool("work_comms_overview",
                                          {"limit": int(q.get("limit") or 6)}))
            if u.path == "/api/comms/notices":
                order = (q.get("order") or "notice").strip()
                if order not in ("notice", "time"):
                    order = "notice"
                args = {
                    "days": max(1, min(30, int(q.get("days") or 3))),
                    "limit": max(1, min(100, int(q.get("limit") or 30))),
                    "per_chat": max(1, min(20, int(q.get("per_chat") or 8))),
                    "max_chats": max(1, min(50, int(q.get("max_chats") or 30))),
                    "channels": (q.get("channels") or "both").strip(),
                    "only_notices": q.get("only_notices") in ("1", "true"),
                    "keyword": (q.get("keyword") or "").strip(),
                    "max_text": max(60, min(1000, int(q.get("max_text") or 220))),
                    "since": (q.get("since") or "").strip(),
                    "dedupe": q.get("dedupe") not in ("0", "false"),
                    "order": order,
                }
                return self._ok(call_tool("work_comms_notices", args))
            if u.path == "/api/wx/sessions":
                limit = int(q.get("limit") or 30)
                only = q.get("unread") in ("1", "true")
                return self._ok(call_tool("work_wechat_sessions",
                                          {"limit": limit, "only_unread": only}))
            if u.path == "/api/wx/messages":
                chat = (q.get("chat") or "").strip()
                if not chat:
                    return self._err("缺少 chat 参数", 400)
                limit = int(q.get("limit") or 40)
                return self._ok(call_tool("work_wechat_messages",
                                          {"chat": chat, "limit": limit}))
            if u.path == "/api/wx/files":
                limit = int(q.get("limit") or 60)
                args = {"mode": "scan", "limit": limit, "with_stats": True, "order": "desc"}
                if q.get("since"):
                    args["since"] = q["since"]
                if q.get("keyword"):
                    args["keyword"] = q["keyword"]
                return self._ok(call_tool("work_wechat_files", args))
            if u.path == "/api/wx/media":
                chat = (q.get("chat") or "").strip()
                args = {"limit": int(q.get("limit") or 12),
                        "prefer": q.get("prefer") or "thumb"}
                if chat:
                    args["chat"] = chat
                if q.get("since"):
                    args["since"] = q["since"]
                if q.get("ref"):
                    # 单张取原图：预览里点开一张图时用，缩略图太小看不清字
                    args["ref"] = q["ref"]
                return self._ok(call_tool("work_wechat_media", args))
            if u.path == "/api/wecom/sessions":
                return self._ok(wc_sessions(int(q.get("limit") or 30),
                                            q.get("unread") in ("1", "true")))
            if u.path == "/api/wecom/messages":
                chat = (q.get("chat") or "").strip()
                if not chat:
                    return self._err("缺少 chat 参数", 400)
                return self._ok(call_tool("work_wecom_messages",
                                          {"chat": chat, "limit": int(q.get("limit") or 40)}))
            if u.path == "/api/wecom/files":
                args = {"limit": int(q.get("limit") or 60)}
                if q.get("since"):
                    args["since"] = q["since"]
                if q.get("exts"):
                    args["exts"] = q["exts"]
                return self._ok(call_tool("work_wecom_files", args))
            if u.path == "/api/wecom/contacts":
                args = {"limit": int(q.get("limit") or 40)}
                if q.get("keyword"):
                    args["keyword"] = q["keyword"]
                return self._ok(call_tool("work_wecom_contacts", args))
            if u.path == "/api/img":
                return self._serve_ws_file((q.get("path") or "").strip())
            if u.path == "/api/ledger":
                action = (q.get("action") or "summary").strip()
                args = {"action": action, "limit": int(q.get("limit") or 40)}
                if q.get("dept"):
                    args["dept"] = q["dept"]
                return self._ok(call_tool("work_ledger", args))
            if u.path == "/api/tree":
                return self._ok(list_tree())
            if u.path == "/api/exp":
                # 任务经验文档库：只列目录 / 取整篇原文，没有检索、没有打分。
                args: dict = {"action": (q.get("action") or "list").strip()}
                for key in ("doc_id", "title", "tags"):
                    if q.get(key):
                        args[key] = q[key]
                if q.get("limit"):
                    args["limit"] = int(q["limit"])
                return self._ok(call_tool("work_exp", args, cache=False))
            if u.path == "/api/fs":
                rel = (q.get("dir") or "").strip()
                d = list_dir(rel, int(q.get("limit") or 80))
                if d.get("ok") is False:
                    # 越界给 403、不存在给 404。都裹成 200 的话，前端和任何
                    # HTTP 审计都看不出这是一次被拒绝的请求。
                    return self._err(d.get("error") or "目录不可读", _fs_status(rel))
                return self._ok(d)
            return self._err("not found", 404)
        except Exception as exc:  # pragma: no cover
            return self._err(f"{type(exc).__name__}: {exc}", 500)

    def do_POST(self):
        u = urlparse(self.path)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(min(n, 1 << 20)) or b"{}")
        except Exception as exc:
            return self._err(f"请求体不是合法 JSON: {exc}", 400)

        # ── 模块装配与配置开关 ──
        if u.path == "/api/config/toggle":
            target = (body or {}).get("target", "mcp")
            ident = (body or {}).get("id") or (body or {}).get("server_id") or ""
            enabled = bool((body or {}).get("enabled", True))
            if target == "mcp":
                res = config_manager.toggle_mcp(ident, enabled)
                return self._ok(res)
            elif target == "skill":
                res = config_manager.toggle_skill(ident, enabled)
                return self._ok(res)
            return self._err("未知 target 类型", 400)

        # ── 经验提炼与重放 ──
        if u.path == "/api/memory/distill":
            title = (body or {}).get("title") or "新提炼经验"
            limit = int((body or {}).get("limit") or 20)
            res = exp_lib.distill_doc(title=title, limit=limit)
            return self._ok(res)

        if u.path == "/api/memory/replay":
            doc_id = (body or {}).get("doc_id") or ""
            dry_run = bool((body or {}).get("dry_run", True))
            v_dict = (body or {}).get("vars") or {}
            def _local_exec(tool_name, args):
                import work_mods
                fn, _ = work_mods.load_tool(tool_name)
                out = fn(**args)
                try:
                    return json.loads(out)
                except Exception:
                    return {"raw": out}
            res = exp_lib.replay_doc(doc_id=doc_id, vars_dict=v_dict, dry_run=dry_run,
                                     executor=None if dry_run else _local_exec)
            return self._ok(res)

        if u.path == "/api/memory/export_obsidian":
            import work_mods
            fn, _ = work_mods.load_tool("work_mem")
            vault = (body or {}).get("vault_path") or ""
            res = json.loads(fn(action="export_obsidian", vault_path=vault))
            return self._ok(res)

        if u.path == "/api/prompt/ask":
            q_text = (body or {}).get("query") or ""
            if not q_text:
                return self._err("缺少 query 参数", 400)
            # 通过 work.py 意图识别或直接调用；subprocess 已在模块级导入
            cmd = [PY, "-X", "utf8", str(ROOT / "scripts" / "work.py"), "ask", q_text]
            try:
                proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, encoding="utf-8", errors="replace", timeout=60)
                out = (proc.stdout or "").strip()
                try:
                    res_json = json.loads(out)
                except Exception:
                    res_json = {"raw": out, "error": proc.stderr}
                return self._ok(res_json)
            except Exception as e:
                return self._err(str(e))

        if u.path != "/api/run":
            return self._err("not found", 404)

        tool = (body or {}).get("tool") or ""
        args = (body or {}).get("args") or {}
        if tool not in ALLOWED:
            return self._err(f"工具未登记: {tool}", 400)
        if not isinstance(args, dict):
            return self._err("args 必须是对象", 400)

        cmd = [PY, str(RUNNER), tool, json.dumps(args, ensure_ascii=False)]
        try:
            r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, encoding="utf-8",
                               errors="replace", timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            return self._err(f"超时（>{TIMEOUT}s）")
        except Exception as exc:
            return self._err(str(exc))

        out = (r.stdout or "").strip()
        try:
            data = json.loads(out)
        except Exception:
            return self._err((r.stderr or out or "无输出").strip()[-600:])
        # 只失效受影响的缓存。早期这里 _CACHE.clear() 全清，跑一次 work_status
        # 就把已经解密的会话列表打回冷态（下一次要 4 秒多）。
        drop = {tool, *_INVALIDATE.get(tool, ())}
        for k in [k for k in _CACHE if any(k.startswith(n) for n in drop)]:
            _CACHE.pop(k, None)
        return self._ok(data, extra={"code": r.returncode, "tool": tool})

    # ── 输出 ──
    _IMG_TYPES = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
    }

    def _serve_ws_file(self, raw: str):
        """只允许读 workspace 之下的图片。

        `/api/img?path=../../../windows/system32/x.png` 这种要挡住——
        这里既是越权读文件的入口，也是把本机文件外泄的入口。
        """
        if not raw:
            return self._err("缺少 path", 400)
        try:
            cand = pathlib.Path(raw)
            if not cand.is_absolute():
                cand = (ROOT / raw).resolve()
            else:
                cand = cand.resolve()
            cand.relative_to(WS)
        except (ValueError, OSError):
            return self._err("只允许读工作区内的文件", 403)
        if not cand.is_file():
            return self._err("文件不存在", 404)
        ctype = self._IMG_TYPES.get(cand.suffix.lower())
        if not ctype:
            return self._err("只提供图片预览", 415)
        if cand.stat().st_size > 24 * 1024 * 1024:
            return self._err("图片过大（>24MB）", 413)
        try:
            data = cand.read_bytes()
        except OSError as exc:
            return self._err(f"读取失败：{exc}", 500)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "max-age=120")
        self.end_headers()
        self.wfile.write(data)
        return None

    def _ok(self, data, extra: dict | None = None, status: int = 200):
        payload = {"ok": bool(data.get("ok", True)), "data": data}
        if extra:
            payload.update(extra)
        self._json(payload, status)

    def _err(self, msg: str, status: int = 200):
        self._json({"ok": False, "error": msg}, status)

    def _file(self, path: pathlib.Path, ctype: str):
        if not path.exists():
            return self._err("console.html 还没生成，先跑 python web/build.py", 500)
        raw = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _json(self, obj, status: int = 200):
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)


def facts_collect() -> dict:
    import facts
    try:
        return facts.collect()
    except Exception:
        return {}


def main() -> int:
    port = 8765
    for i, a in enumerate(sys.argv):
        if a in ("--port", "-p") and i + 1 < len(sys.argv):
            port = int(sys.argv[i + 1])

    if not (HERE / "console.html").exists():
        print("console.html 不存在，先跑：python web/build.py")
        return 1

    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"工作台：{url}", flush=True)
    print(f"解释器：{sys.executable}", flush=True)
    print("Ctrl+C 结束", flush=True)
    if "--no-open" not in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    return 0


if __name__ == "__main__":
    _ensure_runtime()
    raise SystemExit(main())
