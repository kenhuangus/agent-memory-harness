"""Builtin (Claude CLI native-memory) recall instrumentation tests.

The ``builtin`` arm reads its prior knowledge from session Markdown files
(:func:`memeval.claudecode.agent._write_session_files`) and recalls them with the
CLI's OWN Grep/Glob/Read — there is no explicit recall event, so before this fix the
builtin trajectory carried ZERO ``retrieve`` steps and ``recall_attempted`` /
``gold_retrieval_f1`` read 0/0 purely for lack of instrumentation, NOT because native
memory did nothing. :meth:`ClaudeCodeAgent._attribute_builtin_recall` parses the CLI
transcript and emits a ``retrieve`` step per session whose content the native tools
actually surfaced — so recall_attempted/with_hits AND gold_retrieval_f1 measure for
builtin the SAME content-matched way they do for plugin-real.

These tests assert, fully offline (transcript events injected, no CLI, no network):

* a ``Read`` of a session file surfaces that session as a recall hit, with the
  RetrievedItem content = the genuine session text;
* a ``Grep``/``Glob`` whose tool_result names a session file surfaces it too;
* a transcript that never touches the session files yields NO recall (honest 0 —
  never fabricated);
* the pure matcher is deterministic and order-preserving;
* the full ``_attribute_builtin_recall`` records the step onto a fake ctx.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_BASE_DIR = Path(__file__).resolve().parent.parent  # .../eval
if str(_BASE_DIR) not in sys.path:
    sys.path.insert(0, str(_BASE_DIR))

from memeval.claudecode.agent import (  # noqa: E402
    ClaudeCodeAgent,
    _builtin_recalled_items,
    _write_session_files,
)
from memeval.schema import Benchmark, Session, Task, TaskKind  # noqa: E402


def _fake_runner(*args, **kwargs):  # pragma: no cover - never invoked here
    raise AssertionError("runner must not be called by recall attribution")


FACT = "[fact] vendor = Acme Corp, net-30 terms"
DRIFT = "[drift] policy update: approval now requires CFO sign-off"


def _vista_task(task_id: str = "j1") -> Task:
    sessions = [
        Session(session_id=f"{task_id}::fact::0", content=FACT,
                timestamp=0.0, index=0, role="user", metadata={"event_type": "fact"}),
        Session(session_id=f"{task_id}::drift::2", content=DRIFT,
                timestamp=0.0, index=1, role="user", metadata={"event_type": "drift"}),
    ]
    return Task(
        task_id=task_id, benchmark=Benchmark.VISTA, kind=TaskKind.QA,
        question="Set up the new vendor relationship.", answer=None, sessions=sessions,
        gold_memory_ids=[f"{task_id}::fact::0", f"{task_id}::drift::2"],
        group_id="project", order=0, competency="project", metadata={},
    )


def _file_map(task: Task) -> dict:
    """Mirror _attribute_builtin_recall's basename -> (id, content) construction."""
    out = {}
    for i, s in enumerate(task.sessions):
        safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in str(s.session_id))[:60]
        out[f"session_{i:04d}_{safe}.md"] = (s.session_id, s.content)
    return out


def _read_event(file_path: str, tuid: str) -> dict:
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": tuid, "name": "Read", "input": {"file_path": file_path}},
    ]}}


def _result_event(tuid: str, text: str) -> dict:
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tuid,
         "content": [{"type": "text", "text": text}]},
    ]}}


def _grep_result_event(text: str) -> dict:
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "g1", "content": text},
    ]}}


