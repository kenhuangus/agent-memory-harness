"""Unit tests for ``memeval.dreaming._state``.

Covers PR4 rubric sections B (basedir resolution), C (path helpers),
D (sidecar I/O atomicity), E (per-session flock), F (cursor sanity),
G (TTL sweep), plus halliday-revision criteria 165/167/168/170/174.

Engine-level integration (sections A, K, L, M, N, P, Q) lives in
``test_engine.py``; extraction parse-paths (G/H/I) live in
``test_extract.py``; prompt pinning (F/H) lives in ``test_prompts.py``.
"""

from __future__ import annotations

import ast
import fcntl
import hashlib
import inspect
import json
import logging
import multiprocessing
import os
import threading
import time
import typing
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip(
    "detect_secrets",
    reason="install with `pip install -e eval[daydream]` to run state tests",
)

from memeval.dreaming import _state
from memeval.dreaming._state import (
    MAX_AUDIT_LINES_PER_FILE,
    RECENT_MEMORY_CAP,
    SidecarState,
    _LockHeld,
    _per_session_lock,
    _sanity_check_cursor,
    _touch_current_session_files,
    _write_audit_fail_open,
    _write_sidecar_atomic,
    audit_path,
    load_sidecar,
    lock_path,
    resolve_basedir,
    sidecar_path,
    sweep_old_state,
)


