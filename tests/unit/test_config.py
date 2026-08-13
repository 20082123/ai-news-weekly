"""Unit tests for :mod:`ai_signal.config`."""

import os
import pathlib
import sys
import unittest
from io import StringIO

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from ai_signal import cli  # noqa: E402
from ai_signal.config import ALLOWED_RUN_MODES, ConfigError, load_settings  # noqa: E402

# Environment variables that load_settings reads; cleared per-test.
_ENV_KEYS = (
    "AI_SIGNAL_RUN_MODE",
    "AI_SIGNAL_LOG_LEVEL",
    "AI_SIGNAL_TIMEZONE",
    "AI_SIGNAL_DB_PATH",
    "AI_SIGNAL_VAULT_PATH",
)


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self._saved = {key: os.environ.pop(key, None) for key in _ENV_KEYS}

    def tearDown(self):
        for key, value in self._saved.items():
            if value is not None:
                os.environ[key] = value
            else:
                os.environ.pop(key, None)

    def test_default_is_shadow_with_capabilities_disabled(self):
        settings = load_settings()
        self.assertEqual(settings.run_mode, "shadow")
        self.assertIn("shadow", ALLOWED_RUN_MODES)
        self.assertFalse(settings.network_enabled)
        self.assertFalse(settings.email_enabled)
        self.assertFalse(settings.publish_enabled)
        self.assertIsNone(settings.db_path)
        self.assertIsNone(settings.vault_path)

    def test_cli_overrides_environment(self):
        os.environ["AI_SIGNAL_RUN_MODE"] = "live"
        settings = load_settings(run_mode="shadow")
        self.assertEqual(settings.run_mode, "shadow")

    def test_env_overrides_default(self):
        os.environ["AI_SIGNAL_LOG_LEVEL"] = "DEBUG"
        settings = load_settings()
        self.assertEqual(settings.log_level, "DEBUG")

    def test_invalid_run_mode_rejected(self):
        with self.assertRaises(ConfigError):
            load_settings(run_mode="yolo")

    def test_invalid_log_level_rejected(self):
        with self.assertRaises(ConfigError):
            load_settings(log_level="VERBOSE")

    def test_settings_are_immutable(self):
        settings = load_settings()
        with self.assertRaises(Exception):
            settings.run_mode = "live"  # type: ignore[misc]

    def test_config_check_does_not_output_values(self):
        secret_db = os.path.abspath("super_secret_db_path_xyz.db")
        secret_vault = os.path.abspath("super_secret_vault_path_xyz")
        os.environ["AI_SIGNAL_DB_PATH"] = secret_db
        os.environ["AI_SIGNAL_VAULT_PATH"] = secret_vault
        os.environ["AI_SIGNAL_RUN_MODE"] = "shadow"

        buf = StringIO()
        rc = cli.main(["config", "check"], out=buf)
        output = buf.getvalue()

        self.assertEqual(rc, 0)
        # The configured path values must never appear in the output.
        self.assertNotIn("super_secret_db_path_xyz", output)
        self.assertNotIn("super_secret_vault_path_xyz", output)
        # But the configured/not-configured status is shown.
        self.assertIn("db_path: configured", output)
        self.assertIn("vault_path: configured", output)
        self.assertIn("network: disabled", output)
        self.assertIn("publish: disabled", output)

    def test_config_check_reports_invalid_mode(self):
        buf = StringIO()
        rc = cli.main(["config", "check", "--run-mode", "nope"], out=buf)
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
