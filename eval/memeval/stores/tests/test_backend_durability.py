"""Backend durability hardening — the crash/concurrency instrument (EVAL-FIRST).

Owner: Brent (@bgibson1618). Written BEFORE the fixes (RED), then made GREEN by the
Backend Durability Hardening Arc. Provenance: ``BACKEND_DURABILITY_AUDIT.md``
(2026-06-23) — a 52-agent durability Workflow that empirically crash-tested the
sqlite + markdown/OKF backends and found the markdown/OKF store was *POC persistence*
(non-atomic writes, no cross-process lock, frozen RAM mirror, O(N) delete) and the
SQLite-backed stores hold a thread-affine connection that deterministically crashes
under the harness's own ThreadPoolExecutor.

Why these are CI-safe / deterministic (no flaky SIGKILL, no real multiprocessing where
avoidable): each property is proved by a *controlled* interleaving — monkeypatched
mid-write failure, injected partial files, two store instances over one dir in-process,
a read/parse spy, or a ``ThreadPoolExecutor``. A torn write is simulated, not raced.

The six properties (audit severities in brackets):

1. Markdown atomic write [HIGH-1] — a torn mid-write must not destroy the prior good doc.
2. Markdown cross-process lock + read-refresh [HIGH-2] — peer writes become visible after
   a refresh; concurrent same-id writes don't corrupt the bundle.
3. Markdown delete fast-path [HIGH-3] — delete of a canonical id is O(1) doc reads, not O(N).
4. SQLite thread-safety [MED] — driving one ``SqliteVectorStore`` from 2 threads must not
   raise ``sqlite3.ProgrammingError``.
5. SQLite write() rollback [LOW] — ``write()`` rolls back a failed commit like ``delete()``.
6. Graph thread-safety — the graph store's ``path=`` SQLite mirror gets the same treatment.

Stdlib-only; run from ``eval/``:
``python3 -m unittest memeval.stores.tests.test_backend_durability``
"""

from __future__ import annotations

import multiprocessing
import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from memeval.okf import OKFStore, _bundle_write_lock, _doc_relpath, split_doc
from memeval.schema import MemoryItem
from memeval.stores.graph_store import GraphStore
from memeval.stores.markdown_store import MarkdownStore
from memeval.stores.sqlite_store import SqliteVectorStore


def _item(item_id: str, content: str, *, ts: float = 0.0, tokens: int = 0) -> MemoryItem:
    return MemoryItem(item_id=item_id, content=content, timestamp=ts, tokens=tokens)


def _mp_writer(bundle_dir: str, idx: int, rounds: int) -> None:
    """Module-level (picklable) worker: a SEPARATE OS process writes the same hot id many
    times to the shared bundle. Used to genuinely exercise the cross-PROCESS flock — threads
    + atomic-replace alone prove parseability without proving the lock serializes peers."""
    s = OKFStore(bundle_dir)
    for n in range(rounds):
        s.write(_item("hot", f"process {idx} round {n} " + "x" * 300, ts=float(n)))


def _mp_lock_then_signal(bundle_dir: str, done) -> None:  # type: ignore[no-untyped-def]
    """Module-level (picklable) worker: acquire the bundle lock then set ``done``.

    If the parent holds the lock, ``flock(LOCK_EX)`` blocks here until release, so ``done``
    stays unset — that is what proves serialization. Once the parent releases, this proceeds."""
    with _bundle_write_lock(Path(bundle_dir)):
        done.set()


def _bundle_held_noop() -> bool:
    """True when the bundle lock is a no-op (fcntl unavailable, non-POSIX) — the
    serialization proof can't run there."""
    from memeval import okf as _okf_mod
    return _okf_mod._fcntl is None


