"""Tests for the benchmark-native evaluation package.

Per-benchmark implementers add ``test_native_<benchmark>.py`` modules here
(e.g. ``test_native_longmemeval.py``), each driving
:func:`memeval.native.runner.run_native` (or their evaluator directly) over the
matching ``eval/tests/fixtures/<benchmark>.json`` fixture with EchoAgent /
EchoModel + the DeterministicJudge — fully offline, no network, no LLM.

The scaffolding's own contract tests live in ``test_native_scaffold.py``.
"""

from __future__ import annotations

__all__: list[str] = []
