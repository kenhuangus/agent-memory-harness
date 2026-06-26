"""Offline tests for the Cursor CLI harness adapter (ADR-harness-013/014/015).

Prove the Cursor wiring runs DETERMINISTICALLY with no network, no real
``cursor-agent``, and no real API key:

* the ``stream-json`` parser handles Cursor's verified event shapes (system / user /
  assistant / tool_call started+completed / thinking / result) — answer, token usage,
  and deduped MCP tool calls;
* :func:`build_argv` emits the headless flags (``--trust``/``--approve-mcps``/
  ``--force``/``--model``);
* :class:`CursorCodeAgent` satisfies the AgentAdapter seam for ``off`` / ``builtin`` /
  ``plugin-real`` via an injected fake runner, and ISOLATES config in a fresh ``HOME``
  sandbox (writing a memory ``mcp.json`` only for plugin-real, never for the baseline).

A dummy ``CURSOR_API_KEY`` is set so the auth gate (ADR-harness-014) is satisfied
without a real key; the injected runner means the key is never actually used.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

_THIS = Path(__file__).resolve()
_BASE_DIR = _THIS.parent.parent
if str(_BASE_DIR) not in sys.path:
    sys.path.insert(0, str(_BASE_DIR))

from memeval.schema import Benchmark  # noqa: E402
from memeval.agent import run_agent  # noqa: E402
from memeval.cursorcli.agent import CursorCodeAgent  # noqa: E402
from memeval.cursorcli.cli import (  # noqa: E402
    CursorResult,
    build_argv,
    _parse_stream_json,
)
from memeval.cursorcli.platform import CursorRuntime  # noqa: E402
from memeval.cursorcli import sandbox as cursor_sandbox  # noqa: E402


_FIXTURES = _THIS.parent / "fixtures"
_RT = CursorRuntime(exe="cursor-agent")


def _fixture(name: str) -> str:
    return str(_FIXTURES / name)


# --------------------------------------------------------------------------- #
# stream-json parsing (real captured event shapes)
# --------------------------------------------------------------------------- #
class TestStreamJsonParse(unittest.TestCase):
    def _stream(self, *objs: str) -> str:
        return "\n".join(objs) + "\n"

    def test_answer_usage_and_tool_call_dedup(self):
        stream = self._stream(
            '{"type":"system","subtype":"init","model":"Composer 2.5","session_id":"s1"}',
            '{"type":"tool_call","subtype":"started","call_id":"c1",'
            '"tool_call":{"toolCallId":"c1","mcpToolCall":{"args":{'
            '"name":"cookbook-memory-recall","providerIdentifier":"cookbook-memory",'
            '"toolName":"recall","args":{"query":"deadline"}}}}}',
            '{"type":"tool_call","subtype":"completed","call_id":"c1",'
            '"tool_call":{"toolCallId":"c1","mcpToolCall":{"args":{'
            '"name":"cookbook-memory-recall","providerIdentifier":"cookbook-memory",'
            '"toolName":"recall","args":{"query":"deadline"}}}}}',
            '{"type":"thinking","subtype":"delta","text":"hmm"}',
            '{"type":"assistant","message":{"role":"assistant",'
            '"content":[{"type":"text","text":"the answer"}]}}',
            '{"type":"result","subtype":"success","is_error":false,'
            '"result":"the answer","session_id":"s1",'
            '"usage":{"inputTokens":100,"outputTokens":20,'
            '"cacheReadTokens":5,"cacheWriteTokens":0}}',
        )
        r = _parse_stream_json(stream)
        self.assertEqual(r.text, "the answer")
        self.assertEqual(r.tokens_in, 100)
        self.assertEqual(r.tokens_out, 20)
        # started + completed are ONE logical call — deduped by call_id.
        self.assertEqual(len(r.tool_calls), 1)
        tc = r.tool_calls[0]
        self.assertEqual((tc.server, tc.tool), ("cookbook-memory", "recall"))
        self.assertEqual(tc.args.get("query"), "deadline")

    def test_falls_back_to_assistant_text_without_result_text(self):
        stream = self._stream(
            '{"type":"assistant","message":{"role":"assistant",'
            '"content":[{"type":"text","text":"hello"}]}}',
            '{"type":"result","subtype":"success","is_error":false,'
            '"usage":{"inputTokens":3,"outputTokens":1}}',
        )
        r = _parse_stream_json(stream)
        self.assertEqual(r.text, "hello")

    def test_ignores_unknown_event_types_and_blank_lines(self):
        stream = "garbage\n\n" + self._stream(
            '{"type":"some_future_event","foo":1}',
            '{"type":"result","subtype":"success","result":"ok",'
            '"usage":{"inputTokens":1,"outputTokens":1}}',
        )
        r = _parse_stream_json(stream)
        self.assertEqual(r.text, "ok")


class TestBuildArgv(unittest.TestCase):
    def test_headless_flags(self):
        argv = build_argv(_RT, "do it", model="composer-2.5", approve_mcps=True,
                          force=True, trust=True)
        self.assertIn("--output-format", argv)
        self.assertEqual(argv[argv.index("--output-format") + 1], "stream-json")
        for flag in ("--trust", "--approve-mcps", "--force"):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index("--model") + 1], "composer-2.5")

    def test_baseline_omits_approve_and_force(self):
        argv = build_argv(_RT, "q", approve_mcps=False, force=False)
        self.assertNotIn("--approve-mcps", argv)
        self.assertNotIn("--force", argv)
        self.assertIn("--trust", argv)


# --------------------------------------------------------------------------- #
# Agent solve wiring (fake runner; dummy key; tmp HOME sandbox)
# --------------------------------------------------------------------------- #
class _CaptureRunner:
    """Fake ``run_cursor``: records each call's prompt/env/workspace/plugin_dir and
    returns a canned CursorResult, without launching any process."""

    def __init__(self, text: str = "friday") -> None:
        self.calls: list[dict] = []
        self.text = text

    def __call__(self, prompt, *, cwd, model=None, approve_mcps=True, force=False,
                 trust=True, workspace=None, plugin_dir=None, timeout=600, runtime=None,
                 env=None):
        self.calls.append({
            "prompt": prompt, "cwd": str(cwd), "approve_mcps": approve_mcps,
            "force": force, "plugin_dir": str(plugin_dir) if plugin_dir else None,
            "env": dict(env or {}),
        })
        return CursorResult(text=self.text, tokens_in=10, tokens_out=2)


class TestAgentSolve(unittest.TestCase):
    def setUp(self):
        self._prev_key = os.environ.get("CURSOR_API_KEY")
        os.environ["CURSOR_API_KEY"] = "dummy-test-key"
        self._tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self._tmp.name)

    def tearDown(self):
        if self._prev_key is None:
            os.environ.pop("CURSOR_API_KEY", None)
        else:
            os.environ["CURSOR_API_KEY"] = self._prev_key
        self._tmp.cleanup()

    def _agent(self, mode: str, runner) -> CursorCodeAgent:
        return CursorCodeAgent(model="composer-2.5", memory_mode=mode, runner=runner,
                               runtime=_RT, workdir=self.workdir, timeout=30)

    def test_off_no_plugin_no_hooks_uses_home_sandbox(self):
        runner = _CaptureRunner()
        rr = run_agent(Benchmark.LONGMEMEVAL, self._agent("off", runner), memory=False,
                       path_or_id=_fixture("longmemeval.json"), limit=1, seed_sessions=False)
        self.assertEqual(rr.n_tasks, 1)
        self.assertEqual(len(runner.calls), 1)
        call = runner.calls[0]
        # HOME relocated to a sandbox (NOT the real home) — the isolation seam.
        self.assertIn("HOME", call["env"])
        self.assertNotEqual(call["env"]["HOME"], os.path.expanduser("~"))
        # baseline: no plugin bundle, no approval, no user-level daydream hooks →
        # provably tool-less and write-less.
        self.assertFalse(call["approve_mcps"])
        self.assertIsNone(call["plugin_dir"])
        hooks = Path(call["env"]["HOME"]) / ".cursor" / "hooks.json"
        self.assertFalse(hooks.exists(), "baseline must not wire daydream hooks")

    def test_plugin_real_builds_bundle_and_wires_hooks(self):
        runner = _CaptureRunner()
        agent = self._agent("plugin-real", runner)
        # point the shared substrate at a tmp dir so MEMORY_STORE is deterministic
        agent._project_dir = self.workdir / "_memory"
        rr = run_agent(Benchmark.LONGMEMEVAL, agent, memory=True,
                       path_or_id=_fixture("longmemeval.json"), limit=1, seed_sessions=False)
        self.assertEqual(rr.n_tasks, 1)
        call = runner.calls[0]
        # plugin-real: --approve-mcps on + the shipping bundle loaded via --plugin-dir.
        self.assertTrue(call["approve_mcps"])
        self.assertIsNotNone(call["plugin_dir"], "plugin-real must pass --plugin-dir")
        bundle = Path(call["plugin_dir"])
        # the bundle is the real built artifact: root mcp.json + manifest + skill.
        self.assertTrue((bundle / "mcp.json").is_file())
        self.assertTrue((bundle / ".cursor-plugin" / "plugin.json").is_file())
        self.assertTrue(any((bundle / "skills").glob("*/SKILL.md")))
        # the bundle's MCP store is pinned to the shared substrate (per-run MEMORY_STORE).
        import json
        mcp = json.loads((bundle / "mcp.json").read_text())
        store = mcp["mcpServers"][cursor_sandbox.MCP_SERVER_NAME]["env"]["MEMORY_STORE"]
        self.assertIn(".cookbook-memory", store)
        # the DAYDREAM-WRITE hooks are wired at user level (where they fire headless).
        hooks_path = Path(call["env"]["HOME"]) / ".cursor" / "hooks.json"
        self.assertTrue(hooks_path.is_file(), "plugin-real must wire user-level hooks")
        hooks = json.loads(hooks_path.read_text())["hooks"]
        self.assertIn("sessionEnd", hooks)  # the daydream WRITE trigger (Stop analog)
        self.assertIn("hooks_handler sessionEnd", hooks["sessionEnd"][0]["command"])

    def test_builtin_sessions_no_plugin(self):
        runner = _CaptureRunner()
        rr = run_agent(Benchmark.LONGMEMEVAL, self._agent("builtin", runner),
                       memory=True, path_or_id=_fixture("longmemeval.json"), limit=1, seed_sessions=False)
        self.assertEqual(rr.n_tasks, 1)
        call = runner.calls[0]
        self.assertIsNone(call["plugin_dir"], "builtin uses native file memory, no plugin")


class TestTranscriptNormalizer(unittest.TestCase):
    """The Cursor→Daydreamer transcript normalizer (the one Cursor-specific piece of
    the WRITE path): Cursor's ``{role, message:[blocks]}`` JSONL must become the
    ``{type, message:{role, content:[blocks]}}`` shape the Daydreamer's formatter
    parses, so memory extraction actually works on Cursor transcripts."""

    def test_normalizes_cursor_lines_for_daydreamer(self):
        import json
        from cookbook_memory.adapters.cursor.hooks_handler import _normalize_transcript
        from memeval.dreaming.transcript_formatter import format_chunk

        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "cursor.jsonl"
            # Cover BOTH real Cursor shapes: the current one wraps content in a
            # message dict; the older one used a bare content list.
            src.write_text("\n".join([
                json.dumps({"role": "user",
                            "message": {"content": [{"type": "text", "text": "deploy is make ship"}]}}),
                json.dumps({"role": "assistant",
                            "message": [{"type": "text", "text": "got it"}]}),
                json.dumps({"type": "turn_ended", "status": "completed"}),
            ]))
            out = _normalize_transcript(src, Path(d))
            self.assertIsNotNone(out)
            lines = [json.loads(l) for l in out.read_text().splitlines()]
            # message rewritten to {type, message:{role, content}}
            self.assertEqual(lines[0]["type"], "user")
            self.assertEqual(lines[0]["message"]["role"], "user")
            self.assertEqual(lines[0]["message"]["content"][0]["text"], "deploy is make ship")
            # non-message line passed through
            self.assertEqual(lines[2]["type"], "turn_ended")
            # and the Daydreamer's formatter renders the normalized form
            rendered = format_chunk(out.read_text(), limit=0)
            self.assertIn("deploy is make ship", rendered)
            self.assertIn("USER", rendered)


class TestSandboxGuards(unittest.TestCase):
    def test_refuses_real_home_as_sandbox(self):
        with self.assertRaises(ValueError):
            cursor_sandbox.build(os.path.expanduser("~"))

    def test_require_api_key_raises_when_unset(self):
        prev = os.environ.pop("CURSOR_API_KEY", None)
        prev2 = os.environ.pop("CURSOR_AUTH_TOKEN", None)
        try:
            with self.assertRaises(cursor_sandbox.CursorNotAuthenticated):
                cursor_sandbox.require_api_key()
        finally:
            if prev is not None:
                os.environ["CURSOR_API_KEY"] = prev
            if prev2 is not None:
                os.environ["CURSOR_AUTH_TOKEN"] = prev2


if __name__ == "__main__":
    unittest.main()
