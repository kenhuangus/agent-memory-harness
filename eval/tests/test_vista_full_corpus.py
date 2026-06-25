"""VISTA full-corpus vendoring + loader selection (curated default unchanged)."""

from __future__ import annotations

import builtins
import os
import sys
import unittest
from pathlib import Path

_BASE_DIR = Path(__file__).resolve().parent.parent  # .../eval
if str(_BASE_DIR) not in sys.path:
    sys.path.insert(0, str(_BASE_DIR))

from memeval.loaders import get_loader  # noqa: E402
from memeval.loaders.vista import (  # noqa: E402
    _FULL_CORPUS,
    _FULL_SPLITS,
    VistaLoader,
)

_REMOTE_MODULES = ("datasets", "huggingface_hub", "requests")

_EXPECTED_SPLIT_COUNTS = {
    "train": 99,
    "dev": 97,
    "test": 97,
    "challenge": 97,
}

_SCHEMA_KEYS = {
    "id",
    "intent",
    "domain",
    "split",
    "event_trace",
    "route_graph",
    "oracle_bindings",
}


class _NoRemoteImports:
    def __enter__(self):
        self._real = builtins.__import__

        def _guard(name, *a, **k):
            if name.split(".", 1)[0] in _REMOTE_MODULES:
                raise AssertionError(f"vendored load must not import {name!r}")
            return self._real(name, *a, **k)

        builtins.__import__ = _guard
        return self

    def __exit__(self, *exc):
        builtins.__import__ = self._real


class _CleanEnv:
    """Strip VISTA_* env so tests don't inherit a selection from the shell."""

    _KEYS = ("VISTA_DATASET", "VISTA_SPLIT")

    def __enter__(self):
        self._saved = {k: os.environ.pop(k, None) for k in self._KEYS}
        return self

    def __exit__(self, *exc):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class VistaFullCorpusTest(unittest.TestCase):
    def test_vendored_full_files_exist(self) -> None:
        self.assertTrue(_FULL_CORPUS.is_file(), f"missing {_FULL_CORPUS}")
        for split in _EXPECTED_SPLIT_COUNTS:
            p = _FULL_SPLITS / f"{split}.jsonl"
            self.assertTrue(p.is_file(), f"missing {p}")

    def test_curated_default_still_loads_six(self) -> None:
        with _CleanEnv(), _NoRemoteImports():
            tasks = get_loader("vista").load(None, split=None, limit=None)
        self.assertEqual(len(tasks), 6)

    def test_full_corpus_loads_390(self) -> None:
        with _CleanEnv(), _NoRemoteImports():
            tasks = VistaLoader().load(None, dataset="full", split="all", limit=None)
        self.assertEqual(len(tasks), 390)

    def test_each_split_loads_expected_count(self) -> None:
        for split, expected in _EXPECTED_SPLIT_COUNTS.items():
            with _CleanEnv(), _NoRemoteImports():
                tasks = VistaLoader().load(
                    None, dataset="full", split=split, limit=None
                )
            self.assertEqual(len(tasks), expected, f"split={split}")

    def test_env_var_selection(self) -> None:
        with _CleanEnv(), _NoRemoteImports():
            os.environ["VISTA_DATASET"] = "full"
            os.environ["VISTA_SPLIT"] = "dev"
            tasks = get_loader("vista").load(None, split="test", limit=None)
        self.assertEqual(len(tasks), 97)

    def test_full_respects_limit(self) -> None:
        with _CleanEnv(), _NoRemoteImports():
            tasks = VistaLoader().load(None, dataset="full", split="all", limit=5)
        self.assertEqual(len(tasks), 5)

    def test_sample_record_schema(self) -> None:
        import json

        with _FULL_CORPUS.open(encoding="utf-8") as fh:
            rec = json.loads(fh.readline())
        missing = _SCHEMA_KEYS - set(rec)
        self.assertFalse(missing, f"missing schema keys: {missing}")


if __name__ == "__main__":
    unittest.main()
