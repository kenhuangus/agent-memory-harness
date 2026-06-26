"""Unit tests for the effective-profile diagnostic verdict logic."""

from __future__ import annotations

import unittest

from memeval.stores.tools.effective_profile import (
    EffectiveProfile,
    classify_effective_profile,
    format_report,
)


def _facts(
    *,
    profile_name: str = "fusion",
    embedder_class: str = "_HashingEmbedder",
    vector_index: str = "brute_force",
    requested_vector_index: str = "brute_force",
) -> EffectiveProfile:
    return EffectiveProfile(
        profile_name=profile_name,
        vector_backend_class="SqliteVectorStore",
        embedder_class=embedder_class,
        vector_index=vector_index,
        requested_vector_index=requested_vector_index,
        vector_index_status="test status",
    )


class EffectiveProfileVerdictTests(unittest.TestCase):
    def test_default_fusion_hashing_is_normal_offline_default(self) -> None:
        verdict = classify_effective_profile(_facts(), {})

        self.assertEqual(verdict.exit_code, 0)
        self.assertEqual(verdict.message, "offline hashing default (fusion profile).")

    def test_local_ann_request_hashing_fallback_is_hard_failure(self) -> None:
        verdict = classify_effective_profile(
            _facts(),
            {"MEMEVAL_LOCAL_ANN": "1"},
        )

        self.assertEqual(verdict.exit_code, 3)
        self.assertIn("REQUESTED local-ANN", verdict.message)
        self.assertIn("FELL BACK to hashing", verdict.message)

    def test_fusion_local_request_hashing_fallback_is_hard_failure(self) -> None:
        verdict = classify_effective_profile(
            _facts(profile_name="fusion-local"),
            {"MEMORY_PROFILE": "fusion-local"},
        )

        self.assertEqual(verdict.exit_code, 3)
        self.assertIn("REQUESTED local-ANN", verdict.message)
        self.assertIn("FELL BACK to hashing", verdict.message)

    def test_require_local_ann_fails_when_default_profile_is_active(self) -> None:
        verdict = classify_effective_profile(
            _facts(),
            {},
            require_local_ann=True,
        )

        self.assertEqual(verdict.exit_code, 3)
        self.assertIn("REQUIRED local-ANN", verdict.message)
        self.assertIn("profile=fusion", verdict.message)

    def test_local_ann_active_passes(self) -> None:
        verdict = classify_effective_profile(
            _facts(
                profile_name="accuracy-local",
                embedder_class="SentenceTransformersEmbedder",
                vector_index="sqlite_vec",
                requested_vector_index="sqlite_vec",
            ),
            {"MEMEVAL_LOCAL_ANN": "1"},
            require_local_ann=True,
        )

        self.assertEqual(verdict.exit_code, 0)
        self.assertEqual(verdict.message, "local-ANN active (MiniLM + sqlite-vec).")

    def test_fusion_local_ann_active_passes(self) -> None:
        verdict = classify_effective_profile(
            _facts(
                profile_name="fusion-local",
                embedder_class="SentenceTransformersEmbedder",
                vector_index="sqlite_vec",
                requested_vector_index="sqlite_vec",
            ),
            {"MEMORY_PROFILE": "fusion-local"},
            require_local_ann=True,
        )

        self.assertEqual(verdict.exit_code, 0)
        self.assertEqual(verdict.message, "local-ANN active (MiniLM + sqlite-vec).")

    def test_sqlite_vec_request_brute_force_fallback_is_reported(self) -> None:
        verdict = classify_effective_profile(
            _facts(
                profile_name="accuracy-local",
                embedder_class="SentenceTransformersEmbedder",
                vector_index="brute_force",
                requested_vector_index="sqlite_vec",
            ),
            {"MEMEVAL_LOCAL_ANN": "1"},
        )

        self.assertEqual(verdict.exit_code, 3)
        self.assertIn("REQUESTED sqlite-vec ANN", verdict.message)
        self.assertIn("FELL BACK to brute-force", verdict.message)

    def test_report_redacts_environment_values(self) -> None:
        report = format_report(
            _facts(),
            {
                "MEMORY_PROFILE": "accuracy",
                "MEMEVAL_LOCAL_ANN": None,
                "MEMEVAL_ALLOW_MODEL_DOWNLOAD": "1",
                "VOYAGE_API_KEY": "secret-token",
            },
            classify_effective_profile(_facts(), {}),
        )

        self.assertIn("MEMORY_PROFILE=present", report)
        self.assertIn("VOYAGE_API_KEY=present", report)
        self.assertNotIn("secret-token", report)
        self.assertNotIn("MEMORY_PROFILE=accuracy", report)


if __name__ == "__main__":
    unittest.main()
