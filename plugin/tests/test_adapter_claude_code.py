"""Offline tests for the Claude Code adapter: hooks handler + plugin bundle.

The MCP server's live ``run()`` needs the MCP SDK and a stdio peer, so it isn't
invoked here; its tool *logic* is the core's (covered in test_core). These tests
cover the hook handler's fail-open behavior and verify the shipped plugin bundle is
well-formed (valid plugin.json / .mcp.json / hooks.json). Skills live in the core and
are placed into a harness's discovery path by the install command (test_install).
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from cookbook_memory.adapters.claude_code import hooks_handler

BUNDLE = Path(__file__).resolve().parents[1] / "cookbook_memory" / "adapters" / "claude_code"


def test_hook_handle_is_noop_and_logs_note(tmp_path):
    resp = hooks_handler.handle("Stop", {"session_id": "s9"}, store=str(tmp_path))
    assert resp == {}  # no additionalContext / decision — pure observation
    events = json.loads((tmp_path / "events.jsonl").read_text().strip())
    assert events["op"] == "note"
    assert events["meta"]["hook"] == "Stop"
    assert events["session_id"] == "s9"


def test_hook_main_exits_zero_on_bad_stdin(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    monkeypatch.setattr("sys.argv", ["memory-hook", "Stop"])
    assert hooks_handler.main() == 0  # fail-open: never break the session


def test_hook_main_exits_zero_with_no_event_name(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert hooks_handler.main([]) == 0


# --- plugin bundle integrity ------------------------------------------------- #

def test_plugin_json_is_valid():
    data = json.loads((BUNDLE / ".claude-plugin" / "plugin.json").read_text())
    assert data["name"] == "cookbook-memory"
    assert "version" in data and "description" in data


def test_mcp_json_points_at_memory_server():
    data = json.loads((BUNDLE / ".mcp.json").read_text())
    server = data["mcpServers"]["cookbook-memory"]
    assert server["command"] == "memory-cli"
    assert server["args"] == ["mcp"]
    assert "MEMORY_STORE" in server["env"]


def test_hooks_json_wires_lifecycle_events():
    data = json.loads((BUNDLE / "hooks" / "hooks.json").read_text())
    hooks = data["hooks"]
    for evt in ("SessionStart", "UserPromptSubmit", "Stop", "PreCompact", "PostCompact"):
        assert evt in hooks, f"missing hook: {evt}"
    stop = hooks["Stop"][0]["hooks"][0]
    assert stop.get("async") is True
    assert stop["command"].startswith("memory-hook")


def test_committed_adapter_has_no_skills_dir():
    # No skill is COMMITTED under the adapter (no duplication in git): the canonical
    # skill lives once in the core and is materialized into the bundle by the build
    # step, not checked in here (ADR-harness-009).
    assert not (BUNDLE / "skills").exists()


# --- production release build (build_bundle) --------------------------------- #

def test_build_bundle_produces_installable_plugin(tmp_path):
    # The release step materializes a self-contained bundle: manifests + MCP + hooks
    # + the canonical skill copied in, so a single native `claude plugin install`
    # delivers all three (ADR-harness-009, AC3).
    from cookbook_memory.adapters.claude_code.build import build_bundle

    out = build_bundle(tmp_path / "bundle")
    assert (out / ".claude-plugin" / "plugin.json").is_file()
    assert (out / ".mcp.json").is_file()
    assert (out / "hooks" / "hooks.json").is_file()
    # the skill is now PRESENT in the built bundle (materialized, not committed)
    assert (out / "skills" / "recall" / "SKILL.md").is_file()


def test_build_bundle_skill_matches_canonical_source(tmp_path):
    # Materialized == canonical: the build copies, it does not fork the content.
    from cookbook_memory.adapters.claude_code.build import build_bundle
    from cookbook_memory.core.install import canonical_skills_dir

    out = build_bundle(tmp_path / "bundle")
    built = (out / "skills" / "recall" / "SKILL.md").read_text()
    canonical = (canonical_skills_dir() / "recall" / "SKILL.md").read_text()
    assert built == canonical


def test_build_bundle_is_reproducible(tmp_path):
    # A clean rebuild over an existing dir yields the same bundle (idempotent).
    from cookbook_memory.adapters.claude_code.build import build_bundle

    a = build_bundle(tmp_path / "b")
    b = build_bundle(tmp_path / "b")
    assert a == b
    assert (b / "skills" / "recall" / "SKILL.md").is_file()


def test_validate_bundle_rejects_missing_skill(tmp_path):
    from cookbook_memory.adapters.claude_code import build

    out = build.build_bundle(tmp_path / "bundle")
    # remove the materialized skill -> validation must fail
    import shutil
    shutil.rmtree(out / "skills")
    with pytest.raises(build.BundleError):
        build.validate_bundle(out)
