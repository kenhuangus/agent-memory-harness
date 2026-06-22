"""Git checkout + diff capture for the agentic CODE path — injectable runner.

The agentic CODE path (``ClaudeCodeAgent._solve_code_agentic``) and the
:class:`~memeval.grader.LocalExecGrader` both need to (a) materialize a working
checkout of a task's repo at its ``base_commit`` and (b) read back the unified
diff of whatever changed in the tree. This module provides both as small,
stdlib-only functions behind an **injectable git runner** seam.

The seam is the whole point: every git operation goes through a
:data:`GitRunner` callable (default :func:`_subprocess_git`, the only place that
touches ``subprocess``/``git``). Offline tests inject a fake runner that
*materializes files on disk itself* and returns success — so the full
checkout → edit → diff loop is exercised with **no network, no real git, and no
real ``claude``**. The functions never assume the runner shelled out; they only
inspect ``returncode``/``stdout``.

Risk / honesty note: the real (network) path needs a GitHub fetch-by-SHA against
a live repo. Offline correctness is proven only via the injected stub runner; a
real swe_contextbench run additionally needs network + a buildable repo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional


@dataclass(slots=True)
class GitResult:
    """The result of one git invocation (the :data:`GitRunner` contract).

    Explicit so test doubles don't need to import ``subprocess`` or build a
    ``CompletedProcess`` — they just return one of these.
    """

    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


#: A git runner: ``runner(args, cwd) -> GitResult``. ``args`` are the git
#: sub-arguments WITHOUT a leading ``git`` (e.g. ``["init"]``, ``["diff",
#: "--cached"]``). ``cwd`` is the directory the command runs in. The default is
#: :func:`_subprocess_git`; offline tests inject their own.
GitRunner = Callable[..., GitResult]


class CheckoutError(RuntimeError):
    """A git step needed to prepare a checkout failed (non-zero return)."""


def _subprocess_git(args: list[str], cwd: Path, *, timeout: int = 600) -> GitResult:
    """Default :data:`GitRunner` — run ``git <args>`` in ``cwd`` via subprocess.

    The ONLY place this module touches ``subprocess``/``git`` (lazy-imported so
    the module stays import-clean for the offline path). Offline tests never
    reach here — they inject their own runner.
    """
    import subprocess  # lazy: heavy dep kept off the import path

    proc = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True,
        timeout=timeout,
    )
    return GitResult(returncode=proc.returncode, stdout=proc.stdout or "",
                     stderr=proc.stderr or "")


def _normalize_repo_url(repo: str) -> str:
    """``owner/name`` -> ``https://github.com/owner/name``; a URL passes through."""
    r = (repo or "").strip()
    if r.startswith(("http://", "https://", "git@", "ssh://", "file://")):
        return r
    return f"https://github.com/{r}"


def prepare_checkout(
    repo: str,
    base_commit: Optional[str],
    dest: str | Path,
    *,
    git_runner: GitRunner = _subprocess_git,
    strategy: str = "auto",
    timeout: int = 600,
) -> Path:
    """Materialize a working checkout of ``repo`` at ``base_commit`` into ``dest``.

    Every git op goes through ``git_runner``; a non-zero ``returncode`` raises
    :class:`CheckoutError`. Returns the resolved ``dest`` path.

    ``strategy``:

    * ``"auto"`` / ``"shallow"`` (default): ``git init`` -> ``remote add origin
      <url>`` -> shallow ``fetch`` of ``base_commit`` (or ``HEAD``) -> checkout.
      The injected stub runner can satisfy each step by writing files to ``dest``
      itself, so this works fully offline.
    * ``"local"``: ``git clone --shared <repo>`` from a local path (documented
      future cached-mirror path; not the default, not on the offline surface).
    """
    dest_path = Path(dest)
    dest_path.mkdir(parents=True, exist_ok=True)

    def _run(args: list[str]) -> GitResult:
        res = git_runner(args, dest_path)
        if res.returncode != 0:
            raise CheckoutError(
                f"git {' '.join(args)} failed (rc={res.returncode}): "
                f"{(res.stderr or res.stdout or '').strip()[:300]}"
            )
        return res

    if strategy == "local":
        # Cached-mirror path: clone a local repo by reference. Not the default and
        # not exercised on the offline test surface (kept for a future mirror).
        _run(["clone", "--shared", str(repo), "."])
        if base_commit:
            _run(["checkout", str(base_commit)])
        return dest_path.resolve()

    # auto / shallow: init + fetch-by-SHA + checkout (network on the real path;
    # fully synthesizable by an injected stub runner offline).
    _run(["init"])
    _run(["remote", "add", "origin", _normalize_repo_url(repo)])
    ref = str(base_commit) if base_commit else "HEAD"
    _run(["fetch", "--depth", "1", "origin", ref])
    _run(["checkout", "FETCH_HEAD"])
    return dest_path.resolve()


def capture_diff(
    dest: str | Path,
    *,
    base_commit: Optional[str] = None,  # noqa: ARG001 - kept for call-site symmetry
    git_runner: GitRunner = _subprocess_git,
    timeout: int = 120,  # noqa: ARG001 - forwarded via the default runner's own default
) -> str:
    """Return the unified diff of all staged+unstaged changes in ``dest``, or ''.

    Stages everything (``git add -A``) then returns ``git diff --cached`` stdout
    — the raw git patch (already clean; NOT routed through ``_extract_diff``).
    Prefers ``--cached`` over ``diff <base_commit>`` so it works whether or not a
    real commit SHA exists in the (possibly stub) checkout. An empty tree yields
    ``""`` (an honest empty patch); any runner failure also yields ``""`` — a
    missing diff is "no change", never a crash.
    """
    dest_path = Path(dest)
    try:
        add = git_runner(["add", "-A"], dest_path)
        if add.returncode != 0:
            return ""
        res = git_runner(["diff", "--cached"], dest_path)
        if res.returncode != 0:
            return ""
        return res.stdout or ""
    except Exception:  # noqa: BLE001 - a diff we can't read is "no change", not an error
        return ""


__all__ = [
    "prepare_checkout",
    "capture_diff",
    "GitResult",
    "GitRunner",
    "CheckoutError",
]