def _reap(*procs) -> None:  # type: ignore[no-untyped-def]
    """Guarantee every spawned child is reaped, even on timeout/failure: terminate any that
    are still alive after a bounded join, then join again so no orphan bleeds into later tests
    or stalls teardown. Safe to call in a ``finally``."""
    for p in procs:
        if p is None:
            continue
        try:
            if p.is_alive():
                p.terminate()
            p.join(timeout=10)
        except (OSError, ValueError):  # already-dead / never-started child
            pass


# --------------------------------------------------------------------------- #
# Property 1 — Markdown atomic write [HIGH-1]
# --------------------------------------------------------------------------- #
class MarkdownAtomicWriteTests(unittest.TestCase):
    """A crash mid-write must leave the PRIOR good doc intact, not a torn/empty file
    that the next autoload silently drops. Deterministic: monkeypatch the persist so it
    raises AFTER the file would be touched but BEFORE the rename completes, then reopen a
    fresh store from the same dir and assert v1 survives."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.d = self._tmp.name

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_torn_write_preserves_prior_good_doc(self) -> None:
        s = OKFStore(self.d)
        s.write(_item("doc1", "version one canonical content", ts=1.0))
        canonical = Path(self.d) / _doc_relpath(s.get("doc1"))
        self.assertTrue(canonical.exists())
        original_bytes = canonical.read_bytes()

        # Simulate a crash partway through the second write: the durable temp/rename
        # machinery raises before the destination is atomically replaced. With a
        # non-atomic write_text this corrupts/truncates the live doc; with tmp.replace
        # the original file is untouched (a crash leaves the prior doc intact). We patch
        # ``os.replace`` (the stdlib rename) so the atomic store's rename step fails; a
        # store that still uses a bare ``Path.write_text`` never calls os.replace, so this
        # patch is inert there and the test instead catches the truncated/torn live file.
        real_replace = os.replace

        def boom_replace(src, dst, *a, **k):  # type: ignore[no-untyped-def]
            raise OSError("simulated crash mid-rename")

        os.replace = boom_replace  # type: ignore[assignment]
        try:
            try:
                s.write(_item("doc1", "version two should not corrupt v1", ts=2.0))
            except OSError:
                pass  # the atomic path raises on the failed rename; the non-atomic path does not
        finally:
            os.replace = real_replace  # type: ignore[assignment]

        # The on-disk canonical doc must still be the prior good copy (byte-identical),
        # never torn/empty/partial.
        self.assertTrue(canonical.exists(), "the prior good doc must survive a torn write")
        self.assertEqual(canonical.read_bytes(), original_bytes,
                         "a torn write must not corrupt the prior committed doc")

        # And a FRESH store (cold autoload) recovers v1 — proving no torn file was left
        # for import_bundle to silently drop.
        s2 = OKFStore(self.d)
        got = s2.get("doc1")
        self.assertIsNotNone(got, "v1 must autoload after a torn update")
        self.assertIn("version one", got.content)

    def test_no_tmp_artifacts_left_after_clean_write(self) -> None:
        s = OKFStore(self.d)
        s.write(_item("doc1", "clean content", ts=1.0))
        # A clean write must not strand a *.tmp sibling in the bundle.
        leftovers = list(Path(self.d).rglob("*.tmp"))
        self.assertEqual(leftovers, [], f"no tmp artifacts after a clean write: {leftovers}")

    def test_corrupt_doc_on_autoload_is_skipped_not_fatal(self) -> None:
        # Pairs with atomic write (MED autoload guard): one torn/undecodable .md must not
        # brick the whole store at construction.
        s = OKFStore(self.d)
        s.write(_item("good", "good content survives", ts=1.0))
        s.write(_item("alsogood", "another good doc", ts=2.0))
        # Inject an undecodable byte sequence into a stray concept doc.
        bad = Path(self.d) / "memory" / "torn.md"
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_bytes(b"---\ntype: Memory\n---\n\n\xff\xfe broken \x80 bytes")
        # Construction must NOT raise; the good docs are still loaded.
        s2 = OKFStore(self.d)
        self.assertIsNotNone(s2.get("good"), "a torn sibling must not brick the store")
        self.assertIsNotNone(s2.get("alsogood"))


# --------------------------------------------------------------------------- #
# Property 2 — Markdown cross-process lock + read-refresh [HIGH-2]
# --------------------------------------------------------------------------- #
class MarkdownRefreshAndLockTests(unittest.TestCase):
    """(a) Two store instances over one bundle dir: instance B's committed write becomes
    visible to instance A after a refresh (A is otherwise frozen at construction).
    (b) Concurrent same-id writes don't corrupt the index/file (flock serialization)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.d = self._tmp.name
        self._tmp2 = tempfile.TemporaryDirectory()
        self.d2 = self._tmp2.name   # a second, isolated bundle (the MarkdownStore sub-scenario)

    def tearDown(self) -> None:
        self._tmp.cleanup()
        self._tmp2.cleanup()

    def test_okf_auto_refresh_on_read_without_explicit_reload(self) -> None:
        # AUTO-refresh: A is the long-lived reader (MCP daemon), constructed BEFORE the peer
        # write. A NEVER calls reload(). A peer instance B commits a new concept; A's plain
        # get()/search()/all() must surface it via _maybe_refresh (the generation seam) —
        # RED on the old mtime-after-lock code, which would (a) tie mtimes within a tick or
        # (b) stale-ack via the post-lock sample and never reload.
        a = OKFStore(self.d)             # frozen-at-construction reader; never reload()'d
        b = OKFStore(self.d)             # the peer writer (a separate instance == a separate process model)
        b.write(_item("peerdoc", "peer coined a brand new concept zylophistic", ts=1.0))
        self.assertIsNotNone(a.get("peerdoc"),
                             "get() must auto-refresh to a peer's committed write (no explicit reload)")
        self.assertTrue(any(h.item_id == "peerdoc" for h in a.search("zylophistic", k=5)),
                        "search() must auto-refresh + surface a peer's committed write")
        self.assertIn("peerdoc", {i.item_id for i in a.all()},
                      "all() must auto-refresh to include a peer's committed write")

    def test_okf_coherence_survives_coarse_mtime_collision(self) -> None:
        # The granularity-fragility half of gate finding #1, reproduced deterministically: when
        # A's own write and a peer B's write fall in the SAME filesystem mtime tick (coarse
        # st_mtime — a real case on many filesystems), an mtime-based staleness signal does not
        # change between them, so A records the same mtime and NEVER reloads B's commit (permanent
        # stale-serve). We simulate the coarse tick by pinning _root_mtime to a constant; a
        # GENERATION counter is immune (it advances per write regardless of mtime), so A still
        # detects B. RED on the old mtime code, GREEN on the generation seam.
        #
        # The patch targets the OLD code's signal (_root_mtime). The NEW code does not consult
        # _root_mtime at all (it reads .okf-generation), so the patch is inert there — exactly
        # what makes this a clean old/new distinguisher without deadlocking the in-process lock.
        a = OKFStore(self.d)
        b = OKFStore(self.d)
        a.write(_item("a_own", "a writes its own concept", ts=1.0))
        # Pin the (old) mtime signal so a peer write cannot advance it — the same-tick collision.
        if hasattr(a, "_root_mtime"):
            a._root_mtime = lambda: 1234.0  # type: ignore[assignment,method-assign]
            a._loaded_mtime = 1234.0        # type: ignore[attr-defined]
        b.write(_item("b_peer", "b commits in the same mtime tick", ts=2.0))
        # A did NOT reload() and did NOT write again; a plain read must still catch the peer commit.
        self.assertIsNotNone(a.get("b_peer"),
                             "a peer commit must be detected even when mtime cannot distinguish it")
        self.assertIsNotNone(a.get("a_own"), "our own write is not lost by the refresh")

    def test_markdown_peer_write_searchable_without_explicit_reload(self) -> None:
        # EXERCISE MarkdownStore (its inverted index must refresh in lockstep with OKF). A
        # is the long-lived reader; B (peer) adds a doc with a COINED token absent from A's
        # postings. A.search(token) must find it with NO explicit reload — RED on the old
        # code where candidate_ids came from stale _postings before any refresh.
        a = MarkdownStore(self.d)
        b = MarkdownStore(self.d)
        a.write(_item("seed", "seed alpha beta", ts=1.0))   # A has postings for seed's tokens only
        b.write(_item("peerm", "qwizzlefang unique peer token", ts=2.0))  # coined token not in A's postings
        hits = a.search("qwizzlefang", k=5)
        self.assertTrue(any(h.item_id == "peerm" for h in hits),
                        "MarkdownStore.search must rebuild postings for a peer's new doc (no reload)")
        self.assertIsNotNone(a.get("peerm"), "MarkdownStore.get must auto-refresh to the peer doc")

    def test_writer_side_does_not_stale_ack_an_unloaded_peer(self) -> None:
        # The R2 writer-side stale-ack hole (gate finding #1+2+3 share this root cause): A loads
        # gen 0; peer B commits a NEW doc (gen 1); A then writes its OWN different doc. On the
        # 9db51b0 code A's own write bumps to gen 2 and records _loaded_generation=2 WITHOUT ever
        # loading B's gen-1 doc, so every future read sees on-disk==loaded and NEVER reloads ->
        # A permanently misses B. The reconcile-under-lock fix pulls B in during A's own write,
        # so A holds B afterward. Proven at BOTH layers (OKFStore and MarkdownStore) and via
        # search on the coined token — all WITHOUT any explicit reload().
        a_okf = OKFStore(self.d)
        b_okf = OKFStore(self.d)
        b_okf.write(_item("b_peer", "zphlanitic coined peer concept", ts=1.0))  # peer commits gen 1
        a_okf.write(_item("a_own", "a's own unrelated concept", ts=2.0))         # A's OWN write (gen 2)
        # A never reloaded; a plain read MUST surface the peer it skipped over.
        self.assertIsNotNone(a_okf.get("b_peer"),
                             "writer-side: an own write must not stale-ack an unloaded peer (OKFStore.get)")
        self.assertTrue(any(h.item_id == "b_peer" for h in a_okf.search("zphlanitic", k=5)),
                        "writer-side: the skipped peer must be searchable (OKFStore.search)")
        self.assertIsNotNone(a_okf.get("a_own"), "the own write is not lost")

        # Same race through MarkdownStore (the inverted index must reflect the reconciled peer).
        a_md = MarkdownStore(self.d2)
        b_md = MarkdownStore(self.d2)
        b_md.write(_item("b_peer", "blorptacular coined peer token", ts=1.0))    # peer gen 1
        a_md.write(_item("a_own", "a markdown own concept", ts=2.0))             # A's own write (gen 2)
        self.assertTrue(any(h.item_id == "b_peer" for h in a_md.search("blorptacular", k=5)),
                        "writer-side: MarkdownStore postings must include the reconciled peer (no reload)")
        self.assertIsNotNone(a_md.get("b_peer"),
                             "writer-side: MarkdownStore.get must surface the reconciled peer")

    def test_cross_process_flock_serializes_concurrent_writers(self) -> None:
        # Genuinely cross-PROCESS: separate OS processes (multiprocessing) hammer the SAME id.
        # The bundle flock must serialize their tmp-write+replace so a cold reload finds exactly
        # one complete, parseable canonical doc — never an interleaved/torn file. If spawning
        # is unavailable in this CI, fall back to the controlled-interleaving lock proof below.
        rounds = 25
        procs: list = []
        try:
            ctx = multiprocessing.get_context("spawn")
            procs = [ctx.Process(target=_mp_writer, args=(self.d, i, rounds)) for i in range(4)]
            for p in procs:
                p.start()
            for p in procs:
                p.join(timeout=60)
            self.assertTrue(all(p.exitcode == 0 for p in procs),
                            f"all writer processes must exit cleanly: {[p.exitcode for p in procs]}")
        except (OSError, ValueError, RuntimeError) as exc:  # pragma: no cover - CI sandbox without spawn
            self.skipTest(f"multiprocessing spawn unavailable: {exc}")
        finally:
            _reap(*procs)  # never leave a hung writer behind, even on timeout/failure

        canonical = Path(self.d) / _doc_relpath(_item("hot", ""))
        self.assertTrue(canonical.exists())
        fm, _ = split_doc(canonical.read_text(encoding="utf-8"))
        self.assertEqual(str(fm.get("x_item_id")), "hot",
                         "the canonical doc must be a complete, non-torn doc after concurrent processes")
        s2 = OKFStore(self.d)
        self.assertIsNotNone(s2.get("hot"), "concurrent cross-process writes leave a recoverable doc")

    def test_flock_actually_serializes_held_lock_blocks_peer(self) -> None:
        # Direct proof the flock serializes (not just that atomic-replace yields parseable
        # files): while THIS process holds the bundle lock, a child process trying to acquire
        # it must BLOCK until we release — it cannot complete its write meanwhile. A no-op
        # lock (the non-POSIX fallback, or no lock at all) would let the child finish immediately.
        if _bundle_held_noop():
            self.skipTest("fcntl unavailable (non-POSIX) — advisory lock is a no-op here")
        ctx = multiprocessing.get_context("spawn")
        done = ctx.Event()
        child = None
        try:
            # Acquire the lock in-process and hold it across the child's attempt.
            with _bundle_write_lock(Path(self.d)):
                child = ctx.Process(target=_mp_lock_then_signal, args=(self.d, done))
                child.start()
                # The child cannot acquire the held lock; it must NOT signal within this window.
                blocked = not done.wait(timeout=1.0)
                self.assertTrue(blocked, "a peer must BLOCK on the held bundle lock (flock serializes)")
            # Released now -> the child proceeds and signals.
            self.assertTrue(done.wait(timeout=30), "the peer must proceed once the lock is released")
            child.join(timeout=30)
        finally:
            _reap(child)  # reap even if an assertion above fails while the child is blocked


