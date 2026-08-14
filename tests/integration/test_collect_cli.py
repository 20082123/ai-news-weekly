"""Integration tests for the ``collect github`` CLI command."""

import os
import pathlib
import shutil
import sys
import tempfile
import unittest
from io import StringIO

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from ai_signal import cli  # noqa: E402

FIXTURES = pathlib.Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "github"
SCOPE = "github-fixture-v1"


class CollectCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ai_signal_2a_cli_")
        self.db = os.path.join(self.tmp, "test.db")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, fixture="pages.json", db=None, week_key="2026-W33", scope_key=SCOPE):
        argv = [
            "collect",
            "github",
            "--fixture",
            str(FIXTURES / fixture),
            "--db-path",
            db if db is not None else self.db,
            "--week-key",
            week_key,
        ]
        if scope_key is not None:
            argv.extend(["--scope-key", scope_key])
        buf = StringIO()
        rc = cli.main(argv, out=buf)
        return rc, buf.getvalue()

    def test_success_returns_zero(self):
        rc, output = self._run()
        self.assertEqual(rc, 0)
        self.assertIn("status: success", output)
        self.assertIn("cursor_advanced: True", output)

    def test_partial_returns_five(self):
        rc, output = self._run(fixture="malformed.json")
        self.assertEqual(rc, 5)
        self.assertIn("status: partial", output)

    def test_invalid_week_key_returns_two(self):
        rc, _ = self._run(week_key="2026W33")
        self.assertEqual(rc, 2)

    def test_invalid_database_path_returns_three(self):
        # Parent directory does not exist, so the database cannot be opened.
        bad_db = os.path.join(self.tmp, "missing_dir_xyz", "test.db")
        rc, _ = self._run(db=bad_db)
        self.assertEqual(rc, 3)

    def test_missing_scope_key_returns_two(self):
        # argparse treats a missing required option as a usage error (exit 2).
        buf = StringIO()
        with self.assertRaises(SystemExit) as cm:
            cli.main(
                [
                    "collect",
                    "github",
                    "--fixture",
                    str(FIXTURES / "pages.json"),
                    "--db-path",
                    self.db,
                    "--week-key",
                    "2026-W33",
                ],
                out=buf,
            )
        self.assertEqual(cm.exception.code, 2)

    def test_invalid_scope_key_returns_two(self):
        for bad in ("UpperCase", "valid-looking-v1\n"):
            with self.subTest(scope_key=repr(bad)):
                rc, _ = self._run(scope_key=bad)
                self.assertEqual(rc, 2)

    def test_valid_scope_key_runs_successfully(self):
        rc, output = self._run(scope_key="ai-agents-v1")
        self.assertEqual(rc, 0)
        self.assertIn("status: success", output)

    def test_output_contains_no_paths_urls_or_scope(self):
        _, output = self._run()
        self.assertNotIn(str(FIXTURES), output)
        self.assertNotIn(self.db, output)
        self.assertNotIn("example.com", output)
        self.assertNotIn("example-org", output)
        # The CLI prints only the safe summary fields, never the scope key.
        self.assertNotIn(SCOPE, output)
        self.assertNotIn("scope", output)


if __name__ == "__main__":
    unittest.main()
