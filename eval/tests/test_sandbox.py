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
            # The ONLY content is the cookbook-memory plugin MCP allow-rule (so a
            # plugin-real turn needs no restrictive --allowedTools); no hooks/skills/etc.
            settings = json.loads((d / "settings.json").read_text())
            self.assertEqual(settings, sandbox._sandbox_settings())
            self.assertIn(sandbox._PLUGIN_MCP_SERVER_RULE,
                          settings["permissions"]["allow"])
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
            self.assertEqual(json.loads((d / "settings.json").read_text()),
                             sandbox._sandbox_settings())

    def test_ensure_plugin_tool_allowed_upgrades_legacy_empty_settings(self) -> None:
        # A sandbox built before this rule existed has an empty settings.json; the
        # ensure helper merges the allow-rule in (so plugin-real needs no --allowedTools).
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "sbx"
            d.mkdir(parents=True)
            (d / "settings.json").write_text("{}\n")
            wrote = sandbox.ensure_plugin_tool_allowed(d)
            self.assertTrue(wrote)
            allow = json.loads((d / "settings.json").read_text())["permissions"]["allow"]
            self.assertIn(sandbox._PLUGIN_MCP_SERVER_RULE, allow)

    def test_ensure_plugin_tool_allowed_is_idempotent(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "sbx"
            sandbox.build(d)  # already has the rule
            self.assertFalse(sandbox.ensure_plugin_tool_allowed(d))  # no rewrite needed

    def test_ensure_plugin_tool_allowed_preserves_existing_permissions(self) -> None:
        # Existing allow rules + other settings keys are kept; the plugin rule is added.
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "sbx"
            d.mkdir(parents=True)
            (d / "settings.json").write_text(json.dumps({
                "permissions": {"allow": ["Bash"], "deny": ["WebFetch"]},
                "model": "claude-haiku-4-5",
            }))
            sandbox.ensure_plugin_tool_allowed(d)
            data = json.loads((d / "settings.json").read_text())
            self.assertIn("Bash", data["permissions"]["allow"])
            self.assertIn(sandbox._PLUGIN_MCP_SERVER_RULE, data["permissions"]["allow"])
            self.assertEqual(data["permissions"]["deny"], ["WebFetch"])
            self.assertEqual(data["model"], "claude-haiku-4-5")

    def test_ensure_plugin_tool_allowed_skips_when_exact_tool_present(self) -> None:
        # If the exact recall tool (not the server-wide rule) is already allowed, no
        # rewrite — the tool is already granted.
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "sbx"
            d.mkdir(parents=True)
            (d / "settings.json").write_text(json.dumps(
                {"permissions": {"allow": [sandbox.RECALL_MCP_TOOL]}}))
            self.assertFalse(sandbox.ensure_plugin_tool_allowed(d))


class SeedAuthFromHost(unittest.TestCase):
    """Copying the host's file-based OAuth login into a sandbox (Linux/WSL)."""

    def _make_host(self, td: Path):
        """A fake host: ~/.claude/.credentials.json + ~/.claude.json (account)."""
        host = td / "host" / ".claude"
        host.mkdir(parents=True)
        (host / ".credentials.json").write_text(json.dumps(
            {"claudeAiOauth": {"accessToken": "tok", "expiresAt": 9999999999999},
             "trustedDeviceToken": "dev"}))
        (host.parent / ".claude.json").write_text(json.dumps(
            {"oauthAccount": {"emailAddress": "x@y.z"}, "userID": "u1",
             "unrelated": "stays-on-host"}))
        return host

    def test_seeds_credentials_and_account(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            host = self._make_host(tdp)
            sbx = tdp / "sbx"
            sandbox.build(sbx)
            self.assertTrue(sandbox.seed_auth_from_host(sbx, host_dir=host))
            # credential file copied verbatim
            self.assertEqual(
                json.loads((sbx / ".credentials.json").read_text())["claudeAiOauth"]["accessToken"],
                "tok")
            # account identity merged; unrelated host keys NOT pulled in
            sb_cj = json.loads((sbx / ".claude.json").read_text())
            self.assertEqual(sb_cj["oauthAccount"], {"emailAddress": "x@y.z"})
            self.assertEqual(sb_cj["userID"], "u1")
            self.assertNotIn("unrelated", sb_cj)
            self.assertTrue(sandbox.is_logged_in(sbx))

    def test_preserves_existing_sandbox_claude_json(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            host = self._make_host(tdp)
            sbx = tdp / "sbx"
            sandbox.build(sbx)
            (sbx / ".claude.json").write_text(json.dumps({"enabledPlugins": {"cookbook": True}}))
            self.assertTrue(sandbox.seed_auth_from_host(sbx, host_dir=host))
            sb_cj = json.loads((sbx / ".claude.json").read_text())
            self.assertEqual(sb_cj["enabledPlugins"], {"cookbook": True})  # install state kept
            self.assertEqual(sb_cj["userID"], "u1")                        # account merged

    def test_returns_false_when_host_has_no_file_credential(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            host = tdp / "host" / ".claude"  # keychain platform: no .credentials.json
            host.mkdir(parents=True)
            sbx = tdp / "sbx"
            sandbox.build(sbx)
            self.assertFalse(sandbox.seed_auth_from_host(sbx, host_dir=host))
            self.assertFalse((sbx / ".credentials.json").exists())

    def test_returns_false_for_stale_expired_credential(self) -> None:
        # macOS guard: an on-disk .credentials.json that has EXPIRED (the Keychain holds
        # the live token) must NOT be copied — else the sandbox looks seeded but fails
        # "Not logged in" with no /login hint.
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            host = tdp / "host" / ".claude"
            host.mkdir(parents=True)
            (host / ".credentials.json").write_text(json.dumps(
                {"claudeAiOauth": {"accessToken": "stale", "expiresAt": 1}}))  # 1970 -> expired
            sbx = tdp / "sbx"
            sandbox.build(sbx)
            self.assertFalse(sandbox.seed_auth_from_host(sbx, host_dir=host))
            self.assertFalse((sbx / ".credentials.json").exists())

    def test_returns_false_for_credential_without_expiry(self) -> None:
        # No expiresAt -> can't vouch for freshness -> don't seed (fall back to /login).
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            host = tdp / "host" / ".claude"
            host.mkdir(parents=True)
            (host / ".credentials.json").write_text(json.dumps(
                {"claudeAiOauth": {"accessToken": "no-exp"}}))
            sbx = tdp / "sbx"
            sandbox.build(sbx)
            self.assertFalse(sandbox.seed_auth_from_host(sbx, host_dir=host))


class SeedAuthExpiredButRefreshable(unittest.TestCase):
    """Rerun robustness: an expired host token with a refreshToken is recovered.

    The host subscription token expires every ~8h; before the fix that made the whole
    run fail (seeding refused -> sandbox logged out -> "Not logged in"). Now an expired
    cred that still carries a refreshToken triggers a one-time host-side refresh, then
    seeding proceeds. The refresh subprocess is injected (``refresher=``) so no real
    ``claude`` / network is touched.
    """

    def _write_cred(self, host: Path, *, expires_at, refresh_token=None) -> None:
        oauth = {"accessToken": "tok", "expiresAt": expires_at}
        if refresh_token is not None:
            oauth["refreshToken"] = refresh_token
        host.mkdir(parents=True, exist_ok=True)
        (host / ".credentials.json").write_text(json.dumps({"claudeAiOauth": oauth}))
        (host.parent / ".claude.json").write_text(json.dumps(
            {"oauthAccount": {"emailAddress": "x@y.z"}, "userID": "u1"}))

    def test_expired_with_refresh_token_refreshes_then_seeds(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            host = tdp / "host" / ".claude"
            # Expired (1970) but has a refreshToken -> recoverable.
            self._write_cred(host, expires_at=1, refresh_token="rt-abc")
            sbx = tdp / "sbx"
            sandbox.build(sbx)

            refreshed: dict = {}

            def fake_refresh(host_dir: Path) -> bool:
                # The CLI would rewrite .credentials.json with a fresh token; emulate it.
                refreshed["host"] = host_dir
                (host_dir / ".credentials.json").write_text(json.dumps(
                    {"claudeAiOauth": {"accessToken": "fresh",
                                       "expiresAt": 9999999999999,
                                       "refreshToken": "rt-abc"}}))
                return True

            self.assertTrue(sandbox.seed_auth_from_host(
                sbx, host_dir=host, refresher=fake_refresh))
            # Host refresh was invoked against the host dir...
            self.assertEqual(refreshed["host"], host.resolve())
            # ...and the now-fresh token was seeded into the sandbox.
            self.assertEqual(
                json.loads((sbx / ".credentials.json").read_text())["claudeAiOauth"]["accessToken"],
                "fresh")
            self.assertTrue(sandbox.is_logged_in(sbx))

    def test_expired_without_refresh_token_not_seedable(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            host = tdp / "host" / ".claude"
            self._write_cred(host, expires_at=1)  # expired, NO refreshToken -> dead
            sbx = tdp / "sbx"
            sandbox.build(sbx)

            called: dict = {"n": 0}

            def fake_refresh(host_dir: Path) -> bool:
                called["n"] += 1
                return True

            self.assertFalse(sandbox.seed_auth_from_host(
                sbx, host_dir=host, refresher=fake_refresh))
            self.assertEqual(called["n"], 0)  # never even attempted a refresh
            self.assertFalse((sbx / ".credentials.json").exists())

    def test_fresh_token_seeds_directly_without_refresh(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            host = tdp / "host" / ".claude"
            self._write_cred(host, expires_at=9999999999999, refresh_token="rt-abc")
            sbx = tdp / "sbx"
            sandbox.build(sbx)

            called: dict = {"n": 0}

            def fake_refresh(host_dir: Path) -> bool:
                called["n"] += 1
                return True

            self.assertTrue(sandbox.seed_auth_from_host(
                sbx, host_dir=host, refresher=fake_refresh))
            self.assertEqual(called["n"], 0)  # happy path: no refresh needed
            self.assertEqual(
                json.loads((sbx / ".credentials.json").read_text())["claudeAiOauth"]["accessToken"],
                "tok")

    def test_refresh_that_fails_to_become_fresh_does_not_seed(self) -> None:
        # Refresh ran but the cred is still expired afterwards (e.g. refresh errored) ->
        # don't seed a dead token; fall back to interactive login.
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            host = tdp / "host" / ".claude"
            self._write_cred(host, expires_at=1, refresh_token="rt-abc")
            sbx = tdp / "sbx"
            sandbox.build(sbx)

            def fake_refresh(host_dir: Path) -> bool:
                return True  # claims success but leaves the cred expired

            self.assertFalse(sandbox.seed_auth_from_host(
                sbx, host_dir=host, refresher=fake_refresh))
            self.assertFalse((sbx / ".credentials.json").exists())

    def test_refresh_strips_api_key_env_vars(self) -> None:
        # The real refresher MUST unset ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN before
        # invoking claude (they're exported in WSL ~/.profile and force API-key mode,
        # which fails "Invalid API key"). Assert subprocess.run sees them stripped.
        import subprocess
        import tempfile
        captured: dict = {}

        def fake_run(argv, **kw):
            captured["argv"] = argv
            captured["env"] = kw.get("env", {})
            return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

        orig_run = subprocess.run
        orig_find = sandbox._find_claude_exe
        with _Env(ANTHROPIC_API_KEY="sk-x", ANTHROPIC_AUTH_TOKEN="tok"):
            try:
                subprocess.run = fake_run
                sandbox._find_claude_exe = lambda: "claude"
                with tempfile.TemporaryDirectory() as td:
                    host = Path(td) / "host" / ".claude"
                    host.mkdir(parents=True)
                    # cred fresh AFTER the (mocked) run so _refresh returns True
                    (host / ".credentials.json").write_text(json.dumps(
                        {"claudeAiOauth": {"accessToken": "fresh",
                                           "expiresAt": 9999999999999}}))
                    self.assertTrue(sandbox._refresh_host_credential(host))
            finally:
                subprocess.run = orig_run
                sandbox._find_claude_exe = orig_find

        self.assertEqual(captured["argv"][1:], ["-p", "ok"])
        self.assertNotIn("ANTHROPIC_API_KEY", captured["env"])
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", captured["env"])
        # and it pointed CLAUDE_CONFIG_DIR at the host dir so the host token refreshes
        self.assertIn("CLAUDE_CONFIG_DIR", captured["env"])


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

    def test_extra_env_merged_in(self) -> None:
        # plugin-real passes PATH / CLAUDE_PROJECT_DIR so an installed plugin's MCP
        # server + store path resolve; _clean_env must merge them on top.
        with _Env(MEMEVAL_SANDBOX_CONFIG_DIR=None, MEMEVAL_SANDBOX="off"):
            env = cli._clean_env(strip_api_key=False,
                                 extra_env={"CLAUDE_PROJECT_DIR": "/run/x", "PATH": "/v/bin:/usr/bin"})
            assert env is not None
            self.assertEqual(env.get("CLAUDE_PROJECT_DIR"), "/run/x")
            self.assertEqual(env.get("PATH"), "/v/bin:/usr/bin")

    def test_extra_env_none_keeps_inherited_when_nothing_else(self) -> None:
        with _Env(MEMEVAL_SANDBOX_CONFIG_DIR=None, MEMEVAL_SANDBOX="off"):
            self.assertIsNone(cli._clean_env(strip_api_key=False, extra_env=None))


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


class InstallPluginBundle(unittest.TestCase):
    """The eval consumer: native `claude plugin` install into the sandbox.

    No real claude — subprocess.run is faked to capture the argv sequence + env."""

    def _capture(self, returncodes=None):
        import subprocess
        calls = []
        rcs = list(returncodes or [])

        def fake_run(argv, **kw):
            calls.append((argv, kw.get("env", {})))
            rc = rcs.pop(0) if rcs else 0
            return subprocess.CompletedProcess(argv, rc, stdout="", stderr="")
        return calls, fake_run

    def test_native_install_sequence_and_config_dir(self) -> None:
        import subprocess
        calls, fake = self._capture()
        orig = subprocess.run
        try:
            subprocess.run = fake
            sandbox.install_plugin_bundle(
                "/tmp/bundle", config_dir=Path("/tmp/sbx"), claude_exe="claude")
        finally:
            subprocess.run = orig
        verbs = [tuple(a[2:4]) for a, _ in calls]  # (subcmd, arg) after [exe, "plugin"]
        # idempotent clean slate, then add + install
        self.assertEqual(verbs[0], ("uninstall", sandbox.PLUGIN_NAME))
        self.assertEqual(verbs[1], ("marketplace", "remove"))
        self.assertEqual(verbs[2], ("marketplace", "add"))
        self.assertEqual(calls[3][0][2:4], ["install", f"{sandbox.PLUGIN_NAME}@{sandbox.PLUGIN_MARKETPLACE}"])
        # every call points CLAUDE_CONFIG_DIR at the (resolved) sandbox
        expected = str(Path("/tmp/sbx").resolve())
        for _, env in calls:
            self.assertEqual(env.get("CLAUDE_CONFIG_DIR"), expected)

    def test_raises_when_install_fails(self) -> None:
        import subprocess
        # rc sequence: uninstall(ok), mkt remove(ok), mkt add(ok), install(FAIL=1)
        calls, fake = self._capture(returncodes=[0, 0, 0, 1])
        orig = subprocess.run
        try:
            subprocess.run = fake
            with self.assertRaises(RuntimeError):
                sandbox.install_plugin_bundle(
                    "/tmp/bundle", config_dir=Path("/tmp/sbx"), claude_exe="claude")
        finally:
            subprocess.run = orig


class PluginRuntimeEnv(unittest.TestCase):
    def test_prepends_memory_cli_dir_to_path(self) -> None:
        import shutil
        orig_which, orig_path = shutil.which, os.environ.get("PATH", "")
        try:
            shutil.which = lambda name: "/opt/venv/bin/memory-cli" if name == "memory-cli" else None
            os.environ["PATH"] = "/usr/bin"
            env = sandbox.plugin_runtime_env()
            self.assertEqual(env["PATH"], "/opt/venv/bin" + os.pathsep + "/usr/bin")
        finally:
            shutil.which = orig_which
            os.environ["PATH"] = orig_path

    def test_empty_when_already_on_path(self) -> None:
        import shutil
        orig_which, orig_path = shutil.which, os.environ.get("PATH", "")
        try:
            shutil.which = lambda name: "/opt/venv/bin/memory-cli" if name == "memory-cli" else None
            os.environ["PATH"] = "/opt/venv/bin" + os.pathsep + "/usr/bin"
            self.assertEqual(sandbox.plugin_runtime_env(), {})
        finally:
            shutil.which = orig_which
            os.environ["PATH"] = orig_path

    def test_require_runtime_raises_without_memory_cli(self) -> None:
        import shutil
        orig_which = shutil.which
        try:
            shutil.which = lambda name: None
            with self.assertRaises(RuntimeError):
                sandbox._require_plugin_mcp_runtime()
        finally:
            shutil.which = orig_which


if __name__ == "__main__":
    unittest.main()
