"""CODE-task grading — owner: Ken. Real patch-apply / test-run scoring.

The harness grades QA by normalized exact match (``metrics.qa_match``), but CODE
benchmarks (SWE-ContextBench, SWE-Bench-CL) need the model's patch *applied in a
fresh checkout* and the tests *run*. This module provides graders that slot into
``harness.run(grader=...)`` / ``agent.run_agent(grader=...)`` — each a
``Callable[[Task, str], Optional[bool]]`` returning ``True`` (resolved),
``False`` (not resolved), or ``None`` (could not grade).

Graders
-------
* :class:`LocalExecGrader` — the **default for CODE runs**. In a fresh, throwaway
  checkout of the task's repo at ``base_commit``, it applies the agent's
  prediction, then applies the GOLD ``test_patch`` (the harness applies tests —
  never the agent — so the model can't fake passing tests), builds a per-task
  venv best-effort, runs ``FAIL_TO_PASS`` + ``PASS_TO_PASS``, and decides RESOLVED
  by the standard SWE-bench rule (every ``FAIL_TO_PASS`` passes AND every
  ``PASS_TO_PASS`` still passes). It degrades to ``None`` (UNGRADED) whenever the
  environment can't be built or the checkout/patch can't be set up — it never
  returns ``False`` or crashes on an environment problem. Host-dependent and
  partial-coverage; NOT leaderboard-comparable (see ADR-eval-002).
* :func:`overlap_grader` — a cheap, dependency-free heuristic (token overlap of
  the prediction against the gold patch) for smoke tests / offline iteration.
  **Not** a substitute for real test execution; never report it as accuracy.

The pure pieces — :func:`build_prediction`, :func:`instance_id_of`,
:func:`resolved_from_report`, :func:`_parse_pytest` — carry the SWE-bench
contract and are unit-tested without any real venv. The command/git invocation is
behind injectable runners so the offline tests drive the whole flow with no real
git, venv, or network.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from .schema import Task, TaskKind

Grader = Callable[[Task, str], Optional[bool]]


# --------------------------------------------------------------------------- #
# Pure helpers (no execution, unit-tested)
# --------------------------------------------------------------------------- #
def instance_id_of(task: Task) -> str:
    """The SWE-bench instance id for ``task`` (metadata wins, else task_id)."""
    iid = task.metadata.get("instance_id")
    return str(iid) if iid else str(task.task_id)


def build_prediction(task: Task, prediction: str, *, model_name: str) -> dict:
    """Build one SWE-bench prediction record from a task + model output.

    SWE-bench expects ``{instance_id, model_name_or_path, model_patch}``. The
    ``prediction`` string is the model's unified diff (the agent returns it as
    its ``patch``); empty means "no patch produced".
    """
    return {
        "instance_id": instance_id_of(task),
        "model_name_or_path": model_name,
        "model_patch": prediction or "",
    }


def resolved_from_report(report: dict, instance_id: str) -> Optional[bool]:
    """Interpret a SWE-bench evaluation report for one instance.

    Robust across report shapes:

    * **Summary report** (a ``make_run_report``-style summary) — has
      ``resolved_ids`` / ``unresolved_ids`` / ``error_ids`` lists. Resolved iff
      the id is in ``resolved_ids``; ``False`` if it's in any not-resolved list;
      ``None`` if absent (not evaluated).
    * **Per-instance report** — ``{instance_id: {resolved | tests_status}}``;
      honors an explicit ``resolved`` boolean, else derives from ``tests_status``
      (all ``FAIL_TO_PASS`` and ``PASS_TO_PASS`` succeed, no failures).

    Returns ``None`` when the instance cannot be found (not graded).
    """
    # Summary shape first (the modern harness output).
    if "resolved_ids" in report:
        if instance_id in (report.get("resolved_ids") or []):
            return True
        for key in ("unresolved_ids", "error_ids", "incomplete_ids", "empty_patch_ids"):
            if instance_id in (report.get(key) or []):
                return False
        return None

    entry = report.get(instance_id)
    if entry is None:
        # Some reports nest under a top-level key; also accept a flat shape.
        entry = (report.get("instances") or {}).get(instance_id) if isinstance(
            report.get("instances"), dict
        ) else None
    if entry is None:
        return None
    if isinstance(entry, bool):
        return entry
    if "resolved" in entry:
        return bool(entry["resolved"])
    status = entry.get("tests_status")
    if not isinstance(status, dict):
        return None

    def _all_pass(group: str) -> bool:
        g = status.get(group) or {}
        success = list(g.get("success") or [])
        failure = list(g.get("failure") or [])
        return not failure and (group != "FAIL_TO_PASS" or len(success) > 0)

    return _all_pass("FAIL_TO_PASS") and _all_pass("PASS_TO_PASS")


def _parse_pytest(stdout: str, fail_to_pass: list[str],
                  pass_to_pass: list[str]) -> dict:
    """Parse pytest output into a ``tests_status`` dict for ``resolved_from_report``.

    Pure + stdlib-only (unit-testable). For each named selector in
    ``fail_to_pass`` / ``pass_to_pass``, classify it as success/failure by scanning
    pytest's ``<nodeid> PASSED`` / ``FAILED`` lines (either order:
    ``test_x.py::t PASSED`` or ``PASSED test_x.py::t``). A selector pytest never
    reported on (collection error, not found) counts as a failure — it did not
    pass, which the SWE-bench resolved rule treats as not-resolved.
    """
    out = stdout or ""

    def _passed(selector: str) -> bool:
        # A line mentioning this selector marked PASSED, and not also FAILED.
        for line in out.splitlines():
            if selector not in line:
                continue
            up = line.upper()
            if "PASSED" in up or " PASS" in up or up.strip().startswith("PASS"):
                if "FAILED" not in up and "ERROR" not in up:
                    return True
        return False

    def _group(selectors: list[str]) -> dict:
        success = [s for s in selectors if _passed(s)]
        failure = [s for s in selectors if s not in success]
        return {"success": success, "failure": failure}

    return {
        "FAIL_TO_PASS": _group(list(fail_to_pass or [])),
        "PASS_TO_PASS": _group(list(pass_to_pass or [])),
    }


def django_label(selector: str) -> str:
    """Convert a SWE-bench django selector to a ``runtests.py`` test label.

    SWE-bench's django ``FAIL_TO_PASS`` / ``PASS_TO_PASS`` entries come in a few
    shapes; ``runtests.py`` wants a dotted label (``module.Class.method`` or a
    bare app/module). This normalizes:

    * ``"test_method (module.path.TestClass)"``  -> ``module.path.TestClass.test_method``
      (django's unittest ``str(test)`` form — the dominant SWE-bench shape).
    * ``"module.path.TestClass.test_method (sub.case)"`` -> the leading dotted
      part as-is (the method is already dotted; the parenthetical is noise).
    * ``"tests/x/y.py::Class::test"`` (pytest node id, defensive) ->
      ``x.y.Class.test`` (strip a leading ``tests/``, drop ``.py``, ``::`` -> ``.``).
    * an already-dotted label passes through unchanged.

    Best-effort and string-only (unit-testable); an unrecognized shape returns the
    input stripped, which ``runtests.py`` will simply report as not-found.
    """
    s = (selector or "").strip()
    if not s:
        return s
    # "method (dotted.Class)" — the canonical SWE-bench django form.
    if "(" in s and s.endswith(")"):
        head, paren = s.split("(", 1)
        head = head.strip()
        path = paren[:-1].strip()  # drop trailing ')'
        if not head:
            return path
        if "." in head:
            # head is itself dotted (already a label); the paren is redundant.
            return head
        return f"{path}.{head}" if path else head
    # pytest node id: tests/foo/bar.py::Class::test -> foo.bar.Class.test
    if "::" in s or s.endswith(".py"):
        path, _, rest = s.partition("::")
        if path.startswith("tests/"):
            path = path[len("tests/"):]
        if path.endswith(".py"):
            path = path[:-3]
        path = path.replace("/", ".")
        rest = rest.replace("::", ".")
        return f"{path}.{rest}" if rest else path
    return s


def is_django_selector(selector: str) -> bool:
    """True iff ``selector`` looks like a runnable django test label, not prose.

    SWE-bench django ``PASS_TO_PASS`` lists occasionally carry a *docstring* as a
    standalone entry (an artifact of how verbose unittest output was captured) —
    e.g. ``"Paginator.get_page() with an empty object_list."``. Handing that to
    ``runtests.py`` makes unittest try to import a module named ``Paginator`` and
    raise a phantom ``ERROR: ... _FailedTest`` (RC=1), poisoning the whole run.

    A real django selector is one of:

    * ``"method (dotted.Class)"`` — the canonical unittest ``str(test)`` form, OR
    * a bare dotted path / label (``module.Class.method``, ``app.tests``) with no
      whitespace and no call/sentence punctuation.

    Prose is rejected: it contains spaces *outside* a trailing ``(...)``, or ``()``
    call syntax, or sentence punctuation. Best-effort + string-only (unit-tested).
    """
    s = (selector or "").strip()
    if not s:
        return False
    # Canonical "method (dotted.path)" form: head is a bare identifier, paren is a
    # dotted path. Reject "Paginator.get_page() with an empty object_list." which
    # has text after the ')' / call-parens "()" inside.
    if "(" in s and s.endswith(")"):
        head, paren = s.split("(", 1)
        head = head.strip()
        inner = paren[:-1].strip()
        if head and head.replace("_", "").isalnum() and inner and "(" not in inner \
                and " " not in inner and all(
                    part.isidentifier() for part in inner.split(".") if part):
            return True
        return False
    # Bare dotted label: no spaces, no call/prose punctuation.
    if " " in s or "(" in s or ")" in s:
        return False
    if s.endswith("."):  # a sentence, not a label
        return False
    # Every dotted segment must be a Python identifier (allow pytest node-ids too).
    if "::" in s or s.endswith(".py"):
        return True  # defensive: a pytest node id; django_label normalizes it
    return all(part.isidentifier() for part in s.split(".") if part)


def _parse_django(stdout: str, stderr: str, fail_to_pass: list[str],
                  pass_to_pass: list[str]) -> dict:
    """Parse ``tests/runtests.py`` output into a ``tests_status`` dict.

    django's runner is unittest-based, NOT pytest. At ``--verbosity=2`` a test
    that ran prints a line naming its **full dotted label**, e.g.::

        test_paginator_iteration (pagination.tests.PaginationTests.test_paginator_iteration) ... ok

    and — crucially — a test *with a docstring* is split across two lines, the
    ``... ok`` landing on the DOCSTRING line which does NOT repeat the method::

        test_get_page (pagination.tests.PaginationTests.test_get_page)
        Paginator.get_page() returns a valid page ... ok

    So a naive "method on a line ending in ok" check misses docstringed tests.
    Failures/errors are instead listed under ``FAIL:`` / ``ERROR:`` banners that
    name the test by its dotted path. The reliable, docstring-proof rule:

      a selector **PASSED** iff its full dotted label (``django_label``) appears
      anywhere in the output (so it actually ran) AND neither that dotted path nor
      its ``method``/``Class`` pair is named in any ``FAIL:`` / ``ERROR:`` banner.

    A selector django never mentioned (didn't run) is a failure — the honest
    default (not-passed -> not-resolved). runtests writes its progress to STDERR,
    so both streams are scanned.
    """
    out = (stdout or "") + "\n" + (stderr or "")
    lines = out.splitlines()
    # Failure/error banners: "FAIL: <method> (<dotted.path>)" / "ERROR: ...".
    failed_blobs: list[str] = [
        ln.strip() for ln in lines
        if ln.strip().startswith("FAIL:") or ln.strip().startswith("ERROR:")
    ]

    def _short(dotted: str) -> str:
        return dotted.rsplit(".", 1)[-1] if dotted else dotted

    def _method_and_class(selector: str) -> tuple[str, str]:
        """Recover (method, dotted-class-or-path) from any selector shape."""
        s = (selector or "").strip()
        if "(" in s and s.endswith(")"):
            head, paren = s.split("(", 1)
            return head.strip(), paren[:-1].strip()
        label = django_label(s)
        parts = label.rsplit(".", 1)
        return (parts[-1], parts[0]) if len(parts) == 2 else (label, "")

    def _in_failure_banner(method: str, klass: str) -> bool:
        for blob in failed_blobs:
            if method and method in blob and (
                not klass or klass in blob or _short(klass) in blob
            ):
                return True
        return False

    def _passed(selector: str) -> bool:
        label = django_label(selector)          # full dotted form, e.g. a.b.C.m
        method, klass = _method_and_class(selector)
        # Did it actually run? Its full dotted label is printed on the test's
        # header line whether or not it carries a docstring.
        ran = bool(label) and label in out
        if not ran:
            # Fall back to a verbose method+class header line (covers shapes where
            # the dotted label isn't echoed verbatim).
            for ln in lines:
                if method and method in ln and (
                    not klass or klass in ln or _short(klass) in ln
                ):
                    ran = True
                    break
        if not ran:
            return False  # never ran -> not passed (honest default)
        return not _in_failure_banner(method, klass)

    def _group(selectors: list[str]) -> dict:
        success = [s for s in selectors if _passed(s)]
        failure = [s for s in selectors if s not in success]
        return {"success": success, "failure": failure}

    return {
        "FAIL_TO_PASS": _group(list(fail_to_pass or [])),
        "PASS_TO_PASS": _group(list(pass_to_pass or [])),
    }


# --------------------------------------------------------------------------- #
# Cheap offline grader
# --------------------------------------------------------------------------- #
def overlap_grader(task: Task, prediction: str, *, threshold: float = 0.5) -> Optional[bool]:
    """Token-overlap heuristic vs the gold patch. Smoke-test use only.

    Returns ``None`` for QA tasks or when no gold ``patch`` exists (nothing to
    compare). This does **not** run tests; it only signals "the prediction looks
    like the gold change" so the offline pipeline yields a non-trivial number.
    """
    if task.kind is not TaskKind.CODE or not task.patch:
        return None
    pred_tokens = set((prediction or "").split())
    gold_tokens = set(task.patch.split())
    if not pred_tokens or not gold_tokens:
        return False
    overlap = len(pred_tokens & gold_tokens) / len(pred_tokens | gold_tokens)
    return overlap >= threshold


# --------------------------------------------------------------------------- #
# Local-execution grader (host venv, best-effort, honest None; no container runtime)
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class CmdResult:
    """The result of one shell command (the :data:`CmdRunner` contract)."""

    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


#: A command runner: ``runner(args, cwd, env) -> CmdResult``. ``args`` is the
#: full argv (e.g. ``["python", "-m", "pytest", ...]``); ``cwd`` the working dir;
#: ``env`` an optional environment overlay. Default is :func:`_subprocess_cmd`;
#: offline tests inject a fake that returns canned pytest output.
CmdRunner = Callable[..., CmdResult]


def _subprocess_cmd(args: list[str], cwd: Any, env: Optional[dict] = None,
                    *, timeout: int = 1800) -> CmdResult:
    """Default :data:`CmdRunner` — run ``args`` via subprocess (lazy-imported).

    The only place this grader shells out; offline tests inject their own.
    """
    import os
    import subprocess

    full_env = {**os.environ, **(env or {})}
    proc = subprocess.run(
        args, cwd=str(cwd), capture_output=True, text=True,
        timeout=timeout, env=full_env,
    )
    return CmdResult(returncode=proc.returncode, stdout=proc.stdout or "",
                     stderr=proc.stderr or "")


class LocalExecGrader:
    """Grade CODE tasks by applying the patch + gold tests in a fresh host checkout.

    Per call (CODE tasks only):

    1. provision a throwaway checkout of ``task.repo`` @ ``base_commit``;
    2. apply the agent's ``prediction`` patch (empty -> ``False``: a real miss;
       fails to apply -> ``None``: could be base drift, can't grade honestly);
    3. apply the GOLD ``task.test_patch`` (the harness applies tests, NEVER the
       agent — the trust boundary that keeps the number honest);
    4. build a per-task venv best-effort and run ``FAIL_TO_PASS`` + ``PASS_TO_PASS``;
    5. decide RESOLVED by the SWE-bench rule via :func:`resolved_from_report`.

    The CENTRAL honesty rule (ADR-eval-002 §tradeoffs): any inability to build the
    env or run the tests returns ``None`` (UNGRADED), never ``False`` and never a
    crash — so accuracy reflects only what was actually graded. Host-dependent and
    partial-coverage; NOT comparable to a containerized SWE-bench leaderboard.

    Both the command runner and the git runner are injectable so offline tests
    drive the whole flow over a stub repo with canned pytest output.
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

    def __call__(self, task: Task, prediction: str) -> Optional[bool]:
        if task.kind is not TaskKind.CODE:
            return None  # not a CODE task; let QA grading handle it
        # Empty prediction = no patch produced = a real miss (consistent with the
        # overlap grader and SWE-bench's empty_patch handling).
        if not (prediction or "").strip():
            return False
        try:
            return self._grade(task, prediction)
        except Exception:  # noqa: BLE001 - any env/run failure -> UNGRADED, never a crash
            return None

    # -- internals -------------------------------------------------------- #
    def _grade(self, task: Task, prediction: str) -> Optional[bool]:
        import tempfile
        from pathlib import Path

        from .claudecode.checkout import CheckoutError, prepare_checkout

        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "repo"
            git_kwargs = {} if self._git_runner is None else {"git_runner": self._git_runner}
            try:
                prepare_checkout(task.repo or "", task.base_commit, dest,
                                 timeout=self.timeout, **git_kwargs)
            except CheckoutError:
                return None  # can't even check out -> ungraded

            # (2) apply the agent's prediction patch.
            applied = self._apply_patch(dest, prediction)
            if applied is None:
                return None  # patch wouldn't apply (base drift) -> ungraded
            if applied is False:
                return None

            # (3) apply the GOLD test_patch (harness applies tests, never the agent).
            if task.test_patch:
                if self._apply_patch(dest, task.test_patch) is not True:
                    return None  # gold tests won't apply -> can't grade

            # (4) build env + run tests, best-effort -> a SWE-bench tests_status dict.
            status = self._build_and_run(dest, task)
            if status is None:
                return None  # env build / test run failed -> ungraded

            # (5) SWE-bench resolved rule via the shared report interpreter.
            iid = instance_id_of(task)
            return resolved_from_report({iid: {"tests_status": status}}, iid)

    def _apply_patch(self, dest: Any, patch: str) -> Optional[bool]:
        """Write ``patch`` to a temp file and ``git apply`` it in ``dest``.

        Returns ``True`` (applied), ``False`` (empty patch), or ``None`` (apply
        failed). Uses the injectable git runner so offline tests can synthesize the
        apply over the stub repo."""
        import tempfile
        from pathlib import Path

        from .claudecode import checkout as _checkout

        if not (patch or "").strip():
            return False
        git = self._git_runner or _checkout._subprocess_git
        pf = Path(tempfile.gettempdir()) / f"memeval-patch-{abs(hash(patch)) % (10 ** 10)}.patch"
        try:
            pf.write_text(patch if patch.endswith("\n") else patch + "\n", encoding="utf-8")
            res = git(["apply", "--whitespace=nowarn", str(pf)], Path(dest))
            return True if res.returncode == 0 else None
        finally:
            try:
                pf.unlink()
            except OSError:
                pass

    def _build_and_run(self, dest: Any, task: Task) -> Optional[dict]:
        """Best-effort: build a FRESH per-task venv, run the repo's tests with a
        repo-aware command, and return a SWE-bench ``tests_status`` dict
        (``FAIL_TO_PASS``/``PASS_TO_PASS`` -> ``success``/``failure``), or ``None``
        if the env build / run can't yield a gradeable result.

        No Docker: uses ``uv`` for an isolated venv. The test command is dispatched
        per repo — django's ``tests/runtests.py`` (unittest) vs. the default
        ``pytest`` — because SWE-bench repos do not all use pytest. The ENTIRE
        build+run is wrapped so any failure degrades to ``None`` — the honesty rule
        (never a fake ``False`` on an environment problem)."""
        from pathlib import Path

        f2p = list(task.fail_to_pass or [])
        p2p = list(task.pass_to_pass or [])
        selectors = f2p + p2p
        if not selectors:
            return None  # nothing to run -> nothing to grade
        dest = Path(dest)

        # Fresh, isolated venv (best-effort); install the checkout into it.
        py = self._make_venv(dest) or (self._python_exe or "python")
        self._install_repo(dest, py)

        # Repo-aware test command. django uses tests/runtests.py (unittest), not pytest.
        runtests = dest / "tests" / "runtests.py"
        if runtests.exists():
            # Drop non-test-id selectors (prose / leaked docstrings such as
            # ``"Paginator.get_page() with an empty object_list."`` that SWE-bench
            # source data carries in PASS_TO_PASS). Handing them to runtests makes
            # unittest try to import a bogus module and emit a phantom
            # ``ERROR: ... _FailedTest`` (RC=1) that poisons the whole run. We drop
            # them from BOTH the command AND the parsed lists so they neither run
            # nor get scored as a never-ran failure.
            f2p = [s for s in f2p if is_django_selector(s)]
            p2p = [s for s in p2p if is_django_selector(s)]
            valid = f2p + p2p
            if not valid:
                return None  # no runnable selectors left -> nothing to grade
            labels = [django_label(s) for s in valid]
            ran = self._run([py, "tests/runtests.py", "--verbosity=2",
                             "--parallel=1", *labels], dest)
            if ran is None:
                return None
            rc, out, err = ran
            # runtests rc 0 = all passed, 1 = some failed (both gradeable); any
            # other rc with no output = setup/collection error -> ungraded.
            if rc not in (0, 1) and not (out or err):
                return None
            return _parse_django(out, err, f2p, p2p)

        # Default: pytest.
        ran = self._run([py, "-m", "pytest", "-q", *selectors], dest)
        if ran is None:
            return None
        rc, out, _err = ran
        # pytest rc 0 = all passed, 1 = tests failed (still gradeable output);
        # rc >=2 (or no output) = usage/collection error -> can't grade.
        if rc is None or (rc not in (0, 1) and not out):
            return None
        return _parse_pytest(out, f2p, p2p)

    def _run(self, args: list, cwd: Any):
        """Invoke the injectable command runner; return ``(rc, stdout, stderr)`` or
        ``None`` if the runner raised (installer/tool absent, timeout, etc.)."""
        try:
            r = self._runner(args, cwd, None)
        except Exception:  # noqa: BLE001 - tool absent / failed -> ungraded upstream
            return None
        return (getattr(r, "returncode", None),
                getattr(r, "stdout", "") or "",
                getattr(r, "stderr", "") or "")

    def _make_venv(self, dest: Any) -> Optional[str]:
        """Create a fresh ``uv`` venv beside the checkout; return its python exe
        path, or ``None`` if uv is unavailable / failed. Offline (stub runner) this
        is a harmless no-op: the canned runner reports success but creates no files,
        so we fall back to the configured python and still drive the pytest path."""
        from pathlib import Path

        venv = Path(dest).parent / ".venv-grade"
        # ``--clear`` so a re-used parent dir (debug harness, retries) doesn't make
        # ``uv venv`` refuse with "already exists"; harmless on a fresh dir.
        ran = self._run(["uv", "venv", "--clear", str(venv)], dest)
        if ran is None or ran[0] != 0:
            return None
        posix = venv / "bin" / "python"
        if posix.exists():
            return str(posix)
        win = venv / "Scripts" / "python.exe"
        if win.exists():
            return str(win)
        return None  # venv reported OK but no interpreter on disk (offline stub)

    def _install_repo(self, dest: Any, py: str) -> None:
        """Best-effort editable install of the checkout (+ common test extras) into
        the venv ``py``. Failures are tolerated — many repos import from source."""
        for spec in (".[test]", ".[tests]", ".[dev]", "."):
            ran = self._run(["uv", "pip", "install", "--python", py, "-e", spec], dest)
            if ran is not None and ran[0] == 0:
                return
        # Last resort: pip inside the target interpreter.
        self._run([py, "-m", "pip", "install", "-e", "."], dest)


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #
def get_grader(name: str, **kwargs: Any) -> Grader:
    """Resolve a grader by name.

    ``"local"`` / ``"localexec"`` / ``"local-exec"`` -> :class:`LocalExecGrader`
    (kwargs forwarded); ``"overlap"`` -> :func:`overlap_grader`; ``"none"`` -> a
    grader that always returns ``None`` (leave CODE ungraded).
    """
    key = (name or "").strip().lower()
    if key in ("local", "localexec", "local-exec"):
        return LocalExecGrader(**kwargs)
    if key == "overlap":
        return lambda task, pred: overlap_grader(task, pred, **kwargs)
    if key in ("none", "", "off"):
        return lambda task, pred: None
    raise ValueError(f"unknown grader {name!r} (use local / overlap / none)")


__all__ = [
    "Grader",
    "instance_id_of",
    "build_prediction",
    "resolved_from_report",
    "overlap_grader",
    "django_label",
    "is_django_selector",
    "LocalExecGrader",
    "CmdResult",
    "CmdRunner",
    "get_grader",
]