STATE_SOURCE_PATH = Path(_state.__file__)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _make_basedir(tmp_path: Path) -> Path:
    """Build a basedir with the dream/ subdir; return basedir."""
    (tmp_path / "dream").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _set_env_memory_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Create a memory store file and bind $MEMORY_STORE to it; return basedir."""
    store_file = tmp_path / "memory.jsonl"
    store_file.write_text("", encoding="utf-8")
    monkeypatch.setenv("MEMORY_STORE", str(store_file))
    return tmp_path


def _age_file(p: Path, age_seconds: float) -> None:
    """Set the mtime/atime of ``p`` to ``now - age_seconds`` (deterministic)."""
    now = time.time()
    os.utime(p, (now - age_seconds, now - age_seconds))


# Module-level worker helpers — multiprocessing.Process on macOS uses 'spawn'
# which requires pickleable callables; local closures inside test methods
# cannot be pickled, so the worker bodies live here.
def _mp_hold_lock(
    basedir_str: str, session_id: str, held: Any, release: Any
) -> None:
    """Subprocess: acquire the per-session lock and hold it until released."""
    from memeval.dreaming._state import _per_session_lock as inner

    with inner(Path(basedir_str), session_id):
        held.set()
        release.wait(timeout=10)


def _mp_take_lock(
    basedir_str: str, session_id: str, success: Any
) -> None:
    """Subprocess: try to acquire the lock and signal success on entry."""
    from memeval.dreaming._state import _per_session_lock as inner

    try:
        with inner(Path(basedir_str), session_id):
            success.set()
    except Exception:
        pass


def _mp_take_and_exit(
    basedir_str: str, session_id: str, ready_evt: Any
) -> None:
    """Subprocess: take the lock, signal ready, then exit while holding it.

    POSIX flock releases on process death (fd close), so the parent should
    be able to re-acquire after this subprocess returns.
    """
    from memeval.dreaming._state import _per_session_lock as inner

    with inner(Path(basedir_str), session_id):
        ready_evt.set()


# =========================================================================== #
# B. resolve_basedir() — ADR-015 §1
# =========================================================================== #
class TestResolveBasedir:
    """ADR-015 §1 — MEMORY_STORE-based basedir resolution."""

    def test_resolve_basedir_reads_memory_store_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        basedir = _set_env_memory_store(monkeypatch, tmp_path)
        assert resolve_basedir() == basedir.resolve()

    def test_resolve_basedir_keyerror_when_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MEMORY_STORE", raising=False)
        with pytest.raises(KeyError):
            resolve_basedir()

    def test_resolve_basedir_filenotfounderror_on_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        missing = tmp_path / "does-not-exist.jsonl"
        monkeypatch.setenv("MEMORY_STORE", str(missing))
        with pytest.raises(FileNotFoundError) as excinfo:
            resolve_basedir()
        assert str(missing) in str(excinfo.value)

    def test_resolve_basedir_valueerror_on_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MEMORY_STORE", str(tmp_path))
        with pytest.raises(ValueError) as excinfo:
            resolve_basedir()
        assert str(tmp_path.resolve()) in str(excinfo.value)

    def test_resolve_basedir_returns_parent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = tmp_path / "subdir" / "store.jsonl"
        store.parent.mkdir(parents=True)
        store.write_text("", encoding="utf-8")
        monkeypatch.setenv("MEMORY_STORE", str(store))
        assert resolve_basedir() == (tmp_path / "subdir").resolve()

    def test_resolve_basedir_resolves_symlinks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        real_dir = tmp_path / "real"
        real_dir.mkdir()
        real_store = real_dir / "store.jsonl"
        real_store.write_text("", encoding="utf-8")
        symlink_dir = tmp_path / "link"
        symlink_dir.symlink_to(real_dir)
        symlinked_store = symlink_dir / "store.jsonl"
        monkeypatch.setenv("MEMORY_STORE", str(symlinked_store))
        # .resolve() (not .absolute()) follows symlinks; expect real path.
        assert resolve_basedir() == real_dir.resolve()


# =========================================================================== #
# C. sidecar_path() / lock_path() / audit_path()
# =========================================================================== #
class TestSidecarLockAuditPaths:
    """ADR-harness-004 + ADR-014 + ADR-011 — per-session file paths."""

    def test_sidecar_path_format(self, tmp_path: Path) -> None:
        assert sidecar_path(tmp_path, "sess_abc") == (
            tmp_path / "dream" / "sess_abc.json"
        )

    def test_lock_path_format(self, tmp_path: Path) -> None:
        assert lock_path(tmp_path, "sess_abc") == (
            tmp_path / "dream" / "sess_abc.lock"
        )

    def test_audit_path_format(self, tmp_path: Path) -> None:
        assert audit_path(tmp_path, "sess_abc") == (
            tmp_path / "dream" / "sess_abc.redact-audit.jsonl"
        )

    def test_sidecar_path_does_not_create_dir(self, tmp_path: Path) -> None:
        # Pure path helper: must not touch the filesystem.
        sidecar_path(tmp_path, "sess_foo")
        assert not (tmp_path / "dream").exists()

    def test_lock_path_does_not_create_dir(self, tmp_path: Path) -> None:
        lock_path(tmp_path, "sess_foo")
        assert not (tmp_path / "dream").exists()


# =========================================================================== #
# D. Sidecar I/O atomicity — ADR-harness-004 + ADR-013 step 8
# =========================================================================== #
class TestSidecarIO:
    """Atomic write semantics + load-time defaults + first_bytes_hash roundtrip."""

    def test_load_sidecar_missing_file_returns_defaults(self, tmp_path: Path) -> None:
        state = load_sidecar(tmp_path / "missing.json")
        assert state == SidecarState(
            cursor=0,
            last_summary=None,
            recent_memory_ids=[],
            first_bytes_hash=None,
        )

    def test_load_sidecar_corrupt_returns_defaults_and_emits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "bad.json"
        target.write_text("{not-json", encoding="utf-8")
        captured: list[tuple[str, dict[str, Any]]] = []

        def fake_emit(event_type: str, **fields: Any) -> None:
            captured.append((event_type, fields))

        monkeypatch.setattr(_state, "emit", fake_emit)
        state = load_sidecar(target)
        assert state == SidecarState(
            cursor=0,
            last_summary=None,
            recent_memory_ids=[],
            first_bytes_hash=None,
        )
        assert any(evt == "sidecar_corrupt" for evt, _ in captured)

    def test_load_sidecar_non_dict_returns_defaults(self, tmp_path: Path) -> None:
        target = tmp_path / "list.json"
        target.write_text("[1, 2, 3]", encoding="utf-8")
        state = load_sidecar(target)
        assert state["cursor"] == 0
        assert state["recent_memory_ids"] == []

    def test_load_sidecar_missing_keys_default(self, tmp_path: Path) -> None:
        target = tmp_path / "partial.json"
        target.write_text(json.dumps({"cursor": 42}), encoding="utf-8")
        state = load_sidecar(target)
        assert state["cursor"] == 42
        assert state["last_summary"] is None
        assert state["recent_memory_ids"] == []
        assert state["first_bytes_hash"] is None

    def test_load_sidecar_handles_missing_first_bytes_hash(
        self, tmp_path: Path
    ) -> None:
        # Old-format sidecar (pre-F8); first_bytes_hash absent → defaults to None.
        target = tmp_path / "old.json"
        target.write_text(
            json.dumps(
                {
                    "cursor": 100,
                    "last_summary": "prior",
                    "recent_memory_ids": ["mem_aaaa1111"],
                }
            ),
            encoding="utf-8",
        )
        state = load_sidecar(target)
        assert state["first_bytes_hash"] is None
        assert state["cursor"] == 100

    def test_write_sidecar_atomic_uses_tmp_then_rename(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "dream" / "sess.json"
        target.parent.mkdir(parents=True)
        replace_calls: list[tuple[Path, Path]] = []
        real_replace = Path.replace

        def spy_replace(self: Path, dest: Any) -> Path:
            replace_calls.append((Path(self), Path(dest)))
            return real_replace(self, dest)

        monkeypatch.setattr(Path, "replace", spy_replace)
        _write_sidecar_atomic(
            target,
            SidecarState(
                cursor=10,
                last_summary="x",
                recent_memory_ids=[],
                first_bytes_hash=None,
            ),
        )
        assert len(replace_calls) == 1
        src, dst = replace_calls[0]
        assert src.suffix == ".tmp"
        assert dst == target

    def test_write_sidecar_never_uses_w_mode(self) -> None:
        # AST-scan: no open(..., "w") with the literal "w" mode in _state.py.
        tree = ast.parse(STATE_SOURCE_PATH.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = (
                    func.id
                    if isinstance(func, ast.Name)
                    else (
                        func.attr if isinstance(func, ast.Attribute) else ""
                    )
                )
                if name in ("open", "write_text", "open_text"):
                    # Check positional args for a literal "w" mode.
                    for arg in node.args:
                        if isinstance(arg, ast.Constant) and arg.value == "w":
                            raise AssertionError(
                                f"open(..., 'w') found at line {node.lineno}"
                            )
                    for kw in node.keywords:
                        if (
                            kw.arg == "mode"
                            and isinstance(kw.value, ast.Constant)
                            and kw.value.value == "w"
                        ):
                            raise AssertionError(
                                f"open(mode='w') found at line {node.lineno}"
                            )

    def test_write_sidecar_crash_before_rename_preserves_original(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "dream" / "sess.json"
        target.parent.mkdir(parents=True)
        original = json.dumps(
            {
                "cursor": 5,
                "last_summary": "orig",
                "recent_memory_ids": [],
                "first_bytes_hash": None,
            }
        )
        target.write_text(original, encoding="utf-8")

        def boom(self: Path, dest: Any) -> Path:
            raise OSError("simulated rename failure")

        monkeypatch.setattr(Path, "replace", boom)
        with pytest.raises(OSError):
            _write_sidecar_atomic(
                target,
                SidecarState(
                    cursor=999,
                    last_summary="new",
                    recent_memory_ids=[],
                    first_bytes_hash=None,
                ),
            )
        # Original file is unchanged.
        assert target.read_text(encoding="utf-8") == original

    def test_sidecar_roundtrip(self, tmp_path: Path) -> None:
        target = tmp_path / "dream" / "sess.json"
        original = SidecarState(
            cursor=12345,
            last_summary="summary text",
            recent_memory_ids=["mem_aaaaaaaa", "mem_bbbbbbbb"],
            first_bytes_hash="deadbeef",
        )
        _write_sidecar_atomic(target, original)
        reloaded = load_sidecar(target)
        assert reloaded == original

    def test_sidecar_first_bytes_hash_roundtrip(self, tmp_path: Path) -> None:
        target = tmp_path / "dream" / "sess.json"
        hash_val = hashlib.sha256(b"hello world").hexdigest()
        _write_sidecar_atomic(
            target,
            SidecarState(
                cursor=0,
                last_summary=None,
                recent_memory_ids=[],
                first_bytes_hash=hash_val,
            ),
        )
        reloaded = load_sidecar(target)
        assert reloaded["first_bytes_hash"] == hash_val

    def test_recent_memory_ids_truncated_to_cap(self, tmp_path: Path) -> None:
        target = tmp_path / "dream" / "sess.json"
        many_ids = [f"mem_{i:08x}" for i in range(120)]
        _write_sidecar_atomic(
            target,
            SidecarState(
                cursor=0,
                last_summary=None,
                recent_memory_ids=many_ids,
                first_bytes_hash=None,
            ),
        )
        reloaded = load_sidecar(target)
        assert len(reloaded["recent_memory_ids"]) == RECENT_MEMORY_CAP
        # First-N truncation (most-recent-first slice per plan §4 step 10).
        assert reloaded["recent_memory_ids"] == many_ids[:RECENT_MEMORY_CAP]

    def test_recent_memory_cap_equals_50(self) -> None:
        assert RECENT_MEMORY_CAP == 50
        assert isinstance(RECENT_MEMORY_CAP, int)


# =========================================================================== #
# E. Per-session flock — ADR-014
# =========================================================================== #
class TestPerSessionLock:
    """ADR-014 — fcntl.flock LOCK_EX|LOCK_NB advisory locking."""

    def test_per_session_lock_is_context_manager(self) -> None:
        # contextlib.contextmanager wraps generators; the resulting callable
        # returns a _GeneratorContextManager.
        assert hasattr(_per_session_lock, "__wrapped__") or callable(
            _per_session_lock
        )
        sig = inspect.signature(_per_session_lock)
        assert "basedir" in sig.parameters
        assert "session_id" in sig.parameters

    def test_per_session_lock_uses_flock_ex_nb(self) -> None:
        # AST-scan: assert fcntl.flock(...) with LOCK_EX|LOCK_NB appears.
        src = STATE_SOURCE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(src)
        found = False
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "fcntl"
                and node.func.attr == "flock"
                and len(node.args) >= 2
            ):
                second = node.args[1]
                if isinstance(second, ast.BinOp) and isinstance(
                    second.op, ast.BitOr
                ):
                    left = second.left
                    right = second.right
                    names = {
                        getattr(left, "attr", None),
                        getattr(right, "attr", None),
                    }
                    if names == {"LOCK_EX", "LOCK_NB"}:
                        found = True
                        break
        assert found, "fcntl.flock(fd, LOCK_EX | LOCK_NB) not found in _state.py"

    def test_lock_uses_flock_not_lockf(self) -> None:
        # halliday F3 (rubric §T criterion 165): pin flock over lockf.
        src = STATE_SOURCE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(src)
        flock_seen = False
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "fcntl"
            ):
                assert node.func.attr != "lockf", (
                    f"fcntl.lockf found at line {node.lineno}; "
                    "F3 requires fcntl.flock"
                )
                if node.func.attr == "flock":
                    flock_seen = True
        assert flock_seen, "no fcntl.flock(...) call in _state.py"

    def test_per_session_lock_raises_lockheld_on_contention(
        self, tmp_path: Path
    ) -> None:
        basedir = _make_basedir(tmp_path)
        ctx = multiprocessing.get_context("spawn")
        held_event = ctx.Event()
        release_event = ctx.Event()
        proc = ctx.Process(
            target=_mp_hold_lock,
            args=(str(basedir), "sess_x", held_event, release_event),
        )
        proc.start()
        try:
            assert held_event.wait(timeout=10)
            with pytest.raises(_LockHeld):
                with _per_session_lock(basedir, "sess_x"):
                    pytest.fail("acquired lock that should have been held")
        finally:
            release_event.set()
            proc.join(timeout=10)
            # CodeRabbit PR #42 — terminate + assert exit on stuck child so a
            # hung subprocess doesn't leak into later tests holding the lock.
            if proc.is_alive():
                proc.terminate()
                proc.join(timeout=5)
                pytest.fail("lock-holder subprocess did not exit cleanly")
            assert proc.exitcode == 0, f"lock holder exited {proc.exitcode}"

    def test_per_session_lock_emits_before_raising(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        basedir = _make_basedir(tmp_path)
        captured: list[tuple[str, dict[str, Any]]] = []

        def fake_emit(event_type: str, **fields: Any) -> None:
            captured.append((event_type, fields))

        monkeypatch.setattr(_state, "emit", fake_emit)
        # Take the lock at the OS level via os.open + flock so we can release
        # deterministically from the same process.
        target = lock_path(basedir, "sess_y")
        target.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(target), os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with pytest.raises(_LockHeld):
                with _per_session_lock(basedir, "sess_y"):
                    pytest.fail("should not enter on contention")
            # emit("concurrent_daydream_skipped", session_id="sess_y") was called.
            assert any(
                evt == "concurrent_daydream_skipped"
                and fields.get("session_id") == "sess_y"
                for evt, fields in captured
            )
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def test_per_session_lock_releases_on_normal_exit(
        self, tmp_path: Path
    ) -> None:
        basedir = _make_basedir(tmp_path)
        with _per_session_lock(basedir, "sess_z"):
            pass
        # Re-acquire from the same process — would fail if the lock fd were
        # leaked or the LOCK_UN never ran (since fcntl.flock on the same file
        # by the same process is reentrant, the strict test is to acquire from
        # a subprocess; instead, simply ensure no exception on re-enter).
        with _per_session_lock(basedir, "sess_z"):
            pass

    def test_per_session_lock_releases_on_exception(
        self, tmp_path: Path
    ) -> None:
        basedir = _make_basedir(tmp_path)
        with pytest.raises(RuntimeError):
            with _per_session_lock(basedir, "sess_e"):
                raise RuntimeError("boom")
        # If the finally block didn't run, the fd would leak. Confirm we can
        # acquire from a subprocess (true cross-process release check).
        ctx = multiprocessing.get_context("spawn")
        success = ctx.Event()
        proc = ctx.Process(
            target=_mp_take_lock, args=(str(basedir), "sess_e", success)
        )
        proc.start()
        proc.join(timeout=10)
        # CodeRabbit PR #42 — terminate stuck child to avoid lock leak.
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=5)
            pytest.fail("take-lock subprocess did not exit cleanly")
        assert proc.exitcode == 0, f"take-lock subprocess exited {proc.exitcode}"
        assert success.is_set()

    def test_per_session_lock_creates_parent_dir(self, tmp_path: Path) -> None:
        # basedir present, but dream/ subdir absent — _per_session_lock should
        # mkdir parents=True, exist_ok=True before opening the fd.
        assert not (tmp_path / "dream").exists()
        with _per_session_lock(tmp_path, "sess_mk"):
            assert (tmp_path / "dream").is_dir()
            assert (tmp_path / "dream" / "sess_mk.lock").exists()

    def test_per_session_lock_different_sessions_parallel(
        self, tmp_path: Path
    ) -> None:
        basedir = _make_basedir(tmp_path)
        # Hold one session's lock; another session must acquire concurrently.
        with _per_session_lock(basedir, "sess_one"):
            with _per_session_lock(basedir, "sess_two"):
                pass

    def test_per_session_lock_dead_process_releases(self, tmp_path: Path) -> None:
        basedir = _make_basedir(tmp_path)
        ctx = multiprocessing.get_context("spawn")
        ready = ctx.Event()
        proc = ctx.Process(
            target=_mp_take_and_exit, args=(str(basedir), "sess_dead", ready)
        )
        proc.start()
        assert ready.wait(timeout=10)
        proc.join(timeout=10)
        # CodeRabbit PR #42 — terminate stuck child to avoid lock leak.
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=5)
            pytest.fail("take-and-exit subprocess did not exit cleanly")
        # Parent now acquires — POSIX flock released on process death.
        with _per_session_lock(basedir, "sess_dead"):
            pass


# =========================================================================== #
# F. Cursor sanity check — ADR-013
# =========================================================================== #
class TestSanityCheckCursor:
    """ADR-013 — cursor reset on rotation/truncation."""

    def test_sanity_check_cursor_unchanged_when_valid(self, tmp_path: Path) -> None:
        log = tmp_path / "log.jsonl"
        log.write_bytes(b"x" * 100)
        assert _sanity_check_cursor(50, log) == 50
        assert _sanity_check_cursor(100, log) == 100

    def test_sanity_check_cursor_resets_on_rotation(self, tmp_path: Path) -> None:
        log = tmp_path / "log.jsonl"
        log.write_bytes(b"x" * 10)
        # cursor > file_size → reset to 0.
        assert _sanity_check_cursor(500, log) == 0

    def test_sanity_check_emits_cursor_reset_event(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        log = tmp_path / "log.jsonl"
        log.write_bytes(b"x" * 10)
        captured: list[tuple[str, dict[str, Any]]] = []

        def fake_emit(event_type: str, **fields: Any) -> None:
            captured.append((event_type, fields))

        monkeypatch.setattr(_state, "emit", fake_emit)
        _sanity_check_cursor(500, log)
        reset_events = [(e, f) for e, f in captured if e == "cursor_reset"]
        assert len(reset_events) == 1
        _, fields = reset_events[0]
        assert fields.get("old_cursor") == 500
        assert fields.get("file_size") == 10

    def test_sanity_check_propagates_missing_log(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            _sanity_check_cursor(0, tmp_path / "missing.jsonl")


# =========================================================================== #
# G. TTL sweep — ADR-015
# =========================================================================== #
class TestSweepSignature:
    """ADR-015 §Consequences "Shape" — default args + return type."""

    def test_sweep_signature_defaults(self) -> None:
        sig = inspect.signature(sweep_old_state)
        params = sig.parameters
        assert params["ttl_days"].default == 30
        assert params["ttl_days"].kind == inspect.Parameter.KEYWORD_ONLY
        assert params["throttle_min"].default == 60
        assert params["throttle_min"].kind == inspect.Parameter.KEYWORD_ONLY
        hints = typing.get_type_hints(sweep_old_state)
        assert hints["return"] is int


class TestSweepBehavior:
    """ADR-015 §2 + §3 — TTL deletion, throttle, env overrides, per-file failure."""

    def test_sweep_deletes_files_older_than_ttl(self, tmp_path: Path) -> None:
        basedir = _make_basedir(tmp_path)
        old_file = basedir / "dream" / "old_session.json"
        old_file.write_text("{}", encoding="utf-8")
        _age_file(old_file, age_seconds=31 * 86400)
        deleted = sweep_old_state(basedir, ttl_days=30, throttle_min=0)
        assert deleted == 1
        assert not old_file.exists()

    def test_sweep_preserves_fresh_files(self, tmp_path: Path) -> None:
        basedir = _make_basedir(tmp_path)
        fresh = basedir / "dream" / "fresh.json"
        fresh.write_text("{}", encoding="utf-8")
        _age_file(fresh, age_seconds=1 * 86400)
        sweep_old_state(basedir, ttl_days=30, throttle_min=0)
        assert fresh.exists()

    @pytest.mark.parametrize(
        "name",
        [
            "sess.json",
            "sess.daydream-events.jsonl",
            "sess.redact-audit.jsonl",
            "sess.lock",
        ],
    )
    def test_sweep_covers_all_file_classes(
        self, tmp_path: Path, name: str
    ) -> None:
        basedir = _make_basedir(tmp_path)
        target = basedir / "dream" / name
        target.write_text("data", encoding="utf-8")
        _age_file(target, age_seconds=60 * 86400)
        sweep_old_state(basedir, ttl_days=30, throttle_min=0)
        assert not target.exists(), f"expected {name} to be swept"

    def test_sweep_throttled_within_window(self, tmp_path: Path) -> None:
        basedir = _make_basedir(tmp_path)
        # First sweep installs the marker.
        sweep_old_state(basedir, ttl_days=30, throttle_min=60)
        # Create a sweep-eligible file, then call again within the window.
        eligible = basedir / "dream" / "eligible.json"
        eligible.write_text("{}", encoding="utf-8")
        _age_file(eligible, age_seconds=60 * 86400)
        deleted = sweep_old_state(basedir, ttl_days=30, throttle_min=60)
        assert deleted == 0
        assert eligible.exists()

    def test_sweep_emits_skipped_on_throttle(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        basedir = _make_basedir(tmp_path)
        sweep_old_state(basedir, ttl_days=30, throttle_min=60)

        captured: list[tuple[str, dict[str, Any]]] = []

        def fake_emit(event_type: str, **fields: Any) -> None:
            captured.append((event_type, fields))

        monkeypatch.setattr(_state, "emit", fake_emit)
        sweep_old_state(basedir, ttl_days=30, throttle_min=60)
        assert any(
            evt == "sweep_skipped" and fields.get("reason") == "throttled"
            for evt, fields in captured
        )

    def test_sweep_runs_after_throttle_window(self, tmp_path: Path) -> None:
        basedir = _make_basedir(tmp_path)
        # First sweep with non-zero throttle, then age out the marker.
        sweep_old_state(basedir, ttl_days=30, throttle_min=60)
        marker = basedir / "dream" / ".last-swept"
        assert marker.exists()
        # Push the marker back >60 minutes.
        _age_file(marker, age_seconds=2 * 60 * 60)
        # Now a fresh sweep should run.
        eligible = basedir / "dream" / "eligible.json"
        eligible.write_text("{}", encoding="utf-8")
        _age_file(eligible, age_seconds=60 * 86400)
        deleted = sweep_old_state(basedir, ttl_days=30, throttle_min=60)
        assert deleted == 1
        assert not eligible.exists()

    def test_sweep_honors_dream_retention_days_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        basedir = _make_basedir(tmp_path)
        monkeypatch.setenv("DREAM_RETENTION_DAYS", "7")
        target = basedir / "dream" / "sess.json"
        target.write_text("{}", encoding="utf-8")
        _age_file(target, age_seconds=10 * 86400)
        # Default ttl_days=30 would preserve; env override of 7 deletes.
        deleted = sweep_old_state(basedir, throttle_min=0)
        assert deleted == 1
        assert not target.exists()

    def test_sweep_honors_dream_sweep_interval_min_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        basedir = _make_basedir(tmp_path)
        # Install a fresh marker (just-now sweep).
        sweep_old_state(basedir, throttle_min=0)
        # Override throttle to 9999 minutes; next call short-circuits.
        monkeypatch.setenv("DREAM_SWEEP_INTERVAL_MIN", "9999")
        eligible = basedir / "dream" / "should_stay.json"
        eligible.write_text("{}", encoding="utf-8")
        _age_file(eligible, age_seconds=60 * 86400)
        deleted = sweep_old_state(basedir)
        assert deleted == 0
        assert eligible.exists()

    def test_sweep_env_override_invalid_falls_back_to_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        basedir = _make_basedir(tmp_path)
        monkeypatch.setenv("DREAM_RETENTION_DAYS", "not-an-int")
        target = basedir / "dream" / "sess.json"
        target.write_text("{}", encoding="utf-8")
        _age_file(target, age_seconds=10 * 86400)
        # Default 30 days; should NOT delete the 10-day-old file.
        deleted = sweep_old_state(basedir, ttl_days=30, throttle_min=0)
        assert deleted == 0
        assert target.exists()

    def test_sweep_updates_last_swept_atomically(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        basedir = _make_basedir(tmp_path)
        replace_calls: list[tuple[str, str]] = []
        real_replace = os.replace

        def spy(src: Any, dst: Any) -> None:
            replace_calls.append((str(src), str(dst)))
            real_replace(src, dst)

        monkeypatch.setattr(os, "replace", spy)
        sweep_old_state(basedir, throttle_min=0)
        marker = basedir / "dream" / ".last-swept"
        assert marker.exists()
        # At least one os.replace went to the marker.
        assert any(dst == str(marker) for _, dst in replace_calls)

    def test_sweep_uses_os_replace_for_marker_in_source(self) -> None:
        # AST-scan: os.replace must appear (proves atomic marker write).
        src = STATE_SOURCE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(src)
        found = False
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "os"
                and node.func.attr == "replace"
            ):
                found = True
                break
        assert found, "os.replace not present in _state.py"

    def test_sweep_treats_missing_marker_as_never_swept(
        self, tmp_path: Path
    ) -> None:
        basedir = _make_basedir(tmp_path)
        marker = basedir / "dream" / ".last-swept"
        assert not marker.exists()
        eligible = basedir / "dream" / "old.json"
        eligible.write_text("{}", encoding="utf-8")
        _age_file(eligible, age_seconds=60 * 86400)
        # With no marker, the throttle short-circuit should not trigger.
        deleted = sweep_old_state(basedir, throttle_min=60)
        assert deleted == 1

    def test_sweep_emits_per_file_event(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        basedir = _make_basedir(tmp_path)
        for name in ("a.json", "b.lock"):
            p = basedir / "dream" / name
            p.write_text("{}", encoding="utf-8")
            _age_file(p, age_seconds=60 * 86400)

        captured: list[tuple[str, dict[str, Any]]] = []

        def fake_emit(event_type: str, **fields: Any) -> None:
            captured.append((event_type, fields))

        monkeypatch.setattr(_state, "emit", fake_emit)
        sweep_old_state(basedir, throttle_min=0)
        pruned = [(e, f) for e, f in captured if e == "state_file_pruned"]
        assert len(pruned) == 2
        for _, fields in pruned:
            assert fields.get("reason") == "ttl_expired"
            assert "path" in fields

    def test_sweep_emits_completed_summary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        basedir = _make_basedir(tmp_path)
        captured: list[tuple[str, dict[str, Any]]] = []

        def fake_emit(event_type: str, **fields: Any) -> None:
            captured.append((event_type, fields))

        monkeypatch.setattr(_state, "emit", fake_emit)
        sweep_old_state(basedir, throttle_min=0)
        summary = [(e, f) for e, f in captured if e == "sweep_completed"]
        assert len(summary) == 1
        _, fields = summary[0]
        assert "count" in fields

    def test_sweep_returns_deletion_count(self, tmp_path: Path) -> None:
        basedir = _make_basedir(tmp_path)
        for i in range(3):
            p = basedir / "dream" / f"s{i}.json"
            p.write_text("{}", encoding="utf-8")
            _age_file(p, age_seconds=60 * 86400)
        count = sweep_old_state(basedir, throttle_min=0)
        assert count == 3

    def test_sweep_per_file_unlink_failure_continues(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        basedir = _make_basedir(tmp_path)
        bad = basedir / "dream" / "bad.json"
        good = basedir / "dream" / "good.json"
        bad.write_text("{}", encoding="utf-8")
        good.write_text("{}", encoding="utf-8")
        _age_file(bad, age_seconds=60 * 86400)
        _age_file(good, age_seconds=60 * 86400)

        real_unlink = Path.unlink

        def selective_unlink(self: Path, *args: Any, **kwargs: Any) -> None:
            if self.name == "bad.json":
                raise PermissionError("denied")
            real_unlink(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", selective_unlink)
        # Must not propagate the PermissionError; must still delete good.json.
        deleted = sweep_old_state(basedir, throttle_min=0)
        assert deleted == 1
        assert not good.exists()
        assert bad.exists()  # the failing unlink left it behind

    def test_sweep_lock_files_use_30_day_ttl_not_24h(self, tmp_path: Path) -> None:
        basedir = _make_basedir(tmp_path)
        lockf = basedir / "dream" / "sess.lock"
        lockf.write_text("", encoding="utf-8")
        # 5 days old — would be reclaimed by a 24h stale-lock policy, must
        # NOT be reclaimed by the unified 30-day TTL.
        _age_file(lockf, age_seconds=5 * 86400)
        sweep_old_state(basedir, ttl_days=30, throttle_min=0)
        assert lockf.exists()

    def test_sweep_missing_dream_dir_does_not_raise(self, tmp_path: Path) -> None:
        # basedir exists but no dream/ subdir.
        deleted = sweep_old_state(tmp_path, throttle_min=0)
        assert deleted == 0

    def test_sweep_marker_is_not_deleted_by_itself(self, tmp_path: Path) -> None:
        basedir = _make_basedir(tmp_path)
        sweep_old_state(basedir, throttle_min=0)
        marker = basedir / "dream" / ".last-swept"
        assert marker.exists()
        # Age the marker; sweep again with no throttle — it must not delete itself.
        _age_file(marker, age_seconds=60 * 86400)
        sweep_old_state(basedir, throttle_min=0)
        assert marker.exists()


# =========================================================================== #
# Halliday revision T criteria — F5, F8, F11 (criteria 167, 168, 170, 174)
# =========================================================================== #
class TestTouchCurrentSessionFiles:
    """halliday F5 — touch current session before peer sweep (criteria 167, 174)."""

    def test_touch_updates_existing_file_mtime(self, tmp_path: Path) -> None:
        basedir = _make_basedir(tmp_path)
        sidecar = sidecar_path(basedir, "sess_t")
        sidecar.write_text("{}", encoding="utf-8")
        _age_file(sidecar, age_seconds=60 * 86400)
        old_mtime = sidecar.stat().st_mtime
        _touch_current_session_files(basedir, "sess_t")
        new_mtime = sidecar.stat().st_mtime
        assert new_mtime > old_mtime

    def test_touch_protects_current_session_from_peer_sweep(
        self, tmp_path: Path
    ) -> None:
        basedir = _make_basedir(tmp_path)
        session = "sess_active"
        sidecar = sidecar_path(basedir, session)
        sidecar.write_text("{}", encoding="utf-8")
        _age_file(sidecar, age_seconds=60 * 86400)

        # Touch before the sweep — peer sweeper must not unlink it.
        _touch_current_session_files(basedir, session)

        # Run a sweep in a peer thread.
        result: dict[str, int] = {}

        def run_sweep() -> None:
            result["deleted"] = sweep_old_state(basedir, throttle_min=0)

        t = threading.Thread(target=run_sweep)
        t.start()
        t.join(timeout=5)

        assert sidecar.exists()

    @pytest.mark.parametrize(
        "missing_kind",
        ["sidecar", "lock", "diary", "audit", "all"],
    )
    def test_touch_current_session_files_swallows_missing_file_errors(
        self, tmp_path: Path, missing_kind: str
    ) -> None:
        # First-invocation case: any subset of files missing must not raise.
        from memeval.dreaming.events import diary_path_for as _diary

        basedir = _make_basedir(tmp_path)
        session = "sess_missing"

        files = {
            "sidecar": sidecar_path(basedir, session),
            "lock": lock_path(basedir, session),
            "diary": _diary(basedir, session),
            "audit": audit_path(basedir, session),
        }

        # Create everything, then remove the requested subset.
        for path in files.values():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("data", encoding="utf-8")

        if missing_kind == "all":
            for path in files.values():
                path.unlink()
        else:
            files[missing_kind].unlink()

        # Must not raise.
        _touch_current_session_files(basedir, session)

    def test_touch_swallows_oserror_per_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        basedir = _make_basedir(tmp_path)
        session = "sess_err"
        sidecar = sidecar_path(basedir, session)
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text("{}", encoding="utf-8")

        call_count = {"n": 0}
        real_utime = os.utime

        def failing_utime(path: Any, times: Any) -> None:
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise PermissionError("denied")
            real_utime(path, times)

        monkeypatch.setattr(os, "utime", failing_utime)
        with caplog.at_level(logging.WARNING, logger="memeval.dreaming._state"):
            _touch_current_session_files(basedir, session)
        # Did NOT raise; continued past the failing file.
        assert call_count["n"] >= 1


class TestMaxAuditLinesPerFileConst:
    """halliday F11 (criterion 170) — forward-defense seam for audit rotation."""

    def test_max_audit_lines_per_file_const_exists(self) -> None:
        assert hasattr(_state, "MAX_AUDIT_LINES_PER_FILE")
        # Default is None per plan-v2 §3.
        assert MAX_AUDIT_LINES_PER_FILE is None
        hints = typing.get_type_hints(_state)
        assert "MAX_AUDIT_LINES_PER_FILE" in hints
        # Annotation is `int | None`.
        annotated = hints["MAX_AUDIT_LINES_PER_FILE"]
        # `int | None` resolves to typing.Optional[int] / Union[int, None].
        args = typing.get_args(annotated)
        assert int in args
        assert type(None) in args


# =========================================================================== #
# A. Module surface (relevant to test_state)
# =========================================================================== #
class TestStateModuleSurface:
    """Rubric §A criterion 7 — _state.py exports the required public names."""

    def test_state_module_surface(self) -> None:
        required = {
            "resolve_basedir",
            "sidecar_path",
            "lock_path",
            "SidecarState",
            "load_sidecar",
            "sweep_old_state",
            "_LockHeld",
            "_per_session_lock",
            "RECENT_MEMORY_CAP",
        }
        missing = required - set(dir(_state))
        assert not missing, f"missing public names: {sorted(missing)}"


# =========================================================================== #
# Audit-write fail-open wrapper (halliday F13 — coverage of _state helper)
# =========================================================================== #
class TestWriteAuditFailOpen:
    """halliday F13 — _write_audit_fail_open swallows errors per ADR-005."""

    def test_write_audit_fail_open_calls_writer_on_happy_path(
        self, tmp_path: Path
    ) -> None:
        target = audit_path(tmp_path, "sess_a")
        _write_audit_fail_open(
            target,
            chunk_id=0,
            pre="raw text",
            post="redacted text",
            detected={},
        )
        assert target.exists()
        record = json.loads(target.read_text(encoding="utf-8").strip())
        assert record["chunk_id"] == 0
        assert record["pre"] == "raw text"
        assert record["post"] == "redacted text"

    def test_write_audit_fail_open_swallows_exceptions(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(*args: Any, **kwargs: Any) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(_state, "write_audit_record", boom)
        # Must not raise.
        _write_audit_fail_open(
            tmp_path / "audit.jsonl",
            chunk_id=5,
            pre="x",
            post="x",
            detected={},
        )

    def test_write_audit_fail_open_appends_on_retry(self, tmp_path: Path) -> None:
        target = audit_path(tmp_path, "sess_retry")
        _write_audit_fail_open(
            target, chunk_id=1, pre="a", post="a", detected={}
        )
        _write_audit_fail_open(
            target, chunk_id=1, pre="a", post="a", detected={}
        )
        # F10 contract: retries append; downstream dedups by chunk_id.
        lines = target.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2


# =========================================================================== #
# Sweep concurrency — F6-related sweeper-race coverage
# =========================================================================== #
class TestSweepConcurrency:
    """halliday F6 — marker write is atomic content-wise (no torn-bytes).

    Per plan §8 risks row F6, the atomicity guarantee is "two concurrent
    writes can't tear" (i.e. you never read half of write-A's bytes and
    half of write-B's) — supplied by ``os.replace``. Whether both
    concurrent sweepers complete without error is a stronger property
    not promised by the implementation (their ``.last-swept.tmp`` siblings
    can collide); the marker's content integrity is the floor we test.
    """

    def test_serial_sweeps_leave_marker_with_valid_contents(
        self, tmp_path: Path
    ) -> None:
        basedir = _make_basedir(tmp_path)
        for _ in range(3):
            sweep_old_state(basedir, throttle_min=0)
        marker = basedir / "dream" / ".last-swept"
        assert marker.exists()
        # No torn write — contents parse cleanly as a float (a unix ts).
        contents = marker.read_text(encoding="utf-8").strip()
        float(contents)
