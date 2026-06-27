"""The recall score FLOOR wiring in ``build_store`` (``$RECALL_MIN_SCORE`` + the accuracy default).

Recall is pure top-k with no floor, so it can return ``k`` weak matches even when none is a real hit.
``build_store`` wires a precision floor onto the routing config (``RouterConfig.recall_min_score``):

* ``$RECALL_MIN_SCORE`` set -> overrides for ANY profile; a value ``<= 0`` disables the floor (``None``).
* unset -> only the ``accuracy`` profile gets the calibrated ``0.15`` default; every other profile -> no
  floor (``None``).

The floor itself is asserted on the routing config (``store._router._config.recall_min_score``) and on the
observability stamp (``store.recall_min_score`` / ``store.profile_name``). The offline ``fusion`` profile
builds with no key/network; the ``accuracy`` path stubs the Voyage embedder + semantic classifier seams so
it, too, builds offline. Stdlib + pytest only.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from cookbook_memory.core.contract import _resolve_recall_min_score, build_store


def _clear_env(monkeypatch):
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    monkeypatch.delenv("MEMEVAL_LOCAL_ANN", raising=False)
    monkeypatch.delenv("MEMORY_PROFILE", raising=False)
    monkeypatch.delenv("RECALL_MIN_SCORE", raising=False)


# --------------------------------------------------------------------------- #
# The default-resolution contract, unit-tested directly (offline, no build).
# --------------------------------------------------------------------------- #
class TestResolveRecallMinScore:
    def test_accuracy_defaults_to_calibrated_floor(self, monkeypatch):
        monkeypatch.delenv("RECALL_MIN_SCORE", raising=False)
        assert _resolve_recall_min_score("accuracy") == 0.15

    @pytest.mark.parametrize("profile", ["fusion", "speed", "fusion-local", "accuracy-local",
                                         "fusion-falkor", "fusion-rerank-local"])
    def test_non_accuracy_profiles_default_to_no_floor(self, monkeypatch, profile):
        monkeypatch.delenv("RECALL_MIN_SCORE", raising=False)
        assert _resolve_recall_min_score(profile) is None

    def test_env_overrides_any_profile(self, monkeypatch):
        monkeypatch.setenv("RECALL_MIN_SCORE", "0.3")
        assert _resolve_recall_min_score("accuracy") == 0.3
        assert _resolve_recall_min_score("fusion") == 0.3

    @pytest.mark.parametrize("raw", ["0", "0.0", "-1", "-0.2"])
    def test_env_non_positive_disables_floor(self, monkeypatch, raw):
        # <= 0 explicitly disables the floor, even on the accuracy profile (which would otherwise be 0.15).
        monkeypatch.setenv("RECALL_MIN_SCORE", raw)
        assert _resolve_recall_min_score("accuracy") is None
        assert _resolve_recall_min_score("fusion") is None

    def test_unparseable_env_falls_through_to_profile_default(self, monkeypatch):
        # A typo must NOT silently disable the floor — fall through to the profile default.
        monkeypatch.setenv("RECALL_MIN_SCORE", "not-a-float")
        assert _resolve_recall_min_score("accuracy") == 0.15
        assert _resolve_recall_min_score("fusion") is None

    def test_blank_env_falls_through_to_profile_default(self, monkeypatch):
        monkeypatch.setenv("RECALL_MIN_SCORE", "   ")
        assert _resolve_recall_min_score("accuracy") == 0.15
        assert _resolve_recall_min_score("fusion") is None


# --------------------------------------------------------------------------- #
# End-to-end through build_store: the config + observability stamp.
# --------------------------------------------------------------------------- #
class TestBuildStoreFloorWiring:
    def test_fusion_default_has_no_floor(self, tmp_path, monkeypatch):
        _clear_env(monkeypatch)  # no key, no MEMORY_PROFILE -> offline fusion profile
        store = build_store(str(tmp_path))
        assert store._router._config.recall_min_score is None
        # Observability stamp matches the config.
        assert store.recall_min_score is None
        assert store.profile_name == "fusion"

    def test_speed_default_has_no_floor(self, tmp_path, monkeypatch):
        _clear_env(monkeypatch)
        monkeypatch.setenv("MEMORY_PROFILE", "speed")
        store = build_store(str(tmp_path))
        assert store._router._config.recall_min_score is None
        assert store.recall_min_score is None
        assert store.profile_name == "speed"

    def test_env_sets_floor_on_a_non_accuracy_profile(self, tmp_path, monkeypatch):
        _clear_env(monkeypatch)
        monkeypatch.setenv("MEMORY_PROFILE", "fusion")
        monkeypatch.setenv("RECALL_MIN_SCORE", "0.3")
        store = build_store(str(tmp_path))
        assert store._router._config.recall_min_score == 0.3
        assert store.recall_min_score == 0.3

    def test_env_zero_disables_floor(self, tmp_path, monkeypatch):
        _clear_env(monkeypatch)
        monkeypatch.setenv("MEMORY_PROFILE", "fusion")
        monkeypatch.setenv("RECALL_MIN_SCORE", "0")
        store = build_store(str(tmp_path))
        assert store._router._config.recall_min_score is None

    def test_accuracy_profile_defaults_to_calibrated_floor(self, tmp_path, monkeypatch):
        # Build the accuracy profile OFFLINE by stubbing the two Voyage seams the profile pulls in:
        # VoyageEmbedder (a callable text->vector with .model/.dim) and SemanticRouterClassifier (which
        # would otherwise embed its exemplars at construction = a paid call). The floor wiring is profile-
        # driven and independent of those, so the stubs don't affect what we assert.
        _clear_env(monkeypatch)
        monkeypatch.setenv("MEMORY_PROFILE", "accuracy")

        class _FakeEmbed:
            model = "fake-voyage"
            dim = 8

            def __call__(self, text, *, input_type=None):
                return [0.0] * self.dim

        class _FakeClassifier:
            name = "fake-semantic"

            def __init__(self, *args, **kwargs):
                pass

            def classify(self, query):  # pragma: no cover - not exercised at build time
                from memeval.router import ClassificationResult, VECTORS
                return ClassificationResult(choice=VECTORS, scores={}, margin=0.0)

        with patch("memeval.stores.embedders.VoyageEmbedder", _FakeEmbed), \
                patch("memeval.router.SemanticRouterClassifier", _FakeClassifier):
            store = build_store(str(tmp_path))

        assert store._router._config.profile_name == "accuracy"
        assert store._router._config.recall_min_score == 0.15
        assert store.recall_min_score == 0.15
        assert store.profile_name == "accuracy"

    def test_accuracy_profile_env_override_wins(self, tmp_path, monkeypatch):
        _clear_env(monkeypatch)
        monkeypatch.setenv("MEMORY_PROFILE", "accuracy")
        monkeypatch.setenv("RECALL_MIN_SCORE", "0")  # disable even the calibrated default

        class _FakeEmbed:
            model = "fake-voyage"
            dim = 8

            def __call__(self, text, *, input_type=None):
                return [0.0] * self.dim

        class _FakeClassifier:
            name = "fake-semantic"

            def __init__(self, *args, **kwargs):
                pass

        with patch("memeval.stores.embedders.VoyageEmbedder", _FakeEmbed), \
                patch("memeval.router.SemanticRouterClassifier", _FakeClassifier):
            store = build_store(str(tmp_path))

        assert store._router._config.recall_min_score is None
