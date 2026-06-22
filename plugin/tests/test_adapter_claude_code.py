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


def test_hook_handle_is_noop_on_non_gated_event(tmp_path):
    # SessionStart is a non-gated event — handler emits the `note` observation
    # event and returns {} with no subprocess fire. This is the byte-equivalent
    # of the pre-migration no-op-with-note behavior (regression guard).
    resp = hooks_handler.handle("SessionStart", {"session_id": "s9"}, store=str(tmp_path))
    assert resp == {}
    events = json.loads((tmp_path / "events.jsonl").read_text().strip())
    assert events["op"] == "note"
    assert events["meta"]["hook"] == "SessionStart"
    assert events["session_id"] == "s9"


def test_hook_main_exits_zero_on_bad_stdin(monkeypatch):
    # Use a non-gated event so this test doesn't depend on daydream-cli being
    # on PATH (Stop / PreCompact would shell out; SessionStart is a clean no-op).
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    monkeypatch.setattr("sys.argv", ["hooks_handler", "SessionStart"])
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
    # Invoked by module (`python3 -m cookbook_memory mcp`), not by the `memory-cli`
    # console script: module-run resolves the package via the interpreter's sys.path,
    # so it works wherever cookbook_memory is importable — no requirement that the
    # console script be on $PATH. (Production-install fix.)
    assert server["command"] == "python3"
    assert server["args"] == ["-m", "cookbook_memory", "mcp"]
    assert "MEMORY_STORE" in server["env"]


def test_hooks_json_wires_lifecycle_events():
    data = json.loads((BUNDLE / "hooks" / "hooks.json").read_text())
    hooks = data["hooks"]
    for evt in ("SessionStart", "UserPromptSubmit", "Stop", "PreCompact", "PostCompact"):
        assert evt in hooks, f"missing hook: {evt}"
    stop = hooks["Stop"][0]["hooks"][0]
    assert stop.get("async") is True
    # Each hook tries the `memory-hook` console script first (its shebang pins the
    # interpreter the package was installed into — correct even when Claude Code's
    # `python3` is a different interpreter, e.g. a venv-isolated install), and falls
    # back to `python3 -m …` (covers the case where the package is importable but the
    # console-script bin dir isn't on $PATH). Robust to both failure modes.
    assert stop["command"] == (
        "memory-hook Stop || "
        "python3 -m cookbook_memory.adapters.claude_code.hooks_handler Stop"
    )


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


# --- committed release bundle: drift guard (ADR-harness-010) ----------------- #

#: The release bundle that ships from git (committed, per ADR-harness-010), pointed at
#: by the repo-root `.claude-plugin/marketplace.json` via a git-subdir source.
COMMITTED_BUNDLE = (
    Path(__file__).resolve().parents[1] / "marketplace" / "cookbook-memory"
)


def test_committed_release_bundle_matches_fresh_build(tmp_path):
    # ADR-harness-010 commits the materialized bundle so the plugin installs from git
    # with no clone — which means the committed copy can drift from the canonical skill
    # if someone edits the skill/manifests and forgets to rebuild. This asserts the
    # committed bundle is byte-identical to a fresh `build_bundle`, so the canonical
    # skill stays the single source of truth in practice, not just on paper. On
    # failure: re-run `python -m cookbook_memory build-bundle --out marketplace/cookbook-memory`
    # and commit the result.
    from cookbook_memory.adapters.claude_code.build import build_bundle

    assert COMMITTED_BUNDLE.is_dir(), (
        f"committed release bundle missing: {COMMITTED_BUNDLE} — "
        "run `python -m cookbook_memory build-bundle --out marketplace/cookbook-memory`"
    )
    fresh = build_bundle(tmp_path / "fresh")

    def rel_files(root: Path) -> dict[str, bytes]:
        return {
            str(p.relative_to(root)): p.read_bytes()
            for p in sorted(root.rglob("*"))
            if p.is_file()
        }

    committed = rel_files(COMMITTED_BUNDLE)
    built = rel_files(fresh)
    assert set(committed) == set(built), (
        "committed bundle file set differs from a fresh build "
        f"(only-committed={set(committed) - set(built)}, "
        f"only-built={set(built) - set(committed)}) — rebuild and commit"
    )
    drifted = [name for name in committed if committed[name] != built[name]]
    assert not drifted, (
        f"committed bundle content drifted from a fresh build: {drifted} — "
        "rebuild with `python -m cookbook_memory build-bundle --out marketplace/cookbook-memory` and commit"
    )


def test_root_marketplace_manifest_points_at_committed_bundle():
    # The repo-root marketplace manifest is what `claude plugin marketplace add <repo>`
    # reads; assert it declares the plugin and its git-subdir path matches where the
    # committed bundle actually lives (ADR-harness-010).
    repo_root = Path(__file__).resolve().parents[2]
    manifest = repo_root / ".claude-plugin" / "marketplace.json"
    assert manifest.is_file(), f"missing root marketplace manifest: {manifest}"
    data = json.loads(manifest.read_text())
    plugins = {p["name"]: p for p in data["plugins"]}
    assert "cookbook-memory" in plugins
    src = plugins["cookbook-memory"]["source"]
    assert src["source"] == "git-subdir"
    declared = repo_root / src["path"]
    assert declared.resolve() == COMMITTED_BUNDLE.resolve(), (
        f"manifest path {src['path']} does not point at the committed bundle "
        f"{COMMITTED_BUNDLE}"
    )
