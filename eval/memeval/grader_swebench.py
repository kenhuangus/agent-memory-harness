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
    instance_id_of,
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


def _is_root_preinstall(cmd: str) -> bool:
    """True iff ``cmd`` needs root/apt/locale/system paths (skip on host)."""
    s = (cmd or "").strip().lower()
    if not s:
        return True  # empty / no-op -> skip
    if any(s.startswith(p) for p in _ROOT_PREINSTALL_PREFIXES):
        return True
    return any(sub in s for sub in _ROOT_PREINSTALL_SUBSTRINGS)


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
    ) -> None:
        self._runner = runner or _subprocess_cmd
        self._git_runner = git_runner  # forwarded to checkout (None -> its default)
        self.model_name = model_name
        self.timeout = timeout
        self._python_exe = python_exe
        #: Reason the MOST RECENT call returned ``None`` (UNGRADED), else ``None``.
        self.last_reason: Optional[str] = None
        #: Run-lifetime tally reason -> count (loud degradation, mirrors PR #124).
        self.ungraded_reasons: dict[str, int] = {}

    # -- honesty-rule degradation (mirrors LocalExecGrader._ungraded) -------- #
    def _ungraded(self, reason: str, task: Optional[Task] = None) -> None:
        """Record + log an UNGRADED (``None``) outcome and return ``None``."""
        self.last_reason = reason
        self.ungraded_reasons[reason] = self.ungraded_reasons.get(reason, 0) + 1
        tid = getattr(task, "task_id", None) or "?"
        log.warning("SwebenchHostGrader UNGRADED [task=%s]: %s", tid, reason)
        return None

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

        from .claudecode.checkout import CheckoutError, prepare_checkout

        dest = Path(dest)
        git_kwargs = {} if self._git_runner is None else {"git_runner": self._git_runner}
        try:
            prepare_checkout(task.repo or "", task.base_commit, dest,
                             timeout=self.timeout, **git_kwargs)
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
        py = self._make_venv(dest, python=str(spec.get("python") or "") or None)
        if py is None:
            # Fall back to a configured interpreter ONLY if no pin was requested;
            # if a pin was requested and uv couldn't provide it, be honest.
            if spec.get("python") and self._python_exe is None:
                return self._ungraded(
                    f"could not provision python {spec.get('python')}", task)
            py = self._python_exe or "python"

        self._install(dest, py, spec)

        # Build the official eval command: spec.test_cmd + the test directives. This
        # is EXACTLY how swebench's make_eval_script_list_py composes the command;
        # the directives (test modules/files) come from the gold test_patch.
        import shlex
        test_cmd = str(spec.get("test_cmd") or "").strip()
        if not test_cmd:
            return self._ungraded(f"spec for {task.repo} has no test_cmd", task)
        argv = shlex.split(test_cmd) + list(directives)
        ran = self._run(self._with_python(py, argv), dest)
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

    def _with_python(self, py: str, argv: list) -> list:
        """Compose an argv for a test command. A bare ``runtests.py`` script is run
        directly with the interpreter (django: ``python tests/runtests.py ...``); a
        command that already names ``python``/``pytest`` is rebound onto the venv's
        interpreter; anything else (e.g. ``pytest -q``) is run as ``python -m``."""
        if not argv:
            return [py]
        head = argv[0]
        if head.endswith(".py"):
            return [py, *argv]
        if head in ("python", "python3"):
            return [py, *argv[1:]]
        if head == "pytest":
            return [py, "-m", "pytest", *argv[1:]]
        if head in ("tox", "pytest-3"):
            return [py, "-m", "pytest", *argv[1:]]
        # Fallback: run via the interpreter's module runner if it's a known module,
        # else execute the command head as-is (e.g. ``./tests/runtests.py``).
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

    def _make_venv(self, dest: Any, *, python: Optional[str] = None) -> Optional[str]:
        """``uv venv --python <python>`` beside the checkout; return its python exe
        path or ``None`` if uv is unavailable / the pin can't be fetched. Offline
        (stub runner) this is a harmless no-op — the canned runner reports success
        but writes no files, so we return ``None`` and the caller falls back."""
        from pathlib import Path

        venv = Path(dest).parent / ".venv-swe-grade"
        argv = ["uv", "venv", "--clear"]
        if python:
            argv += ["--python", python]
        argv.append(str(venv))
        ran = self._run(argv, dest)
        if ran is None or ran[0] != 0:
            return None
        posix = venv / "bin" / "python"
        if posix.exists():
            return str(posix)
        win = venv / "Scripts" / "python.exe"
        if win.exists():
            return str(win)
        return None  # venv reported OK but no interpreter on disk (offline stub)

    def _install(self, dest: Any, py: str, spec: dict) -> None:
        """Run the spec's install steps in the venv, best-effort. ROOT/apt
        ``pre_install`` entries are SKIPPED (host can't run them, logged). Failures
        are tolerated — many repos import from source; the test-run step decides
        gradeability (no parseable output -> UNGRADED there, not here)."""
        # pre_install: skip root/apt/locale entries; attempt the rest in the checkout.
        for cmd in (spec.get("pre_install") or []):
            if _is_root_preinstall(cmd):
                log.info("SwebenchHostGrader: skipping root/apt pre_install: %s", cmd)
                continue
            self._run(self._shell(cmd), dest)
        # install command (e.g. ``python setup.py install`` / ``pip install -e .``).
        install = str(spec.get("install") or "").strip()
        if install:
            self._run(self._rebind(install, py), dest)
        # pip_packages: install each into the venv.
        pkgs = list(spec.get("pip_packages") or [])
        if pkgs:
            self._run(["uv", "pip", "install", "--python", py, *pkgs], dest)
        # Best-effort editable install of the checkout itself (source imports).
        self._run(["uv", "pip", "install", "--python", py, "-e", "."], dest)

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
