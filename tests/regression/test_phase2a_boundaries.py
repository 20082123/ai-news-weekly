"""Phase 2A boundary regression tests.

These tests guard the invariants of the phase 2A work without importing or
running ``main.py``:

* migration 0001 and the legacy files are byte-identical to the phase-2A
  start (hardcoded SHA-256);
* no production module imports a network/subprocess client;
* the GitHub fixtures contain no emails, credentials or real GitHub URLs.
"""

import ast
import hashlib
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "ai_signal"
TESTS = ROOT / "tests"

# SHA-256 of migration 0001 as it was at the start of phase 2A.
_MIGRATION_0001_SHA256 = "c30e25a1f6414edcd3cbc59d4113f642752f974e771ede10be3664c5ae752448"

# SHA-256 of migration 0002 as frozen at phase 2B1 start.
_MIGRATION_0002_SHA256 = "41920e4b7ec58327a4bb0cf52d904c0894d638ad4a8e647f51f2533444e5150f"

# SHA-256 of migration 0003 as frozen at phase 2B2 (observation attribution).
_MIGRATION_0003_SHA256 = "2695b62f772147f174ef2faf0d7bbc8dbecf28798651daf672967b14a5d81a0b"

# SHA-256 of migration 0004 as frozen at phase 2C1 (candidate qualification v2).
_MIGRATION_0004_SHA256 = "6434574ecc111e84b039e5a10b2b2bd34090fb7bc6e9edce333b04158b9fb82b"

# SHA-256 of the legacy files as recorded in the phase-1 baseline fixture.
_LEGACY_SHA256 = {
    "main.py": "693fb3504170449a72506d3b3db661000860aa6fda8852f6db80278eb6b307aa",
    ".github/workflows/weekly.yml": "b6f0ad084e6568022379410d1d4cddb277421fc7a81e182bf650a84d312a4fc9",
    "requirements.txt": "e54658f2c701859042a2c54f1f29ff5b6151e3e2956191fa3b4982ce1ba8704b",
}

# Network/subprocess modules forbidden in EVERY production module.
_FORBIDDEN_IMPORTS_EVERYWHERE = ("requests", "httpx", "socket", "subprocess")

# urllib.request / urllib.error are allowed ONLY in the two transport modules
# (the GitHub REST/research client and the phase-2D3 official-page client).
_NETWORK_IMPORTS_RESTRICTED = ("urllib.request", "urllib.error")
_REST_MODULE = pathlib.PurePath("src") / "ai_signal" / "sources" / "github_rest.py"
_OFFICIAL_MODULE = pathlib.PurePath("src") / "ai_signal" / "sources" / "official_http.py"
_NETWORK_MODULES = (_REST_MODULE, _OFFICIAL_MODULE)

# Network modules forbidden in test code (tests must never use real network).
_FORBIDDEN_TEST_IMPORTS = (
    "requests",
    "httpx",
    "socket",
    "subprocess",
    "urllib.request",
    "urllib.error",
)

