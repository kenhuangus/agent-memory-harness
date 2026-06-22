"""Unit tests for the sandboxed CLAUDE_CONFIG_DIR (memeval.claudecode.sandbox)
and its wiring into cli._clean_env. Stdlib-only: no `claude` CLI, no network.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

# Make the package importable when run directly (mirrors test_smoke.py).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memeval.claudecode import sandbox  # noqa: E402
from memeval.claudecode import cli  # noqa: E402


class _Env:
    """Context manager: set/clear env vars and restore them on exit."""

    def __init__(self, **vars_: object) -> None:
        self._new = vars_
        self._old: dict[str, str | None] = {}

    def __enter__(self) -> "_Env":
        for k, v in self._new.items():
            self._old[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = str(v)
        return self

    def __exit__(self, *exc: object) -> None:
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class ActiveConfigDir(unittest.TestCase):
    def test_explicit_env_takes_precedence(self) -> None:
        with _Env(MEMEVAL_SANDBOX_CONFIG_DIR="/tmp/explicit-sbx", MEMEVAL_SANDBOX=None):
            self.assertEqual(sandbox.active_config_dir(), "/tmp/explicit-sbx")

    def test_toggle_off_disables(self) -> None:
        for falsey in ("0", "false", "no", "off", ""):
            with _Env(MEMEVAL_SANDBOX_CONFIG_DIR=None, MEMEVAL_SANDBOX=falsey):
                self.assertIsNone(sandbox.active_config_dir(), f"{falsey!r} should disable")

    def test_default_dir_used_only_when_built(self) -> None:
        # No env overrides: falls back to the default project dir IFF it's built
        # (has a settings.json written by build()).
        with _Env(MEMEVAL_SANDBOX_CONFIG_DIR=None, MEMEVAL_SANDBOX=None):
            expected = str(sandbox.default_config_dir()) if sandbox.exists() else None
            self.assertEqual(sandbox.active_config_dir(), expected)


class BuildSandbox(unittest.TestCase):
    def test_build_writes_minimal_settings_only(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "sbx"
            out = sandbox.build(d)
            self.assertEqual(out, d.resolve())
            self.assertTrue((d / "settings.json").is_file())
            self.assertEqual(json.loads((d / "settings.json").read_text()), {})
            self.assertTrue(sandbox.exists(d))
            # Auth is NOT seeded — a fresh build is logged out.
            self.assertFalse((d / ".credentials.json").exists())
            self.assertFalse(sandbox.is_logged_in(d))

    def test_is_logged_in_detects_credentials_file(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "sbx"
            sandbox.build(d)
            (d / ".credentials.json").write_text("{}")
            self.assertTrue(sandbox.is_logged_in(d))

    def test_is_logged_in_detects_oauth_account(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "sbx"
            sandbox.build(d)
            (d / ".claude.json").write_text(json.dumps({"oauthAccount": {"x": 1}}))
            self.assertTrue(sandbox.is_logged_in(d))

    def test_overwrite_resets_settings(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "sbx"
            sandbox.build(d)
            (d / "settings.json").write_text('{"tampered": true}')
            sandbox.build(d, overwrite=True)
            self.assertEqual(json.loads((d / "settings.json").read_text()), {})


class LoginCommands(unittest.TestCase):
    def test_posix_form(self) -> None:
        cmds = sandbox.login_commands(Path("/x/sbx"), windows=False)
        self.assertEqual(len(cmds), 1)
        self.assertIn("CLAUDE_CONFIG_DIR=/x/sbx claude", cmds[0])

    def test_windows_powershell_form(self) -> None:
        cmds = sandbox.login_commands(Path(r"C:\x\sbx"), windows=True)
        self.assertTrue(any("$env:CLAUDE_CONFIG_DIR" in c for c in cmds))
        self.assertTrue(any(c.strip().startswith("claude") for c in cmds))


class CleanEnvWiring(unittest.TestCase):
    def test_sets_config_dir_and_strips_api_keys(self) -> None:
        with _Env(MEMEVAL_SANDBOX_CONFIG_DIR="/tmp/sbx", MEMEVAL_SANDBOX=None,
                  ANTHROPIC_API_KEY="sk-should-be-stripped"):
            env = cli._clean_env(strip_api_key=True)
            assert env is not None
            self.assertEqual(env.get("CLAUDE_CONFIG_DIR"), "/tmp/sbx")
            self.assertNotIn("ANTHROPIC_API_KEY", env)

    def test_sets_config_dir_even_when_not_stripping_keys(self) -> None:
        with _Env(MEMEVAL_SANDBOX_CONFIG_DIR="/tmp/sbx", MEMEVAL_SANDBOX=None):
            env = cli._clean_env(strip_api_key=False)
            assert env is not None
            self.assertEqual(env.get("CLAUDE_CONFIG_DIR"), "/tmp/sbx")

    def test_none_when_no_sandbox_and_no_strip(self) -> None:
        with _Env(MEMEVAL_SANDBOX_CONFIG_DIR=None, MEMEVAL_SANDBOX="off"):
            self.assertIsNone(cli._clean_env(strip_api_key=False))

    def test_strip_only_when_sandbox_disabled(self) -> None:
        with _Env(MEMEVAL_SANDBOX_CONFIG_DIR=None, MEMEVAL_SANDBOX="off",
                  ANTHROPIC_AUTH_TOKEN="tok"):
            env = cli._clean_env(strip_api_key=True)
            assert env is not None
            self.assertNotIn("ANTHROPIC_AUTH_TOKEN", env)
            self.assertNotIn("CLAUDE_CONFIG_DIR", env)


class WslEnvPrefix(unittest.TestCase):
    """The in-WSL `env ...` prefix carries both adjustments across the boundary."""

    def test_unsets_keys_and_sets_translated_config_dir(self) -> None:
        with _Env(MEMEVAL_SANDBOX_CONFIG_DIR="/mnt/c/x/sbx", MEMEVAL_SANDBOX=None):
            prefix = cli._wsl_env_prefix(strip_api_key=True)
            self.assertEqual(prefix[0], "env")
            self.assertIn("-u", prefix)
            self.assertIn("ANTHROPIC_API_KEY", prefix)
            self.assertIn("CLAUDE_CONFIG_DIR=/mnt/c/x/sbx", prefix)

    def test_translates_windows_path(self) -> None:
        with _Env(MEMEVAL_SANDBOX_CONFIG_DIR=r"C:\x\sbx", MEMEVAL_SANDBOX=None):
            prefix = cli._wsl_env_prefix(strip_api_key=False)
            self.assertIn("CLAUDE_CONFIG_DIR=/mnt/c/x/sbx", prefix)

    def test_empty_when_nothing_to_set(self) -> None:
        with _Env(MEMEVAL_SANDBOX_CONFIG_DIR=None, MEMEVAL_SANDBOX="off"):
            self.assertEqual(cli._wsl_env_prefix(strip_api_key=False), [])


if __name__ == "__main__":
    unittest.main()
