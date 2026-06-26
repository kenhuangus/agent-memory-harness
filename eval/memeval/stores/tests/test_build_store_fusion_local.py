"""Integration coverage for the plugin build_store fusion-local profile."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
PLUGIN_DIR = ROOT / "plugin"
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from cookbook_memory.core.contract import build_store  # noqa: E402
from memeval.schema import MemoryItem  # noqa: E402


class BuildStoreFusionLocalTests(unittest.TestCase):
    def test_fusion_local_falls_back_to_fusion_hashing_store(self) -> None:
        env = os.environ.copy()
        env["MEMORY_PROFILE"] = "fusion-local"
        env.pop("MEMEVAL_LOCAL_ANN", None)
        env.pop("VOYAGE_API_KEY", None)

        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ, env, clear=True
        ), patch(
            "memeval.stores.embedders.SentenceTransformersEmbedder.embed",
            side_effect=RuntimeError("local MiniLM unavailable"),
        ):
            store = build_store(tmpdir)
            try:
                router = store._router
                config = router._config
                vectors = router.backends["vectors"]

                self.assertEqual(config.profile_name, "fusion-local")
                self.assertTrue(config.consult2.enabled)
                self.assertIsNone(config.classifier)
                self.assertEqual(type(vectors._embed).__name__, "_HashingEmbedder")
                self.assertNotEqual(vectors.requested_vector_index, "sqlite_vec")

                store.write(
                    MemoryItem(
                        item_id="fusion-local-fallback",
                        content="Fusion local fallback keeps hashing dimensions consistent.",
                    )
                )
                hits = store.search("hashing dimensions consistent", k=1)

                self.assertTrue(hits)
                self.assertEqual(hits[0].item_id, "fusion-local-fallback")
            finally:
                _close_registered_backends(store)


def _close_registered_backends(store: object) -> None:
    router = getattr(store, "_router", None)
    backends = getattr(router, "backends", None)
    if not isinstance(backends, dict):
        return
    seen: set[int] = set()
    for backend in backends.values():
        marker = id(backend)
        if marker in seen:
            continue
        seen.add(marker)
        close = getattr(backend, "close", None)
        if callable(close):
            close()


if __name__ == "__main__":
    unittest.main()
