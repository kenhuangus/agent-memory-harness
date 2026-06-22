"""PR5 plugin-manifest tests — every criterion in PR5_DAYDREAM_CLI_RUBRIC.md §I + §J.

Manifest path: eval/memeval/claudecode/plugin/.claude-plugin/plugin.json
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest


MANIFEST_PATH = (
    Path(__file__).resolve().parents[3]
    / "memeval/claudecode/plugin/.claude-plugin/plugin.json"
)


# Pinned hash constants — computed once at commit time from the canonical manifest.
# These guard against silent drift in the hook command/async/timeout shape.
STOP_HOOK_SHA256 = "4082d3349e73278b429d3872e69fb2a6f54d83849a2795c6df2a253ecb4b3ac9"
PRECOMPACT_HOOK_SHA256 = "f32d8782af06860dc5dfbf2a1d22593006ff95234309c5878c7d558ac2eb0cca"


def _load_manifest() -> dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text())


# --------------------------------------------------------------------------- #
# §I — Plugin manifest hooks block shape (CC plugin spec)
# --------------------------------------------------------------------------- #


def test_plugin_manifest_is_valid_json() -> None:
    """Rubric §I criterion 56 — manifest loads via json.loads without error."""
    assert isinstance(_load_manifest(), dict)


def test_plugin_manifest_has_hooks_block() -> None:
    """Rubric §I criterion 57 — manifest["hooks"] is a dict."""
    m = _load_manifest()
    assert isinstance(m.get("hooks"), dict)


def test_plugin_manifest_stop_is_list_of_dicts() -> None:
    """Rubric §I criterion 58 — hooks["Stop"] is a list of dicts."""
    m = _load_manifest()
    stop = m["hooks"]["Stop"]
    assert isinstance(stop, list)
    for entry in stop:
        assert isinstance(entry, dict)


def test_plugin_manifest_stop_has_single_hook_group() -> None:
    """Rubric §I criterion 59 — hooks["Stop"] has exactly one element."""
    m = _load_manifest()
    assert len(m["hooks"]["Stop"]) == 1


def test_plugin_manifest_stop_has_single_hook() -> None:
    """Rubric §I criterion 60 — Stop hook group's inner hooks list has exactly one entry."""
    m = _load_manifest()
    assert len(m["hooks"]["Stop"][0]["hooks"]) == 1


def test_plugin_manifest_stop_hook_type_is_command() -> None:
    """Rubric §I criterion 61 — single Stop hook entry has type=="command"."""
    m = _load_manifest()
    assert m["hooks"]["Stop"][0]["hooks"][0]["type"] == "command"


def test_plugin_manifest_stop_command_is_daydream_cli_daydream() -> None:
    """Rubric §I criterion 62 — Stop hook command equals literal "daydream-cli daydream"."""
    m = _load_manifest()
    assert m["hooks"]["Stop"][0]["hooks"][0]["command"] == "daydream-cli daydream"


def test_plugin_manifest_stop_hook_async_is_true() -> None:
    """Rubric §I criterion 63 — Stop hook entry's async field is JSON boolean true."""
    m = _load_manifest()
    assert m["hooks"]["Stop"][0]["hooks"][0]["async"] is True


def test_plugin_manifest_stop_has_positive_timeout() -> None:
    """Rubric §I criterion 64 — Stop hook entry has positive-integer timeout field."""
    m = _load_manifest()
    timeout = m["hooks"]["Stop"][0]["hooks"][0]["timeout"]
    assert isinstance(timeout, int)
    assert timeout > 0


def test_plugin_manifest_precompact_is_list_of_dicts() -> None:
    """Rubric §I criterion 65 — hooks["PreCompact"] is a list of dicts."""
    m = _load_manifest()
    pre = m["hooks"]["PreCompact"]
    assert isinstance(pre, list)
    for entry in pre:
        assert isinstance(entry, dict)


def test_plugin_manifest_precompact_has_single_hook_group() -> None:
    """Rubric §I criterion 66 — hooks["PreCompact"] has exactly one hook-group element."""
    m = _load_manifest()
    assert len(m["hooks"]["PreCompact"]) == 1


def test_plugin_manifest_precompact_matcher_shape() -> None:
    """Rubric §I criterion 67 — PreCompact matcher is "manual|auto" or absent."""
    m = _load_manifest()
    group = m["hooks"]["PreCompact"][0]
    assert group.get("matcher", "manual|auto") == "manual|auto"


