"""Offline command line interface.

Implemented commands:

* ``ai-signal config check``              show configured / not configured status
* ``ai-signal db init --path PATH``       create or migrate the local database
* ``ai-signal db status --path PATH``     show schema status
* ``ai-signal doctor``                    offline environment diagnostics
* ``ai-signal collect github ...``        offline fixture-backed collection
* ``ai-signal collect github-live ...``   read-only public GitHub Search API
                                           (requires ``--allow-network``)

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
import re
import sqlite3
import sys
from pathlib import Path
from typing import Optional

from .config import ConfigError, load_settings
from .domain.models import validate_scope_key
from .pipeline.collect import CollectionPolicyError, collect_source_once
from .sources.github_fixture import FixtureError, FixtureGitHubClient
from .sources.github_rest import GitHubRestClient, GitHubSearchSpec, UrllibTransport
from .storage import sqlite as sqlite_storage

EXIT_OK = 0
EXIT_CONFIG_ERROR = 2
EXIT_DB_ERROR = 3
EXIT_SECURITY = 4
EXIT_CAPABILITY = 5

# Strict ISO-8601-ish week key, e.g. 2026-W33.
_WEEK_KEY_RE = re.compile(r"^\d{4}-W\d{2}$")


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

    # collect ----------------------------------------------------------------
    p_collect = sub.add_parser("collect", help="offline fixture-backed collection")
    collect_sub = p_collect.add_subparsers(dest="collect_command", required=True)
    p_github = collect_sub.add_parser(
        "github", help="collect one page from a GitHub JSON fixture"
    )
    p_github.add_argument("--fixture", required=True, help="path to a GitHub JSON fixture")
    p_github.add_argument("--db-path", dest="db_path", required=True, help="SQLite database path")
    p_github.add_argument(
        "--week-key", dest="week_key", required=True, help="week key, e.g. 2026-W33"
    )
    p_github.add_argument(
        "--scope-key",
        dest="scope_key",
        required=True,
        help="stable logical collection scope, e.g. github-fixture-v1",
    )
    p_live = collect_sub.add_parser(
        "github-live",
        help="collect one page from the real public GitHub Search API",
    )
    p_live.add_argument("--query", required=True, help="GitHub repository search query")
    p_live.add_argument("--db-path", dest="db_path", required=True, help="SQLite database path")
    p_live.add_argument(
        "--week-key", dest="week_key", required=True, help="week key, e.g. 2026-W33"
    )
    p_live.add_argument(
        "--scope-key",
        dest="scope_key",
        required=True,
        help="stable logical collection scope, e.g. ai-agents-v1",
    )
    p_live.add_argument("--sort", default="updated", help="updated or stars")
    p_live.add_argument("--order", default="desc", help="asc or desc")
    p_live.add_argument("--per-page", dest="per_page", type=int, default=10)
    p_live.add_argument("--max-pages", dest="max_pages", type=int, default=1)
    p_live.add_argument("--timeout", type=int, default=10)
    p_live.add_argument(
        "--allow-network",
        dest="allow_network",
        action="store_true",
        help="required gate to enable a real network request",
    )
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


def cmd_collect_github(args, out) -> int:
    if not _WEEK_KEY_RE.match(args.week_key):
        out.write("invalid week key: expected YYYY-Www\n")
        return EXIT_CONFIG_ERROR

    try:
        validate_scope_key(args.scope_key)
    except (TypeError, ValueError):
        out.write("invalid scope key\n")
        return EXIT_CONFIG_ERROR

    try:
        client = FixtureGitHubClient(args.fixture)
    except FixtureError as exc:
        out.write("fixture error: %s\n" % exc)
        return EXIT_CONFIG_ERROR
    except OSError:
        # Deliberately do not echo the path-bearing OS error text.
        out.write("fixture error: unable to read fixture\n")
        return EXIT_CONFIG_ERROR

    config_snapshot = {
        "run_mode": "shadow",
        "source": "github",
        "scope_key": args.scope_key,
        "adapter_kind": "fixture",
        "fixture": True,
        "fixture_sha256": client.fixture_sha256,
    }

    try:
        result = collect_source_once(
            db_path=args.db_path,
            source="github",
            week_key=args.week_key,
            scope_key=args.scope_key,
            client=client,
            config_snapshot=config_snapshot,
        )
    except CollectionPolicyError as exc:
        out.write("policy error: %s\n" % exc)
        return EXIT_SECURITY
    except sqlite_storage.StorageError:
        # Generic message: never print the db path or low-level error text.
        out.write("database error\n")
        return EXIT_DB_ERROR

    # Output only safe, payload-free fields. No fixture path, db path, URL,
    # scope key or item payload is ever printed.
    out.write("run_id: %s\n" % result.run_id)
    out.write("status: %s\n" % result.status)
    out.write("processed_item_count: %d\n" % result.processed_item_count)
    out.write("warning_count: %d\n" % result.warning_count)
    out.write("cursor_advanced: %s\n" % result.cursor_advanced)

    if result.status == "success":
        return EXIT_OK
    # partial / unavailable / failed are non-success completions.
    return EXIT_CAPABILITY


def cmd_collect_github_live(args, out) -> int:
    if not _WEEK_KEY_RE.match(args.week_key):
        out.write("invalid week key: expected YYYY-Www\n")
        return EXIT_CONFIG_ERROR

    try:
        validate_scope_key(args.scope_key)
    except (TypeError, ValueError):
        out.write("invalid scope key\n")
        return EXIT_CONFIG_ERROR

    if isinstance(args.timeout, bool) or not isinstance(args.timeout, int) or not 1 <= args.timeout <= 30:
        out.write("invalid timeout\n")
        return EXIT_CONFIG_ERROR

    # Build the search spec; this validates the raw query without echoing it.
    try:
        spec = GitHubSearchSpec(
            query=args.query,
            sort=args.sort,
            order=args.order,
            per_page=args.per_page,
            max_pages=args.max_pages,
        )
    except (TypeError, ValueError):
        out.write("invalid search parameters\n")
        return EXIT_CONFIG_ERROR

    # Security gate: the real network is opt-in and must be explicit. This is
    # checked before any database is created and before any request is made.
    if not args.allow_network:
        out.write("network not allowed: --allow-network is required\n")
        return EXIT_SECURITY

    config_snapshot = {
        "run_mode": "shadow",
        "source": "github",
        "scope_key": args.scope_key,
        "adapter_kind": "github-rest-v1",
        "query_sha256": spec.query_sha256,
        "sort": spec.sort,
        "order": spec.order,
        "per_page": spec.per_page,
        "max_pages": spec.max_pages,
    }

    client = GitHubRestClient(spec, UrllibTransport(), timeout_seconds=args.timeout)

    try:
        result = collect_source_once(
            db_path=args.db_path,
            source="github",
            week_key=args.week_key,
            scope_key=args.scope_key,
            client=client,
            config_snapshot=config_snapshot,
        )
    except CollectionPolicyError as exc:
        out.write("policy error: %s\n" % exc)
        return EXIT_SECURITY
    except sqlite_storage.StorageError:
        out.write("database error\n")
        return EXIT_DB_ERROR

    out.write("run_id: %s\n" % result.run_id)
    out.write("status: %s\n" % result.status)
    out.write("processed_item_count: %d\n" % result.processed_item_count)
    out.write("warning_count: %d\n" % result.warning_count)
    out.write("cursor_advanced: %s\n" % result.cursor_advanced)

    if result.status == "success":
        return EXIT_OK
    return EXIT_CAPABILITY


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
    if args.command == "collect" and args.collect_command == "github":
        return cmd_collect_github(args, stream)
    if args.command == "collect" and args.collect_command == "github-live":
        return cmd_collect_github_live(args, stream)

    parser.print_help(stream)
    return EXIT_OK
