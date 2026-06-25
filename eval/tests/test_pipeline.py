"""Offline end-to-end test for the single-stage memory pipeline.

Drives ``run_pipeline`` with a fake ``claude`` runner (no network, no real plugin, no
``daydream-cli``) on the vendored fixture, asserting the pipeline:

* runs exactly ONE eval stage over ONE sequence and writes a self-describing results
  file with the ``pipeline`` metadata block (benchmark, sequence, stage, model, dreamer,
  version);
* points a plugin-real stage at the shared ``_memory/`` substrate, which persists across
  invocations (no harness copy);
* runs a dream consolidation pass first when the stage is ``plugin-dreamed``;
* writes a SUMMARY (.md + .json) with the stage's metrics (no cross-stage deltas).

Stdlib + pytest only.
"""

from __future__ import annotations

import json
import types
import tempfile
from pathlib import Path

import pytest

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


def _cfg(tmp: str, *, stage: str = "plugin-accum", benchmark: str = "swe_bench_cl",
         sequence: str = "pytest-dev_pytest_sequence", path: str = _FIXTURE) -> dict:
    return {
        "benchmark": benchmark,
        "sequence": sequence,
        "stage": stage,
        "limit": 3,
        "model": "claude-haiku-4-5",
        "grader": "overlap",          # offline heuristic; no real test execution
        "grader_timeout": 60,
        "budget_usd": 0.0,
        "code_mode": "agentic",
        "plugin_workers": 1,
        "timeout": 60,
        "path": path,
        "results_dir": tmp,
        "native_cl": False,           # the native A/B is exercised separately; keep this fast
    }


def test_pipeline_end_to_end_offline(monkeypatch) -> None:
    substrate_seen: list = []
    _install_fakes(monkeypatch, substrate_seen)

    with tempfile.TemporaryDirectory() as tmp:
        cfg = _cfg(tmp)  # single stage: plugin-accum (the default)
        summary = P.run_pipeline(cfg)

        # The single plugin-real stage saw the shared substrate.
        assert substrate_seen, "no plugin-real stage ran"
        assert len(set(substrate_seen)) == 1, f"used different substrates: {set(substrate_seen)}"
        substrate = Path(substrate_seen[0])
        assert substrate.name == "_memory"
        assert (substrate / ".cookbook-memory").is_dir()
        # Memory landed in the shared store (the dir persists across invocations).
        markers = list((substrate / ".cookbook-memory").glob("mem_*.md"))
        assert markers, "no memory written to the shared substrate"

        # Results file written, self-describing, with the pipeline block.
        results = list((Path(tmp)).rglob("swe_bench_cl-*.json"))
        assert len(results) == 1, f"expected one results file, got {results}"
        doc = json.loads(results[0].read_text())
        assert doc["benchmark"] == "swe_bench_cl"
        pm = doc["pipeline"]
        assert pm["benchmark"] == "swe_bench_cl"
        assert pm["sequence"] == "pytest-dev_pytest_sequence"
        assert pm["stage"] == "plugin-accum"
        assert pm["model"] == "claude-haiku-4-5"
        assert pm["n_stages"] == 1            # single stage, no dream pass
        assert pm["stages"] == ["plugin-accum"]
        assert pm["dream"]["model"]           # dreamer model recorded for provenance

        # Exactly ONE eval-stage row, stamped with its stage identity.
        stages = [r["pipeline_stage"] for r in doc["runs"]]
        assert stages == ["plugin-accum"]
        row = doc["runs"][0]
        assert "memory_health" in row
        assert row["memory_health"]["delta"]["recall_events"] >= 1
        assert row["reliability"]["recall_attempted"] >= 1

        # SUMMARY written (md + json); a single-stage run has no cross-stage deltas.
        md = list(Path(tmp).rglob("SUMMARY-swe_bench_cl-*.md"))
        js = list(Path(tmp).rglob("SUMMARY-swe_bench_cl-*.json"))
        assert len(md) == 1 and len(js) == 1
        sj = json.loads(js[0].read_text())
        assert sj["deltas"] == {}             # one stage -> no transitions
        assert "Memory health" in md[0].read_text()
        assert "## Deltas" not in md[0].read_text()
        assert sj["stages"][0]["memory_health"]["recall_events"] >= 1
        assert summary["benchmark"] == "swe_bench_cl"


