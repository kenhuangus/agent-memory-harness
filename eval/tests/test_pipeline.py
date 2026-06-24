"""Offline end-to-end test for the 5-stage SWE-Bench-CL pipeline.

Drives ``run_pipeline`` with a fake ``claude`` runner (no network, no real plugin, no
``daydream-cli``) on the vendored fixture, asserting the pipeline:

* runs all 4 eval stages + the dream stage and writes a self-describing results file
  with the ``pipeline`` metadata block (sequence, model, dreamer, version, n_stages);
* points every plugin-real stage at the SAME shared ``_memory/`` substrate, which
  accumulates across stages because the directory persists (no harness copy);
* writes a SUMMARY (.md + .json) with per-stage metrics and base->final deltas.

Stdlib + pytest only.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import memeval.claudecode.pipeline as P
from memeval.claudecode.cli import ClaudeResult
from memeval.claudecode.platform import ClaudeRuntime

_NATIVE = ClaudeRuntime(kind="native", exe="claude", python="python")
_FIXTURE = str(Path(__file__).resolve().parents[1] / "memeval" / "data"
               / "swe_bench_cl" / "SWE-Bench-CL.json")


def _fake_runner(prompt, *, cwd, extra_env=None, **kw) -> ClaudeResult:
    """Stand in for the installed plugin: write a recall event into the resolved store
    ($MEMORY_STORE, falling back to ${CLAUDE_PROJECT_DIR}/.cookbook-memory) so
    attribution produces a retrieve step, plus a per-prompt marker memory file proving
    cross-stage persistence."""
    env = extra_env or {}
    store = Path(
        env.get("MEMORY_STORE")
        or (Path(env.get("CLAUDE_PROJECT_DIR", cwd)) / ".cookbook-memory")
    )
    store.mkdir(parents=True, exist_ok=True)
    (store / f"mem_{abs(hash(prompt)) % 10000}.md").write_text("learned\n", encoding="utf-8")
    ev = {"ts": 1.0, "op": "recall", "ids": ["m1"], "query": prompt,
          "meta": {"hits": [{"id": "m1", "content": "x", "score": 0.9, "rank": 0,
                             "tokens": 1, "timestamp": 1.0}]}}
    with open(store / "events.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(ev) + "\n")
    return ClaudeResult(text="done", tokens_in=5, tokens_out=1, raw={})


def _install_fakes(monkeypatch, substrate_seen: list):
    """Patch the agent factory so every stage uses the fake runner, and stub the CODE
    checkout/diff so no real git runs. Records each plugin-real substrate seen."""
    import memeval.claudecode.agent as agmod
    from memeval.claudecode.agent import ClaudeCodeAgent

    orig_init = ClaudeCodeAgent.__init__

    def patched_init(self, **kw):
        kw.setdefault("runner", _fake_runner)
        kw.setdefault("runtime", _NATIVE)
        orig_init(self, **kw)
        if self._project_dir is not None:
            substrate_seen.append(str(self._project_dir))

    monkeypatch.setattr(ClaudeCodeAgent, "__init__", patched_init)
    monkeypatch.setattr(agmod, "prepare_checkout", lambda *a, **k: None)
    monkeypatch.setattr(agmod, "capture_diff", lambda *a, **k: "")
    # The pipeline probes the sandbox login with a real `claude -p` turn before stage 1;
    # offline tests use a fake runner and must NOT make that network/subprocess call.
    monkeypatch.setenv("MEMEVAL_PIPELINE_SKIP_AUTH_PROBE", "1")


def _cfg(tmp: str) -> dict:
    return {
        "sequence": "pytest-dev_pytest_sequence",
        "limit": 3,
        "model": "claude-haiku-4-5",
        "grader": "overlap",          # offline heuristic; no real test execution
        "grader_timeout": 60,
        "budget_usd": 0.0,
        "code_mode": "agentic",
        "plugin_workers": 1,
        "timeout": 60,
        "path": _FIXTURE,
        "results_dir": tmp,
        "native_cl": False,           # the native A/B is exercised separately; keep this fast
    }


def test_pipeline_end_to_end_offline(monkeypatch) -> None:
    substrate_seen: list = []
    _install_fakes(monkeypatch, substrate_seen)

    with tempfile.TemporaryDirectory() as tmp:
        cfg = _cfg(tmp)
        summary = P.run_pipeline(cfg)

        # All plugin-real stages (blank/accum/dreamed = 3) saw the SAME substrate.
        assert substrate_seen, "no plugin-real stage ran"
        assert len(set(substrate_seen)) == 1, f"stages used different substrates: {set(substrate_seen)}"
        substrate = Path(substrate_seen[0])
        assert substrate.name == "_memory"
        assert (substrate / ".cookbook-memory").is_dir()
        # The shared store accumulated memory across stages (the dir persisted).
        markers = list((substrate / ".cookbook-memory").glob("mem_*.md"))
        assert markers, "no memory accumulated in the shared substrate"

        # Results file written, self-describing, with the pipeline + dream blocks.
        results = list((Path(tmp)).rglob("swe_bench_cl-*.json"))
        assert len(results) == 1, f"expected one results file, got {results}"
        doc = json.loads(results[0].read_text())
        assert doc["benchmark"] == "swe_bench_cl"
        pm = doc["pipeline"]
        assert pm["sequence"] == "pytest-dev_pytest_sequence"
        assert pm["model"] == "claude-haiku-4-5"
        assert pm["n_stages"] == 5
        assert pm["dream"]["model"]  # dreamer model recorded
        assert "dream" in doc
        # Four eval-stage rows, in order, each stamped with its stage identity.
        stages = [r["pipeline_stage"] for r in doc["runs"]]
        assert stages == ["base", "plugin-blank", "plugin-accum", "plugin-dreamed"]
        plugin_rows = [r for r in doc["runs"] if r["pipeline_stage"].startswith("plugin-")]
        assert all("memory_health" in r for r in plugin_rows)
        assert all((r["memory_health"]["delta"]["recall_events"] >= 1) for r in plugin_rows)
        assert all((r["reliability"]["recall_attempted"] >= 1) for r in plugin_rows)

        # SUMMARY written (md + json) with base->final deltas.
        md = list(Path(tmp).rglob("SUMMARY-swe_bench_cl-*.md"))
        js = list(Path(tmp).rglob("SUMMARY-swe_bench_cl-*.json"))
        assert len(md) == 1 and len(js) == 1
        sj = json.loads(js[0].read_text())
        assert "base_to_final" in sj["deltas"]
        assert "Memory health" in md[0].read_text()
        assert sj["stages"][1]["memory_health"]["recall_events"] >= 1
        assert summary["benchmark"] == "swe_bench_cl"


def test_pipeline_results_path_is_version_scoped(monkeypatch) -> None:
    # The results + substrate live under results/v{version}/ (ADR-eval-004).
    substrate_seen: list = []
    _install_fakes(monkeypatch, substrate_seen)
    with tempfile.TemporaryDirectory() as tmp:
        P.run_pipeline(_cfg(tmp))
        version_dirs = [p for p in Path(tmp).iterdir() if p.is_dir() and p.name.startswith("v")]
        assert version_dirs, f"no v{{version}} dir under {tmp}"
        vd = version_dirs[0]
        assert (vd / "_memory").is_dir()
        assert list(vd.glob("swe_bench_cl-*.json"))


def test_interactive_config_can_skip_base(monkeypatch) -> None:
    answers = iter(["", "", "", "", "", "y"])
    monkeypatch.setattr(P, "_interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    args = P._build_parser().parse_args([])

    cfg = P._resolve_config(args)

    assert cfg["stages"] == ["plugin-blank", "plugin-accum", "plugin-dreamed"]


def test_pipeline_grader_defaults_to_auto() -> None:
    args = P._build_parser().parse_args([])
    cfg = P._resolve_config(args)
    assert cfg["grader"] == "auto"


def test_interactive_config_respects_selected_grader(monkeypatch) -> None:
    answers = iter(["", "", "", "local", "", "n"])
    monkeypatch.setattr(P, "_interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    args = P._build_parser().parse_args([])

    cfg = P._resolve_config(args)

    assert cfg["grader"] == "local"


def test_interactive_config_accepts_auto_grader(monkeypatch) -> None:
    answers = iter(["", "", "", "auto", "", "n"])
    monkeypatch.setattr(P, "_interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    args = P._build_parser().parse_args(["--grader", "none"])

    cfg = P._resolve_config(args)

    assert cfg["grader"] == "auto"


def test_interactive_config_stages_override_skip_prompt(monkeypatch) -> None:
    prompts: list[str] = []
    answers = iter(["", "", "", "", ""])
    monkeypatch.setattr(P, "_interactive", lambda: True)

    def fake_input(prompt: str) -> str:
        prompts.append(prompt)
        return next(answers)

    monkeypatch.setattr("builtins.input", fake_input)
    args = P._build_parser().parse_args(["--stages", "plugin-blank"])

    cfg = P._resolve_config(args)

    assert cfg["stages"] == ["plugin-blank"]
    assert not any("skip stage 1" in p for p in prompts)


def test_pipeline_native_cl_defaults_off() -> None:
    args = P._build_parser().parse_args([])
    cfg = P._resolve_config(args)
    assert cfg["native_cl"] is False


def test_pipeline_native_cl_is_opt_in() -> None:
    args = P._build_parser().parse_args(["--native-cl"])
    cfg = P._resolve_config(args)
    assert cfg["native_cl"] is True


def test_pipeline_results_version_override_is_configured() -> None:
    args = P._build_parser().parse_args(["--results-version", "reuse-memory"])
    cfg = P._resolve_config(args)
    assert cfg["results_version"] == "reuse-memory"


def test_summary_renders_ungraded_accuracy_as_dash() -> None:
    from memeval.claudecode import pipeline_summary as PS

    md = PS.render_summary_md({
        "benchmark": "swe_bench_cl",
        "pipeline": {"version": "vtest", "sequence": "s", "model": "m", "n_tasks": 1,
                     "n_stages": 5, "dream": {"provider": "p", "model": "d"},
                     "grader": "none", "git_sha": "abc"},
        "stages": [{
            "stage": "plugin-blank",
            "metrics": {"accuracy": 0.0, "relevancy": 0.0, "recency": 0.0, "efficiency": 0.0},
            "n_tasks": 1,
            "cost_usd": 0.0,
            "graded_n": 0,
            "recall_attempted": 1,
            "memory_health": {"recall_events": 1, "recall_with_hits": 0,
                              "daydream_memory_written": 0, "durable_items_after": 0},
            "warnings": [{"code": "accuracy_ungraded", "message": "no graded tasks"}],
        }],
        "deltas": {},
        "dream": {"status": "not-run"},
    })

    # ``resolved`` cell is ``—`` here: this stage dict predates the grading-visibility
    # fields, so it falls back gracefully (no resolved/n -> dash).
    assert "| plugin-blank | — | 0.0000 | 0.0000 | 0.0000 | — | 1 | $0.0000 |" in md
    assert "accuracy_ungraded" in md


def test_summary_surfaces_resolved_and_grade_reasons() -> None:
    """A floored accuracy reads honestly: resolved/total + the ungraded reason."""
    from memeval.claudecode import pipeline_summary as PS

    md = PS.render_summary_md({
        "benchmark": "swe_bench_cl",
        "pipeline": {"version": "vtest", "sequence": "s", "model": "m", "n_tasks": 3,
                     "n_stages": 5, "dream": {"provider": "p", "model": "d"},
                     "grader": "auto", "git_sha": "abc"},
        "stages": [{
            "stage": "base",
            "metrics": {"accuracy": 0.0, "relevancy": 0.0, "recency": 0.0, "efficiency": 0.0},
            "n_tasks": 3,
            "cost_usd": 0.1,
            "graded_n": 1,
            "resolved": 0,
            "ungraded": 2,
            "grade_reasons": {"checkout_failed": 2, "graded": 1},
            "recall_attempted": 0,
            "memory_health": {},
            "warnings": [],
        }],
        "deltas": {},
        "dream": {"status": "not-run"},
    })

    # Main table carries a resolved column (0/3 here).
    assert "| base |" in md and " 0/3 " in md
    # Task grading section breaks down graded/ungraded + the reason histogram.
    assert "## Task grading" in md
    assert "checkout_failed×2" in md
    assert "graded×1" in md


def test_store_health_counts_daydream_diary_writes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        substrate = Path(tmp) / "_memory"
        store = substrate / ".cookbook-memory"
        dream = store / "dream"
        dream.mkdir(parents=True)
        (store / "events.jsonl").write_text(
            json.dumps({"event_type": "daydream.hook_subprocess_fired"}) + "\n",
            encoding="utf-8",
        )
        (dream / "session.daydream-events.jsonl").write_text(
            "\n".join([
                json.dumps({"event_type": "daydream.chunk_extracted"}),
                json.dumps({"event_type": "daydream.memory_written"}),
            ]) + "\n",
            encoding="utf-8",
        )

        health = P._store_health(substrate)

    assert health["events"] == 1
    assert health["daydream_events"] == 2
    assert health["daydream_completed"] == 2
    assert health["daydream_memory_written"] == 1


def test_pipeline_fails_closed_when_sandbox_not_logged_in(monkeypatch) -> None:
    # The pipeline MUST abort before any stage runs if the sandbox isn't authenticated —
    # every stage uses the isolated sandbox, never the host, so a logged-out sandbox can't
    # silently fall through. The auth probe returning False => SystemExit, no stage runs.
    import memeval.claudecode.sandbox as sb

    monkeypatch.delenv("MEMEVAL_SANDBOX", raising=False)
    monkeypatch.delenv("MEMEVAL_PIPELINE_SKIP_AUTH_PROBE", raising=False)
    # Sandbox exists (built) but the real auth probe says "not logged in".
    monkeypatch.setattr(sb, "exists", lambda *a, **k: True)
    monkeypatch.setattr(P, "_sandbox_auth_probe", lambda *a, **k: False)

    ran = {"stage": False}
    monkeypatch.setattr(P, "_run_one", lambda *a, **k: ran.__setitem__("stage", True))

    import pytest
    with pytest.raises(SystemExit) as exc:
        P.run_pipeline(_cfg("/tmp/should-not-be-written"))
    assert "not logged in" in str(exc.value).lower()
    assert ran["stage"] is False, "a stage ran despite the logged-out sandbox"


def test_pipeline_disabled_sandbox_is_explicit_optout(monkeypatch) -> None:
    # MEMEVAL_SANDBOX=0 is an intentional opt-out: no probe, no abort (runs on host).
    import memeval.claudecode.sandbox as sb

    monkeypatch.setenv("MEMEVAL_SANDBOX", "0")
    probed = {"n": 0}
    monkeypatch.setattr(P, "_sandbox_auth_probe", lambda *a, **k: probed.__setitem__("n", probed["n"] + 1) or True)
    P._ensure_sandbox_ready()  # must NOT raise, must NOT probe
    assert probed["n"] == 0
