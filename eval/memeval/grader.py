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

import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

from .schema import Task, TaskKind

Grader = Callable[[Task, str], Optional[bool]]

#: Logger for grading diagnostics. Every UNGRADED (``None``) result is logged at
#: WARNING with a specific reason so a CODE run that "comes back empty" is no longer
#: silent -- the operator can see WHY each task could not be graded.
log = logging.getLogger(__name__)


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

    Pure + stdlib-only (unit-testable). Reads pytest's ``-rA`` short-summary, whose
    lines are ``<STATUS> <nodeid>`` (e.g. ``PASSED testing/x.py::test_y``). For each
    named selector, the verdict is the STATUS word at the START of a summary line
    whose nodeid matches the selector — NOT a substring scan, because a node id can
    itself contain ``failed``/``error`` (``...::test_failed`` would otherwise be
    misread as a failure). A selector pytest never reported on (collection error,
    not found) counts as a failure — it did not pass, which the SWE-bench resolved
    rule treats as not-resolved.
    """
    out = stdout or ""
    # pytest -rA summary line statuses, in precedence order (a not-passed status wins
    # so a test reported both ways is conservatively not a pass).
    _PASS_WORDS = ("PASSED", "XPASS")
    _NOTPASS_WORDS = ("FAILED", "ERROR", "XFAIL", "SKIPPED")

    def _status_for(selector: str) -> Optional[str]:
        # Find a summary line "<STATUS> <nodeid...>" whose nodeid equals/extends the
        # selector. Match on the line's leading STATUS token only.
        for line in out.splitlines():
            stripped = line.strip()
            parts = stripped.split(None, 1)
            if len(parts) != 2:
                continue
            status, rest = parts[0].upper(), parts[1].strip()
            if status not in _PASS_WORDS and status not in _NOTPASS_WORDS:
                continue
            # rest is the nodeid (possibly with trailing reason text after a space or
            # ' - '); accept an exact match or a nodeid that starts with the selector.
            node = rest.split(" - ", 1)[0].split()[0] if rest else ""
            if node == selector or node.startswith(selector + "["):
                return status
        return None

    def _passed(selector: str) -> bool:
        status = _status_for(selector)
        return status in _PASS_WORDS

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


def is_pytest_selector(selector: str) -> bool:
    """True iff ``selector`` is a *runnable* pytest node id, not captured junk.

    Two failure shapes the SWE-bench-CL ``FAIL_TO_PASS`` / ``PASS_TO_PASS`` lists
    carry, both of which abort the WHOLE pytest run (rc=4, ``no tests ran``) so that
    every real selector then scores as a never-ran failure:

    * **Progress-output fragments** — e.g. ``"[100%]"`` (a captured progress bar).
      Pytest treats it as a missing file path. Rejected: no ``::``.
    * **Truncated parametrized ids** — when a parametrize id contains ``", "`` the
      upstream capture split on the comma and stored only the prefix, leaving an
      UNBALANCED bracket: ``test_xfail_raises[(AttributeError,`` or
      ``test_skipif_reporting["hasattr(sys,``. The full id is unrecoverable, and
      pytest reports ``ERROR: not found`` for it. Rejected: ``[`` count != ``]``.

    A genuine selector is a node id containing ``::`` (``path::Class::test`` or
    ``path::test[param]``) with balanced ``[]``. Dropping the unrecoverable ones
    (like the django prose filter) keeps the run grading on the selectors that CAN
    run — honest as long as ``FAIL_TO_PASS`` survives; a task whose F2P is entirely
    junk has nothing to resolve and degrades to ungraded upstream. String-only
    (unit-tested)."""
    s = (selector or "").strip()
    if not s or "::" not in s:
        return False
    return s.count("[") == s.count("]")  # reject truncated parametrized ids


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
# Per-task environment resolution (Python pin + version-gate workaround)
# --------------------------------------------------------------------------- #
#: Interpreter pins for repos whose pinned commits predate the host Python. SWE-bench
#: instances run at a historical ``base_commit`` whose code assumes a then-current
#: interpreter; a host-default venv (e.g. 3.12) breaks them — 2019-era pytest does
#: ``import imp`` (gone in 3.12). Keyed by ``owner/name`` lowercased. Best-effort and
#: deliberately conservative: an unlisted repo returns ``None`` (host default, the
#: prior behavior), never a wrong guess. Refine per-instance if the dataset ever
#: carries an explicit ``environment_setup_commit`` / python version.
_REPO_PYTHON_PIN: dict[str, str] = {
    "pytest-dev/pytest": "3.8",
}


def _python_for_task(task: Task) -> Optional[str]:
    """Resolve the interpreter version to pin for ``task``'s venv, or ``None`` for
    the host default. Currently a repo-level map (:data:`_REPO_PYTHON_PIN`); honors
    an explicit ``task.metadata['python']`` override if a dataset ever supplies one."""
    explicit = (task.metadata or {}).get("python")
    if explicit:
        return str(explicit)
    return _REPO_PYTHON_PIN.get((task.repo or "").strip().lower())


def _scm_env(task: Task) -> dict[str, str]:
    """Environment overlay that neutralizes setuptools-scm version gates (install time).

    A shallow ``--depth 1`` checkout carries no git tags, so setuptools-scm computes
    a bogus ``0.1.dev1+g<sha>`` version; projects that gate on their own
    ``minversion`` (pytest's ``tox.ini`` / ``pyproject.toml`` ``requires
    pytest-2.0``) then refuse to run (pytest rc=4). Pretending a high version
    satisfies the gate without affecting test behavior. Keyed by the distribution's
    scm env var; we set the generic one plus pytest's specific one."""
    pretend = "9999.0.0"
    return {
        "SETUPTOOLS_SCM_PRETEND_VERSION": pretend,
        "SETUPTOOLS_SCM_PRETEND_VERSION_FOR_PYTEST": pretend,
    }


def _test_run_env(task: Task) -> dict[str, str]:
    """Environment overlay for the TEST-RUN step (a superset of :func:`_scm_env`).

    Adds ``PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`` so third-party pytest plugins that the
    *modern* toolchain drags into the venv don't auto-register and crash an old
    pytest. Concretely: current ``setuptools`` vendors a ``typeguard`` pytest plugin
    whose ``pytest_addoption`` calls ``parser.addini(type="string")``, but 2019-era
    pytest's ``argparsing.py`` only allows ``(None, pathlist, args, linelist, bool)``
    and asserts — pytest exits rc=1 at load before any test runs. Disabling autoload
    loads only the repo's own pytest, which is what SWE-bench grading wants anyway."""
    return {**_scm_env(task), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}


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
        #: Reason the MOST RECENT call returned ``None`` (UNGRADED), or ``None`` when
        #: the last call produced a real verdict. Read by callers (run_bench / the
        #: evaluators) to surface why a CODE task could not be graded.
        self.last_reason: Optional[str] = None
        #: Run-lifetime tally of UNGRADED reasons (reason -> count). Lets a driver
        #: report, e.g., "12/15 ungraded: env build failed" instead of a silent 0.0.
        self.ungraded_reasons: dict[str, int] = {}

    def _ungraded(self, reason: str, task: Optional[Task] = None) -> None:
        """Record + log an UNGRADED (``None``) outcome and return ``None``.

        Centralizes the honesty-rule degradation: keeps ``None`` (never a fake
        ``False``), but makes it LOUD -- sets :attr:`last_reason`, tallies
        :attr:`ungraded_reasons`, and logs at WARNING so an empty CODE run is
        explainable. Returns ``None`` so callers can ``return self._ungraded(...)``.
        """
        self.last_reason = reason
        self.ungraded_reasons[reason] = self.ungraded_reasons.get(reason, 0) + 1
        tid = getattr(task, "task_id", None) or "?"
        log.warning("LocalExecGrader UNGRADED [task=%s]: %s", tid, reason)
        return None

    def __call__(self, task: Task, prediction: str) -> Optional[bool]:
        self.last_reason = None  # reset per call; set only on a None (ungraded) path
        if task.kind is not TaskKind.CODE:
            return None  # not a CODE task; let QA grading handle it (not a degradation)
        # Empty prediction = no patch produced = a real miss (consistent with the
        # overlap grader and SWE-bench's empty_patch handling).
        if not (prediction or "").strip():
            return False
        try:
            return self._grade(task, prediction)
        except Exception as exc:  # noqa: BLE001 - any env/run failure -> UNGRADED, never a crash
            return self._ungraded(f"exception: {type(exc).__name__}: {exc}", task)

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
            except CheckoutError as exc:
                return self._ungraded(f"checkout failed: {exc}", task)

            # (2) apply the agent's prediction patch.
            applied = self._apply_patch(dest, prediction)
            if applied is None:
                return self._ungraded("prediction patch did not apply (base drift)", task)
            if applied is False:
                return self._ungraded("prediction patch was empty/no-op after strip", task)

            # (3) apply the GOLD test_patch (harness applies tests, never the agent).
            if task.test_patch:
                if self._apply_patch(dest, task.test_patch) is not True:
                    return self._ungraded("gold test_patch did not apply", task)

            # (4) build env + run tests, best-effort -> a SWE-bench tests_status dict.
            status = self._build_and_run(dest, task)
            if status is None:
                # _build_and_run already set a specific last_reason via _ungraded;
                # add a generic one only if some other path left it unset.
                if self.last_reason is None:
                    return self._ungraded("env build / test run produced no gradeable status", task)
                return None

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
            return self._ungraded("no FAIL_TO_PASS/PASS_TO_PASS selectors to run", task)
        dest = Path(dest)

        # Fresh, isolated venv (best-effort), pinned to a task-appropriate Python so
        # historical repo commits run on a compatible interpreter; install the
        # checkout into it. ``scm_env`` neutralizes setuptools-scm minversion gates
        # that a tagless shallow checkout would otherwise trip; ``test_env`` adds the
        # plugin-autoload guard for the actual test run (see _test_run_env).
        scm_env = _scm_env(task)
        test_env = _test_run_env(task)
        py = self._make_venv(dest, python=_python_for_task(task)) or (
            self._python_exe or "python")
        self._install_repo(dest, py, env=scm_env)

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
                return self._ungraded("no runnable django selectors after filtering", task)
            labels = [django_label(s) for s in valid]
            ran = self._run([py, "tests/runtests.py", "--verbosity=2",
                             "--parallel=1", *labels], dest, env=test_env)
            if ran is None:
                return self._ungraded("django test runner unavailable/raised", task)
            rc, out, err = ran
            # runtests rc 0 = all passed, 1 = some failed (both gradeable); any
            # other rc with no output = setup/collection error -> ungraded.
            if rc not in (0, 1) and not (out or err):
                return self._ungraded(f"django runtests setup/collection error (rc={rc})", task)
            return _parse_django(out, err, f2p, p2p)

        # Default: pytest.
        # Drop captured-junk selectors (e.g. a leaked ``[100%]`` progress token) from
        # BOTH the command AND the parsed lists — handing one to pytest aborts the
        # whole run (rc=4, "no tests ran"), poisoning every real selector. Mirrors the
        # django prose-filter above.
        f2p = [s for s in f2p if is_pytest_selector(s)]
        p2p = [s for s in p2p if is_pytest_selector(s)]
        selectors = f2p + p2p
        if not selectors:
            return self._ungraded("no runnable pytest selectors after filtering", task)
        # ``-rA`` emits an explicit per-test summary (``PASSED <nodeid>`` /
        # ``FAILED <nodeid>``) that :func:`_parse_pytest` reads; plain ``-q`` prints
        # only dots, which the parser can't attribute to a selector (so every test
        # would score as a never-passed failure).
        ran = self._run([py, "-m", "pytest", "-rA", *selectors], dest, env=test_env)
        if ran is None:
            return self._ungraded("pytest runner unavailable/raised", task)
        rc, out, _err = ran
        # pytest rc 0 = all passed, 1 = tests failed (still gradeable output);
        # rc >=2 (or no output) = usage/collection error -> can't grade.
        if rc is None or (rc not in (0, 1) and not out):
            return self._ungraded(f"pytest usage/collection error (rc={rc})", task)
        return _parse_pytest(out, f2p, p2p)

    def _run(self, args: list, cwd: Any, *, env: Optional[dict] = None):
        """Invoke the injectable command runner; return ``(rc, stdout, stderr)`` or
        ``None`` if the runner raised (installer/tool absent, timeout, etc.). ``env``
        is an overlay merged onto the process environment by the runner."""
        try:
            r = self._runner(args, cwd, env)
        except Exception:  # noqa: BLE001 - tool absent / failed -> ungraded upstream
            return None
        return (getattr(r, "returncode", None),
                getattr(r, "stdout", "") or "",
                getattr(r, "stderr", "") or "")

    def _make_venv(self, dest: Any, *, python: Optional[str] = None) -> Optional[str]:
        """Create a fresh ``uv`` venv beside the checkout; return its python exe
        path, or ``None`` if uv is unavailable / failed. Offline (stub runner) this
        is a harmless no-op: the canned runner reports success but creates no files,
        so we fall back to the configured python and still drive the pytest path.

        ``python`` pins the interpreter version (e.g. ``"3.8"``); ``uv`` fetches it
        if absent. This matters because SWE-bench tasks are pinned to historical
        repo commits whose code does not run on a current interpreter — e.g.
        2019-era pytest does ``import imp``, removed in Python 3.12, so collection
        crashes on a host-default venv. ``None`` -> host default (legacy behavior)."""
        from pathlib import Path

        venv = Path(dest).parent / ".venv-grade"
        # ``--clear`` so a re-used parent dir (debug harness, retries) doesn't make
        # ``uv venv`` refuse with "already exists"; harmless on a fresh dir.
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

    def _install_repo(self, dest: Any, py: str, *, env: Optional[dict] = None) -> None:
        """Best-effort editable install of the checkout (+ common test extras) into
        the venv ``py``. Failures are tolerated — many repos import from source.
        ``env`` overlays the install environment (e.g. setuptools-scm pretend
        version, so a tagless shallow checkout produces a sane package version)."""
        for spec in (".[test]", ".[tests]", ".[dev]", "."):
            ran = self._run(["uv", "pip", "install", "--python", py, "-e", spec], dest,
                            env=env)
            if ran is not None and ran[0] == 0:
                return
        # Last resort: pip inside the target interpreter.
        self._run([py, "-m", "pip", "install", "-e", "."], dest, env=env)


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #
def get_grader(name: str, **kwargs: Any) -> Grader:
    """Resolve a grader by name.

    ``"local"`` / ``"localexec"`` / ``"local-exec"`` -> :class:`LocalExecGrader`
    (kwargs forwarded); ``"swebench"`` / ``"swebench-host"`` / ``"swebenchhost"``
    -> :class:`memeval.grader_swebench.SwebenchHostGrader` (reuses SWE-bench's own
    env specs + official log parsers in a host ``uv`` venv; needs the optional
    ``swebench`` extra — imported lazily so it stays optional); ``"overlap"`` ->
    :func:`overlap_grader`; ``"none"`` -> a grader that always returns ``None``
    (leave CODE ungraded).
    """
    key = (name or "").strip().lower()
    if key in ("local", "localexec", "local-exec"):
        return LocalExecGrader(**kwargs)
    if key in ("swebench", "swebench-host", "swebenchhost"):
        # Lazy import so swebench remains an OPTIONAL dependency (no hard import at
        # module load of this grader registry).
        from .grader_swebench import SwebenchHostGrader

        return SwebenchHostGrader(**kwargs)
    if key == "overlap":
        return lambda task, pred: overlap_grader(task, pred, **kwargs)
    if key in ("none", "", "off"):
        return lambda task, pred: None
    raise ValueError(
        f"unknown grader {name!r} (use local / swebench / overlap / none)")


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
