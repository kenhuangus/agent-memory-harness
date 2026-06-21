"""Events shim tests — ADR-dreaming-009 PR2 stub.

PR2 ships a logging-only stub of `emit()`. PR3 swaps in the local
diary writer; these tests verify the PR2 surface (never raises, logs
at DEBUG with the event_type + fields).
"""

from __future__ import annotations

import logging
import socket

import pytest

from memeval.dreaming.events import emit


def test_emit_does_not_raise_with_no_fields():
    emit("simple_event")


def test_emit_does_not_raise_with_kwargs():
    emit("event_with_fields", a=1, b="x", c=None, d=[1, 2, 3])


def test_emit_logs_at_debug_with_event_type(caplog):
    caplog.set_level(logging.DEBUG, logger="memeval.dreaming.events")
    emit("my_event", k="v")
    msgs = [r.getMessage() for r in caplog.records]
    assert any("my_event" in m and "'k': 'v'" in m for m in msgs)


def test_emit_makes_no_network_connect(monkeypatch):
    """PR2 stub is logging-only — no diary writer, no network."""

    def _no_connect(self, *args, **kwargs):
        raise AssertionError(f"network connect attempted by emit(): {args!r}")

    monkeypatch.setattr(socket.socket, "connect", _no_connect)
    emit("event_with_no_network", count=3)


def test_emit_does_not_propagate_errors_from_unrepresentable_fields():
    """PR2 contract: fail-open. Weird fields should not break the caller.

    The stub calls _logger.debug with %s formatting which calls repr() on
    the fields dict. Most objects can be repr()'d; this test pins that
    even with an awkward custom object, emit() doesn't surface an error.
    """

    class WeirdReprable:
        def __repr__(self) -> str:
            return "weird"

    # Must not raise.
    emit("event_weird", thing=WeirdReprable())