# --------------------------------------------------------------------------- #
# Property 3 — Markdown delete fast-path [HIGH-3]
# --------------------------------------------------------------------------- #
class _ReadSpy:
    """Counts per-CONCEPT-DOC (.md) text reads so a delete's doc-scan cost is observable.

    Only ``*.md`` reads are counted — the O(N) full-scan is the concern. The constant
    generation/lock dotfile reads (``.okf-generation``) are deliberately excluded: they are
    fixed overhead (the reconcile-under-lock coherence seam reads the counter), not a function
    of bundle size, and counting them would couple this O(1) assertion to that seam."""

    def __init__(self) -> None:
        self.reads = 0
        self._orig = Path.read_text

    def __enter__(self) -> "_ReadSpy":
        spy = self

        def counting_read_text(self_path, *a, **k):  # type: ignore[no-untyped-def]
            if str(self_path).endswith(".md"):
                spy.reads += 1
            return spy._orig(self_path, *a, **k)

        Path.read_text = counting_read_text  # type: ignore[assignment]
        return self

    def __exit__(self, *exc) -> None:  # type: ignore[no-untyped-def]
        Path.read_text = self._orig  # type: ignore[assignment]


class MarkdownDeleteFastPathTests(unittest.TestCase):
    """Delete of a CANONICAL id must not full-scan + parse every .md in the bundle.
    With N docs present, deleting one canonical id reads O(1) docs, not O(N)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.d = self._tmp.name

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_delete_canonical_is_constant_read_not_full_scan(self) -> None:
        s = OKFStore(self.d)
        n = 30
        for i in range(n):
            s.write(_item(f"doc{i:03d}", f"content number {i}", ts=float(i)))

        with _ReadSpy() as spy:
            self.assertTrue(s.delete("doc015"))
        # The fast path unlinks the deterministic canonical path; it must NOT read+parse
        # every other doc in the bundle. Allow a small constant; reject O(N).
        self.assertLess(spy.reads, n,
                        f"delete of a canonical id must not full-scan {n} docs (read {spy.reads})")
        self.assertLessEqual(spy.reads, 2,
                             f"canonical delete should be O(1) doc reads, got {spy.reads}")

    def test_foreign_filename_delete_still_correct(self) -> None:
        # Correctness for the foreign case: a doc whose on-disk filename is NOT the
        # canonical slug (an imported bundle) must still be deleted (fall back to scan).
        s = OKFStore(self.d)
        s.write(_item("native", "native doc", ts=1.0))
        # Hand-place a foreign-named doc that parses to id "foreign".
        foreign = Path(self.d) / "imported" / "weird-Name.md"
        foreign.parent.mkdir(parents=True, exist_ok=True)
        foreign.write_text(
            "---\ntype: Memory\nx_item_id: foreign\n---\n\nforeign body\n", encoding="utf-8"
        )
        s2 = OKFStore(self.d)  # autoload picks up the foreign doc
        self.assertIsNotNone(s2.get("foreign"))
        self.assertTrue(s2.delete("foreign"), "foreign-named doc must be deletable")
        self.assertFalse(foreign.exists(), "the foreign doc file must be unlinked")
        s3 = OKFStore(self.d)
        self.assertIsNone(s3.get("foreign"), "the delete must persist (no resurrection)")

    def test_delete_absent_id_is_false(self) -> None:
        s = OKFStore(self.d)
        s.write(_item("present", "here", ts=1.0))
        self.assertFalse(s.delete("absent"))


# --------------------------------------------------------------------------- #
# Property 4 — SQLite thread-safety [MED]
# --------------------------------------------------------------------------- #
class SqliteThreadSafetyTests(unittest.TestCase):
    """One ``SqliteVectorStore`` driven from a 2-worker pool must not raise
    ``sqlite3.ProgrammingError`` (the thread-affine connection crash)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self._tmp.name) / "v.db")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_two_threads_no_programming_error(self) -> None:
        store = SqliteVectorStore(self.path)
        errors: list[BaseException] = []

        def worker(i: int) -> None:
            try:
                for n in range(25):
                    store.write(_item(f"t{i}-{n}", f"thread {i} item {n} kubernetes deploy", ts=float(n)))
                    store.search("kubernetes deploy", k=3)
                    store.get(f"t{i}-{n}")
                    store.all()
            except BaseException as exc:  # noqa: BLE001 — capture for the assertion
                errors.append(exc)

        with ThreadPoolExecutor(max_workers=2) as ex:
            list(ex.map(worker, range(2)))

        prog_errors = [e for e in errors if isinstance(e, sqlite3.ProgrammingError)]
        self.assertEqual(prog_errors, [],
                         f"thread-affine connection crashed across threads: {prog_errors}")
        self.assertEqual(errors, [], f"no errors expected driving the store from 2 threads: {errors}")
        # All writes landed (no lost-update / interleave corruption).
        self.assertEqual(len(store.all()), 50, "every concurrent write persisted")
        store.close()

    def test_post_close_write_fails_loud_and_close_is_idempotent(self) -> None:
        # close() takes _lock then marks closed; protocol methods check _closed UNDER _lock, so
        # a write after close fails loud instead of silently mutating a closed connection.
        store = SqliteVectorStore(self.path)
        store.write(_item("a", "before close", ts=1.0))
        store.close()
        store.close()  # idempotent — must not raise
        with self.assertRaises(RuntimeError):
            store.write(_item("b", "after close must fail loud", ts=2.0))
        with self.assertRaises(RuntimeError):
            store.all()
        # The pre-close write persisted to disk; a fresh store reads exactly it (no phantom 'b').
        cold = SqliteVectorStore(self.path)
        self.assertEqual({i.item_id for i in cold.all()}, {"a"})
        cold.close()


