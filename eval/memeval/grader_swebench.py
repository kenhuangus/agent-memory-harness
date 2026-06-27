"""Realistic, Docker-free CODE grader that reuses SWE-bench's OWN env specs +
official log parsers — owner: Ken. Opt-in; complements (never replaces) the
``local`` (:class:`memeval.grader.LocalExecGrader`) and ``overlap`` graders.

Why this exists
---------------
:class:`LocalExecGrader` hand-rolls its test command (``pytest -rA`` / django
``runtests.py``) and its own output parsers (:func:`memeval.grader._parse_pytest`
/ ``_parse_django``). That is portable but *approximate* — it is not what the
official SWE-bench harness does. :class:`SwebenchHostGrader` instead drives the
SAME building blocks SWE-bench's container harness uses, just executed in a host
``uv`` venv instead of Docker:

* per-instance environment spec — ``swebench.harness.constants.
  MAP_REPO_VERSION_TO_SPECS[repo][version]`` (python pin, install command,
  pip_packages, the canonical ``test_cmd``);
* the exact test selectors — ``swebench``'s ``get_test_directives(instance)``
  (derived from the gold ``test_patch``, NOT the F2P/P2P lists — this is how
  the real harness builds the eval command);
* the official per-repo log parser — ``MAP_REPO_TO_PARSER[repo]``;
* the official grading fold — ``get_eval_tests_report`` + ``get_resolution_status``
  (RESOLVED == ``ResolvedStatus.FULL``: every F2P passes AND every P2P holds).

Host-faithfulness ceiling (honest by design)
-------------------------------------------
A host venv is NOT a SWE-bench container. We pin the historical Python via ``uv``,
but root/apt ``pre_install`` steps (locale-gen, apt-get) cannot run unprivileged
and are skipped (logged). Some repos/commits therefore will not build on the host;
per ADR-eval-002's honesty rule, any inability to build/run returns ``None``
(UNGRADED) with a loud reason — never a fake ``False`` and never a crash. So the
accuracy this grader reports reflects only what genuinely ran; it is NOT
leaderboard-comparable to a containerized SWE-bench run.

``swebench`` is an OPTIONAL dependency (extra ``swebench``); it is imported lazily
inside the grader so importing this module — or :mod:`memeval.grader` — never
requires it.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from .grader import (
    CmdResult,
    CmdRunner,
    _subprocess_cmd,
    django_label,
    instance_id_of,
    is_django_selector,
    patch_target_files,
    revert_test_files,
)
from .schema import Task, TaskKind

log = logging.getLogger(__name__)

#: ``pre_install`` lines a non-root host venv cannot execute. SWE-bench specs
#: assume a root container; these manipulate system packages / locales / system
#: paths and are skipped (logged) on the host. Matching is by command prefix or
#: substring (case-insensitive).
_ROOT_PREINSTALL_PREFIXES = ("apt-get", "apt ", "apt-", "locale-gen", "dpkg", "add-apt")
_ROOT_PREINSTALL_SUBSTRINGS = ("sudo", "> /etc", ">/etc", "/etc/", "locale-gen")

#: Repos whose host ``uv`` venv needs SWE-bench's conda-base parity (pip via ``--seed``,
#: and possibly era-pinned base deps — see :meth:`SwebenchHostGrader._era_base_pins`).
#: Scoped deliberately: only these repos get the extra setup, so grading every OTHER
#: repo stays byte-identical to the pre-fix behavior (no cross-benchmark blast radius).
#: Extend this set (with the same eval-first validation) as other eras are characterized.
_CONDA_BASE_REPOS = frozenset({"sphinx-doc/sphinx"})


def _scm_env(repo: str) -> dict[str, str]:
    """Environment overlay for projects whose version comes from setuptools-scm.

    The host grader checks out a single commit by SHA. For pytest, that tagless
    shallow checkout makes setuptools-scm compute ``0.1.dev1+...``. Historical
    pytest then refuses to run its own tests because ``tox.ini`` requires
    ``pytest>=2.0`` before collection starts. Pretend a high package version for
    pytest only; this satisfies the self-version gate without changing the code
    under test or the SWE-bench selectors.
    """
    if (repo or "").strip().lower() != "pytest-dev/pytest":
        return {}
    pretend = "9999.0.0"
    return {
        "SETUPTOOLS_SCM_PRETEND_VERSION": pretend,
        "SETUPTOOLS_SCM_PRETEND_VERSION_FOR_PYTEST": pretend,
    }


def _is_root_preinstall(cmd: str) -> bool:
    """True iff ``cmd`` needs root/apt/locale/system paths (skip on host)."""
    s = (cmd or "").strip().lower()
    if not s:
        return True  # empty / no-op -> skip
    if any(s.startswith(p) for p in _ROOT_PREINSTALL_PREFIXES):
        return True
    return any(sub in s for sub in _ROOT_PREINSTALL_SUBSTRINGS)


def _split_env_prefix(tokens: list) -> "tuple[dict, list]":
    """Peel leading ``VAR=val`` env-assignment tokens off a split command.

    A spec ``test_cmd`` can carry an inline shell env prefix (sympy:
    ``PYTHONWARNINGS='...' bin/test ...``). After ``shlex.split`` those become
    leading argv tokens; they must go to the subprocess ENV, not be exec'd as a
    program. Returns ``(env_overlay, remaining_argv)``. A token counts as an
    assignment only if everything before the first ``=`` is a valid shell name
    (``[A-Za-z_][A-Za-z0-9_]*``), so a real argument like ``--opt=val`` or a path
    is never mistaken for one. Pure + stdlib-only (unit-testable)."""
    env: dict = {}
    i = 0
    for tok in tokens:
        name, sep, val = tok.partition("=")
        if not sep or not name or not (name[0].isalpha() or name[0] == "_") \
                or not all(c.isalnum() or c == "_" for c in name):
            break
        env[name] = val
        i += 1
    return env, list(tokens[i:])


class SwebenchHostGrader:
    """Grade CODE tasks via SWE-bench's own specs + parsers in a host ``uv`` venv.

    Callable ``(task, prediction) -> Optional[bool]`` — same contract as
    :class:`LocalExecGrader`: ``True`` resolved, ``False`` not resolved (or a real
    empty-patch miss), ``None`` UNGRADED (could not build/run honestly).

    Per call (CODE only):

    1. resolve ``spec = MAP_REPO_VERSION_TO_SPECS[repo][version]`` (missing -> None);
    2. throwaway checkout of ``repo`` @ ``base_commit``;
    3. apply the agent ``prediction`` patch, then the GOLD ``test_patch`` (the
       harness applies tests, NEVER the agent — the trust boundary);
    4. provision ``uv venv --python <spec.python>`` (can't pin -> None);
    5. run ``spec.install`` + ``spec.pip_packages`` (failures tolerated);
    6. run ``spec.test_cmd`` + ``get_test_directives(instance)``;
    7. parse with ``MAP_REPO_TO_PARSER[repo]``; fold via the official
       ``get_eval_tests_report`` + ``get_resolution_status``.

    Both the command runner and git runner are injectable so offline tests drive
    the whole flow over a stub repo with a canned official-format log.
    """

    def __init__(
        self,
        *,
        runner: Optional[CmdRunner] = None,
        git_runner: Any = None,
        model_name: str = "memeval",
        timeout: int = 1800,
        python_exe: Optional[str] = None,
        python_exes: Optional[dict[str, str]] = None,
        allow_python_substitution: bool = False,
    ) -> None:
        self._runner = runner or _subprocess_cmd
        self._git_runner = git_runner  # forwarded to checkout (None -> its default)
        self.model_name = model_name
        self.timeout = timeout
        self._python_exe = python_exe
        self._python_exes = dict(python_exes or {})
        self.allow_python_substitution = allow_python_substitution
        #: Reason the MOST RECENT call returned ``None`` (UNGRADED), else ``None``.
        self.last_reason: Optional[str] = None
        #: Run-lifetime tally reason -> count (loud degradation, mirrors PR #124).
        self.ungraded_reasons: dict[str, int] = {}
        #: task_id -> "pin->used" when explicitly enabled and the pinned python was
        #: unavailable, so a nearest uv-available python was substituted
        #: (host-substitution; not leaderboard-comparable).
        self.python_substitutions: dict[str, str] = {}
        #: cached sorted [(major, minor), ...] of uv-provisionable CPython versions.
        self._uv_minors_cache: Optional[list] = None
        #: Per-sequence shared venv cache: (repo, version) -> (venv_dir, python_exe).
        #: Every task of one SWE-Bench-CL sequence shares a repo+version, hence the same
        #: interpreter + third-party deps; build that ONCE per sequence and reuse it,
        #: re-running only the cheap editable install of each task's checkout. Lives for
        #: the grader instance's lifetime (one per run).
        self._seq_venvs: dict[tuple[str, str], tuple[Path, str]] = {}
        #: Root holding the shared per-sequence venvs (persists across tasks within a
        #: run, unlike the per-task checkout temp dir). Lazily created on first use.
        self._venv_root: Optional[Path] = None

    # -- honesty-rule degradation (mirrors LocalExecGrader._ungraded) -------- #
    def _ungraded(self, reason: str, task: Optional[Task] = None) -> None:
        """Record + log an UNGRADED (``None``) outcome and return ``None``."""
        self.last_reason = reason
        self.ungraded_reasons[reason] = self.ungraded_reasons.get(reason, 0) + 1
        tid = getattr(task, "task_id", None) or "?"
        log.warning("SwebenchHostGrader UNGRADED [task=%s]: %s", tid, reason)
        return None

    # -- per-sequence shared venv (built ahead of the sequence's tasks) ------ #
    def _venv_root_dir(self) -> Path:
        """The directory holding the shared per-sequence venvs. Persists across a
        sequence's tasks (unlike each task's throwaway checkout dir), so the interpreter
        + third-party deps are provisioned once and reused.

        Created lazily under the system temp dir and registered for removal at process
        exit, so the (potentially large) shared venvs are not leaked under ``/tmp``.
        Also removable explicitly via :meth:`cleanup`."""
        from pathlib import Path
        if self._venv_root is None:
            import atexit
            import tempfile
            self._venv_root = Path(tempfile.mkdtemp(prefix="memeval-swe-seqvenv-"))
            atexit.register(self.cleanup)
        return self._venv_root

    def cleanup(self) -> None:
        """Remove the shared per-sequence venv root (and its cached venvs). Idempotent
        and best-effort — safe to call explicitly or via the atexit hook."""
        import shutil
        root = self._venv_root
        if root is None:
            return
        self._venv_root = None
        self._seq_venvs.clear()
        shutil.rmtree(root, ignore_errors=True)

    @staticmethod
    def _resolve_spec(repo: str, version: str) -> Optional[dict]:
        """Resolve the SWE-bench install spec for ``repo@version``, or ``None`` when the
        optional ``swebench`` package or the spec entry is absent."""
        try:
            from swebench.harness.constants import MAP_REPO_VERSION_TO_SPECS
        except Exception:  # noqa: BLE001 - missing optional dep
            return None
        return MAP_REPO_VERSION_TO_SPECS.get(repo, {}).get(version)

    def prewarm_sequence(self, repo: str, version: str) -> Optional[str]:
        """Build the shared venv for a ``repo@version`` SWE-Bench-CL sequence AHEAD of
        its tasks, returning the venv's python exe (or ``None`` if it can't be built).

        Provisions the pinned interpreter and installs the sequence-invariant pieces —
        ``pre_install`` and ``pip_packages`` (the heavy third-party wheels every task in
        the sequence shares) — exactly once. Per-task grading then only re-runs the cheap
        editable install of that task's own checkout into this shared venv. Idempotent:
        a second call for the same ``(repo, version)`` returns the cached interpreter.

        Best-effort and fail-open: a build failure caches nothing and returns ``None``,
        so grading falls back to the per-task venv path and is never blocked."""
        repo = (repo or "").strip()
        version = str(version or "").strip()
        if not repo or not version:
            return None
        key = (repo, version)
        cached = self._seq_venvs.get(key)
        if cached is not None:
            return cached[1]

        spec = self._resolve_spec(repo, version)
        if not spec:
            log.info("SwebenchHostGrader: no spec for %s@%s; skipping sequence prewarm",
                     repo, version)
            return None

        # A per-(repo,version) subdir under the shared root holds this sequence's venv.
        # The venv lands at ``seq_dir/.venv-swe-grade`` (beside a scratch ``checkout``
        # dir _make_venv derives from its arg's parent); create the scratch dir so the
        # run cwd exists.
        seq_dir = self._venv_root_dir() / f"{repo.replace('/', '__')}__{version}"
        scratch = seq_dir / "checkout"
        scratch.mkdir(parents=True, exist_ok=True)
        py = self._make_venv(scratch, python=str(spec.get("python") or "") or None,
                             seed=self._needs_seed(repo))
        if py is None:
            log.info("SwebenchHostGrader: could not provision interpreter for %s@%s; "
                     "sequence prewarm skipped (per-task venv will be used)", repo, version)
            return None

        # Install only the sequence-invariant pieces here (pre_install + pip_packages);
        # the editable install of each task's checkout happens per task in _install.
        # Run from the per-sequence scratch dir so pre_install shell steps that use
        # relative paths / write into cwd land beside this sequence's venv, not in the
        # shared root.
        self._install_shared(py, spec, dest=scratch)
        self._seq_venvs[key] = (seq_dir, py)
        log.info("SwebenchHostGrader: prewarmed shared venv for %s@%s at %s",
                 repo, version, seq_dir)
        return py

    def __call__(self, task: Task, prediction: str) -> Optional[bool]:
        self.last_reason = None  # reset per call; set only on a None (ungraded) path
        if task.kind is not TaskKind.CODE:
            return None  # not CODE; QA grading handles it (not a degradation)
        if not (prediction or "").strip():
            return False  # no patch produced = a real miss (SWE-bench empty_patch)
        try:
            return self._grade(task, prediction)
        except Exception as exc:  # noqa: BLE001 - any env/run failure -> UNGRADED
            return self._ungraded(f"exception: {type(exc).__name__}: {exc}", task)

    # -- internals ---------------------------------------------------------- #
    def _grade(self, task: Task, prediction: str) -> Optional[bool]:
        import tempfile
        from pathlib import Path

        # Lazy import — swebench stays an OPTIONAL dependency.
        try:
            from swebench.harness.constants import (
                FAIL_TO_PASS,
                PASS_TO_PASS,
                MAP_REPO_VERSION_TO_SPECS,
            )
            from swebench.harness.grading import (
                ResolvedStatus,
                get_eval_tests_report,
                get_resolution_status,
            )
            from swebench.harness.log_parsers import MAP_REPO_TO_PARSER
            from swebench.harness.test_spec.python import get_test_directives
        except Exception as exc:  # noqa: BLE001 - missing optional dep -> UNGRADED
            return self._ungraded(
                f"swebench not installed ({exc}); install the 'swebench' extra", task)

        repo = (task.repo or "").strip()
        version = str((task.metadata or {}).get("version") or "").strip()
        if not repo or not version:
            return self._ungraded(
                f"missing repo/version (repo={repo!r} version={version!r})", task)

        spec = MAP_REPO_VERSION_TO_SPECS.get(repo, {}).get(version)
        if not spec:
            return self._ungraded(f"no swebench spec for {repo}@{version}", task)
        parser = MAP_REPO_TO_PARSER.get(repo)
        if parser is None:
            return self._ungraded(f"no swebench log parser for {repo}", task)

        # Build the SWE-bench instance dict from the Task. ``get_test_directives``
        # reads ``test_patch`` (the directives ARE derived from it), so it must be
        # present and correct; FAIL_TO_PASS / PASS_TO_PASS become the gold lists.
        instance: dict[str, Any] = {
            "instance_id": instance_id_of(task),
            "repo": repo,
            "version": version,
            "base_commit": task.base_commit or "",
            "problem_statement": task.question or "",
            "patch": task.patch or "",
            "test_patch": task.test_patch or "",
            FAIL_TO_PASS: list(task.fail_to_pass or []),
            PASS_TO_PASS: list(task.pass_to_pass or []),
        }

        directives = get_test_directives(instance)
        if not directives:
            directives = _django_directives_from_patch_or_selectors(task) if repo == "django/django" else []
        if not directives:
            return self._ungraded(
                "get_test_directives yielded no test files from test_patch", task)

        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "repo"
            verdict = self._grade_in_checkout(
                dest, task, prediction, spec, directives, parser, instance,
                get_eval_tests_report=get_eval_tests_report,
                get_resolution_status=get_resolution_status,
                ResolvedStatus=ResolvedStatus,
                FAIL_TO_PASS=FAIL_TO_PASS,
                PASS_TO_PASS=PASS_TO_PASS,
            )
            return verdict

    def _grade_in_checkout(  # noqa: PLR0913 - cohesive single flow, kept explicit
        self, dest, task: Task, prediction: str, spec: dict, directives: list,
        parser, instance: dict, *, get_eval_tests_report, get_resolution_status,
        ResolvedStatus, FAIL_TO_PASS, PASS_TO_PASS,
    ) -> Optional[bool]:
        from pathlib import Path

        from .claudecode.checkout import CheckoutError

        dest = Path(dest)
        try:
            self._checkout_repo(dest, task)
        except CheckoutError as exc:
            return self._ungraded(f"checkout failed: {exc}", task)

        # Apply the agent's prediction patch.
        applied = self._apply_patch(dest, prediction)
        if applied is None:
            return self._ungraded("prediction patch did not apply (base drift)", task)
        if applied is False:
            return self._ungraded("prediction patch was empty/no-op after strip", task)

        # Apply the GOLD test_patch (harness applies tests, NEVER the agent).
        if task.test_patch:
            # Trust boundary (SWE-bench rule): revert the gold test_patch's target
            # files to base BEFORE applying it, discarding any agent edits to them.
            # Otherwise an agent that touched a test file makes the gold patch fail
            # to apply (gold_test_apply_failed) AND could influence the tests.
            revert_test_files(self._git_runner, dest, patch_target_files(task.test_patch))
            if self._apply_patch(dest, task.test_patch) is not True:
                return self._ungraded("gold test_patch did not apply", task)

        # Provision the pinned interpreter via uv. The pinned (often old) python is
        # the whole point — SWE-bench commits assume a then-current interpreter; a
        # host-default venv breaks historical code. If we cannot get it, UNGRADED.
        #
        # Prefer the sequence's prewarmed shared venv (one per repo@version) when present
        # — its interpreter + third-party deps are already installed, so only this task's
        # editable checkout install runs below. Otherwise build a per-task venv as before.
        repo = (task.repo or "").strip()
        version = str((task.metadata or {}).get("version") or "").strip()
        shared = self._seq_venvs.get((repo, version))
        if shared is not None:
            py: Optional[str] = shared[1]
            prewarmed = True
        else:
            py = self._make_venv(dest, python=str(spec.get("python") or "") or None,
                                 task=task, seed=self._needs_seed(repo))
            prewarmed = False
        if py is None:
            # Fall back to a configured interpreter ONLY if no pin was requested;
            # if a pin was requested and no exact interpreter could be provisioned,
            # be honest.
            if spec.get("python") and self._python_exe is None:
                return self._ungraded(
                    f"could not provision python {spec.get('python')} "
                    f"(no exact interpreter fallback available)", task)
            py = self._python_exe or "python"
            prewarmed = False

        # shared=True skips the sequence-invariant install (already done at prewarm); only
        # this checkout's spec-install + editable install run.
        env_overlay = _scm_env(repo)
        self._install(dest, py, spec, shared=prewarmed,
                      era_pins=self._era_base_pins(repo, version),
                      env=env_overlay or None)

        # Build the official eval command: spec.test_cmd + the test directives. This
        # is EXACTLY how swebench's make_eval_script_list_py composes the command;
        # the directives (test modules/files) come from the gold test_patch.
        import shlex
        test_cmd = str(spec.get("test_cmd") or "").strip()
        if not test_cmd:
            return self._ungraded(f"spec for {task.repo} has no test_cmd", task)
        # A spec test_cmd may carry a leading inline env prefix, e.g. sympy's
        # ``PYTHONWARNINGS='...' bin/test -C --verbose``. shlex.split turns those
        # ``VAR=val`` tokens into argv[0..]; passing them to _with_python makes it
        # try to EXEC ``PYTHONWARNINGS=...`` as a program (raises -> ungraded).
        # Peel them off into the subprocess ENV, where they belong.
        cmd_env, rest = _split_env_prefix(shlex.split(test_cmd))
        if env_overlay:
            cmd_env = {**env_overlay, **cmd_env}
        argv = rest + list(directives)
        ran = self._run(self._with_python(py, argv), dest,
                        env=cmd_env or None)
        if ran is None:
            return self._ungraded("test command runner unavailable/raised", task)
        _rc, out, err = ran
        log_text = (out or "") + "\n" + (err or "")

        # Parse with the OFFICIAL per-repo parser. For all SWE-bench Verified python
        # repos the parser ignores ``test_spec``, so a host run needs no container
        # TestSpec; we pass ``None`` (the parser signature accepts it).
        status_map = parser(log_text, None)
        if not status_map:
            return self._ungraded("official parser produced no statuses", task)

        gold_results = {
            FAIL_TO_PASS: list(task.fail_to_pass or []),
            PASS_TO_PASS: list(task.pass_to_pass or []),
        }
        report = get_eval_tests_report(status_map, gold_results)
        resolution = get_resolution_status(report)
        return resolution == ResolvedStatus.FULL.value

    def _checkout_repo(self, dest, task: Task) -> None:
        """Materialize the task's checkout into ``dest``; raise ``CheckoutError`` only
        if every avenue fails (mirroring the old single ``prepare_checkout`` call).

        Delegates to the shared :func:`~memeval.claudecode.checkout.checkout_with_cache`
        — the ONE cache-aware checkout entrypoint every per-task checkout (both graders
        and both agent-side checkouts) routes through. It reads ``MEMEVAL_REPO_CACHE``
        itself: **unset** → the historical network ``auto`` path UNCHANGED (fetch
        retried on a transient blip, byte-identical when nothing fails); **set** → a
        persistent bare mirror so per-task checkouts are network-free, with a
        WARNING-logged fallback to the network path so a cache problem NEVER turns a
        gradeable task into an UNGRADED one. Under an injected stub ``git_runner``
        (offline tests) the backoff sleep is a no-op, so the tests never block.
        """
        from pathlib import Path

        from .claudecode.checkout import checkout_with_cache

        git_kwargs = {} if self._git_runner is None else {"git_runner": self._git_runner}
        checkout_with_cache(task.repo or "", task.base_commit, Path(dest),
                            timeout=self.timeout, **git_kwargs)

    def _with_python(self, py: str, argv: list) -> list:
        """Compose an argv for a test command. A bare ``runtests.py`` script is run
        directly with the interpreter (django: ``python tests/runtests.py ...``); a
        command that already names ``python``/``pytest`` is rebound onto the venv's
        interpreter; anything else (e.g. ``pytest -q``) is run as ``python -m``.

        A path-like script (``bin/test``, ``./tests/runtests.py`` — contains ``/`` or
        ends ``.py``) is run THROUGH the venv interpreter (``py bin/test ...``) rather
        than executed directly, so it neither needs the +x bit nor a shebang pointing
        at the right Python — sympy's ``bin/test`` is the motivating case.

        ``tox`` is NOT pytest: sphinx's ``tox --current-env -epy39 -v -- <files>`` uses
        the ``tox-current-env`` plugin (both installed by the spec's ``pip_packages``)
        to run the py39 env's command — pytest — IN this venv, forwarding everything
        after ``--`` to it. It MUST run as ``py -m tox`` with its own flags intact;
        rewriting it to ``py -m pytest --current-env -epy39 ...`` feeds tox-only flags
        to pytest, which exits with a usage error -> empty log -> the official parser
        produces no statuses (the sphinx I-11 failure mode)."""
        if not argv:
            return [py]
        head = argv[0]
        if head.endswith(".py") or "/" in head:
            return [py, *argv]
        if head in ("python", "python3"):
            return [py, *argv[1:]]
        if head in ("pytest", "pytest-3"):
            return [py, "-m", "pytest", *argv[1:]]
        if head == "tox":
            # Run tox itself (with --current-env / -e / -- intact), not pytest.
            return [py, "-m", "tox", *argv[1:]]
        # Fallback: run via the interpreter's module runner if it's a known module,
        # else execute the command head as-is.
        return [py, "-m", head, *argv[1:]] if head.isidentifier() else [head, *argv[1:]]

    def _apply_patch(self, dest: Any, patch: str) -> Optional[bool]:
        """``git apply`` ``patch`` in ``dest``. ``True`` applied / ``False`` empty /
        ``None`` failed. Mirrors :meth:`LocalExecGrader._apply_patch`."""
        import tempfile
        from pathlib import Path

        from .claudecode import checkout as _checkout

        if not (patch or "").strip():
            return False
        git = self._git_runner or _checkout._subprocess_git
        pf = Path(tempfile.gettempdir()) / (
            f"memeval-swe-patch-{abs(hash(patch)) % (10 ** 10)}.patch")
        try:
            pf.write_text(patch if patch.endswith("\n") else patch + "\n",
                          encoding="utf-8")
            res = git(["apply", "--whitespace=nowarn", str(pf)], Path(dest))
            return True if res.returncode == 0 else None
        finally:
            try:
                pf.unlink()
            except OSError:
                pass

    def _run(self, args: list, cwd: Any, *, env: Optional[dict] = None):
        """Invoke the injectable runner; ``(rc, stdout, stderr)`` or ``None`` if it
        raised (tool absent / timeout)."""
        try:
            r = self._runner(args, cwd, env)
        except Exception:  # noqa: BLE001 - tool absent / failed -> ungraded upstream
            return None
        return (getattr(r, "returncode", None),
                getattr(r, "stdout", "") or "",
                getattr(r, "stderr", "") or "")

    def _make_venv(self, dest: Any, *, python: Optional[str] = None,
                   task: Any = None, seed: bool = False) -> Optional[str]:
        """Provision a venv beside the checkout and return its python exe path, or
        ``None``.

        First tries the SWE-bench-pinned interpreter via ``uv venv --python <pin>``.
        uv's managed CPython builds only go back to 3.8, so old pins (3.5/3.6/3.7) may
        not be fetched. Before degrading, try exact external interpreters from the
        constructor, ``MEMEVAL_SWEBENCH_PYTHON_3_6`` / ``..._3_5``,
        ``MEMEVAL_SWEBENCH_PYTHONS=3.6=/path,3.5=/path``, or ``python3.6`` /
        ``python3.5`` on PATH. Only when ``allow_python_substitution`` is set do we fall
        back to the nearest uv-available python >= the pin; that is logged + recorded
        as host-substitution and is NOT leaderboard-comparable. Offline (stub runner)
        ``uv venv`` reports rc 0 but writes no interpreter -> ``None`` (caller falls
        back), and no fallback search runs.

        ``seed`` adds ``--seed`` (pip/setuptools/wheel) — opt-in PER REPO via
        :meth:`_needs_seed`, so a repo that doesn't need it gets a venv byte-identical
        to before (no behavior change for the rest of the benchmark suite)."""
        from pathlib import Path

        venv = Path(dest).parent / ".venv-swe-grade"

        def _try(cand: Optional[str]) -> tuple[str, Optional[str]]:
            # --seed installs pip/setuptools/wheel into the venv. A fresh uv venv has
            # NONE; SWE-bench's conda base env does, and spec install commands assume
            # pip (e.g. ``python -m pip install -e .[test]``). Without it that install
            # fails -> the [test] extra (pytest) never lands -> the test command can't
            # run -> empty log -> "official parser produced no statuses". Gated to repos
            # that need it (see _needs_seed) so other benchmarks are unaffected.
            argv = ["uv", "venv", "--seed", "--clear"] if seed else ["uv", "venv", "--clear"]
            if cand:
                argv += ["--python", cand]
            argv.append(str(venv))
            ran = self._run(argv, dest)
            if ran is None or ran[0] != 0:
                return "failed", None
            posix = venv / "bin" / "python"
            win = venv / "Scripts" / "python.exe"
            if posix.exists():
                return "ok", str(posix)
            if win.exists():
                return "ok", str(win)
            return "no-interpreter", None  # offline stub: rc 0 but nothing on disk

        status, py = _try(python)
        if status == "ok":
            return py
        # No interpreter on disk (offline stub), or no pin to substitute -> done.
        if status == "no-interpreter" or not python:
            return None
        for cand in self._exact_python_candidates(python):
            status, py = _try(cand)
            if status == "ok":
                log.info("SwebenchHostGrader: using exact external python %s for pin %s",
                         cand, python)
                return py
            if status == "no-interpreter":
                return None
        if not self.allow_python_substitution:
            return None
        # The pin itself couldn't be fetched: try the nearest uv-available python >= pin.
        for cand in self._fallback_pythons(dest, python):
            status, py = _try(cand)
            if status == "ok":
                self._note_python_substitution(python, cand, task)
                return py
            if status == "no-interpreter":
                return None
        return None

    def _exact_python_candidates(self, pin: str) -> list:
        """Exact external interpreters for ``pin`` (e.g. ``3.6``), in preference order."""
        import os
        import shutil

        norm = self._python_minor(pin)
        if norm is None:
            return []
        env_key = f"MEMEVAL_SWEBENCH_PYTHON_{norm.replace('.', '_')}"
        raw_map = os.environ.get("MEMEVAL_SWEBENCH_PYTHONS") or ""
        mapped: dict[str, str] = {}
        for part in raw_map.split(","):
            key, sep, val = part.strip().partition("=")
            if sep and key.strip() and val.strip():
                mapped[key.strip()] = val.strip()

        candidates = [
            self._python_exes.get(norm),
            os.environ.get(env_key),
            mapped.get(norm),
            shutil.which(f"python{norm}"),
        ]
        out: list = []
        for cand in candidates:
            if cand and cand not in out:
                out.append(cand)
        return out

    @staticmethod
    def _python_minor(pin: str) -> Optional[str]:
        """Normalize a Python pin to ``major.minor``."""
        try:
            parts = (str(pin).split(".") + ["0"])[:2]
            return f"{int(parts[0])}.{int(parts[1])}"
        except (ValueError, AttributeError):
            return None

    def _fallback_pythons(self, dest: Any, pin: str) -> list:
        """uv-available ``"major.minor"`` strings strictly newer than ``pin`` (ascending),
        so we substitute the SMALLEST compatible interpreter uv can actually provision."""
        norm = self._python_minor(pin)
        if norm is None:
            return []
        pin_mm = tuple(int(p) for p in norm.split("."))
        out: list = []
        for mm in self._uv_minors(dest):
            if mm > pin_mm:
                s = f"{mm[0]}.{mm[1]}"
                if s not in out:
                    out.append(s)
        return out

    def _uv_minors(self, dest: Any) -> list:
        """Sorted unique ``(major, minor)`` of CPython versions uv can provision, parsed
        from ``uv python list --all-versions``. Cached for the grader's lifetime."""
        if self._uv_minors_cache is not None:
            return self._uv_minors_cache
        import re

        mins: set = set()
        ran = self._run(["uv", "python", "list", "--all-versions"], dest)
        if ran is not None and ran[0] == 0:
            for m in re.finditer(r"cpython-(\d+)\.(\d+)\.", (ran[1] or "") + (ran[2] or "")):
                mins.add((int(m.group(1)), int(m.group(2))))
        self._uv_minors_cache = sorted(mins)
        return self._uv_minors_cache

    def _note_python_substitution(self, pin: str, used: str, task: Any) -> None:
        tid = getattr(task, "task_id", None) or "?"
        self.python_substitutions[tid] = f"{pin}->{used}"
        log.warning(
            "SwebenchHostGrader [task=%s]: pinned python %s not provisionable via uv; "
            "grading under nearest available python %s (host-substitution — NOT "
            "leaderboard-comparable)", tid, pin, used)

    @staticmethod
    def _version_tuple(version: str) -> Optional[tuple]:
        """Parse a spec version (``"3.0"`` / ``"4.1"``) into a comparable ``(major,
        minor)`` tuple, or ``None`` if unparseable."""
        parts = str(version or "").strip().split(".")
        try:
            return (int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
        except (ValueError, IndexError):
            return None

    @staticmethod
    def _needs_seed(repo: str) -> bool:
        """Whether this repo's grading venv should be created with ``--seed`` (pip etc.).
        Scoped to :data:`_CONDA_BASE_REPOS` so every other repo's venv is byte-identical
        to before — no cross-benchmark behavior change."""
        return (repo or "").strip().lower() in _CONDA_BASE_REPOS

    def _era_base_pins(self, repo: str, version: str) -> list:
        """Era-appropriate base packages a fresh ``--seed`` venv gets WRONG for some OLD
        repo eras (``--seed`` / pip install the LATEST):

        * setuptools >= 81 dropped ``pkg_resources`` — old sphinx ``registry.py`` does
          ``from pkg_resources import iter_entry_points``;
        * docutils >= 0.16 dropped the top-level ``roman`` module — old sphinx
          ``writers/latex.py`` does ``from roman import toRoman``.

        SWE-bench's conda base shipped era-correct versions; on the host we clamp them
        for the AFFECTED eras only (newer versions REQUIRE the modern deps, so a blanket
        clamp would regress them). Installed LAST by :meth:`_install` so neither the spec
        install nor the editable install can pull them forward again. ``[]`` = no clamp.
        Scoped to :data:`_CONDA_BASE_REPOS` — every other repo gets ``[]``, unchanged.

        Scope (eval-infra follow-up, cc @kenhuangus): this unblocks sphinx 3.x — 22/44 of
        the sphinx sequence. sphinx >= 4.x fails DIFFERENTLY (tox-current-env does not
        engage on the newer ``tox.ini``; tox builds an isolated ``.tox/py39`` without
        pytest -> 'No module named pytest'), a separate tox/plugin-compat fix, not a pin.
        """
        if (repo or "").strip().lower() not in _CONDA_BASE_REPOS:
            return []
        ver = self._version_tuple(version)
        return (["setuptools<60", "docutils<0.16"]
                if ver is not None and ver < (4, 0) else [])

    def _install(self, dest: Any, py: str, spec: dict, *, shared: bool = False,
                 era_pins: "tuple | list" = (),
                 env: Optional[dict] = None) -> None:
        """Run the spec's install steps in the venv, best-effort. ROOT/apt
        ``pre_install`` entries are SKIPPED (host can't run them, logged). Failures
        are tolerated — many repos import from source; the test-run step decides
        gradeability (no parseable output -> UNGRADED there, not here).

        When the venv was prewarmed for the sequence (``shared=True``), the
        sequence-invariant pieces (``pre_install`` + ``pip_packages``) were already
        installed by :meth:`_install_shared`, so only the per-task pieces (the spec
        ``install`` command and the editable install of THIS checkout) run here.

        ``era_pins`` (from :meth:`_era_base_pins`) are clamped LAST so neither the spec
        install nor the editable install can leave them resolved too-new."""
        if not shared:
            self._install_shared(py, spec, dest=dest)
        # install command (e.g. ``python setup.py install`` / ``pip install -e .``) —
        # per-task because it runs against THIS checkout's source.
        install = str(spec.get("install") or "").strip()
        if install:
            self._run(self._rebind(install, py), dest, env=env)
        # Best-effort editable install of the checkout itself (source imports) — per-task.
        self._run(["uv", "pip", "install", "--python", py, "-e", "."], dest,
                  env=env)
        # Era-appropriate base-dep clamp (see _era_base_pins), LAST so it wins over any
        # too-new resolve from the installs above.
        if era_pins:
            self._run(["uv", "pip", "install", "--python", py, *era_pins], dest,
                      env=env)

    def _install_shared(self, py: str, spec: dict, *, dest: Any = None) -> None:
        """Install the sequence-invariant pieces into the venv (``pre_install`` +
        ``pip_packages``) — the heavy third-party wheels every task of a sequence shares.
        Done once per sequence at prewarm; ``dest`` is the cwd for ``pre_install`` shell
        steps (the shared venv's scratch dir at prewarm, the checkout otherwise)."""
        cwd = dest if dest is not None else (self._venv_root_dir())
        # pre_install: skip root/apt/locale entries; attempt the rest.
        for cmd in (spec.get("pre_install") or []):
            if _is_root_preinstall(cmd):
                log.info("SwebenchHostGrader: skipping root/apt pre_install: %s", cmd)
                continue
            self._run(self._shell(cmd), cwd)
        # pip_packages: install each into the venv.
        pkgs = list(spec.get("pip_packages") or [])
        if pkgs:
            self._run(["uv", "pip", "install", "--python", py, *pkgs], cwd)

    @staticmethod
    def _shell(cmd: str) -> list:
        """Wrap a shell command string as an argv for the runner."""
        import shlex
        return shlex.split(cmd)

    @staticmethod
    def _rebind(install_cmd: str, py: str) -> list:
        """Rebind a spec ``install`` command onto the venv interpreter where it names
        ``python``/``pip``; otherwise run it as a shell argv."""
        import shlex
        parts = shlex.split(install_cmd)
        if not parts:
            return [py]
        if parts[0] in ("python", "python3"):
            return [py, *parts[1:]]
        if parts[0] == "pip":
            return [py, "-m", "pip", *parts[1:]]
        return parts


__all__ = ["SwebenchHostGrader"]


def _django_directives_from_patch_or_selectors(task: Task) -> list[str]:
    """Recover Django runtests directives when a gold patch edits only fixtures.

    SWE-bench normally derives Django directives from Python files touched by
    ``test_patch``. A few Django instances add fixture rows (for example
    ``tests/validators/*.txt``), so that function returns an empty list even
    though there is still an obvious Django test app. Prefer the fixture's
    ``tests/<app>/...`` label; fall back to selector modules only if no such
    label is present.
    """
    app_labels: list[str] = []
    seen_apps: set[str] = set()
    for target in patch_target_files(task.test_patch or ""):
        parts = target.split("/")
        if len(parts) >= 3 and parts[0] == "tests" and parts[1]:
            app = parts[1]
            if app not in seen_apps:
                app_labels.append(app)
                seen_apps.add(app)
    if app_labels:
        return app_labels

    modules: list[str] = []
    seen: set[str] = set()
    for selector in [*(task.fail_to_pass or []), *(task.pass_to_pass or [])]:
        if not is_django_selector(selector):
            continue
        # The normalized label is itself a runnable runtests directive — bare
        # module (``validators.tests``), ``module.Class``, or the full
        # ``module.Class.method`` all run. Don't strip to ``parts[:-2]``: that
        # both discarded bare labels (``len < 3``) and over-trimmed
        # ``module.Class`` down to ``module``. Keep the label as-is and dedupe.
        label = django_label(selector).strip()
        if label and label not in seen:
            modules.append(label)
            seen.add(label)
    return modules