def test_pipeline_dreamed_stage_runs_dream_pass(monkeypatch) -> None:
    """The plugin-dreamed stage runs ONE dream consolidation pass before evaluating, and
    records the dream block + a 2-stage (dream + eval) self-description."""
    substrate_seen: list = []
    _install_fakes(monkeypatch, substrate_seen)

    with tempfile.TemporaryDirectory() as tmp:
        summary = P.run_pipeline(_cfg(tmp, stage="plugin-dreamed"))

        results = list(Path(tmp).rglob("swe_bench_cl-*.json"))
        doc = json.loads(results[0].read_text())
        pm = doc["pipeline"]
        assert pm["stage"] == "plugin-dreamed"
        assert pm["stages"] == ["dream", "plugin-dreamed"]
        assert pm["n_stages"] == 2
        # The dream block reflects a real (offline no-op) consolidation pass, not "not-run".
        assert doc["dream"].get("status") != "not-run"
        assert [r["pipeline_stage"] for r in doc["runs"]] == ["plugin-dreamed"]
        assert summary["dream"].get("status") != "not-run"


def test_pipeline_base_stage_has_no_plugin(monkeypatch) -> None:
    """The base stage is memoryless: no plugin-real substrate is wired."""
    substrate_seen: list = []
    _install_fakes(monkeypatch, substrate_seen)

    with tempfile.TemporaryDirectory() as tmp:
        P.run_pipeline(_cfg(tmp, stage="base"))

        assert substrate_seen == [], "base stage should not wire a plugin substrate"
        doc = json.loads(list(Path(tmp).rglob("swe_bench_cl-*.json"))[0].read_text())
        assert [r["pipeline_stage"] for r in doc["runs"]] == ["base"]
        assert doc["dream"]["status"] == "not-run"  # base runs no dream pass


def test_pipeline_plugin_real_default_and_primed_stage_options() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        substrate = Path(tmp) / "_memory"
        cfg = _cfg(tmp)

        default = P._make_agent("plugin-blank", cfg, substrate)
        assert default.memory_mode == "plugin-real"
        assert default.plugin_real_recall_policy == "natural"
        assert default.plugin_real_invocation == "unprimed"

        primed = P._make_agent("plugin-primed", cfg, substrate)
        assert primed.plugin_real_recall_policy == "natural"
        assert primed.plugin_real_invocation == "primed"


def test_pipeline_natural_plugin_stage_allows_zero_recall_warning() -> None:
    warnings = P._stage_warnings(
        "plugin-blank", _cfg("/tmp"), types.SimpleNamespace(metadata={"graded_n": 1}),
        before={}, after={}, delta={"recall_events": 0})
    assert [w["code"] for w in warnings] == []


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


