"""CLI tests for ``ai-signal event-candidate`` (phase 2C3)."""

import io
import os
import pathlib
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from ai_signal.cli import main  # noqa: E402

EXIT_OK = 0
EXIT_CONFIG_ERROR = 2
EXIT_DB_ERROR = 3


def _add_args(db, **overrides):
    args = [
        "event-candidate", "add", "--db-path", db,
        "--signal-type", "economics_access",
        "--subject", "DeepSeek V4 API",
        "--change-summary", "峰谷计价生效",
        "--audience", "API 开发者",
        "--impact", "成本重算",
        "--priority", "90",
    ]
    for key, value in overrides.items():
        args.extend([key, str(value)])
    return args


class EventCandidateCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ai_evc_cli_")
        self.db = os.path.join(self.tmp, "test.db")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, argv):
        out = io.StringIO()
        code = main(argv, out)
        return code, out.getvalue()

    def test_add_and_list_roundtrip(self):
        code, text = self._run(_add_args(
            self.db,
            **{"--ref": "official_announcement_candidate:deepseek-news260813:DeepSeek V4-Pro GA 公告"}
        ))
        self.assertEqual(code, EXIT_OK)
        self.assertIn("refs: 1", text)
        code, text = self._run(["event-candidate", "list", "--db-path", self.db])
        self.assertEqual(code, EXIT_OK)
        self.assertIn("type=economics_access", text)
        self.assertIn("priority=90", text)
        self.assertIn("subject=DeepSeek V4 API", text)

    def test_add_idempotent(self):
        first, _ = self._run(_add_args(self.db))
        second, text = self._run(_add_args(self.db))
        self.assertEqual(first, EXIT_OK)
        self.assertEqual(second, EXIT_OK)
        self.assertIn("id:", text)
        code, text = self._run(["event-candidate", "list", "--db-path", self.db])
        self.assertEqual(text.count("subject=DeepSeek V4 API"), 1)

    def test_invalid_signal_type_rejected(self):
        code, text = self._run(_add_args(self.db, **{"--signal-type": "nope"}))
        self.assertEqual(code, EXIT_CONFIG_ERROR)
        self.assertIn("invalid signal type", text)

    def test_invalid_priority_rejected(self):
        code, text = self._run(_add_args(self.db, **{"--priority": "101"}))
        self.assertEqual(code, EXIT_CONFIG_ERROR)
        self.assertIn("priority", text)

    def test_invalid_ref_rejected(self):
        code, text = self._run(_add_args(self.db, **{"--ref": "broken"}))
        self.assertEqual(code, EXIT_CONFIG_ERROR)
        self.assertIn("--ref", text)

    def test_list_on_missing_db(self):
        code, text = self._run(["event-candidate", "list", "--db-path", self.db])
        self.assertEqual(code, EXIT_DB_ERROR)
        self.assertIn("database does not exist", text)


if __name__ == "__main__":
    unittest.main()