# Substrings that must never appear in the GitHub fixtures.
_FORBIDDEN_FIXTURE_SUBSTRINGS = (
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


def _sha256(relative):
    return hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()


def _imported_dotted_names(tree):
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            for alias in node.names:
                if alias.name == "*":
                    names.add(base)
                else:
                    names.add((base + "." + alias.name) if base else alias.name)
    return names


class FrozenBaselineTest(unittest.TestCase):
    def test_migration_0001_is_unchanged(self):
        actual = _sha256("src/ai_signal/storage/migrations/0001_initial.sql")
        self.assertEqual(
            actual,
            _MIGRATION_0001_SHA256,
            "0001_initial.sql must never be modified; add a new migration instead",
        )

    def test_migration_0002_is_unchanged(self):
        actual = _sha256("src/ai_signal/storage/migrations/0002_source_collection.sql")
        self.assertEqual(
            actual,
            _MIGRATION_0002_SHA256,
            "0002_source_collection.sql must never be modified; add a new migration instead",
        )

    def test_migration_0003_is_unchanged(self):
        actual = _sha256("src/ai_signal/storage/migrations/0003_raw_signal_observation.sql")
        self.assertEqual(
            actual,
            _MIGRATION_0003_SHA256,
            "0003_raw_signal_observation.sql must never be modified; add a new migration instead",
        )

    def test_migration_0004_is_unchanged(self):
        actual = _sha256("src/ai_signal/storage/migrations/0004_candidate_qualification.sql")
        self.assertEqual(
            actual,
            _MIGRATION_0004_SHA256,
            "0004_candidate_qualification.sql must never be modified; add a new migration instead",
        )

    def test_legacy_files_are_unchanged(self):
        for relative, expected in _LEGACY_SHA256.items():
            self.assertEqual(_sha256(relative), expected, "%s changed from baseline" % relative)


class ProductionImportBoundaryTest(unittest.TestCase):
    def test_no_network_or_subprocess_imports(self):
        offenders = []
        for path in sorted(SRC.rglob("*.py")):
            rel = path.relative_to(ROOT)
            tree = ast.parse(path.read_text(encoding="utf-8"))
            names = _imported_dotted_names(tree)
            is_network_module = rel in _NETWORK_MODULES
            for name in names:
                for forbidden in _FORBIDDEN_IMPORTS_EVERYWHERE:
                    if name == forbidden or name.startswith(forbidden + "."):
                        offenders.append((str(rel), name))
                # urllib.request / urllib.error only permitted in the two
                # transport modules (GitHub REST + official HTTP).
                if not is_network_module:
                    for forbidden in _NETWORK_IMPORTS_RESTRICTED:
                        if name == forbidden or name.startswith(forbidden + "."):
                            offenders.append((str(rel), name))
        self.assertEqual(
            offenders,
            [],
            "forbidden network/subprocess imports found: %s" % offenders,
        )

    def test_transport_modules_are_the_only_urllib_users(self):
        # Conversely: both transport modules MUST use urllib (they own all
        # production network access).
        for module in _NETWORK_MODULES:
            with self.subTest(module=str(module)):
                tree = ast.parse((ROOT / module).read_text(encoding="utf-8"))
                joined = " ".join(_imported_dotted_names(tree))
                self.assertIn("urllib.request", joined)

    def test_test_code_has_no_network_imports(self):
        offenders = []
        for path in sorted(TESTS.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for name in _imported_dotted_names(tree):
                for forbidden in _FORBIDDEN_TEST_IMPORTS:
                    if name == forbidden or name.startswith(forbidden + "."):
                        offenders.append((str(path.relative_to(ROOT)), name))
        self.assertEqual(
            offenders,
            [],
            "test code must not import network modules: %s" % offenders,
        )

    def test_main_module_is_not_imported(self):
        self.assertNotIn("main", sys.modules)


class FixtureHygieneTest(unittest.TestCase):
    def test_github_fixtures_are_clean(self):
        fixtures_dir = ROOT / "tests" / "fixtures" / "github"
        for name in ("pages.json", "malformed.json", "rest_search_page.json"):
            text = (fixtures_dir / name).read_text(encoding="utf-8")
            lowered = text.lower()
            for forbidden in _FORBIDDEN_FIXTURE_SUBSTRINGS:
                self.assertNotIn(forbidden, lowered, "%s contains %s" % (name, forbidden))
            self.assertNotIn("@", text, "%s contains an email address" % name)
            self.assertNotIn("github.com", text, "%s contains a real GitHub URL" % name)
            self.assertTrue(
                "example.com" in text or "example.org" in text or "example.net" in text,
                "%s should use reserved example.* URLs" % name,
            )

    def test_material_fixtures_are_clean(self):
        fixtures_dir = ROOT / "tests" / "fixtures" / "materials"
        for name in ("repos.json",):
            text = (fixtures_dir / name).read_text(encoding="utf-8")
            lowered = text.lower()
            for forbidden in _FORBIDDEN_FIXTURE_SUBSTRINGS:
                self.assertNotIn(forbidden, lowered, "%s contains %s" % (name, forbidden))
            self.assertNotIn("@", text, "%s contains an email address" % name)
            self.assertNotIn("github.com", text, "%s contains a real GitHub URL" % name)
            self.assertTrue(
                "example.com" in text or "example.org" in text or "example.net" in text,
                "%s should use reserved example.* URLs" % name,
            )

    def test_candidate_fixtures_are_clean(self):
        fixtures_dir = ROOT / "tests" / "fixtures" / "candidates"
        for path in sorted(fixtures_dir.glob("*.json")):
            text = path.read_text(encoding="utf-8")
            lowered = text.lower()
            for forbidden in _FORBIDDEN_FIXTURE_SUBSTRINGS:
                # "system prompt" is a legitimate injection-marker TEST payload;
                # the marker itself must not be a credential string.
                if forbidden == "secret" and "reveal secrets" in lowered:
                    continue
                self.assertNotIn(
                    forbidden, lowered, "%s contains %s" % (path.name, forbidden)
                )
            self.assertNotIn("@", text, "%s contains an email address" % path.name)
            self.assertNotIn("github.com", text, "%s contains a real GitHub URL" % path.name)
            self.assertTrue(
                "example.com" in text or "example.org" in text or "example.net" in text,
                "%s should use reserved example.* URLs" % path.name,
            )


if __name__ == "__main__":
    unittest.main()
