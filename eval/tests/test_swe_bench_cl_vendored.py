"""Offline test: the SWE-Bench-CL loader resolves the vendored dataset copy.

Asserts that, with NO explicit source and NO network, the loader returns the
full vendored dataset (273 tasks across 8 per-repo sequences) by parsing the
in-tree ``eval/memeval/data/swe_bench_cl/SWE-Bench-CL.json``.

This test is deliberately self-contained and stdlib-only: it must never import
``datasets`` / ``huggingface_hub`` / ``requests``. To prove no remote path is
taken, it blocks those imports for the duration of the load and fails if any is
attempted.

Runnable two ways (no pytest install required):

    python -m pytest tests/test_swe_bench_cl_vendored.py
    python tests/test_swe_bench_cl_vendored.py
"""

from __future__ import annotations

import builtins
import sys
import unittest
from pathlib import Path

# Make ``memeval`` importable when run directly from anywhere.
_THIS = Path(__file__).resolve()
_BASE_DIR = _THIS.parent.parent  # .../eval (holds the memeval package)
if str(_BASE_DIR) not in sys.path:
    sys.path.insert(0, str(_BASE_DIR))

from memeval.loaders import get_loader  # noqa: E402
from memeval.loaders.swe_bench_cl import _VENDORED, SWEBenchCLLoader  # noqa: E402
from memeval.schema import Benchmark, TaskKind  # noqa: E402

#: Banned modules that would indicate a remote/network code path was taken.
_REMOTE_MODULES = ("datasets", "huggingface_hub", "requests")

_EXPECTED_TASKS = 273
_EXPECTED_GROUPS = 8
#: Per-sequence task counts (the eight per-repo continual-learning sequences).
_EXPECTED_SEQUENCE_SIZES = {
    "django_django_sequence": 50,
    "sympy_sympy_sequence": 50,
    "sphinx-doc_sphinx_sequence": 44,
    "matplotlib_matplotlib_sequence": 34,
    "scikit-learn_scikit-learn_sequence": 32,
    "astropy_astropy_sequence": 22,
    "pydata_xarray_sequence": 22,
    "pytest-dev_pytest_sequence": 19,
}


class _NoRemoteImports:
    """Context manager: raise if any remote/network module is imported."""

    def __enter__(self) -> "_NoRemoteImports":
        self._real_import = builtins.__import__

        def _guard(name, *args, **kwargs):
            top = name.split(".", 1)[0]
            if top in _REMOTE_MODULES:
                raise AssertionError(
                    f"vendored load must not import {name!r} (network path)"
                )
            return self._real_import(name, *args, **kwargs)

        builtins.__import__ = _guard
        return self

    def __exit__(self, *exc) -> None:
        builtins.__import__ = self._real_import


class VendoredSWEBenchCLTest(unittest.TestCase):
    def test_vendored_file_exists_and_is_packaged(self) -> None:
        # The resolver points at the in-tree, package-relative copy.
        self.assertTrue(
            _VENDORED.is_file(),
            f"vendored dataset missing at {_VENDORED}",
        )
        # It lives under the memeval package data dir, so it ships in wheels.
        self.assertEqual(_VENDORED.name, "SWE-Bench-CL.json")
        self.assertEqual(_VENDORED.parent.name, "swe_bench_cl")
        self.assertEqual(_VENDORED.parent.parent.name, "data")

    def test_default_load_uses_vendored_copy_offline(self) -> None:
        loader = get_loader("swe_bench_cl")
        self.assertIsInstance(loader, SWEBenchCLLoader)

        # No source given -> must resolve the vendored copy with no network.
        with _NoRemoteImports():
            tasks = loader.load(None, limit=None)

        self.assertEqual(
            len(tasks), _EXPECTED_TASKS,
            f"expected {_EXPECTED_TASKS} tasks, got {len(tasks)}",
        )
        # Every task is a CODE task for this benchmark.
        for t in tasks:
            self.assertIs(t.benchmark, Benchmark.SWE_BENCH_CL)
            self.assertIs(t.kind, TaskKind.CODE)

        # Eight per-repo sequences, with the expected sizes.
        groups: dict[str, int] = {}
        for t in tasks:
            groups[t.group_id] = groups.get(t.group_id, 0) + 1
        self.assertEqual(len(groups), _EXPECTED_GROUPS)
        self.assertEqual(groups, _EXPECTED_SEQUENCE_SIZES)

    def test_limit_is_respected_on_vendored_load(self) -> None:
        with _NoRemoteImports():
            tasks = get_loader("swe_bench_cl").load(None, limit=5)
        self.assertEqual(len(tasks), 5)


if __name__ == "__main__":  # pragma: no cover - manual runner
    unittest.main(verbosity=2)
