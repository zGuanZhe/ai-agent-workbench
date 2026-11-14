#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression for web/config_manager.py using a temporary config.toml copy."""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys
import tempfile
import tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "web"))
import config_manager as cm  # noqa: E402


def _hash(path: pathlib.Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _parsed(path: pathlib.Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _assert_valid(path: pathlib.Path, label: str) -> dict:
    cfg = _parsed(path)
    assert isinstance(cfg.get("mcp_servers"), dict), label
    assert isinstance(cfg.get("skills"), dict), label
    return cfg


def main() -> int:
    real_cfg = pathlib.Path.home() / ".codex" / "config.toml"
    real_before = _hash(real_cfg)

    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        cfg = tmp / "config.toml"
        skills = tmp / "skills"
        target = skills / "work-comms" / "SKILL.md"
        target.parent.mkdir(parents=True)
        target.write_text("---\nname: work-comms\n---\n", encoding="utf-8")

        plugin_path = str(tmp / "plugins" / "create-pet" / "SKILL.md").replace("/", "\\")
        legacy_path = str(skills / "work-comms.disabled" / "SKILL.md").replace("/", "\\")
        cfg.write_text(
            "[mcp_servers.work_comms]\n"
            "command = \"python\"\n"
            "enabled = true\n\n"
            "[skills]\n"
            "config = [\n"
            f"  {{ \"path\" = {json.dumps(plugin_path, ensure_ascii=False)}, \"enabled\" = false }},\n"
            f"  {{ \"path\" = {json.dumps(legacy_path, ensure_ascii=False)}, \"enabled\" = false }},\n"
            "]\n",
            encoding="utf-8",
        )

        cm.CODEX_CONFIG = cfg
        cm.CODEX_SKILLS = skills
        cm.ROOT = ROOT

        status = cm.get_config_status()
        assert status.get("ok") is True, status
        assert status["mcps"]["work_comms"]["mcp_registered"] is True
        assert status["mcps"]["work_comms"]["skill_enabled"] is False

        checks = [
            ("toggle_mcp false", lambda: cm.toggle_mcp("work_comms", False)),
            ("toggle_mcp true", lambda: cm.toggle_mcp("work_comms", True)),
            ("toggle_skill false", lambda: cm.toggle_skill("work-comms", False)),
            ("toggle_skill true", lambda: cm.toggle_skill("work-comms", True)),
        ]
        for label, fn in checks:
            result = fn()
            assert result.get("ok") is True, (label, result)
            parsed = _assert_valid(cfg, label)

        parsed = _parsed(cfg)
        entries = parsed["skills"]["config"]
        keys = [cm._skill_key(x.get("path", "")) for x in entries]
        assert len(keys) == len(set(keys)), entries
        assert sum(1 for k in keys if k == "work-comms") == 1, entries
        own = [x for x in entries if cm._skill_key(x.get("path", "")) == "work-comms"][0]
        assert own["path"] == str(target).replace("/", "\\"), own
        assert own["enabled"] is True, own
        assert parsed["mcp_servers"]["work_comms"]["enabled"] is True, parsed
        assert not (tmp / "config.toml.tmp").exists(), "tmp file left behind"
        assert list(tmp.glob("config.toml.bak_*")), "backup file missing"

        status = cm.get_config_status()
        assert status["mcps"]["work_comms"]["mcp_enabled"] is True
        assert status["mcps"]["work_comms"]["skill_enabled"] is True

        # A malformed current config must be reported, never replaced.
        bad = tmp / "bad.toml"
        bad.write_text("[skills\nconfig = [", encoding="utf-8")
        cm.CODEX_CONFIG = bad
        result = cm.toggle_mcp("work_comms", False)
        assert result.get("ok") is False, result
        assert bad.read_text(encoding="utf-8") == "[skills\nconfig = [", "invalid config was overwritten"

    real_after = _hash(real_cfg)
    assert real_before == real_after, "real Codex config.toml changed during regression"
    print("config_manager regression: OK (temporary copy only)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
