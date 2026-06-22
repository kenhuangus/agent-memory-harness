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
        # No env overrides: falls back to the default project dir IFF it exists.
        with _Env(MEMEVAL_SANDBOX_CONFIG_DIR=None, MEMEVAL_SANDBOX=None):
            expected = str(sandbox.default_config_dir()) if sandbox.default_config_dir().is_dir() else None
            self.assertEqual(sandbox.active_config_dir(), expected)


class BuildSandbox(unittest.TestCase):
    def test_build_writes_settings_and_no_creds(self, ) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "sbx"
            out = sandbox.build(d, seed_credentials=False)
            self.assertEqual(out, d.resolve())
            self.assertTrue((d / "settings.json").is_file())
            self.assertEqual(json.loads((d / "settings.json").read_text()), {})
            # No credential seeded -> not considered "built" for auth purposes.
            self.assertFalse(sandbox.is_built(d))

    def test_build_seeds_credential_when_present(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            fake_home = Path(td) / "home"
            (fake_home / ".claude").mkdir(parents=True)
            cred = fake_home / ".claude" / ".credentials.json"
            cred.write_text('{"token":"fake"}')
            d = Path(td) / "sbx"
            # Point the module's host-cred path at our fake home for this test.
            orig = sandbox._HOST_CREDENTIALS
            try:
                sandbox._HOST_CREDENTIALS = cred
                out = sandbox.build(d, seed_credentials=True)
            finally:
                sandbox._HOST_CREDENTIALS = orig
            self.assertTrue(sandbox.is_built(out))
            self.assertEqual(json.loads((out / ".credentials.json").read_text()),
                             {"token": "fake"})

    def test_build_raises_when_no_host_credential(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "sbx"
            orig = sandbox._HOST_CREDENTIALS
            try:
                sandbox._HOST_CREDENTIALS = Path(td) / "nope" / ".credentials.json"
                with self.assertRaises(FileNotFoundError):
                    sandbox.build(d, seed_credentials=True)
            finally:
                sandbox._HOST_CREDENTIALS = orig


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


if __name__ == "__main__":
    unittest.main()
