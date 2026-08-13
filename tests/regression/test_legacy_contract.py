"""Regression tests guarding the legacy contract.

These tests never import or execute ``main.py``. They only read the
synthetic fixtures under ``tests/fixtures/legacy`` and assert that:

* the fixtures parse as JSON;
* the fixtures contain no credentials, secrets or personal email addresses;
* the baseline records that the legacy entry point is still
  ``python main.py`` and that the weekly workflow still runs it;
* the baseline references commit ``46d6371``.
"""

import json
import hashlib
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

FIXTURES = pathlib.Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "legacy"
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

_FORBIDDEN_SUBSTRINGS = (
    "password",
    "token",
    "cookie",
    "authorization",
    "authorisation",
    "api_key",
    "apikey",
    "secret",
    "credential",
    "smtp",
    "bearer",
)

_FIXTURE_FILES = ("news.json", "github_repos.json", "baseline.json")


class FixtureParsingTest(unittest.TestCase):
    def test_news_fixture_parses(self):
        data = json.loads((FIXTURES / "news.json").read_text(encoding="utf-8"))
        self.assertIsInstance(data, list)
        self.assertGreater(len(data), 0)
        for item in data:
            self.assertIn("url", item)
            self.assertIn("source", item)

    def test_github_fixture_parses(self):
        data = json.loads((FIXTURES / "github_repos.json").read_text(encoding="utf-8"))
        self.assertIsInstance(data, list)
        self.assertGreater(len(data), 0)
        for item in data:
            self.assertIn("name", item)
            self.assertIn("url", item)

    def test_baseline_fixture_parses(self):
        data = json.loads((FIXTURES / "baseline.json").read_text(encoding="utf-8"))
        self.assertIsInstance(data, dict)
        self.assertIn("contract", data)
        self.assertIsInstance(data["contract"], list)


class FixtureHygieneTest(unittest.TestCase):
    def test_no_credentials_or_personal_emails(self):
        for name in _FIXTURE_FILES:
            raw = (FIXTURES / name).read_text(encoding="utf-8")
            lowered = raw.lower()
            for forbidden in _FORBIDDEN_SUBSTRINGS:
                self.assertNotIn(
                    forbidden,
                    lowered,
                    "%s must not contain %r" % (name, forbidden),
                )
            # No email addresses at all (example.com URLs have no '@').
            self.assertNotIn("@", raw, "%s must not contain email addresses" % name)

    def test_fixtures_use_reserved_domains(self):
        blob = "".join(
            (FIXTURES / name).read_text(encoding="utf-8") for name in _FIXTURE_FILES
        )
        # URLs are restricted to documentation/testing reserved domains.
        self.assertIn("example.com", blob)
        for scheme in ("https://github.com/", "https://news.ycombin", "https://x.com"):
            self.assertNotIn(scheme, blob)


class LegacyContractTest(unittest.TestCase):
    def test_baseline_states_legacy_entry_point(self):
        text = (FIXTURES / "baseline.json").read_text(encoding="utf-8")
        self.assertIn("python main.py", text)
        self.assertIn("main.py", text)

    def test_baseline_states_workflow_runs_main(self):
        text = (FIXTURES / "baseline.json").read_text(encoding="utf-8")
        self.assertIn("python main.py", text)
        self.assertIn("weekly", text)

    def test_baseline_references_baseline_commit(self):
        text = (FIXTURES / "baseline.json").read_text(encoding="utf-8")
        self.assertIn("46d6371", text)

    def test_legacy_files_match_frozen_sha256(self):
        baseline = json.loads((FIXTURES / "baseline.json").read_text(encoding="utf-8"))
        for relative, expected in baseline["legacy_sha256"].items():
            actual = hashlib.sha256((REPO_ROOT / relative).read_bytes()).hexdigest()
            self.assertEqual(actual, expected, "%s changed from the phase-1 baseline" % relative)

    def test_main_module_is_not_imported_by_tests(self):
        # Guard rail: main.py must never be imported from this regression suite.
        modules = list(sys.modules.keys())
        self.assertNotIn("main", modules)


if __name__ == "__main__":
    unittest.main()
