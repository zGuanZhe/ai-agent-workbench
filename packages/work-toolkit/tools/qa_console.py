#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""End-to-end console QA against a temporary local server."""

from __future__ import annotations

import hashlib
import json
import pathlib
import urllib.request

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:18891"
ROOT = pathlib.Path(__file__).resolve().parent.parent
SHOT_DIR = ROOT / "workspace" / "06_运行状态"
REAL_CFG = pathlib.Path.home() / ".codex" / "config.toml"


def cfg_hash() -> str | None:
    return hashlib.sha256(REAL_CFG.read_bytes()).hexdigest() if REAL_CFG.is_file() else None


def get_json(path: str) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=40) as r:
        assert r.status == 200, (path, r.status)
        return json.loads(r.read().decode("utf-8"))


def main() -> int:
    before = cfg_hash()
    SHOT_DIR.mkdir(parents=True, exist_ok=True)

    endpoints = {
        "/api/status": ("root", "project", "config_valid", "tools"),
        "/api/config/status": ("ok", "mcps", "config_path", "config_valid"),
        "/api/comms/overview": ("ok", "channels", "recent"),
        "/api/comms/notices?days=3&limit=8&only_notices=0&order=time": ("ok", "items", "notice_count", "due_count"),
        "/api/wx/sessions?limit=3": ("ok", "sessions"),
        "/api/wx/files?limit=3": ("ok", "files", "stats"),
        "/api/wx/media?limit=1&prefer=thumb": ("ok", "mode", "sample"),
        "/api/memory/data": ("docs", "docs_count", "trace", "engram_memories", "ledger_summary"),
        "/api/trace?limit=20": ("ok", "effective_steps", "discovery_calls", "failed_calls"),
        "/api/tree": ("ok", "dirs"),
        "/api/fs?dir=&limit=10": ("ok", "entries", "total"),
    }
    results = {}
    for path, keys in endpoints.items():
        payload = get_json(path)
        assert payload.get("ok") is not False, (path, payload)
        data = payload.get("data", payload)
        missing = [k for k in keys if k not in data]
        assert not missing, (path, missing)
        results[path] = {
            "keys": sorted(list(data.keys()))[:8],
            "items": len(data.get("items") or data.get("sessions") or data.get("files") or data.get("entries") or data.get("dirs") or data.get("docs") or []),
        }
    status = get_json("/api/status")["data"]
    assert status["project"] == "work-super-agent", status
    assert status["config_valid"] is True, status
    notices = get_json("/api/comms/notices?days=3&limit=8&only_notices=0&order=time")["data"]
    assert isinstance(notices["items"], list), notices
    files = get_json("/api/wx/files?limit=3")["data"]
    if files["files"]:
        f = files["files"][0]
        for key in ("name", "path", "size", "ext", "month", "kind", "mtime_text"):
            assert key in f, (key, f)

    def post_run(tool: str, args: dict) -> dict:
        req = urllib.request.Request(
            BASE + "/api/run",
            data=json.dumps({"tool": tool, "args": args}, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=90) as r:
            payload = json.loads(r.read().decode("utf-8"))
        assert payload.get("ok") is not False, (tool, payload)
        data = payload.get("data", payload)
        assert data.get("ok") is not False, (tool, data)
        return data

    xlsx = ROOT / "workspace" / "科室底册.xlsx"
    assert xlsx.is_file(), xlsx
    inspect_res = post_run("work_office_inspect", {"file": str(xlsx), "mode": "text", "max_lines": 8})
    diff_res = post_run("work_office_diff", {"file_a": str(xlsx), "file_b": str(xlsx), "sheet": "", "key_column": "", "output": ""})
    sessions = get_json("/api/wx/sessions?limit=3")["data"]["sessions"]
    assert sessions and sessions[0].get("name"), sessions
    digest_path = SHOT_DIR / "_console_digest_qa.md"
    digest_path.unlink(missing_ok=True)
    digest_res = post_run("work_wechat_digest", {
        "chat": sessions[0]["name"], "limit": 3, "format": "md", "output": str(digest_path)
    })
    assert digest_path.is_file(), digest_res
    digest_path.unlink(missing_ok=True)
    action_results = {
        "work_office_inspect": sorted(list(inspect_res.keys()))[:8],
        "work_office_diff": sorted(list(diff_res.keys()))[:8],
        "work_wechat_digest": {"messages": digest_res.get("messages"), "bytes": digest_res.get("bytes")},
    }

    console_errors = []
    page_errors = []
    failed_responses = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda err: page_errors.append(str(err)))
        page.on("response", lambda r: failed_responses.append((r.status, r.url)) if r.status >= 400 else None)
        page.goto(BASE + "/", wait_until="networkidle", timeout=40000)
        page.wait_for_function("document.querySelector('#statTools').textContent.trim() !== '--'", timeout=15000)
        assert page.locator("#apiBadge").inner_text() == "接口在线"
        assert page.locator("#statConfig").inner_text() == "可解析"
        assert page.locator("#statMcp").inner_text() != "--"
        assert page.locator("#moduleRows tr").count() >= 6
        page.screenshot(path=str(SHOT_DIR / "console_qa_assembly.png"))

        page.locator('.side-nav [data-view="comms"]').click()
        page.wait_for_selector("#channelRows .list-row", timeout=20000)
        page.locator('#view-comms [data-comms-sub="files"]').click()
        page.wait_for_selector("#fileTableBody tr", timeout=20000)
        page.screenshot(path=str(SHOT_DIR / "console_qa_comms.png"))

        page.locator('.side-nav [data-view="memory"]').click()
        page.wait_for_selector("#memMetrics b", timeout=20000)
        page.wait_for_selector("#memoryGraph circle", timeout=20000)
        page.screenshot(path=str(SHOT_DIR / "console_qa_memory.png"))

        page.locator('.side-nav [data-view="office"]').click()
        page.wait_for_selector("#officeDirList .dir-row", timeout=20000)
        page.locator('.side-nav [data-view="trace"]').click()
        page.wait_for_selector("#traceTableBody tr", timeout=20000)
        page.locator('.side-nav [data-view="tools"]').click()
        assert page.locator("#catalogList .catalog-row").count() > 0
        page.locator('.side-nav [data-view="workspace"]').click()
        page.wait_for_selector("#wsDirList .dir-row", timeout=20000)

        text = page.locator("body").inner_text()
        for fake in ("完全访问", "已全部启用", "本地服务 8765 已连接", "Local Python 3.14", "MCP/Skill 架构"):
            assert fake not in text, fake
        page.set_viewport_size({"width": 390, "height": 844})
        page.locator('.rail [data-view="assembly"]').first.click()
        page.wait_for_function("document.querySelector('#statTools').textContent.trim() !== '--'", timeout=15000)
        mobile_overflow = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
        assert mobile_overflow <= 1, mobile_overflow
        page.screenshot(path=str(SHOT_DIR / "console_qa_mobile.png"))
        bad = page.evaluate(r"""() => {
          const s = document.body.innerText;
          const re = /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{21BB}\u{2190}-\u{2193}\u{2715}]/gu;
          return (s.match(re) || []).slice(0, 10);
        }""")
        assert not bad, bad
        assert not console_errors, console_errors
        assert not page_errors, page_errors
        assert not failed_responses, failed_responses
        browser.close()

    after = cfg_hash()
    assert before == after, "real Codex config.toml changed during console QA"
    print("console API checks: OK")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    print("console action checks: OK")
    print(json.dumps(action_results, ensure_ascii=False, indent=2))
    print("console browser checks: OK")
    print("screenshots:", ", ".join(str(SHOT_DIR / x) for x in ("console_qa_assembly.png", "console_qa_comms.png", "console_qa_memory.png", "console_qa_mobile.png")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
