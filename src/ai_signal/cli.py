"""Offline command line interface.

Implemented commands:

* ``ai-signal config check``              show configured / not configured status
* ``ai-signal db init --path PATH``       create or migrate the local database
* ``ai-signal db status --path PATH``     show schema status
* ``ai-signal doctor``                    offline environment diagnostics

Exit codes:

* 0 - success
* 2 - configuration error
* 3 - database or migration error
* 4 - security policy blocked
* 5 - capability incomplete

The CLI performs no work at import time. ``doctor`` never reaches the
network, never calls Agent-Reach, never reads cookies, never creates a
vault and never sends email.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Optional

from .config import ConfigError, load_settings
from .storage import sqlite as sqlite_storage

EXIT_OK = 0
EXIT_CONFIG_ERROR = 2
EXIT_DB_ERROR = 3
EXIT_SECURITY = 4
EXIT_CAPABILITY = 5


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai-signal",
        description="AI Signal Agent offline CLI (phase 1, shadow mode).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # config -----------------------------------------------------------------
    p_config = sub.add_parser("config", help="configuration utilities")
    config_sub = p_config.add_subparsers(dest="config_command", required=True)
    p_check = config_sub.add_parser("check", help="show configured / not configured status")
    p_check.add_argument("--run-mode", dest="run_mode")
    p_check.add_argument("--log-level", dest="log_level")
    p_check.add_argument("--db-path", dest="db_path")
    p_check.add_argument("--vault-path", dest="vault_path")
    p_check.add_argument("--timezone")

    # db ---------------------------------------------------------------------
    p_db = sub.add_parser("db", help="local SQLite database utilities")
    db_sub = p_db.add_subparsers(dest="db_command", required=True)
    p_init = db_sub.add_parser("init", help="initialize or migrate the local database")
    p_init.add_argument("--path", required=True)
    p_status = db_sub.add_parser("status", help="show schema status")
    p_status.add_argument("--path", required=True)

    # doctor -----------------------------------------------------------------
    sub.add_parser("doctor", help="run offline environment diagnostics")
    return parser


def _status_of(value) -> str:
    return "configured" if value else "not configured"


def cmd_config_check(args, out) -> int:
    try:
        settings = load_settings(
            run_mode=args.run_mode,
            log_level=args.log_level,
            db_path=args.db_path,
            vault_path=args.vault_path,
            timezone=args.timezone,
        )
    except ConfigError as exc:
        out.write("config error: %s\n" % exc)
        return EXIT_CONFIG_ERROR

    # Only configured/not-configified is printed - never the values themselves
    # (paths could leak the location of a vault or database).
    out.write("db_path: %s\n" % _status_of(settings.db_path))
    out.write("vault_path: %s\n" % _status_of(settings.vault_path))
    out.write("run_mode: configured\n")
    out.write("log_level: configured\n")
    out.write("timezone: configured\n")
    out.write("network: %s\n" % ("enabled" if settings.network_enabled else "disabled"))
    out.write("email: %s\n" % ("enabled" if settings.email_enabled else "disabled"))
    out.write("publish: %s\n" % ("enabled" if settings.publish_enabled else "disabled"))
    return EXIT_OK


def cmd_db_init(args, out) -> int:
    try:
        status = sqlite_storage.initialize_database(args.path)
    except sqlite_storage.MigrationError as exc:
        out.write("migration error: %s\n" % exc)
        return EXIT_DB_ERROR
    except sqlite_storage.StorageError as exc:
        out.write("storage error: %s\n" % exc)
        return EXIT_DB_ERROR
    out.write("database initialized: %s\n" % args.path)
    out.write("schema up_to_date: %s\n" % status.get("up_to_date"))
    out.write("schema checksum_valid: %s\n" % status.get("checksum_valid"))
    out.write("latest_applied: %s\n" % status.get("latest_applied"))
    return EXIT_OK


def cmd_db_status(args, out) -> int:
    target = Path(args.path)
    if not target.exists():
        out.write("database does not exist: %s\n" % args.path)
        return EXIT_DB_ERROR
    try:
        with sqlite_storage.connect(target) as conn:
            status = sqlite_storage.schema_status(conn)
    except sqlite_storage.StorageError as exc:
        out.write("storage error: %s\n" % exc)
        return EXIT_DB_ERROR
    out.write("initialized: %s\n" % status.get("initialized"))
    out.write("latest_applied: %s\n" % status.get("latest_applied"))
    out.write("target: %s\n" % status.get("target"))
    out.write("checksum_valid: %s\n" % status.get("checksum_valid"))
    out.write("up_to_date: %s\n" % status.get("up_to_date"))
    for applied in status.get("applied", []):
        out.write("applied: %s %s\n" % (applied["version"], applied["filename"]))
    return EXIT_OK


def cmd_doctor(args, out) -> int:
    checks = []

    python_ok = sys.version_info >= (3, 10)
    checks.append(
        ("python", "ok" if python_ok else "fail", "%d.%d.%d" % sys.version_info[:3])
    )

    checks.append(("sqlite", "ok", sqlite3.sqlite_version))

    try:
        import ai_signal  # noqa: F401
        from ai_signal import cli as _cli  # noqa: F401
        from ai_signal.config import load_settings as _ls  # noqa: F401
        from ai_signal.domain import models as _models  # noqa: F401
        from ai_signal.domain import states as _states  # noqa: F401
        from ai_signal.storage import sqlite as _sqlite  # noqa: F401
        checks.append(("import", "ok", ""))
        import_ok = True
    except Exception as exc:  # noqa: BLE001
        checks.append(("import", "fail", str(exc)))
        import_ok = False

    try:
        settings = load_settings()
        checks.append(("config", "ok", ""))
        config_ok = True
    except ConfigError as exc:
        settings = None
        checks.append(("config", "fail", str(exc)))
        config_ok = False

    if settings is not None and settings.db_path is not None:
        parent = settings.db_path.parent
        if parent.exists():
            checks.append(("db_parent_dir", "ok", "exists"))
        else:
            checks.append(("db_parent_dir", "warn", "missing"))
    else:
        checks.append(("db_path", "warn", "not configured"))

    if settings is not None and settings.db_path is not None and settings.db_path.exists():
        try:
            with sqlite_storage.connect(settings.db_path) as conn:
                status = sqlite_storage.schema_status(conn)
            label = "ok" if status.get("up_to_date") else "warn"
            checks.append(("schema", label, "latest=%s" % status.get("latest_applied")))
        except sqlite_storage.StorageError as exc:
            checks.append(("schema", "warn", str(exc)))
    else:
        checks.append(("schema", "warn", "not configured"))

    for name, status, detail in checks:
        suffix = (": " + detail) if detail else ""
        out.write("[%s] %s%s\n" % (status.upper(), name, suffix))

    if not python_ok or not import_ok:
        return EXIT_CAPABILITY
    if not config_ok:
        return EXIT_CONFIG_ERROR
    return EXIT_OK


def main(argv: Optional[list] = None, out=None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    stream = out if out is not None else sys.stdout

    if args.command == "config" and args.config_command == "check":
        return cmd_config_check(args, stream)
    if args.command == "db" and args.db_command == "init":
        return cmd_db_init(args, stream)
    if args.command == "db" and args.db_command == "status":
        return cmd_db_status(args, stream)
    if args.command == "doctor":
        return cmd_doctor(args, stream)

    parser.print_help(stream)
    return EXIT_OK
