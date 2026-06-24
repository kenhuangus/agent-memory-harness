---
id: ADR-eval-006
domain: eval
title: Make the docker-free grader run historical SWE-bench commits by pinning Python, faking the scm version, and disabling pytest plugin autoload
status: Accepted
date: 2026-06-23
contract: false
supersedes: none
superseded_by: none
owner: Ken (P2)
origin: results/vbranch-eval-persist-task-success-303b714 — 4/5 pytest tasks ungraded
---

# ADR-eval-006: Make the docker-free grader run historical SWE-bench commits by pinning Python, faking the scm version, and disabling pytest plugin autoload

**Status:** Accepted · **Date:** 2026-06-23 · **Contract:** false

## Context

[ADR-eval-002](ADR-eval-002-docker-free-code-grading.md) chose a **docker-free**
`LocalExecGrader`: a host-local `uv` venv per task instead of SWE-bench's per-instance
container images. The accepted cost was "host-dependent, partial-coverage." The
grade-reason visibility from [ADR-eval-005](ADR-eval-005-grade-reason-visibility.md)
then made that cost concrete: on the `pytest-dev_pytest_sequence`, **only 1 of 5
tasks graded**; the other 4 split between `gold_test_apply_failed` and
`env_build_failed`. Running the failing steps by hand surfaced **three distinct
historical-compatibility breaks**, none related to the agent or the task data
(checkout, both patch applies, venv create, and editable install all succeeded):

1. **Interpreter too new.** The venv defaulted to the host's CPython 3.12/3.13. The
   2019-era pytest commits do `import imp` in `_pytest/assertion/rewrite.py`; `imp`
   was removed in Python 3.12, so pytest crashes at import (rc=1) before collecting.
2. **setuptools-scm version gate.** The shallow `--depth 1` checkout has no git tags,
   so setuptools-scm computes `0.1.dev1+g<sha>`. The project's own `minversion`
   (`tox.ini` / `pyproject.toml` "requires pytest-2.0") rejects that as too old
   (pytest rc=4).
