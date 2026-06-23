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

import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from memeval.okf import OKFStore, _doc_relpath, split_doc
from memeval.schema import MemoryItem
from memeval.stores.graph_store import GraphStore
from memeval.stores.markdown_store import MarkdownStore
from memeval.stores.sqlite_store import SqliteVectorStore


def _item(item_id: str, content: str, *, ts: float = 0.0, tokens: int = 0) -> MemoryItem:
    return MemoryItem(item_id=item_id, content=content, timestamp=ts, tokens=tokens)


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

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_peer_write_visible_after_refresh(self) -> None:
        a = OKFStore(self.d)
        b = OKFStore(self.d)
        a.write(_item("seed", "seed content", ts=1.0))
        # B is the long-lived reader (e.g. the MCP daemon); A is the peer writer
        # (e.g. the Daydreamer). B was constructed BEFORE A's peer write below.
        b_fresh = OKFStore(self.d)  # B sees seed at its own construction
        a.write(_item("peer", "peer committed this concept", ts=2.0))
        # Without a refresh seam, b_fresh is frozen at construction and never sees `peer`.
        b_fresh.reload()
        self.assertIsNotNone(b_fresh.get("peer"),
                             "a peer's committed write must be visible after reload()")
        self.assertTrue(any(h.item_id == "peer" for h in b_fresh.search("peer committed", k=5)),
                        "refresh must update the recall path, not just get()")

    def test_concurrent_same_id_writes_do_not_corrupt_bundle(self) -> None:
        # Two store instances over one dir hammer the SAME id; the flock serializes the
        # tmp-write+replace so the canonical doc is always a complete, parseable doc
        # (never an interleaved/torn file) and a fresh autoload recovers exactly one good copy.
        stores = [OKFStore(self.d) for _ in range(2)]

        def writer(idx: int) -> None:
            s = stores[idx % len(stores)]
            for n in range(20):
                s.write(_item("hot", f"writer {idx} round {n} " + "x" * 200, ts=float(n)))

        with ThreadPoolExecutor(max_workers=4) as ex:
            list(ex.map(writer, range(4)))

        # A cold reload must find exactly one good, fully-parseable canonical doc for `hot`.
        canonical = Path(self.d) / _doc_relpath(_item("hot", ""))
        self.assertTrue(canonical.exists())
        fm, body = split_doc(canonical.read_text(encoding="utf-8"))
        self.assertEqual(str(fm.get("x_item_id")), "hot",
                         "the canonical doc must be a complete, non-torn doc after concurrent writes")
        s2 = OKFStore(self.d)
        self.assertIsNotNone(s2.get("hot"), "concurrent same-id writes leave a recoverable doc")


# --------------------------------------------------------------------------- #
# Property 3 — Markdown delete fast-path [HIGH-3]
# --------------------------------------------------------------------------- #
class _ReadSpy:
    """Counts per-file text reads so a delete's disk-read cost is observable."""

    def __init__(self) -> None:
        self.reads = 0
        self._orig = Path.read_text

    def __enter__(self) -> "_ReadSpy":
        spy = self

        def counting_read_text(self_path, *a, **k):  # type: ignore[no-untyped-def]
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
