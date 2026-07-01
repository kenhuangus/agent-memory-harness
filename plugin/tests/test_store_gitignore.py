"""Tests for the store's self-scaffolded .gitignore (ADR-harness-017).

A store keeps its markdown memories shareable through git and everything else
(databases, locks, events, dream state) out of it. The first writer into a fresh
store drops a .gitignore saying exactly that; user edits are never clobbered.
"""

from __future__ import annotations

from cookbook_memory.core.config import STORE_GITIGNORE, ensure_store_gitignore
from cookbook_memory.core.events import EventStream


def test_scaffold_writes_gitignore_into_new_store(tmp_path):
    store = tmp_path / "store"
    ensure_store_gitignore(store)
    body = (store / ".gitignore").read_text()
    assert body == STORE_GITIGNORE
    # the load-bearing lines: ignore everything, keep dirs traversable, keep
    # the scaffold itself, keep the markdown memories
    lines = [l for l in body.splitlines() if l and not l.startswith("#")]
    assert lines == ["*", "!*/", "!.gitignore", "!markdown/**/*.md"]


def test_scaffold_never_overwrites_user_edits(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    (store / ".gitignore").write_text("# mine\n")
    ensure_store_gitignore(store)
    assert (store / ".gitignore").read_text() == "# mine\n"


def test_scaffold_is_fail_open(tmp_path):
    # A file where the store dir should be -> mkdir fails -> no raise.
    bogus = tmp_path / "not-a-dir"
    bogus.write_text("")
    ensure_store_gitignore(bogus / "store")  # must not raise


def test_event_stream_scaffolds_store_on_first_write(tmp_path):
    store = tmp_path / "fresh-store"
    EventStream(store / "events.jsonl").emit("note", hook="SessionStart")
    assert (store / "events.jsonl").is_file()
    assert (store / ".gitignore").read_text() == STORE_GITIGNORE
