"""Unit tests for the phase 2D/2E research domain models."""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from ai_signal.domain.models import (  # noqa: E402
    EditorialDecision,
    ResearchDossier,
    ResearchFact,
)


def _dossier(**overrides):
    fields = {
        "event_candidate_id": "e" * 64,
        "summary_judgment": "机器草案",
        "timeline": ("2026-08-12 v0.3.0: release (https://github.com/x)",
                     ),
        "target_audience": "开发者",
        "job_to_be_done": "选工具",
        "limits_unknowns": ("无独立评测",),
        "forbidden_claims": ("爆火",),
        "needs_testing": True,
        "test_plan": ("装一个",),
        "bundle_hash": "b" * 64,
    }
    fields.update(overrides)
    return ResearchDossier(**fields)


class ResearchDossierModelTest(unittest.TestCase):
    def test_identity_covers_bundle(self):
        a = _dossier()
        b = _dossier()
        self.assertEqual(a.id, b.id)
        self.assertNotEqual(a.id, _dossier(bundle_hash="c" * 64).id)
        self.assertEqual(len(a.id), 64)

    def test_validation(self):
        with self.assertRaises(ValueError):
            _dossier(summary_judgment="  ")
        with self.assertRaises(ValueError):
            _dossier(status="nope")
        with self.assertRaises(TypeError):
            _dossier(needs_testing=1)
        with self.assertRaises(TypeError):
            _dossier(limits_unknowns=(1,))
        self.assertEqual(_dossier().timeline,
                         ("2026-08-12 v0.3.0: release (https://github.com/x)",))


class ResearchFactModelTest(unittest.TestCase):
    def test_fact_identity_and_url_validation(self):
        fact = ResearchFact(
            dossier_id="d" * 64,
            kind="fact",
            text="发布 v0.3.0",
            source_kind="github_release",
            source_url="https://github.com/x/y/releases/tag/v0.3.0",
        )
        self.assertEqual(len(fact.id), 64)
        with self.assertRaises(ValueError):
            ResearchFact(dossier_id="d" * 64, kind="fact", text="x",
                         source_kind="github_release",
                         source_url="http://insecure.example/x")
        with self.assertRaises(ValueError):
            ResearchFact(dossier_id="d" * 64, kind="fact", text="x",
                         source_kind="github_release",
                         source_url="https://user:pass@github.com/x")
        with self.assertRaises(ValueError):
            ResearchFact(dossier_id="d" * 64, kind="opinion", text="x",
                         source_kind="github_release")
        # No URL is fine for manual facts.
        self.assertEqual(
            ResearchFact(dossier_id="d" * 64, kind="fact", text="x",
                         source_kind="manual").source_url,
            None,
        )


class EditorialDecisionModelTest(unittest.TestCase):
    def test_identity_and_validation(self):
        decision = EditorialDecision(
            dossier_id="d" * 64,
            policy_version="editorial-v1",
            input_hash="a" * 64,
            decision="ready_to_write",
            reason_codes=("has_release_fact",),
        )
        self.assertEqual(len(decision.id), 64)
        self.assertEqual(decision.decision, "ready_to_write")
        with self.assertRaises(ValueError):
            EditorialDecision(
                dossier_id="d" * 64,
                policy_version="editorial-v1",
                input_hash="a" * 64,
                decision="publish",
                reason_codes=(),
            )
        with self.assertRaises(ValueError):
            EditorialDecision(
                dossier_id="d" * 64,
                policy_version="editorial-v1",
                input_hash="short",
                decision="watch",
                reason_codes=(),
            )


if __name__ == "__main__":
    unittest.main()
