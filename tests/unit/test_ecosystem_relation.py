"""Unit tests for the phase 2C2-C ecosystem relation resolver."""

import pathlib
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from ai_signal.discovery.relation import (  # noqa: E402
    EcosystemRelationMatch,
    EcosystemTargetSpec,
    resolve_relation,
)

_TARGET = (EcosystemTargetSpec(target="example-org/core", aliases=("core", "example-core")),)


def _parsed(full_name=None, description=None, topics=()):
    return SimpleNamespace(full_name=full_name, description=description, topics=topics)


class RelationResolverTest(unittest.TestCase):
    def test_full_name_match(self):
        match = resolve_relation(
            _parsed(full_name="example-org/CORE"), _TARGET
        )
        self.assertIsNotNone(match)
        self.assertEqual(match.kind, "full_name_match")
        self.assertEqual(match.field, "full_name")
        self.assertEqual(match.target, "example-org/core")

    def test_topic_match(self):
        match = resolve_relation(
            _parsed(topics=("ui", "example-core")), _TARGET
        )
        self.assertIsNotNone(match)
        self.assertEqual(match.kind, "topic_match")
        self.assertEqual(match.field, "topics")

    def test_description_mention_word_boundary(self):
        match = resolve_relation(
            _parsed(description="A dashboard wrapper around core for teams."),
            _TARGET,
        )
        self.assertIsNotNone(match)
        self.assertEqual(match.kind, "description_mention")
        self.assertEqual(match.field, "description")

    def test_word_boundary_prevents_false_positive(self):
        # "score" must NOT match the alias "core".
        self.assertIsNone(
            resolve_relation(
                _parsed(description="A scoring harness for score keeping."),
                _TARGET,
            )
        )

    def test_no_match_returns_none(self):
        self.assertIsNone(
            resolve_relation(
                _parsed(description="An unrelated task manager."),
                _TARGET,
            )
        )

    def test_precedence_full_name_over_description(self):
        match = resolve_relation(
            _parsed(full_name="example-org/core", description="mentions core"),
            _TARGET,
        )
        self.assertEqual(match.kind, "full_name_match")

    def test_empty_targets_return_none(self):
        self.assertIsNone(resolve_relation(_parsed(description="core"), ()))

    def test_match_is_frozen_and_validated(self):
        with self.assertRaises(ValueError):
            EcosystemRelationMatch(target="x/y", kind="nope", field="description")
        with self.assertRaises(ValueError):
            EcosystemRelationMatch(target="x/y", kind="topic_match", field="url")

    def test_target_spec_normalizes_and_dedupes_aliases(self):
        spec = EcosystemTargetSpec(
            target=" owner/repo ", aliases=("Repo", "repo", " other ", "")
        )
        self.assertEqual(spec.target, "owner/repo")
        self.assertEqual(spec.aliases, ("Repo", "repo", "other"))

    def test_target_spec_rejects_bad_target(self):
        with self.assertRaises(ValueError):
            EcosystemTargetSpec(target="no-slash")


if __name__ == "__main__":
    unittest.main()
