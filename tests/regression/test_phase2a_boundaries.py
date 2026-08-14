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

# SHA-256 of migration 0001 as it was at the start of phase 2A.
_MIGRATION_0001_SHA256 = "c30e25a1f6414edcd3cbc59d4113f642752f974e771ede10be3664c5ae752448"

# SHA-256 of the legacy files as recorded in the phase-1 baseline fixture.
_LEGACY_SHA256 = {
    "main.py": "693fb3504170449a72506d3b3db661000860aa6fda8852f6db80278eb6b307aa",
    ".github/workflows/weekly.yml": "b6f0ad084e6568022379410d1d4cddb277421fc7a81e182bf650a84d312a4fc9",
    "requirements.txt": "e54658f2c701859042a2c54f1f29ff5b6151e3e2956191fa3b4982ce1ba8704b",
}

# Dotted module names that must never be imported by production code.
_FORBIDDEN_IMPORTS = ("requests", "httpx", "socket", "subprocess", "urllib.request")

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

    def test_legacy_files_are_unchanged(self):
        for relative, expected in _LEGACY_SHA256.items():
            self.assertEqual(_sha256(relative), expected, "%s changed from baseline" % relative)


class ProductionImportBoundaryTest(unittest.TestCase):
    def test_no_network_or_subprocess_imports(self):
        offenders = []
        for path in sorted(SRC.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for name in _imported_dotted_names(tree):
                for forbidden in _FORBIDDEN_IMPORTS:
                    if name == forbidden or name.startswith(forbidden + "."):
                        offenders.append((str(path.relative_to(ROOT)), name))
        self.assertEqual(
            offenders,
            [],
            "forbidden network/subprocess imports found: %s" % offenders,
        )

    def test_main_module_is_not_imported(self):
        self.assertNotIn("main", sys.modules)


class FixtureHygieneTest(unittest.TestCase):
    def test_github_fixtures_are_clean(self):
        fixtures_dir = ROOT / "tests" / "fixtures" / "github"
        for name in ("pages.json", "malformed.json"):
            text = (fixtures_dir / name).read_text(encoding="utf-8")
            lowered = text.lower()
            for forbidden in _FORBIDDEN_FIXTURE_SUBSTRINGS:
                self.assertNotIn(forbidden, lowered, "%s contains %s" % (name, forbidden))
            self.assertNotIn("@", text, "%s contains an email address" % name)
            self.assertNotIn("github.com", text, "%s contains a real GitHub URL" % name)
            self.assertIn("example.com", text, "%s should use reserved example.com URLs" % name)


if __name__ == "__main__":
    unittest.main()