class BuiltinRecalledItemsTest(unittest.TestCase):
    def test_read_of_session_file_is_a_hit(self) -> None:
        task = _vista_task()
        fmap = _file_map(task)
        fact_file = [f for f, (sid, _) in fmap.items() if sid.endswith("fact::0")][0]
        events = [
            _read_event(f"/run/sessions/{fact_file}", "tu1"),
            _result_event("tu1", f"# Session\n\n{FACT}\n"),
        ]
        items = _builtin_recalled_items(events, fmap)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].item_id, f"{task.task_id}::fact::0")
        self.assertEqual(items[0].item.content, FACT)  # genuine surfaced text

    def test_grep_result_naming_file_is_a_hit(self) -> None:
        task = _vista_task()
        fmap = _file_map(task)
        drift_file = [f for f, (sid, _) in fmap.items() if sid.endswith("drift::2")][0]
        events = [_grep_result_event(f"sessions/{drift_file}:3: {DRIFT}")]
        items = _builtin_recalled_items(events, fmap)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].item.content, DRIFT)

    def test_no_session_access_is_honest_zero(self) -> None:
        fmap = _file_map(_vista_task())
        events = [
            _read_event("/run/CLAUDE.md", "x1"),
            _result_event("x1", "# Project memory\nsearch the sessions/ dir"),
        ]
        self.assertEqual(_builtin_recalled_items(events, fmap), [])

    def test_distinct_and_order_preserving(self) -> None:
        task = _vista_task()
        fmap = _file_map(task)
        files = list(fmap)
        events = [
            _read_event(f"/s/{files[1]}", "a"), _result_event("a", DRIFT),
            _read_event(f"/s/{files[0]}", "b"), _result_event("b", FACT),
            _read_event(f"/s/{files[1]}", "c"), _result_event("c", DRIFT),  # dup
        ]
        items = _builtin_recalled_items(events, fmap)
        self.assertEqual([i.item_id for i in items],
                         [task.sessions[1].session_id, task.sessions[0].session_id])

    def test_empty_events(self) -> None:
        self.assertEqual(_builtin_recalled_items([], _file_map(_vista_task())), [])


class _FakeCtx:
    def __init__(self) -> None:
        self.calls: list = []

    def record_retrieve(self, hits, *, query: str = "") -> None:
        self.calls.append((hits, query))


class AttributeBuiltinRecallTest(unittest.TestCase):
    def _agent(self) -> ClaudeCodeAgent:
        return ClaudeCodeAgent(memory_mode="builtin", runner=_fake_runner)

    def test_records_step_from_injected_transcript(self) -> None:
        task = _vista_task()
        agent = self._agent()
        with tempfile.TemporaryDirectory() as d:
            run_dir = Path(d)
            _write_session_files(run_dir, task)  # lay real files; build the basenames
            fmap = _file_map(task)
            files = list(fmap)
            agent._builtin_transcript_override = [  # type: ignore[attr-defined]
                _read_event(f"/x/{files[0]}", "t1"),
                _result_event("t1", FACT),
            ]
            ctx = _FakeCtx()
            agent._attribute_builtin_recall(run_dir, task, ctx, _DummyResult(), 0.0)
        self.assertEqual(len(ctx.calls), 1)
        hits, query = ctx.calls[0]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].item.content, FACT)
        self.assertEqual(query, task.question)

    def test_honest_zero_records_nothing(self) -> None:
        task = _vista_task()
        agent = self._agent()
        with tempfile.TemporaryDirectory() as d:
            run_dir = Path(d)
            _write_session_files(run_dir, task)
            agent._builtin_transcript_override = [  # type: ignore[attr-defined]
                _read_event("/x/CLAUDE.md", "z1"),
                _result_event("z1", "nothing relevant"),
            ]
            ctx = _FakeCtx()
            agent._attribute_builtin_recall(run_dir, task, ctx, _DummyResult(), 0.0)
        self.assertEqual(ctx.calls, [], "no surfaced session -> no fabricated recall")

    def test_no_override_offline_is_noop(self) -> None:
        # No override + fake runner -> no real transcript -> no recall, no crash.
        task = _vista_task()
        agent = self._agent()
        with tempfile.TemporaryDirectory() as d:
            run_dir = Path(d)
            _write_session_files(run_dir, task)
            ctx = _FakeCtx()
            agent._attribute_builtin_recall(run_dir, task, ctx, _DummyResult(), 0.0)
        self.assertEqual(ctx.calls, [])


class _DummyResult:
    raw: dict = {}


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