def test_interactive_config_defaults_to_single_accum_stage(monkeypatch) -> None:
    # Accept every default (8 prompts: benchmark, sequence, stage, tasks, model,
    # grader, budget, version slug).
    answers = iter([""] * 8)
    monkeypatch.setattr(P, "_interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    args = P._build_parser().parse_args([])

    cfg = P._resolve_config(args)

    assert cfg["benchmark"] == "swe_bench_cl"
    assert cfg["stage"] == "plugin-accum"
    assert cfg["sequence"] == "pytest-dev_pytest_sequence"


def test_interactive_config_can_select_base_stage(monkeypatch) -> None:
    # benchmark, sequence default; mode = "base"; rest default.
    answers = iter(["", "", "base", "", "", "", "", ""])
    monkeypatch.setattr(P, "_interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    args = P._build_parser().parse_args([])

    cfg = P._resolve_config(args)

    assert cfg["stage"] == "base"


def test_interactive_mode_menu_accepts_a_number(monkeypatch) -> None:
    # The mode prompt is a numbered menu; "2" selects the 2nd mode (plugin-blank).
    prompts: list[str] = []
    answers = iter(["", "", "2", "", "", "", "", ""])
    monkeypatch.setattr(P, "_interactive", lambda: True)

    def fake_input(prompt: str) -> str:
        prompts.append(prompt)
        return next(answers)

    monkeypatch.setattr("builtins.input", fake_input)
    args = P._build_parser().parse_args([])

    cfg = P._resolve_config(args)

    assert cfg["stage"] == P._EVAL_STAGES[1] == "plugin-blank"
    assert any("mode [" in p for p in prompts)  # the mode menu was shown


def test_interactive_config_can_select_vista(monkeypatch) -> None:
    # benchmark = "vista" resets the sequence default to the vista default journey; the
    # sequence prompt then accepts that default.
    answers = iter(["vista", "", "", "", "", "", "", ""])
    monkeypatch.setattr(P, "_interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    args = P._build_parser().parse_args([])

    cfg = P._resolve_config(args)

    assert cfg["benchmark"] == "vista"
    assert cfg["sequence"] == "coding-pr-review-001"


def test_vista_sequences_are_the_six_journeys() -> None:
    # VISTA exposes its six journeys (by task_id), not the three domains.
    seqs = list(P._sequences("vista"))
    assert len(seqs) == 6
    assert "coding-pr-review-001" in seqs
    assert "research-synthesis-001" in seqs
    # the old domain ids are no longer selectable sequences
    assert "coding" not in seqs and "project" not in seqs


def test_in_sequence_matches_group_id_or_task_id() -> None:
    import types
    swe = types.SimpleNamespace(group_id="django_django_sequence", task_id="t1")
    vista = types.SimpleNamespace(group_id="coding", task_id="coding-pr-review-001")
    # SWE-Bench-CL: matched by group_id (the sequence).
    assert P._in_sequence(swe, "django_django_sequence")
    assert not P._in_sequence(swe, "t1-other")
    # VISTA: matched by its journey task_id (the selectable unit). The registry only
    # offers journey ids as VISTA sequences, so the journey is what's ever passed here.
    assert P._in_sequence(vista, "coding-pr-review-001")
    # A non-matching id (neither this task's group nor its id) is excluded.
    assert not P._in_sequence(vista, "research-synthesis-001")


def test_pipeline_grader_defaults_to_swebench() -> None:
    args = P._build_parser().parse_args([])
    cfg = P._resolve_config(args)
    assert cfg["grader"] == "swebench"


def test_interactive_config_respects_selected_grader(monkeypatch) -> None:
    # grader is the 6th prompt (benchmark, sequence, stage, tasks, model, grader, budget,
    # version slug).
    answers = iter(["", "", "", "", "", "local", "", ""])
    monkeypatch.setattr(P, "_interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    args = P._build_parser().parse_args([])

    cfg = P._resolve_config(args)

    assert cfg["grader"] == "local"


def test_interactive_config_accepts_auto_grader(monkeypatch) -> None:
    answers = iter(["", "", "", "", "", "auto", "", ""])
    monkeypatch.setattr(P, "_interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    args = P._build_parser().parse_args(["--grader", "none"])

    cfg = P._resolve_config(args)

    assert cfg["grader"] == "auto"


def test_pipeline_stage_flag_selects_single_stage() -> None:
    args = P._build_parser().parse_args(["--stage", "plugin-blank"])
    cfg = P._resolve_config(args)
    assert cfg["stage"] == "plugin-blank"


def test_version_slug_default_is_sequence_type_sha_int() -> None:
    # --yes (non-interactive) falls through to the computed default slug.
    args = P._build_parser().parse_args(
        ["--yes", "--sequence", "django_django_sequence", "--stage", "plugin-dreamed"])
    cfg = P._resolve_config(args)
    v = cfg["results_version"]
    assert v.startswith("django_django_sequence-plugin-dreamed-")
    assert v.split("-")[-1].isdigit()  # trailing dedup integer


def test_version_slug_dedup_int_bumps_on_existing_dir(tmp_path, monkeypatch) -> None:
    from memeval.results import normalize_version
    monkeypatch.setattr(P, "_git_short_sha", lambda *a, **k: "abc1234")
    cfg = {"sequence": "coding", "stage": "plugin-accum"}
    s1 = P._default_version_slug(cfg, tmp_path)
    assert s1 == "coding-plugin-accum-abc1234-1"
    (tmp_path / normalize_version(s1)).mkdir(parents=True)
    s2 = P._default_version_slug(cfg, tmp_path)
    assert s2 == "coding-plugin-accum-abc1234-2"


def test_interactive_version_slug_is_prompted_and_accepted(monkeypatch) -> None:
    # 8th prompt is the version slug; a typed value is accepted verbatim (reuse path).
    answers = iter(["", "", "", "", "", "", "", "reuse-this-bucket"])
    monkeypatch.setattr(P, "_interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    args = P._build_parser().parse_args([])

    cfg = P._resolve_config(args)

    assert cfg["results_version"] == "reuse-this-bucket"


def test_explicit_results_version_skips_slug_default() -> None:
    args = P._build_parser().parse_args(["--yes", "--results-version", "pinned-bucket"])
    cfg = P._resolve_config(args)
    assert cfg["results_version"] == "pinned-bucket"


def test_pipeline_benchmark_flag_selects_vista() -> None:
    args = P._build_parser().parse_args(["--benchmark", "vista"])
    cfg = P._resolve_config(args)
    assert cfg["benchmark"] == "vista"
    assert cfg["sequence"] == "coding-pr-review-001"  # vista default journey


def test_pipeline_rejects_cross_benchmark_sequence() -> None:
    args = P._build_parser().parse_args(
        ["--benchmark", "vista", "--sequence", "django_django_sequence"])
    with pytest.raises(SystemExit):
        P._resolve_config(args)


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
                     "n_stages": 1, "dream": {"provider": "p", "model": "d"},
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
                     "n_stages": 1, "dream": {"provider": "p", "model": "d"},
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
    P._ensure_sandbox_ready("claude-haiku-4-5")  # must NOT raise, must NOT probe
    assert probed["n"] == 0