# --------------------------------------------------------------------------- #
# Property 5 — SQLite write() rollback [LOW]
# --------------------------------------------------------------------------- #
class SqliteWriteRollbackTests(unittest.TestCase):
    """``write()`` must roll back a failed commit (like ``delete()`` already does) so a
    failure precisely at commit cannot strand a partial txn a later write silently commits."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self._tmp.name) / "v.db")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_write_rolls_back_on_commit_failure(self) -> None:
        store = SqliteVectorStore(self.path)
        store.write(_item("a", "first good item", ts=1.0))

        # ``sqlite3.Connection.commit`` is a read-only C attribute, so we wrap the live
        # connection: ``commit`` raises ONCE (simulating a failure precisely at commit),
        # everything else (execute, rollback, …) delegates to the real connection — so the
        # store's own ``self._conn.rollback()`` actually runs against the live txn.
        class _FlakyCommitConn:
            def __init__(self, real):  # type: ignore[no-untyped-def]
                self._real = real
                self._fail_next = True

            def commit(self):  # type: ignore[no-untyped-def]
                if self._fail_next:
                    self._fail_next = False
                    raise sqlite3.OperationalError("simulated commit failure")
                return self._real.commit()

            def __getattr__(self, name):  # type: ignore[no-untyped-def]
                return getattr(self._real, name)

        real_conn = store._conn
        store._conn = _FlakyCommitConn(real_conn)  # type: ignore[assignment]
        with self.assertRaises(sqlite3.OperationalError):
            store.write(_item("b", "should be rolled back", ts=2.0))
        store._conn = real_conn  # type: ignore[assignment]

        # The failed write must NOT be stranded as an open txn. A later clean write must
        # commit ONLY its own row — `b` must be absent (it was rolled back).
        store.write(_item("c", "third good item", ts=3.0))
        store.close()
        cold = SqliteVectorStore(self.path)
        ids = {i.item_id for i in cold.all()}
        self.assertEqual(ids, {"a", "c"},
                         "a write whose commit failed must roll back, not ride a later commit")
        cold.close()


# --------------------------------------------------------------------------- #
# Property 6 — Graph store thread-safety
# --------------------------------------------------------------------------- #
class GraphThreadSafetyTests(unittest.TestCase):
    """The graph store's ``path=`` SQLite mirror gets the same thread-safe-connection
    treatment: driven from 2 threads, no ``sqlite3.ProgrammingError``."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self._tmp.name) / "g.db")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_two_threads_no_programming_error(self) -> None:
        store = GraphStore(path=self.path)
        errors: list[BaseException] = []

        def worker(i: int) -> None:
            try:
                for n in range(25):
                    store.write(_item(f"g{i}-{n}", f"graph {i} node {n} kubernetes", ts=float(n)))
                    store.search("kubernetes", k=3)
                    store.get(f"g{i}-{n}")
                    store.all()
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        with ThreadPoolExecutor(max_workers=2) as ex:
            list(ex.map(worker, range(2)))
        store.close()

        prog_errors = [e for e in errors if isinstance(e, sqlite3.ProgrammingError)]
        self.assertEqual(prog_errors, [],
                         f"graph mirror connection crashed across threads: {prog_errors}")
        self.assertEqual(errors, [], f"no errors expected driving the graph store from 2 threads: {errors}")
        cold = GraphStore(path=self.path)
        self.assertEqual(len(cold.all()), 50, "all concurrent graph writes persisted")
        cold.close()


if __name__ == "__main__":
    unittest.main()
