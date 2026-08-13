"""Configuration for AI Signal Agent.

Settings are immutable and loaded with an explicit priority:

    CLI overrides  >  environment variables  >  safe defaults

Phase 1 always defaults to ``shadow`` mode, in which network access,
email delivery and publishing are disabled. No secrets, cookies or
vault paths are hard-coded anywhere; when a value is not provided it
stays unset rather than being guessed, and no directory is created as a
side effect of loading configuration.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


class ConfigError(Exception):
    """Raised when configuration is invalid or unsafe."""


# Only these explicit values are accepted; anything else is rejected.
ALLOWED_RUN_MODES = ("shadow", "live")
ALLOWED_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


@dataclass(frozen=True)
class Settings:
    """Immutable runtime configuration.

    Note: ``db_path`` and ``vault_path`` are only *resolved* into ``Path``
    objects here. They are never created on disk and their string values
    are never printed by ``config check``.
    """

    run_mode: str
    log_level: str
    timezone: str
    db_path: Optional[Path] = None
    vault_path: Optional[Path] = None

    @property
    def network_enabled(self) -> bool:
        return self.run_mode == "live"

    @property
    def email_enabled(self) -> bool:
        return self.run_mode == "live"

    @property
    def publish_enabled(self) -> bool:
        return self.run_mode == "live"


def _first(*candidates):
    """Return the first non-empty value, or ``None``."""
    for value in candidates:
        if value is not None and value != "":
            return value
    return None


def load_settings(
    *,
    run_mode: Optional[str] = None,
    log_level: Optional[str] = None,
    timezone: Optional[str] = None,
    db_path: Optional[str] = None,
    vault_path: Optional[str] = None,
) -> Settings:
    """Build a :class:`Settings` instance from CLI > env > defaults.

    ``run_mode`` and ``log_level`` are validated against their explicit
    allow-lists; an unknown value raises :class:`ConfigError` rather than
    being silently coerced.
    """

    mode = _first(run_mode, os.environ.get("AI_SIGNAL_RUN_MODE"), "shadow")
    if mode not in ALLOWED_RUN_MODES:
        raise ConfigError(
            "invalid run mode: %r (allowed: %s)" % (mode, ", ".join(ALLOWED_RUN_MODES))
        )

    level = _first(log_level, os.environ.get("AI_SIGNAL_LOG_LEVEL"), "INFO")
    if level not in ALLOWED_LOG_LEVELS:
        raise ConfigError(
            "invalid log level: %r (allowed: %s)" % (level, ", ".join(ALLOWED_LOG_LEVELS))
        )

    tz = _first(timezone, os.environ.get("AI_SIGNAL_TIMEZONE"), "UTC")

    db_raw = _first(db_path, os.environ.get("AI_SIGNAL_DB_PATH"))
    vault_raw = _first(vault_path, os.environ.get("AI_SIGNAL_VAULT_PATH"))

    return Settings(
        run_mode=mode,
        log_level=level,
        timezone=tz,
        db_path=Path(db_raw) if db_raw else None,
        vault_path=Path(vault_raw) if vault_raw else None,
    )
