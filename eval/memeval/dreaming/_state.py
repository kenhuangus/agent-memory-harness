"""Per-session Daydream state — sidecar I/O, flock, TTL sweep, current-session touch.

This module is the filesystem-state layer of the Daydream engine. Public surface
is consumed by :mod:`memeval.dreaming.engine`; nothing here is intended for
callers outside the dreaming package.

Pinned ADRs:
- ADR-dreaming-013 (cursor-advance ordering — sidecar is the last persistent op).
- ADR-dreaming-014 (per-session flock; ``fcntl.flock`` is the chosen primitive).
- ADR-dreaming-015 (filesystem state management — basedir resolution + TTL sweep).
- ADR-harness-004 (sidecar shape + per-session path convention).
- ADR-harness-006 (fail-open everywhere except ``resolve_basedir``).

Heavy deps are stdlib-only here; no third-party imports.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import re
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Mapping, TypedDict

from memeval.dreaming.events import diary_path_for, emit
from memeval.dreaming.redaction._audit import audit_path_for, write_audit_record

__all__ = [
    "MAX_AUDIT_LINES_PER_FILE",
    "RECENT_MEMORY_CAP",
    "SidecarState",
    "_DreamLockHeld",
    "_LockHeld",
    "_UnsupportedFsError",
    "_basedir_dream_lock",
    "_is_network_fs",
    "_per_session_lock",
    "_sanity_check_cursor",
    "_touch_current_session_files",
    "_write_audit_fail_open",
    "_write_sidecar_atomic",
    "audit_path",
    "load_sidecar",
    "lock_path",
    "resolve_basedir",
    "safe_session_stem",
    "sidecar_path",
    "sweep_old_state",
]

_logger = logging.getLogger(__name__)

RECENT_MEMORY_CAP: int = 50
"""Maximum number of recent ``MemoryItem.item_id`` values retained in the sidecar."""

MAX_AUDIT_LINES_PER_FILE: int | None = None
"""Forward-defense seam for ADR-011 audit-file rotation. ``None`` disables rotation."""

_SECONDS_PER_DAY: int = 86400
_SECONDS_PER_MINUTE: int = 60
_LAST_SWEPT_MARKER: str = ".last-swept"
_DREAM_SUBDIR: str = "dream"


# --------------------------------------------------------------------------- #
# Sidecar state shape
# --------------------------------------------------------------------------- #
class SidecarState(TypedDict):
    """Persistent per-session Daydream state — see ADR-harness-004 + ADR-dreaming-013."""

    cursor: int
    last_summary: str | None
    recent_memory_ids: list[str]
    first_bytes_hash: str | None


def _default_state() -> SidecarState:
    """Return the canonical empty sidecar state used on missing/corrupt files."""
    return SidecarState(
        cursor=0,
        last_summary=None,
        recent_memory_ids=[],
        first_bytes_hash=None,
    )


# --------------------------------------------------------------------------- #
# Path helpers
# --------------------------------------------------------------------------- #
def resolve_basedir() -> Path:
    """Resolve the Daydream basedir from ``$MEMORY_STORE`` per ADR-019.

    Reads ``os.environ["MEMORY_STORE"]`` (raises ``KeyError`` if unset),
    resolves symlinks via ``.resolve()``, raises ``ValueError`` if it
    points to an existing regular file (the inverted error mode from
    ADR-015 §1, which is superseded), creates the directory idempotently
    with ``mkdir(parents=True, exist_ok=True)`` if missing, and returns
    the directory itself.

    ``KeyError`` and ``ValueError`` are the ONLY non-fail-open exits —
    the engine deliberately does not swallow them. The PR5+ plugin shim
    is responsible for handling them at the harness boundary.
    """
    raw = os.environ["MEMORY_STORE"]
    basedir = Path(raw).resolve()
    if basedir.exists() and not basedir.is_dir():
        raise ValueError(
            f"MEMORY_STORE must be a directory, got a file: {basedir}"
        )
    basedir.mkdir(parents=True, exist_ok=True)
    return basedir


_SAFE_SESSION_ID = re.compile(r"^[A-Za-z0-9_.-]+$")
"""Path-safe session_id pattern. Anything that doesn't match is hashed (see
:func:`safe_session_stem`) before being used as a filesystem stem — prevents
the path-traversal vector flagged by CodeRabbit on PR #42 (e.g., a
``session_id`` containing ``/`` or ``..`` would otherwise escape
``<basedir>/dream/``)."""


def safe_session_stem(session_id: str) -> str:
    """Return a filesystem-safe stem for ``session_id``.

    A session_id matching :data:`_SAFE_SESSION_ID` is returned unchanged.
    Otherwise (path separators, ``..``, control bytes, empty, etc.) it is
    replaced by ``"sess_" + sha256(session_id)[:16]`` so all per-session
    state artifacts stay inside ``<basedir>/dream/``.

    Idempotent: passing an already-safe stem returns it unchanged.
    """
    if session_id and session_id not in {".", ".."} and _SAFE_SESSION_ID.fullmatch(session_id):
        return session_id
    return "sess_" + hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:16]


def sidecar_path(basedir: Path, session_id: str) -> Path:
    """Return ``<basedir>/dream/<session_id>.json`` per ADR-harness-004.

    ``session_id`` is sanitized via :func:`safe_session_stem` so a malicious
    value containing path separators cannot escape ``<basedir>/dream/``.
    """
    return basedir / _DREAM_SUBDIR / f"{safe_session_stem(session_id)}.json"


def lock_path(basedir: Path, session_id: str) -> Path:
    """Return ``<basedir>/dream/<session_id>.lock`` per ADR-dreaming-014.

    ``session_id`` is sanitized via :func:`safe_session_stem`.
    """
    return basedir / _DREAM_SUBDIR / f"{safe_session_stem(session_id)}.lock"


def audit_path(basedir: Path, session_id: str) -> Path:
    """Return ``<basedir>/dream/<session_id>.redact-audit.jsonl`` per ADR-011.

    ``session_id`` is sanitized via :func:`safe_session_stem` before being
    passed to :func:`audit_path_for`.
    """
    return audit_path_for(basedir, safe_session_stem(session_id))


# --------------------------------------------------------------------------- #
# Sidecar I/O
# --------------------------------------------------------------------------- #
def load_sidecar(path: Path) -> SidecarState:
    """Load a sidecar; return defaults on missing or corrupt files.

    On ``FileNotFoundError`` the canonical empty state is returned without
    side-effects. On ``json.JSONDecodeError`` a ``sidecar_corrupt`` event is
    emitted (per ADR-harness-006 fail-open + ADR-013's "cursor must remain
    valid"); the same defaults are returned. Missing keys in an otherwise
    well-formed JSON object are padded with defaults for forward-compat
    with sidecars written by older versions.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _default_state()
    except UnicodeDecodeError:
        # Non-UTF-8 bytes — treat as corrupt. CodeRabbit PR #42 finding:
        # without this, load_sidecar raises and the engine would fail
        # repeatedly without resetting state.
        emit("sidecar_corrupt", path=str(path), reason="invalid_utf8")
        return _default_state()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        emit("sidecar_corrupt", path=str(path))
        return _default_state()

    if not isinstance(parsed, dict):
        emit("sidecar_corrupt", path=str(path))
        return _default_state()

    defaults = _default_state()
    cursor_val = parsed.get("cursor", defaults["cursor"])
    # Cursor must be a non-negative int. A negative cursor from a corrupt
    # sidecar would otherwise reach fp.seek() and silently mis-read the log.
    # CodeRabbit PR #42 finding — guard with type AND range check.
    if type(cursor_val) is int and cursor_val >= 0:
        cursor = cursor_val
    else:
        if cursor_val != defaults["cursor"]:
            emit("sidecar_corrupt", path=str(path), field="cursor", value=repr(cursor_val))
        cursor = defaults["cursor"]

    last_summary_val = parsed.get("last_summary", defaults["last_summary"])
    last_summary = (
        last_summary_val
        if (last_summary_val is None or isinstance(last_summary_val, str))
        else defaults["last_summary"]
    )

    recent_val = parsed.get("recent_memory_ids", defaults["recent_memory_ids"])
    recent_memory_ids: list[str] = (
        [str(x) for x in recent_val]
        if isinstance(recent_val, list)
        else list(defaults["recent_memory_ids"])
    )

    fbh_val = parsed.get("first_bytes_hash", defaults["first_bytes_hash"])
    first_bytes_hash = (
        fbh_val
        if (fbh_val is None or isinstance(fbh_val, str))
        else defaults["first_bytes_hash"]
    )

    return SidecarState(
        cursor=cursor,
        last_summary=last_summary,
        recent_memory_ids=recent_memory_ids,
        first_bytes_hash=first_bytes_hash,
    )


def _write_sidecar_atomic(path: Path, state: SidecarState) -> None:
    """Atomically write the sidecar via ``tmp.replace(path)`` per ADR-013 step 8.

    Writes to ``path.with_suffix(path.suffix + ".tmp")`` first, then renames
    over the destination. A crash between the tmp-write and the replace
    leaves the original file intact. The destination is never opened in
    ``"w"`` mode directly.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    capped_state: SidecarState = SidecarState(
        cursor=state["cursor"],
        last_summary=state["last_summary"],
        recent_memory_ids=list(state["recent_memory_ids"])[:RECENT_MEMORY_CAP],
        first_bytes_hash=state["first_bytes_hash"],
    )
    tmp.write_text(json.dumps(capped_state), encoding="utf-8")
    tmp.replace(path)


# --------------------------------------------------------------------------- #
# Cursor sanity check — ADR-013 §Decision "Cursor sanity check"
# --------------------------------------------------------------------------- #
def _sanity_check_cursor(cursor: int, log_path: Path) -> int:
    """Reset the cursor to 0 if it exceeds the current log size (rotation case).

    When ``cursor > log_path.stat().st_size`` the log was likely truncated or
    rotated; we emit ``cursor_reset`` so the reprocess is visible and return
    ``0``. Otherwise the cursor is returned unchanged. ``FileNotFoundError``
    from a missing log is propagated for the engine to handle at its boundary.
    """
    file_size = log_path.stat().st_size
    if cursor > file_size:
        emit(
            "cursor_reset",
            reason="rotation_or_truncation",
            old_cursor=cursor,
            file_size=file_size,
        )
        return 0
    return cursor


# --------------------------------------------------------------------------- #
# Per-session flock — ADR-014
# --------------------------------------------------------------------------- #
class _LockHeld(Exception):
    """Raised by :func:`_per_session_lock` when another process holds the lock."""


@contextmanager
def _per_session_lock(basedir: Path, session_id: str) -> Iterator[None]:
    """Acquire a non-blocking exclusive advisory lock on the per-session lock file.

    Uses ``fcntl.flock(fd, LOCK_EX | LOCK_NB)`` per ADR-014 (pinned over
    ``fcntl.lockf`` per halliday F3 — flock is fd-bound and releases on
    process death). On contention (``BlockingIOError``) emits
    ``concurrent_daydream_skipped`` and raises :class:`_LockHeld`; the
    engine catches this and exits 0 (idempotent skip).

    The lock is released in a ``finally`` block via ``LOCK_UN`` so it
    drops on both normal exit AND exception paths. Callers must not hold
    the context across multiple engine invocations — PR5's plugin shim
    invokes ``daydream()`` once per Stop hook fire, which honors this.
    """
    target = lock_path(basedir, session_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(target), os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            emit("concurrent_daydream_skipped", session_id=session_id)
            raise _LockHeld(str(exc)) from exc
        try:
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError as exc:
                _logger.warning(
                    "flock LOCK_UN failed for session %s: %s", session_id, exc
                )
    finally:
        try:
            os.close(fd)
        except OSError as exc:
            _logger.warning(
                "lock fd close failed for session %s: %s", session_id, exc
            )


# --------------------------------------------------------------------------- #
# Current-session touch — halliday F5 (must run BEFORE sweep)
# --------------------------------------------------------------------------- #
def _touch_current_session_files(basedir: Path, session_id: str) -> None:
    """Fresh-mtime the current session's state files so a peer sweep can't unlink them.

    Touches the four per-session artifacts — sidecar, lock, diary, audit —
    individually via ``os.utime``. Per-file ``FileNotFoundError`` is
    swallowed silently (first invocation of a session has none of these
    yet). Other ``OSError`` subclasses are logged at WARNING and skipped;
    the function never raises (fail-open per ADR-harness-006).
    """
    candidates = [
        sidecar_path(basedir, session_id),
        lock_path(basedir, session_id),
        diary_path_for(basedir, session_id),
        audit_path(basedir, session_id),
    ]
    for candidate in candidates:
        try:
            os.utime(candidate, None)
        except FileNotFoundError:
            continue
        except OSError as exc:
            _logger.warning(
                "touch failed for %s (session=%s): %s", candidate, session_id, exc
            )


# --------------------------------------------------------------------------- #
# Audit-write fail-open wrapper — halliday F13
# --------------------------------------------------------------------------- #
def _write_audit_fail_open(
    path: Path,
    *,
    chunk_id: int,
    pre: str,
    post: str,
    detected: Mapping[str, int],
) -> None:
    """Call :func:`write_audit_record`; swallow any exception per ADR-005 fail-open.

    F10 contract: callers MAY write the same ``chunk_id`` multiple times.
    The append-only diary will contain one row per retry. Downstream
    FP/FN analyzers MUST dedup by ``chunk_id`` (semantics: latest wins,
    OR all rows kept and aggregated — consumer's choice). The engine
    retries the same chunk when an LLM call empty-returns or
    ``extract_memories`` fails parse, both of which leave the cursor
    un-advanced per ADR-013.
    """
    try:
        write_audit_record(
            path,
            chunk_id=chunk_id,
            pre=pre,
            post=post,
            detected=detected,
        )
    except Exception as exc:
        _logger.warning(
            "audit write failed for chunk_id=%s path=%s: %s", chunk_id, path, exc
        )


# --------------------------------------------------------------------------- #
# TTL sweep — ADR-015 §2 + §3 + §4
# --------------------------------------------------------------------------- #
_SWEEP_PATTERNS: tuple[str, ...] = (
    "*.json",
    "*.daydream-events.jsonl",
    "*.redact-audit.jsonl",
    "*.lock",
)


def _read_ttl_days(default: int) -> int:
    """Return the TTL in days, honoring ``DREAM_RETENTION_DAYS`` override.

    A negative value would make ``cutoff`` a future timestamp, so every file
    becomes sweep-eligible — including the current-session files
    :func:`_touch_current_session_files` just refreshed. CodeRabbit PR #42
    finding: bounds-check after parse and fall back to the default.
    """
    override = os.environ.get("DREAM_RETENTION_DAYS")
    if override is None:
        return default
    try:
        value = int(override)
    except ValueError:
        _logger.warning(
            "DREAM_RETENTION_DAYS=%r is not an integer; using default %d",
            override,
            default,
        )
        return default
    if value < 0:
        _logger.warning(
            "DREAM_RETENTION_DAYS=%r is negative; using default %d",
            override,
            default,
        )
        return default
    return value


def _read_throttle_min(default: int) -> int:
    """Return the throttle window in minutes, honoring ``DREAM_SWEEP_INTERVAL_MIN``.

    Negative throttle would cause the sweeper to skip every invocation (it
    can never have been "long enough" ago). CodeRabbit PR #42 finding:
    bounds-check after parse.
    """
    override = os.environ.get("DREAM_SWEEP_INTERVAL_MIN")
    if override is None:
        return default
    try:
        value = int(override)
    except ValueError:
        _logger.warning(
            "DREAM_SWEEP_INTERVAL_MIN=%r is not an integer; using default %d",
            override,
            default,
        )
        return default
    if value < 0:
        _logger.warning(
            "DREAM_SWEEP_INTERVAL_MIN=%r is negative; using default %d",
            override,
            default,
        )
        return default
    return value


def _update_last_swept_marker(marker: Path, now: float) -> None:
    """Atomically update the ``.last-swept`` marker mtime via ``os.replace``.

    Writes a tmp sibling and replaces; protects against the
    two-concurrent-writes tear identified by halliday F6.
    """
    marker.parent.mkdir(parents=True, exist_ok=True)
    # Unique temp filename per writer prevents the concurrent-sweepers race
    # CodeRabbit PR #42 flagged: with a shared ``.last-swept.tmp``, one
    # sweeper's ``os.replace`` can clobber the other mid-flow. PID + uuid
    # makes each writer's temp file disjoint.
    tmp = marker.parent / f"{marker.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    try:
        tmp.write_text(str(now), encoding="utf-8")
        os.replace(tmp, marker)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def sweep_old_state(
    basedir: Path,
    *,
    ttl_days: int = 30,
    throttle_min: int = 60,
) -> int:
    """Delete state files older than ``ttl_days`` in ``basedir/dream/`` (throttled).

    Throttled via a ``.last-swept`` marker file: a call within
    ``throttle_min`` minutes of the prior real sweep is a no-op that
    emits ``sweep_skipped(reason="throttled")``. Otherwise iterates the
    four file-class patterns (``*.json``, ``*.daydream-events.jsonl``,
    ``*.redact-audit.jsonl``, ``*.lock``), unlinks any whose mtime is
    older than the TTL, emits ``state_file_pruned`` per deleted file,
    updates the marker atomically via ``os.replace``, and emits a
    ``sweep_completed`` summary.

    Defaults are env-overridable: ``DREAM_RETENTION_DAYS`` (int days),
    ``DREAM_SWEEP_INTERVAL_MIN`` (int minutes). Fail-open per ADR-015
    §Tradeoffs: a per-file unlink failure logs and continues sweeping
    the remaining files; the engine ignores any exception that escapes.
    Returns the integer count of files deleted.
    """
    effective_ttl_days = _read_ttl_days(ttl_days)
    effective_throttle_min = _read_throttle_min(throttle_min)

    dream_dir = basedir / _DREAM_SUBDIR
    marker = dream_dir / _LAST_SWEPT_MARKER
    now = time.time()

    if marker.exists():
        try:
            last_swept = marker.stat().st_mtime
        except OSError as exc:
            _logger.warning("could not stat last-swept marker %s: %s", marker, exc)
            last_swept = 0.0
        if now - last_swept < effective_throttle_min * _SECONDS_PER_MINUTE:
            emit("sweep_skipped", reason="throttled")
            return 0

    if not dream_dir.is_dir():
        _update_last_swept_marker(marker, now)
        emit("sweep_completed", count=0, duration_s=0.0)
        return 0

    cutoff = now - effective_ttl_days * _SECONDS_PER_DAY
    started = time.time()
    deleted = 0

    seen: set[Path] = set()
    for pattern in _SWEEP_PATTERNS:
        for candidate in dream_dir.glob(pattern):
            if candidate in seen:
                continue
            seen.add(candidate)
            if candidate.name == _LAST_SWEPT_MARKER:
                continue
            try:
                mtime = candidate.stat().st_mtime
            except FileNotFoundError:
                continue
            except OSError as exc:
                _logger.warning("stat failed for %s: %s", candidate, exc)
                continue
            if mtime >= cutoff:
                continue
            try:
                candidate.unlink()
            except FileNotFoundError:
                continue
            except OSError as exc:
                _logger.warning("unlink failed for %s: %s", candidate, exc)
                continue
            deleted += 1
            emit("state_file_pruned", path=str(candidate), reason="ttl_expired")

    _update_last_swept_marker(marker, now)
    duration_s = time.time() - started
    emit("sweep_completed", count=deleted, duration_s=duration_s)
    return deleted


def _first_bytes_hash(data: bytes) -> str:
    """Return ``sha256(data).hexdigest()`` — convenience for the engine's F8 seam."""
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------- #
# Basedir flock — ADR-021 (Dream mutation concurrency)
# --------------------------------------------------------------------------- #
class _DreamLockHeld(Exception):
    """Raised by :func:`_basedir_dream_lock` when another process holds the basedir lock."""


class _UnsupportedFsError(Exception):
    """Raised when ``$MEMORY_STORE`` is on a network filesystem (NFS/SMB) and the override is unset."""


@contextmanager
def _basedir_dream_lock(basedir: Path) -> Iterator[None]:
    """Acquire the basedir-scope dream lock at ``<basedir>/.dream.lock`` (ADR-021 Decision 2).

    Structural copy of :func:`_per_session_lock` lifted from session scope to
    basedir scope. ``fcntl.flock(LOCK_EX | LOCK_NB)`` — exclusive non-blocking
    advisory, fd-bound so it releases on process death (per ADR-014 halliday
    F3, mirrored here). On contention emits ``dream.lock_contended`` and
    raises :class:`_DreamLockHeld`. Distinct from :class:`_LockHeld` so the
    CLI catches them separately (ADR-021 §Consequences §Shape).
    """
    target = basedir / ".dream.lock"
    basedir.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(target), os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            emit("dream.lock_contended", basedir=str(basedir))
            raise _DreamLockHeld(str(exc)) from exc
        try:
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError as exc:
                _logger.warning("dream basedir flock LOCK_UN failed: %s", exc)
    finally:
        try:
            os.close(fd)
        except OSError as exc:
            _logger.warning("dream basedir lock fd close failed: %s", exc)


def _is_network_fs(path: Path) -> bool:
    """Detect whether ``path`` resides on a network filesystem (NFS/SMB) per ADR-021 Decision 3.

    Linux: read ``/proc/mounts`` and match the longest prefix; recognize
    ``nfs``, ``nfs4``, ``cifs``, ``smb*``, ``smbfs`` as network FS.
    Darwin: best-effort ``getattrlist`` via ``ATTR_VOL_INFO_FSTYPE`` is
    complex stdlib; fall back to parsing ``mount`` output for ``nfs``/``smbfs``.
    Unknown platforms log a warning and return ``False`` (jasnah Pushback 6 —
    "false-positive preferred" applies to detector OUTPUT, not to unknown-
    platform DEFAULT; hard-failing every BSD CI run would be hostile).

    Monkeypatchable from tests per rubric §L16.
    """
    import sys

    network_types = {"nfs", "nfs4", "cifs", "smb", "smb2", "smb3", "smbfs", "afpfs"}
    try:
        resolved = str(path.resolve())
    except OSError:
        return False

    if sys.platform.startswith("linux"):
        try:
            with open("/proc/mounts", "r", encoding="utf-8") as f:
                mounts = [line.split() for line in f if line.strip() and not line.startswith("#")]
        except OSError as exc:
            _logger.warning("_is_network_fs: /proc/mounts read failed: %s", exc)
            return False
        best_mount = ""
        best_fstype = ""
        for parts in mounts:
            if len(parts) < 3:
                continue
            mount_point = parts[1]
            fstype = parts[2]
            if (resolved == mount_point or resolved.startswith(mount_point.rstrip("/") + "/")) and len(mount_point) > len(best_mount):
                best_mount = mount_point
                best_fstype = fstype
        return best_fstype.lower() in network_types

    if sys.platform == "darwin":
        import subprocess
        try:
            result = subprocess.run(["mount"], capture_output=True, text=True, timeout=2.0, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            _logger.warning("_is_network_fs: darwin `mount` invocation failed: %s", exc)
            return False
        best_mount = ""
        best_fstype = ""
        for line in result.stdout.splitlines():
            if " on " not in line or " (" not in line:
                continue
            try:
                _src, rest = line.split(" on ", 1)
                mount_point, paren = rest.split(" (", 1)
            except ValueError:
                continue
            fstype = paren.split(",", 1)[0].rstrip(")").strip().lower()
            if (resolved == mount_point or resolved.startswith(mount_point.rstrip("/") + "/")) and len(mount_point) > len(best_mount):
                best_mount = mount_point
                best_fstype = fstype
        return best_fstype in network_types

    _logger.warning("_is_network_fs: unknown platform %r, returning False", sys.platform)
    return False
