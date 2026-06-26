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

import contextlib
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Generator, Optional, TypeVar


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


#: A sleep callable ``sleep(seconds) -> None``. Injectable so offline tests run
#: with no real delay (default :func:`time.sleep`). Used only by :func:`_retry`.
Sleeper = Callable[[float], None]

_T = TypeVar("_T")


def _retry(
    fn: Callable[[], _T],
    *,
    attempts: int = 3,
    backoff: float = 1.0,
    sleep: Sleeper = time.sleep,
) -> _T:
    """Call ``fn`` up to ``attempts`` times, retrying on :class:`CheckoutError`.

    Between failed attempts it sleeps ``backoff * 2**n`` seconds (1s/2s/4s with the
    default ``backoff``) — exponential backoff for a transient network blip. The
    final attempt's failure re-raises. ``sleep`` is injected so offline tests run
    with no real delay, and it is invoked ONLY between failed attempts (never after
    success, never after the last attempt), so a single-attempt or first-try-success
    call never sleeps. Defense-in-depth: it only engages on an *error* — a step that
    already succeeds is unchanged (one call, no sleep).
    """
    last: Optional[CheckoutError] = None
    for n in range(max(1, attempts)):
        try:
            return fn()
        except CheckoutError as exc:
            last = exc
            if n + 1 < attempts:
                sleep(backoff * (2 ** n))
    assert last is not None  # unreachable: the loop runs >= 1 time
    raise last


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
    retries: int = 1,
    sleep: Sleeper = time.sleep,
) -> Path:
    """Materialize a working checkout of ``repo`` at ``base_commit`` into ``dest``.

    Every git op goes through ``git_runner``; a non-zero ``returncode`` raises
    :class:`CheckoutError`. Returns the resolved ``dest`` path.

    ``strategy``:

    * ``"auto"`` / ``"shallow"`` (default): ``git init`` -> ``remote add origin
      <url>`` -> shallow ``fetch`` of ``base_commit`` (or ``HEAD``) -> checkout.
      The injected stub runner can satisfy each step by writing files to ``dest``
      itself, so this works fully offline.
    * ``"local"``: ``git clone --shared <repo>`` from a local path — the
      network-free cached-mirror path the grader uses when ``MEMEVAL_REPO_CACHE``
      is set (``repo`` is then a bare mirror under that cache; see
      :func:`ensure_mirror`).

    ``retries`` (>1) wraps ONLY the network ``fetch`` (auto path) in
    :func:`_retry` with exponential backoff via ``sleep`` — defense-in-depth for a
    transient github blip. The default ``retries=1`` is the historical behavior
    (one attempt, no sleep); the local path makes no network call and is never
    retried here.
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
        # Cached-mirror path: clone a local repo by reference (network-free). Wipe a
        # stale tree first for rerun-robustness — symmetric with the auto path below —
        # since ``git clone --shared . `` refuses a non-empty dest (e.g. a half-clone
        # from a prior failed attempt). Guarded on a real ``.git`` so an injected stub
        # runner's pre-seeded dest is untouched.
        if (dest_path / ".git").exists():
            shutil.rmtree(dest_path, ignore_errors=True)
            dest_path.mkdir(parents=True, exist_ok=True)
        _run(["clone", "--shared", str(repo), "."])
        if base_commit:
            _run(["checkout", str(base_commit)])
        return dest_path.resolve()

    # auto / shallow: init + fetch-by-SHA + checkout (network on the real path;
    # fully synthesizable by an injected stub runner offline).
    #
    # Idempotency on a reused dest (rerun robustness): with a stable working dir
    # (``--out-dir`` set) the checkout path persists across runs, so a second run
    # finds a populated ``.git`` here. ``git init`` is harmless on an existing repo
    # but ``git remote add origin`` then fails rc=3 "remote origin already exists",
    # which raises CheckoutError -> empty prediction -> every task scores 0. Wipe a
    # stale checkout so each run starts from a clean tree (also clears a stale index
    # / FETCH_HEAD). Guarded on a *real* ``.git`` so the offline stub-runner path —
    # where the injected runner writes files into a dest that has no real ``.git`` —
    # is untouched. Safe: the plugin memory store does NOT live under the checkout — it
    # is the shared substrate the plugin resolves from ``CLAUDE_PROJECT_DIR`` (ADR-eval-003),
    # so wiping the checkout never touches memory.
    if (dest_path / ".git").exists():
        shutil.rmtree(dest_path, ignore_errors=True)
        dest_path.mkdir(parents=True, exist_ok=True)

    _run(["init"])
    _run(["remote", "add", "origin", _normalize_repo_url(repo)])
    ref = str(base_commit) if base_commit else "HEAD"
    # The fetch is the only network step; retry it with backoff (defense-in-depth for
    # a transient github connection timeout). With the default retries=1 this is one
    # call, no sleep — byte-identical to before.
    _retry(lambda: _run(["fetch", "--depth", "1", "origin", ref]),
           attempts=retries, sleep=sleep)
    _run(["checkout", "FETCH_HEAD"])
    return dest_path.resolve()


# --------------------------------------------------------------------------- #
# Persistent local mirror (opt-in via MEMEVAL_REPO_CACHE; env read at the grader
# edge). A bare mirror per repo turns each per-task checkout into a network-free
# local clone, so a single github blip during a multi-hour run can no longer drop
# a task. The env read lives in the grader; these helpers stay pure + injectable.
# --------------------------------------------------------------------------- #
def _owner_name(repo: str) -> tuple[str, str]:
    """Derive ``(owner, name)`` from ``owner/name`` or a full git URL.

    Accepts the SWE-bench ``owner/name`` short form and the URL forms
    :func:`_normalize_repo_url` would emit/accept (``https://github.com/o/n(.git)``,
    ``git@github.com:o/n.git``, ``ssh://…``). Raises :class:`CheckoutError` if it
    cannot find an ``owner/name`` pair.
    """
    r = (repo or "").strip()
    if r.endswith(".git"):
        r = r[:-4]
    # Drop the scheme / user@host prefix of a URL, leaving the host+path tail.
    if "://" in r:
        r = r.split("://", 1)[1]
    elif "@" in r:
        r = r.split("@", 1)[1]
    # ``host:owner/name`` (scp-like) -> ``host/owner/name``; then take the last two.
    parts = [p for p in r.replace(":", "/").split("/") if p]
    if len(parts) < 2:
        raise CheckoutError(f"cannot derive owner/name from repo {repo!r}")
    return parts[-2], parts[-1]


def mirror_path_for(repo: str, cache_dir: str | Path) -> Path:
    """Map ``repo`` to its bare-mirror path ``<cache_dir>/<owner>__<name>.git``.

    ``pydata/xarray`` and ``https://github.com/pydata/xarray(.git)`` both map to the
    same ``<cache_dir>/pydata__xarray.git``, so the short form and the URL form share
    one mirror.
    """
    owner, name = _owner_name(repo)
    return Path(cache_dir) / f"{owner}__{name}.git"


@contextlib.contextmanager
def _file_lock(lock_path: Path) -> Generator[None, None, None]:
    """Best-effort exclusive lock on ``lock_path`` via ``fcntl.flock`` (POSIX).

    Serializes concurrent mirror clones into the same path so parallel grading can't
    race two clones. A no-op where ``fcntl`` is unavailable (non-POSIX) — the
    clone-into-temp + atomic ``os.replace`` in :func:`ensure_mirror` still prevents a
    half-mirror from being observed at the final path even without the lock.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl  # POSIX-only; lazy so the module imports on any platform
    except ImportError:  # pragma: no cover - non-POSIX fallback
        yield
        return
    f = open(lock_path, "w", encoding="utf-8")
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        finally:
            f.close()


def _is_mirror(path: Path) -> bool:
    """True iff ``path`` looks like a populated bare mirror (has a ``HEAD``)."""
    return (path / "HEAD").exists()


def ensure_mirror(
    repo: str,
    cache_dir: str | Path,
    *,
    git_runner: GitRunner = _subprocess_git,
    retries: int = 3,
    sleep: Sleeper = time.sleep,
) -> Path:
    """Ensure a bare mirror of ``repo`` exists under ``cache_dir`` and return its path.

    A mirror that already exists is reused with **zero network** — this is the warm
    path every per-task checkout takes. If absent, ``git clone --mirror <url>`` runs
    ONCE (the only network cost, amortized across every task of that repo), retried
    with backoff. Concurrent graders are serialized by an ``flock`` on
    ``<mirror>.lock`` and the clone lands in a temp dir then atomically replaces the
    final path, so a race or a killed clone never leaves a half-mirror behind.
    """
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    mirror = mirror_path_for(repo, cache)
    if _is_mirror(mirror):
        return mirror
    url = _normalize_repo_url(repo)
    with _file_lock(cache / (mirror.name + ".lock")):
        # Re-check inside the lock: another worker may have created it while we waited.
        if _is_mirror(mirror):
            return mirror
        tmp = mirror.parent / (mirror.name + ".tmp")
        shutil.rmtree(tmp, ignore_errors=True)

        def _clone() -> GitResult:
            res = git_runner(["clone", "--mirror", url, str(tmp)], cache)
            if res.returncode != 0:
                raise CheckoutError(
                    f"git clone --mirror {url} failed (rc={res.returncode}): "
                    f"{(res.stderr or res.stdout or '').strip()[:300]}"
                )
            return res

        _retry(_clone, attempts=retries, sleep=sleep)
        import os  # lazy: only the mirror path touches the fs beyond Path/shutil
        os.replace(tmp, mirror)
    return mirror


def update_mirror(
    mirror: str | Path,
    *,
    git_runner: GitRunner = _subprocess_git,
    retries: int = 3,
    sleep: Sleeper = time.sleep,
) -> None:
    """Best-effort ``git remote update`` on an existing bare ``mirror`` (network,
    retried with backoff). Refreshes a stale mirror so a ``base_commit`` pushed
    after the mirror was first cloned becomes available for a local checkout. A
    non-zero return raises :class:`CheckoutError` (the caller decides whether to fall
    back to the network path).
    """
    mirror_path = Path(mirror)

    def _update() -> GitResult:
        res = git_runner(["remote", "update"], mirror_path)
        if res.returncode != 0:
            raise CheckoutError(
                f"git remote update failed (rc={res.returncode}): "
                f"{(res.stderr or res.stdout or '').strip()[:300]}"
            )
        return res

    _retry(_update, attempts=retries, sleep=sleep)


#: Paths excluded from the captured PREDICTION diff. Agentic CODE runs memory
#: substrates inside the checkout: plugin-real uses ``.cookbook-memory`` and builtin
#: memory writes ``CLAUDE.md`` plus ``sessions/``. ``git add -A`` would otherwise
#: stage those memory artifacts and corrupt the SWE-bench patch. These are git
#: pathspecs (``:(exclude)…``) applied to BOTH the stage and the diff so tracked AND
#: untracked memory content is kept out; real source-file changes are unaffected.
_PREDICTION_DIFF_EXCLUDES = (".cookbook-memory", "CLAUDE.md", "sessions")


def _exclude_pathspecs() -> list[str]:
    """git ``:(exclude)`` pathspecs for :data:`_PREDICTION_DIFF_EXCLUDES`."""
    return [f":(exclude){p}" for p in _PREDICTION_DIFF_EXCLUDES]


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

    The plugin store dir (:data:`_PREDICTION_DIFF_EXCLUDES`, e.g. ``.cookbook-memory``)
    is excluded from both the stage and the diff via git ``:(exclude)`` pathspecs, so
    the prediction is the clean CODE patch — the memory store living inside the checkout
    never pollutes it. Real source-file changes are captured in full.
    """
    dest_path = Path(dest)
    excludes = _exclude_pathspecs()
    try:
        # Stage everything EXCEPT the store dir. "." + excludes (not "-A") so the
        # exclude pathspec applies; "." stages new + modified + deleted under cwd.
        add = git_runner(["add", "--", ".", *excludes], dest_path)
        if add.returncode != 0:
            # Fallback: some git versions/edge cases dislike the combined pathspec.
            # A plain add still works; the diff-side exclude below is the real guard.
            add = git_runner(["add", "-A"], dest_path)
            if add.returncode != 0:
                return ""
        res = git_runner(["diff", "--cached", "--", ".", *excludes], dest_path)
        if res.returncode != 0:
            return ""
        return res.stdout or ""
    except Exception:  # noqa: BLE001 - a diff we can't read is "no change", not an error
        return ""


__all__ = [
    "prepare_checkout",
    "capture_diff",
    "ensure_mirror",
    "update_mirror",
    "mirror_path_for",
    "GitResult",
    "GitRunner",
    "CheckoutError",
]