3. **Modern plugin crashes old pytest.** Current `setuptools` vendors a `typeguard`
   pytest plugin that auto-registers and calls `parser.addini(type="string")`, but
   2019 pytest's `argparsing.py` asserts `type in (None, pathlist, args, linelist,
   bool)` — pytest exits rc=1 at plugin load.

All three are "modern toolchain vs. a pinned-2019 repo." Each independently makes the
task ungradeable, so all three must be fixed for the grader to produce a verdict.

Fixing those three exposed two further **parser/data** breaks in the pytest path
(distinct from the env, but on the same critical path to a correct verdict):

4. **Leaked progress token.** A ``PASS_TO_PASS`` entry was literally ``"[100%]"`` (a
   captured pytest progress bar). On the command line pytest treats it as a missing
   file and aborts the whole run (rc=4, "no tests ran") — every real selector then
   scores as a never-ran failure.
5. **Substring status mis-parse.** ``_parse_pytest`` ran ``pytest -q`` (dots only, no
   per-test status lines) and matched ``PASSED``/``FAILED`` by *substring*. With
   ``-rA`` summary lines, a node id named ``...::test_failed`` contains the substring
   ``FAILED`` and was mis-scored as a failure even when it passed.
6. **Truncated parametrized selectors.** A parametrize id containing ``", "`` was
   split by the upstream capture, which stored only the prefix — leaving an
   unbalanced bracket (``test_skipif_reporting["hasattr(sys,``). Handed to pytest it
   is ``ERROR: not found`` and aborts the run (rc=4). The full id is unrecoverable.

All six lie on the path to a correct verdict; the first three are env, the last three
are data/parser. None is the agent or the task's intent.

## Options considered

- **Adopt SWE-bench's Docker images.** The robust, leaderboard-faithful fix — each
  instance ships an exact environment. Rejected here for the same reasons as
  ADR-eval-002 (no Docker dependency in this eval harness, fast offline iteration);
  this ADR does not reopen that decision, it makes the chosen path actually work.
- **Skip/whitelist only the tasks that grade on the host.** Honest but defeats the
  point — silently shrinks coverage to whatever happens to match the host toolchain.
- **Fix the three breaks at the venv/run boundary** — pin the interpreter per repo,
  inject a setuptools-scm pretend-version at install, and disable pytest plugin
  autoload at test-run. Chosen: small, targeted, and each maps 1:1 to an observed
  failure with a verified before/after.

## Decision

In `LocalExecGrader._build_and_run`:

1. **Pin Python.** `_make_venv` accepts a version and passes `uv venv --python X.Y`
   (uv fetches it). `_python_for_task` resolves it from a conservative repo map
   (`_REPO_PYTHON_PIN`, `pytest-dev/pytest → 3.8`), overridable by
   `task.metadata["python"]`; an unmapped repo returns `None` (host default — prior
   behavior, no regression).
2. **Fake the scm version** (`_scm_env`): set `SETUPTOOLS_SCM_PRETEND_VERSION`
   (+ the pytest-specific var) to a high value for the **install** step.
3. **Disable plugin autoload** (`_test_run_env`, a superset of `_scm_env`): set
   `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` for the **test-run** step so stray vendored
   plugins don't load; the repo's own pytest still runs.
4. **Filter junk + unrunnable selectors** (`is_pytest_selector`): drop non-node-id
   tokens (``[100%]``) AND truncated parametrized ids (unbalanced ``[``) from BOTH
   the command and the parsed lists, mirroring the django prose-filter, so one bad
   token can't abort the run. Honest while ``FAIL_TO_PASS`` survives (the resolution
   check); a task whose entire F2P is junk has nothing to resolve and degrades to
   ungraded upstream.
5. **Read real status**: run ``pytest -rA`` (explicit ``<STATUS> <nodeid>`` summary)
   and rewrite ``_parse_pytest`` to key off the leading STATUS token of a summary
   line whose nodeid matches the selector — never a substring scan.

Verified end-to-end on the `pytest-dev_pytest_sequence` first-5: all moved from
**1/5 graded → 5/5 graded**, and **5/5 gold patches now correctly resolve `True`**
(before the whole fix, the env lied and reported 0/5 resolvable). For `pytest-7432`,
the 5 unrecoverable selectors are all `PASS_TO_PASS` (72/77 still run) with the F2P
intact, so the grade stays meaningful.

## Rationale

Each knob is the minimal neutralization of one observed, reproduced break, with no
effect on what the test actually asserts: `--python 3.8` gives the era-correct
interpreter; the pretend-version only satisfies a self-imposed gate that exists
solely because of the shallow checkout; disabling autoload loads exactly the plugins
SWE-bench grading wants (the repo's own) and none of the host's accidental ones. The
result is more real coverage from the same docker-free design, not a new dependency.

## Tradeoffs & risks

- **The Python map is a stub.** Only `pytest-dev/pytest` is mapped today; other repos
  fall back to host Python and may still hit break #1. Mitigated by the
  `metadata["python"]` override and the conservative default (never a wrong guess).
  The real cure is a per-instance version from the dataset (SWE-bench's
  `environment_setup_commit`), which this dataset doesn't carry — noted for later.
- **Disabling plugin autoload could hide a plugin a test legitimately needs.** Rare
  for SWE-bench unit selectors, and the alternative (a host plugin crashing pytest)
  is strictly worse. If a task needs a specific plugin, name it via `-p` rather than
  re-enabling autoload.
- **Pretending a high scm version is a lie to the build.** It only affects the
  version gate; it does not change code under test. Acceptable for grading.
- **Still host-dependent** (ADR-eval-002 stands): `uv` must be able to fetch 3.8.
  When it can't, the task degrades to `env_build_failed` — now visible (ADR-eval-005)
  rather than silent.
- **Dropping truncated selectors loses coverage on those cases.** A param id that
  contained ``", "`` is unrecoverable from the dataset, so we drop it rather than
  guess. This silently shrinks ``PASS_TO_PASS`` for the affected task (5/77 for
  pytest-7432); acceptable because ``FAIL_TO_PASS`` is intact and the alternative is
  a whole-run abort that grades nothing. The real cure is upstream data with intact
  parametrize ids.

## Consequences for the build

- **Policy consequence** — a repo whose pinned commits predate the host interpreter
  must get a `_REPO_PYTHON_PIN` entry (or dataset `metadata["python"]`); don't assume
  the host Python. New historical-compat breaks should be fixed at this same
  venv/run boundary (a targeted env var or flag), not by widening what counts as a
  pass. No public signature changes; `_make_venv`/`_install_repo`/`_run` gained
  internal `python`/`env` params only.
