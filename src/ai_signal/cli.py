"""Offline command line interface.

Implemented commands:

* ``ai-signal config check``              show configured / not configured status
* ``ai-signal db init --path PATH``       create or migrate the local database
* ``ai-signal db status --path PATH``     show schema status
* ``ai-signal doctor``                    offline environment diagnostics
* ``ai-signal collect github ...``        offline fixture-backed collection
* ``ai-signal collect github-live ...``   read-only public GitHub Search API
                                           (requires ``--allow-network``)
* ``ai-signal discover github ...``       run a GitHub discovery policy (2C2),
                                           list policy catalog, show run status
                                           (network probes require
                                           ``--allow-network``)

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

    # materialize ------------------------------------------------------------
    p_mat = sub.add_parser("materialize", help="build material packs from collected data")
    mat_sub = p_mat.add_subparsers(dest="materialize_command", required=True)
    p_mat_gh = mat_sub.add_parser("github", help="materialize GitHub raw signals")
    p_mat_gh.add_argument("--db-path", dest="db_path", required=True)
    p_mat_gh.add_argument("--week-key", dest="week_key", required=True)
    p_mat_gh.add_argument("--scope-key", dest="scope_key", required=True)
    p_mat_gh.add_argument("--output-root", dest="output_root", required=True)
    p_mat_gh.add_argument("--limit", type=int, default=10)
    p_mat_gh.add_argument(
        "--allow-output-write",
        dest="allow_output_write",
        action="store_true",
        help="required gate to write Markdown files",
    )

    # feedback ---------------------------------------------------------------
    p_fb = sub.add_parser("feedback", help="sync human feedback into the database")
    fb_sub = p_fb.add_subparsers(dest="feedback_command", required=True)
    p_fb_sync = fb_sub.add_parser("sync", help="scan inbox and sync feedback")
    p_fb_sync.add_argument("--db-path", dest="db_path", required=True)
    p_fb_sync.add_argument("--inbox-dir", dest="inbox_dir", required=True)
    p_fb_sync.add_argument(
        "--allow-feedback-write",
        dest="allow_feedback_write",
        action="store_true",
        help="required gate to write feedback rows",
    )

    # candidate --------------------------------------------------------------
    p_cand = sub.add_parser(
        "candidate", help="candidate qualification (research / watch / reject)"
    )
    cand_sub = p_cand.add_subparsers(dest="candidate_command", required=True)
    p_cq = cand_sub.add_parser(
        "qualify-github", help="qualify GitHub candidates from collected snapshots"
    )
    p_cq.add_argument("--db-path", dest="db_path", required=True)
    p_cq.add_argument("--week-key", dest="week_key", required=True)
    p_cq.add_argument("--scope-key", dest="scope_key", required=True)
    p_cq.add_argument(
        "--lane", required=True, help="watchlist | mature | emerging | ecosystem"
    )
    p_cq.add_argument("--limit", type=int, default=50)
    p_cq.add_argument(
        "--emit-candidate-markdown",
        dest="emit_candidate_markdown",
        action="store_true",
        help="optional debug output: only RESEARCH candidates render a card",
    )
    p_cq.add_argument("--output-root", dest="output_root")
    p_cq.add_argument(
        "--allow-output-write",
        dest="allow_output_write",
        action="store_true",
        help="required (with --emit-candidate-markdown) to write debug cards",
    )
    # discover ---------------------------------------------------------------
    p_disc = sub.add_parser(
        "discover", help="GitHub discovery policy runner (phase 2C2)"
    )
    disc_sub = p_disc.add_subparsers(dest="discover_command", required=True)
    p_disc_gh = disc_sub.add_parser(
        "github", help="run a GitHub discovery policy or inspect catalog/status"
    )
    p_disc_gh.add_argument("--db-path", dest="db_path")
    p_disc_gh.add_argument("--week-key", dest="week_key")
    p_disc_gh.add_argument(
        "--policy", dest="policy_id", help="policy id from the catalog"
    )
    p_disc_gh.add_argument("--timeout", type=int, default=10)
    p_disc_gh.add_argument(
        "--allow-network",
        dest="allow_network",
        action="store_true",
        help="required gate to enable real network requests",
    )
    p_disc_gh.add_argument(
        "--list-policies",
        dest="list_policies",
        action="store_true",
        help="print safe catalog summaries (never queries or hashes)",
    )
    p_disc_gh.add_argument(
        "--status",
        dest="show_status",
        action="store_true",
        help="print recent discovery runs with safe counts",
    )
    p_disc_gh.add_argument("--limit", type=int, default=10)
    # event-candidate (2C3) ---------------------------------------------------
    p_evc = sub.add_parser(
        "event-candidate",
        help="source-independent event candidates (phase 2C3)",
    )
    evc_sub = p_evc.add_subparsers(dest="event_candidate_command", required=True)
    p_evc_add = evc_sub.add_parser("add", help="record one event candidate")
    p_evc_add.add_argument("--db-path", dest="db_path", required=True)
    p_evc_add.add_argument("--signal-type", dest="signal_type", required=True)
    p_evc_add.add_argument("--subject", required=True)
    p_evc_add.add_argument("--change-summary", dest="change_summary", required=True)
    p_evc_add.add_argument("--audience", dest="affected_audience", required=True)
    p_evc_add.add_argument("--impact", dest="work_impact_hypothesis", required=True)
    p_evc_add.add_argument("--priority", dest="research_priority", type=int, required=True)
    p_evc_add.add_argument(
        "--missing-evidence",
        dest="missing_evidence",
        default="",
        help="comma-separated stable evidence codes",
    )
    p_evc_add.add_argument(
        "--ref",
        dest="refs",
        action="append",
        default=[],
        help="kind:ref_id[:label], repeatable, e.g. "
        "github_repository_candidate:<id>:<label>",
    )
    p_evc_list = evc_sub.add_parser("list", help="list event candidates (safe summary)")
    p_evc_list.add_argument("--db-path", dest="db_path", required=True)
    p_evc_list.add_argument("--signal-type", dest="signal_type")
    p_evc_promo = evc_sub.add_parser(
        "promote-github",
        help="promote a week's queued GitHub candidates into event-candidate drafts",
    )
    p_evc_promo.add_argument("--db-path", dest="db_path", required=True)
    p_evc_promo.add_argument("--week-key", dest="week_key", required=True)
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


def cmd_materialize_github(args, out) -> int:
    if not _WEEK_KEY_RE.match(args.week_key):
        out.write("invalid week key: expected YYYY-Www\n")
        return EXIT_CONFIG_ERROR
    try:
        validate_scope_key(args.scope_key)
    except (TypeError, ValueError):
        out.write("invalid scope key\n")
        return EXIT_CONFIG_ERROR
    if not isinstance(args.limit, int) or isinstance(args.limit, bool) or not 1 <= args.limit <= 50:
        out.write("invalid limit\n")
        return EXIT_CONFIG_ERROR

    if not args.allow_output_write:
        out.write("output write not allowed: --allow-output-write is required\n")
        return EXIT_SECURITY

    from pathlib import Path

    from .outputs.markdown import MarkdownPublishError, publish_pack_markdown
    from .pipeline.materialize import materialize_github
    from .pipeline.package import build_and_store_pack
    from .storage import sqlite as sqlite_storage

    try:
        sqlite_storage.initialize_database(args.db_path)
    except sqlite_storage.StorageError:
        out.write("database error\n")
        return EXIT_DB_ERROR

    # Phase 1: materialize + validate + persist packs + packaged state, all in
    # one SQLite transaction. Markdown artifacts are prepared in memory only.
    artifacts = []  # (pack_id, event_id, content, claim_evidence_links)
    conn = sqlite_storage._open(args.db_path)
    try:
        conn.execute("BEGIN")
        mat_result = materialize_github(
            conn, args.week_key, args.scope_key, args.limit
        )
        packs_built = 0
        for touch in mat_result.events:
            pr = build_and_store_pack(
                conn,
                week_key=args.week_key,
                event_id=touch.event_id,
                signal_id=touch.signal_id,
                title=touch.title,
                claims=touch.claims,
            )
            if pr is None:
                continue
            packs_built += 1
            artifacts.append(
                (pr.pack_id, touch.event_id, pr.content, pr.claim_evidence_links)
            )
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        conn.close()
        out.write("database error\n")
        return EXIT_DB_ERROR
    conn.close()

    # Phase 2: publish Markdown AFTER the DB commit. A publish failure keeps
    # the committed packs and is reported as an output error, never a database
    # error; a re-run can safely补写 any missing file.
    output_failed = False
    for pack_id, event_id, content, links in artifacts:
        try:
            publish_pack_markdown(
                Path(args.output_root), pack_id, event_id, args.week_key, content, links
            )
        except MarkdownPublishError:
            output_failed = True

    out.write("processed: %d\n" % mat_result.processed)
    out.write("signals_created: %d\n" % mat_result.signals_created)
    out.write("signals_updated: %d\n" % mat_result.signals_updated)
    out.write("events_created: %d\n" % mat_result.events_created)
    out.write("claims_created: %d\n" % mat_result.claims_created)
    out.write("packs: %d\n" % packs_built)
    out.write("quarantined: %d\n" % mat_result.quarantined)

    if output_failed:
        out.write("output error\n")
        return EXIT_CAPABILITY
    return EXIT_OK


def cmd_feedback_sync(args, out) -> int:
    from pathlib import Path

    from .feedback.sync import FeedbackSyncError, sync_feedback
    from .storage import sqlite as sqlite_storage

    if not args.allow_feedback_write:
        out.write("feedback write not allowed: --allow-feedback-write is required\n")
        return EXIT_SECURITY

    try:
        sqlite_storage.initialize_database(args.db_path)
    except sqlite_storage.StorageError:
        out.write("database error\n")
        return EXIT_DB_ERROR

    conn = sqlite_storage._open(args.db_path)
    try:
        conn.execute("BEGIN")
        result = sync_feedback(conn, Path(args.inbox_dir))
        conn.execute("COMMIT")
    except sqlite_storage.StorageError:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        conn.close()
        out.write("database error\n")
        return EXIT_DB_ERROR
    except FeedbackSyncError:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        conn.close()
        out.write("feedback error\n")
        return EXIT_CONFIG_ERROR
    conn.close()

    out.write("scanned: %d\n" % result.scanned)
    out.write("inserted: %d\n" % result.inserted)
    out.write("skipped: %d\n" % result.skipped)
    out.write("invalid: %d\n" % result.invalid)
    return EXIT_OK


def cmd_candidate_qualify_github(args, out) -> int:
    from .pipeline.qualify import QualifyError, qualify_github
    from .storage import sqlite as sqlite_storage

    if not _WEEK_KEY_RE.match(args.week_key):
        out.write("invalid week key: expected YYYY-Www\n")
        return EXIT_CONFIG_ERROR
    try:
        validate_scope_key(args.scope_key)
    except (TypeError, ValueError):
        out.write("invalid scope key\n")
        return EXIT_CONFIG_ERROR
    if args.lane not in ("watchlist", "mature", "emerging", "ecosystem"):
        out.write("invalid lane\n")
        return EXIT_CONFIG_ERROR
    if not isinstance(args.limit, int) or isinstance(args.limit, bool) or not 1 <= args.limit <= 100:
        out.write("invalid limit\n")
        return EXIT_CONFIG_ERROR

    # Optional debug Markdown requires all three flags together. Validation
    # happens BEFORE any database work so a bad invocation has no side effects.
    emit_markdown = bool(args.emit_candidate_markdown)
    if emit_markdown and not args.output_root:
        out.write("config error: --output-root is required with --emit-candidate-markdown\n")
        return EXIT_CONFIG_ERROR
    if emit_markdown and not args.allow_output_write:
        out.write("output write not allowed: --allow-output-write is required\n")
        return EXIT_SECURITY

    try:
        sqlite_storage.initialize_database(args.db_path)
    except sqlite_storage.StorageError:
        out.write("database error\n")
        return EXIT_DB_ERROR

    # Phase 1: qualification + candidates + discoveries + assessments in one
    # transaction. Default runs are DB-only.
    conn = sqlite_storage._open(args.db_path)
    try:
        conn.execute("BEGIN")
        result = qualify_github(
            conn, args.week_key, args.scope_key, args.lane, args.limit
        )
        conn.execute("COMMIT")
    except sqlite_storage.StorageError:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        conn.close()
        out.write("database error\n")
        return EXIT_DB_ERROR
    except QualifyError as exc:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        conn.close()
        out.write("config error: %s\n" % exc)
        return EXIT_CONFIG_ERROR
    conn.close()

    # Phase 2: optional debug Markdown after the DB commit; only RESEARCH
    # candidates render, one failure keeps the committed candidate and does
    # not block the remaining cards.
    markdown_written = 0
    output_failed = False
    if emit_markdown:
        from pathlib import Path

        from .outputs.candidate_markdown import (
            CandidateMarkdownError,
            publish_candidate_markdown,
        )

        for qc in result.qualified:
            try:
                publish_candidate_markdown(Path(args.output_root), qc)
                markdown_written += 1
            except CandidateMarkdownError:
                output_failed = True

    out.write("processed: %d\n" % result.processed)
    out.write("candidates_created: %d\n" % result.candidates_created)
    out.write("candidates_updated: %d\n" % result.candidates_updated)
    out.write("discoveries_created: %d\n" % result.discoveries_created)
    out.write("discoveries_existing: %d\n" % result.discoveries_existing)
    out.write("assessments_created: %d\n" % result.assessments_created)
    out.write("research: %d\n" % result.research)
    out.write("watch: %d\n" % result.watch)
    out.write("rejected: %d\n" % result.rejected)
    out.write("quarantined: %d\n" % result.quarantined)
    out.write("markdown_written: %d\n" % markdown_written)

    if output_failed:
        out.write("output error\n")
        return EXIT_CAPABILITY
    return EXIT_OK


def cmd_discover_github(args, out) -> int:
    from .discovery import list_policies, run_github_discovery
    from .discovery.policy import DiscoveryPolicyError
    from .pipeline.collect import CollectionPolicyError
    from .storage import sqlite as sqlite_storage

    if args.list_policies:
        # Safe catalog metadata only: never a query, scope, spec or hash.
        for entry in list_policies():
            out.write("policy: %s\n" % entry["id"])
            out.write("  lane: %s\n" % entry["lane"])
            out.write("  probes: %d\n" % entry["probe_count"])
            out.write("  candidate_limit: %d\n" % entry["candidate_limit"])
            out.write("  research_budget: %d\n" % entry["research_budget"])
        return EXIT_OK

    if args.show_status:
        if not args.db_path:
            out.write("config error: --db-path is required\n")
            return EXIT_CONFIG_ERROR
        if not isinstance(args.limit, int) or isinstance(args.limit, bool) or not 1 <= args.limit <= 50:
            out.write("invalid limit\n")
            return EXIT_CONFIG_ERROR
        from pathlib import Path

        if not Path(args.db_path).exists():
            out.write("database does not exist\n")
            return EXIT_DB_ERROR
        try:
            with sqlite_storage.connect(args.db_path) as conn:
                rows = conn.execute(
                    "SELECT r.id, r.policy_id, r.week_key, r.status, r.started_at, "
                    " (SELECT COUNT(*) FROM github_candidate_selection s "
                    "  WHERE s.discovery_run_id = r.id) AS selected "
                    "FROM github_discovery_run r ORDER BY r.started_at DESC LIMIT ?",
                    (args.limit,),
                ).fetchall()
        except sqlite_storage.StorageError:
            out.write("database error\n")
            return EXIT_DB_ERROR
        except Exception:  # noqa: BLE001 - missing tables etc.
            out.write("database error\n")
            return EXIT_DB_ERROR
        for row in rows:
            out.write(
                "run: %s policy=%s week=%s status=%s selected=%d\n"
                % (row["id"], row["policy_id"], row["week_key"],
                   row["status"], int(row["selected"]))
            )
        return EXIT_OK

    # Run mode: everything is validated before any database or network work.
    if not args.db_path:
        out.write("config error: --db-path is required\n")
        return EXIT_CONFIG_ERROR
    if not args.week_key:
        out.write("config error: --week-key is required\n")
        return EXIT_CONFIG_ERROR
    if not args.policy_id:
        out.write("config error: --policy is required\n")
        return EXIT_CONFIG_ERROR
    if not _WEEK_KEY_RE.match(args.week_key):
        out.write("invalid week key: expected YYYY-Www\n")
        return EXIT_CONFIG_ERROR
    if isinstance(args.timeout, bool) or not isinstance(args.timeout, int) or not 1 <= args.timeout <= 30:
        out.write("invalid timeout\n")
        return EXIT_CONFIG_ERROR

    try:
        result = run_github_discovery(
            args.db_path,
            args.week_key,
            args.policy_id,
            allow_network=bool(args.allow_network),
            timeout_seconds=args.timeout,
        )
    except DiscoveryPolicyError as exc:
        out.write("config error: %s\n" % exc)
        return EXIT_CONFIG_ERROR
    except CollectionPolicyError as exc:
        out.write("policy error: %s\n" % exc)
        return EXIT_SECURITY
    except sqlite_storage.StorageError:
        out.write("database error\n")
        return EXIT_DB_ERROR

    # Only safe, payload-free counts. No query, URL, scope key, hash or path.
    out.write("run_id: %s\n" % result.run_id)
    out.write("status: %s\n" % result.status)
    out.write("probes_total: %d\n" % result.probes_total)
    out.write("probes_blocked: %d\n" % result.probes_blocked)
    out.write("probes_failed: %d\n" % result.probes_failed)
    out.write("processed: %d\n" % result.processed)
    out.write("research: %d\n" % result.research)
    out.write("watch: %d\n" % result.watch)
    out.write("rejected: %d\n" % result.rejected)
    out.write("quarantined: %d\n" % result.quarantined)
    out.write("selections_total: %d\n" % result.selections_total)
    out.write("queued: %d\n" % result.queued)
    out.write("over_budget: %d\n" % result.over_budget)
    out.write("beyond_candidate_limit: %d\n" % result.beyond_candidate_limit)

    if result.status == "success":
        return EXIT_OK
    # partial / failed are non-success completions (degradation is visible).
    return EXIT_CAPABILITY


def cmd_event_candidate(args, out) -> int:
    from pathlib import Path

    from .domain.models import (
        SIGNAL_TYPES,
        EventCandidate,
        EventCandidateSourceRef,
    )
    from .pipeline.event_candidate import (
        EventCandidateError,
        promote_github_queue,
        record_event_candidate,
    )
    from .storage import sqlite as sqlite_storage
    from .storage.event_candidate_repositories import (
        EventCandidateRepository,
        EventCandidateSourceRefRepository,
    )

    if args.event_candidate_command == "promote-github":
        if not _WEEK_KEY_RE.match(args.week_key):
            out.write("invalid week key: expected YYYY-Www\n")
            return EXIT_CONFIG_ERROR
        try:
            sqlite_storage.initialize_database(args.db_path)
            conn = sqlite_storage._open(args.db_path)
            try:
                conn.execute("BEGIN")
                result = promote_github_queue(conn, args.week_key)
                conn.execute("COMMIT")
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                raise
            finally:
                conn.close()
        except EventCandidateError as exc:
            out.write("config error: %s\n" % exc)
            return EXIT_CONFIG_ERROR
        except sqlite_storage.StorageError:
            out.write("database error\n")
            return EXIT_DB_ERROR
        out.write("processed: %d\n" % result.processed)
        out.write("promoted: %d\n" % result.promoted)
        out.write("already_promoted: %d\n" % result.already_promoted)
        out.write("refs_added: %d\n" % result.refs_added)
        return EXIT_OK

    if args.event_candidate_command == "list":
        if not Path(args.db_path).exists():
            out.write("database does not exist\n")
            return EXIT_DB_ERROR
        try:
            sqlite_storage.initialize_database(args.db_path)
            with sqlite_storage.connect(args.db_path) as conn:
                rows = EventCandidateRepository(conn).list(args.signal_type)
                ref_repo = EventCandidateSourceRefRepository(conn)
                for row in rows:
                    refs = ref_repo.list_for_candidate(row.id)
                    out.write(
                        "id=%s type=%s priority=%d refs=%d subject=%s\n"
                        % (row.id, row.signal_type, row.research_priority,
                           len(refs), row.subject)
                    )
        except ValueError as exc:
            out.write("config error: %s\n" % exc)
            return EXIT_CONFIG_ERROR
        except sqlite_storage.StorageError:
            out.write("database error\n")
            return EXIT_DB_ERROR
        return EXIT_OK

    # add
    if args.signal_type not in SIGNAL_TYPES:
        out.write("config error: invalid signal type\n")
        return EXIT_CONFIG_ERROR
    if (
        isinstance(args.research_priority, bool)
        or not isinstance(args.research_priority, int)
        or not 0 <= args.research_priority <= 100
    ):
        out.write("invalid priority: must be 0-100\n")
        return EXIT_CONFIG_ERROR
    missing = tuple(
        code.strip() for code in args.missing_evidence.split(",") if code.strip()
    )
    candidate = EventCandidate(
        signal_type=args.signal_type,
        subject=args.subject,
        change_summary=args.change_summary,
        affected_audience=args.affected_audience,
        work_impact_hypothesis=args.work_impact_hypothesis,
        research_priority=args.research_priority,
        missing_evidence=missing,
    )
    refs = []
    for raw in args.refs:
        parts = raw.split(":", 2)
        if len(parts) < 2 or not parts[0].strip() or not parts[1].strip():
            out.write("config error: invalid --ref (expected kind:ref_id[:label])\n")
            return EXIT_CONFIG_ERROR
        kind, ref_id = parts[0].strip(), parts[1].strip()
        label = parts[2].strip() if len(parts) == 3 and parts[2].strip() else ref_id
        refs.append(EventCandidateSourceRef(
            event_candidate_id=candidate.id,
            source_kind=kind,
            ref_id=ref_id,
            ref_label=label,
        ))

    try:
        sqlite_storage.initialize_database(args.db_path)
        conn = sqlite_storage._open(args.db_path)
        try:
            conn.execute("BEGIN")
            record = record_event_candidate(conn, candidate, refs)
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()
    except ValueError as exc:
        out.write("config error: %s\n" % exc)
        return EXIT_CONFIG_ERROR
    except sqlite_storage.StorageError:
        out.write("database error\n")
        return EXIT_DB_ERROR

    out.write("id: %s\n" % record.candidate.id)
    out.write("refs: %d\n" % len(record.refs))
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
    if args.command == "collect" and args.collect_command == "github":
        return cmd_collect_github(args, stream)
    if args.command == "collect" and args.collect_command == "github-live":
        return cmd_collect_github_live(args, stream)
    if args.command == "materialize" and args.materialize_command == "github":
        return cmd_materialize_github(args, stream)
    if args.command == "feedback" and args.feedback_command == "sync":
        return cmd_feedback_sync(args, stream)
    if args.command == "candidate" and args.candidate_command == "qualify-github":
        return cmd_candidate_qualify_github(args, stream)
    if args.command == "discover" and args.discover_command == "github":
        return cmd_discover_github(args, stream)
    if args.command == "event-candidate":
        return cmd_event_candidate(args, stream)

    parser.print_help(stream)
    return EXIT_OK
