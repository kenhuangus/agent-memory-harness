"""FP/FN audit-writer tests (rubric §Q + §R) — ADR-dreaming-011 §3."""

from __future__ import annotations

import builtins
import json
import socket
from pathlib import Path

import pytest

from memeval.dreaming.redaction._audit import audit_path_for, write_audit_record


# --- Q. shape + path composition ---------------------------------------- #
def test_audit_writer_exists():
    assert callable(write_audit_record)


def test_audit_writer_path_pattern():
    p = audit_path_for("/tmp/store-dir", "sess-abc")
    assert str(p).endswith("/dream/sess-abc.redact-audit.jsonl")


def test_audit_writer_appends_one_line_per_call(tmp_path: Path):
    f = tmp_path / "x.jsonl"
    for i in range(3):
        write_audit_record(
            f, chunk_id=i, pre="raw", post="clean", detected={"AWSKey": 1}
        )
    lines = f.read_text().splitlines()
    assert len(lines) == 3


def test_audit_writer_record_shape(tmp_path: Path):
    f = tmp_path / "x.jsonl"
    write_audit_record(
        f,
        chunk_id=7,
        pre="raw secret AKIA…",
        post="raw secret [REDACTED:AWS Access Key]",
        detected={"AWS Access Key": 1, "Anthropic API Key": 0},
    )
    [line] = f.read_text().splitlines()
    rec = json.loads(line)
    assert set(rec.keys()) == {"ts", "chunk_id", "pre", "post", "detected"}


def test_audit_writer_ts_is_unix_float(tmp_path: Path):
    f = tmp_path / "x.jsonl"
    write_audit_record(f, chunk_id=0, pre="", post="", detected={})
    [line] = f.read_text().splitlines()
    rec = json.loads(line)
    assert isinstance(rec["ts"], (int, float))
    assert rec["ts"] > 1_700_000_000  # sanity: post-2023


def test_audit_writer_chunk_id_is_int(tmp_path: Path):
    f = tmp_path / "x.jsonl"
    write_audit_record(f, chunk_id=42, pre="", post="", detected={})
    rec = json.loads(f.read_text().splitlines()[0])
    assert rec["chunk_id"] == 42
    assert isinstance(rec["chunk_id"], int)


def test_audit_writer_pre_and_post_are_verbatim(tmp_path: Path):
    f = tmp_path / "x.jsonl"
    pre = "  raw\twith whitespace and unicode ☕  "
    post = "  raw\twith [REDACTED:X] and unicode ☕  "
    write_audit_record(f, chunk_id=0, pre=pre, post=post, detected={})
    rec = json.loads(f.read_text().splitlines()[0])
    assert rec["pre"] == pre
    assert rec["post"] == post


def test_audit_writer_detected_is_count_dict(tmp_path: Path):
    f = tmp_path / "x.jsonl"
    write_audit_record(
        f,
        chunk_id=0,
        pre="",
        post="",
        detected={"AWS Access Key": 2, "GitHub Token": 1},
    )
    rec = json.loads(f.read_text().splitlines()[0])
    assert rec["detected"] == {"AWS Access Key": 2, "GitHub Token": 1}


def test_audit_writer_creates_parent_dir(tmp_path: Path):
    target = tmp_path / "does" / "not" / "exist" / "x.jsonl"
    write_audit_record(target, chunk_id=0, pre="", post="", detected={})
    assert target.exists()


def test_audit_writer_uses_append_mode(tmp_path: Path):
    """Two calls — second must not truncate the first."""
    f = tmp_path / "x.jsonl"
    write_audit_record(f, chunk_id=0, pre="first", post="first", detected={})
    write_audit_record(f, chunk_id=1, pre="second", post="second", detected={})
    lines = f.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["pre"] == "first"
    assert json.loads(lines[1])["pre"] == "second"


# --- R. local-only invariant ------------------------------------------- #
def test_audit_writer_makes_no_network_connect(tmp_path: Path, monkeypatch):
    def _no_connect(self, *args, **kwargs):
        raise AssertionError(f"network connect attempted: {args!r}")

    monkeypatch.setattr(socket.socket, "connect", _no_connect)
    write_audit_record(
        tmp_path / "x.jsonl",
        chunk_id=0,
        pre="some text",
        post="some text",
        detected={"X": 1},
    )


def test_audit_writer_writes_only_to_supplied_path(tmp_path: Path, monkeypatch):
    target = tmp_path / "expected.jsonl"
    opened_for_write: list[str] = []
    real_open = builtins.open

    def spy_open(path, mode="r", *args, **kwargs):
        if any(m in str(mode) for m in ("w", "a", "+")):
            opened_for_write.append(str(path))
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", spy_open)
    write_audit_record(target, chunk_id=0, pre="", post="", detected={})

    # Only the supplied target should be opened for write/append.
    write_paths = [p for p in opened_for_write if "redact-audit" in p or p == str(target)]
    assert write_paths == [str(target)], (
        f"unexpected write paths: {opened_for_write!r}"
    )