def test_plugin_manifest_precompact_command_matches_stop() -> None:
    """Rubric §I criterion 68 — PreCompact inner hook command equals "daydream-cli daydream"."""
    m = _load_manifest()
    assert m["hooks"]["PreCompact"][0]["hooks"][0]["command"] == "daydream-cli daydream"


def test_plugin_manifest_precompact_is_synchronous() -> None:
    """Rubric §I criterion 69 — PreCompact inner hook entry does NOT carry async=true."""
    m = _load_manifest()
    entry = m["hooks"]["PreCompact"][0]["hooks"][0]
    assert entry.get("async", False) is False


def test_plugin_manifest_precompact_has_positive_timeout() -> None:
    """Rubric §I criterion 70 — PreCompact inner hook entry has positive-integer timeout."""
    m = _load_manifest()
    timeout = m["hooks"]["PreCompact"][0]["hooks"][0]["timeout"]
    assert isinstance(timeout, int)
    assert timeout > 0


def test_plugin_manifest_command_has_no_session_interpolation() -> None:
    """Rubric §I criterion 71 — command strings contain no placeholder/interpolation tokens."""
    m = _load_manifest()
    forbidden = ("$", "${", "{{", "$CLAUDE_SESSION_ID", "$CLAUDE_TRANSCRIPT_PATH", "--session", "--log")
    for path in (
        m["hooks"]["Stop"][0]["hooks"][0]["command"],
        m["hooks"]["PreCompact"][0]["hooks"][0]["command"],
    ):
        for token in forbidden:
            assert token not in path, f"forbidden token {token!r} in command {path!r}"


def test_plugin_manifest_name_unchanged() -> None:
    """Rubric §I criterion 72 — top-level name field unchanged ("memeval-memory")."""
    m = _load_manifest()
    assert m["name"] == "memeval-memory"


def test_plugin_manifest_version_bumped() -> None:
    """Rubric §I criterion 73 — top-level version bumped from pre-PR5 value (0.1.0)."""
    m = _load_manifest()
    assert m["version"] != "0.1.0"


def test_plugin_manifest_stop_hook_sha256_pinned() -> None:
    """Rubric §I criterion 74 — Stop hook entry sha256 matches pinned constant."""
    m = _load_manifest()
    entry = m["hooks"]["Stop"][0]["hooks"][0]
    serialized = json.dumps(entry, sort_keys=True)
    digest = hashlib.sha256(serialized.encode()).hexdigest()
    assert digest == STOP_HOOK_SHA256, (
        f"Stop hook entry drifted from pinned hash. Got {digest}; "
        f"if the shape change is intentional, recompute and update STOP_HOOK_SHA256."
    )


def test_plugin_manifest_precompact_hook_sha256_pinned() -> None:
    """Rubric §I criterion 75 — PreCompact hook entry sha256 matches pinned constant."""
    m = _load_manifest()
    entry = m["hooks"]["PreCompact"][0]["hooks"][0]
    serialized = json.dumps(entry, sort_keys=True)
    digest = hashlib.sha256(serialized.encode()).hexdigest()
    assert digest == PRECOMPACT_HOOK_SHA256, (
        f"PreCompact hook entry drifted from pinned hash. Got {digest}; "
        f"if the shape change is intentional, recompute and update PRECOMPACT_HOOK_SHA256."
    )


# --------------------------------------------------------------------------- #
# §J — Plugin manifest distribution surface
# --------------------------------------------------------------------------- #


def test_plugin_manifest_in_package_data() -> None:
    """Rubric §J criterion 76 — manifest path is included in setuptools package-data."""
    import tomllib
    pyproject = Path(__file__).resolve().parents[3] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text())
    package_data = data.get("tool", {}).get("setuptools", {}).get("package-data", {})
    patterns = package_data.get("memeval.claudecode", [])
    if isinstance(patterns, str):
        patterns = [patterns]
    # At least one pattern must cover the manifest file
    import fnmatch
    manifest_rel = "plugin/.claude-plugin/plugin.json"
    matched = any(fnmatch.fnmatch(manifest_rel, p) for p in patterns)
    assert matched, (
        f"manifest path {manifest_rel} not matched by [tool.setuptools.package-data] "
        f"patterns {patterns}"
    )
