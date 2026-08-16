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
    p_fb_sync = fb_sub.add_parser(
        "sync", help="scan briefs/inbox and sync two-round feedback (DEC-018)"
    )
    p_fb_sync.add_argument("--db-path", dest="db_path", required=True)
    p_fb_sync.add_argument(
        "--inbox-dir", dest="inbox_dir",
        help="legacy 2B material pack dir (optional)",
    )
    p_fb_sync.add_argument(
        "--content-dir", dest="content_dir",
        help="content brief dir, e.g. the Obsidian vault Content folder",
    )
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

    # research (2D) -----------------------------------------------------------
    p_res = sub.add_parser(
        "research", help="build/show research dossiers (phase 2D)"
    )
    res_sub = p_res.add_subparsers(dest="research_command", required=True)
    p_res_build = res_sub.add_parser("build", help="build a GitHub research dossier")
    p_res_build.add_argument("--db-path", dest="db_path", required=True)
    p_res_build.add_argument("--event-id", dest="event_id", required=True)
    p_res_build.add_argument("--timeout", type=int, default=10)
    p_res_build.add_argument(
        "--allow-network",
        dest="allow_network",
        action="store_true",
        help="required gate for README/Release fetches",
    )
    p_res_show = res_sub.add_parser("show", help="show a dossier with its facts")
    p_res_show.add_argument("--db-path", dest="db_path", required=True)
    p_res_show.add_argument("--event-id", dest="event_id", required=True)
    p_res_evid = res_sub.add_parser(
        "add-evidence",
        help="attach one official first-party page to the latest dossier",
    )
    p_res_evid.add_argument("--db-path", dest="db_path", required=True)
    p_res_evid.add_argument("--event-id", dest="event_id", required=True)
    p_res_evid.add_argument("--url", required=True)
    p_res_evid.add_argument("--kind", default="fact", help="fact | official_claim")
    p_res_evid.add_argument("--timeout", type=int, default=10)
    p_res_evid.add_argument(
        "--allow-network",
        dest="allow_network",
        action="store_true",
        help="required gate for the official page fetch",
    )
    p_res_note = res_sub.add_parser(
        "add-note",
        help="attach manually quoted evidence (Reddit/X/user reality) to the "
        "latest dossier - no fetch, text is stored verbatim",
    )
    p_res_note.add_argument("--db-path", dest="db_path", required=True)
    p_res_note.add_argument("--event-id", dest="event_id", required=True)
    p_res_note.add_argument("--text", required=True)
    p_res_note.add_argument("--url", dest="url")
    p_res_note.add_argument(
        "--kind", default="fact",
        help="fact | official_claim | contradiction | unknown",
    )

    # editorial (2E) ----------------------------------------------------------
    p_ed = sub.add_parser("editorial", help="editorial decisions (phase 2E)")
    ed_sub = p_ed.add_subparsers(dest="editorial_command", required=True)
    p_ed_decide = ed_sub.add_parser("decide", help="decide on the latest dossier")
    p_ed_decide.add_argument("--db-path", dest="db_path", required=True)
    p_ed_decide.add_argument("--event-id", dest="event_id", required=True)

    # content (2F) ------------------------------------------------------------
    p_ct = sub.add_parser("content", help="content brief output (phase 2F)")
    ct_sub = p_ct.add_subparsers(dest="content_command", required=True)
    p_ct_brief = ct_sub.add_parser("brief", help="write one content brief file")
    p_ct_brief.add_argument("--db-path", dest="db_path", required=True)
    p_ct_brief.add_argument("--event-id", dest="event_id", required=True)
    p_ct_brief.add_argument("--week-key", dest="week_key", required=True)
    p_ct_brief.add_argument("--output-root", dest="output_root", required=True)
    p_ct_brief.add_argument(
        "--allow-output-write",
        dest="allow_output_write",
        action="store_true",
        help="required gate to write brief files",
    )
    # content prompt-pack / platform-draft (DEC-021 publishing templates)
    p_ct_pack = ct_sub.add_parser(
        "prompt-pack", help="assemble the guardrail prompt pack (DEC-021)"
    )
    p_ct_plat = ct_sub.add_parser(
        "platform-draft", help="render a platform skeleton draft (DEC-021)"
    )
    for sub_parser in (p_ct_pack, p_ct_plat):
        sub_parser.add_argument("--db-path", dest="db_path", required=True)
        sub_parser.add_argument("--event-id", dest="event_id", required=True)
        sub_parser.add_argument("--week-key", dest="week_key", required=True)
        sub_parser.add_argument("--output-root", dest="output_root", required=True)
        sub_parser.add_argument(
            "--platform", dest="platform", default="xiaohongshu",
            help="platform template key (default: xiaohongshu)",
        )
        sub_parser.add_argument(
            "--allow-output-write",
            dest="allow_output_write",
            action="store_true",
            help="required gate to write draft files",
        )

    # weekly (Phase 3) --------------------------------------------------------
    p_wk = sub.add_parser("weekly", help="weekly end-to-end run (Phase 3)")
    wk_sub = p_wk.add_subparsers(dest="weekly_command", required=True)
    p_wk_run = wk_sub.add_parser("run", help="discover -> research -> editorial -> brief")
    p_wk_run.add_argument("--db-path", dest="db_path", required=True)
    p_wk_run.add_argument("--week-key", dest="week_key", required=True)
    p_wk_run.add_argument("--timeout", type=int, default=10)
    p_wk_run.add_argument("--research-limit", dest="research_limit", type=int, default=5)
    p_wk_run.add_argument(
        "--allow-network",
        dest="allow_network",
        action="store_true",
        help="required gate for discovery and research fetches",
    )
    p_wk_run.add_argument("--output-root", dest="output_root")
    p_wk_run.add_argument(
        "--allow-output-write",
        dest="allow_output_write",
        action="store_true",
        help="optional: also write content brief files",
    )

    # official (2D3-B) ---------------------------------------------------------
    p_off = sub.add_parser(
        "official", help="official announcement sensor (phase 2D3-B)"
    )
    off_sub = p_off.add_subparsers(dest="official_command", required=True)
    p_off_collect = off_sub.add_parser(
        "collect", help="collect first-party announcements from official feeds"
    )
    p_off_collect.add_argument("--db-path", dest="db_path", required=True)
    p_off_collect.add_argument("--timeout", type=int, default=10)
    p_off_collect.add_argument(
        "--allow-network",
        dest="allow_network",
        action="store_true",
        help="required gate for feed fetches",
    )
    p_off_list = off_sub.add_parser(
        "list", help="list official announcement candidates (safe summary)"
    )
    p_off_list.add_argument("--db-path", dest="db_path", required=True)
    p_off_list.add_argument("--status", dest="status")
    p_off_list.add_argument("--limit", type=int, default=30)

    # choice (DEC-017 creator-centric hub) ------------------------------------
    p_ch = sub.add_parser(
        "choice", help="creator-centric weekly choices (DEC-017)"
    )
    ch_sub = p_ch.add_subparsers(dest="choice_command", required=True)
    p_ch_pick = ch_sub.add_parser("pick", help="register this week's choice")
    p_ch_pick.add_argument("--db-path", dest="db_path", required=True)
    p_ch_pick.add_argument("--week-key", dest="week_key", required=True)
    p_ch_pick.add_argument("--subject", required=True)
    p_ch_pick.add_argument("--event-id", dest="event_id")
    p_ch_gaps = ch_sub.add_parser("gaps", help="show the evidence gap list")
    p_ch_gaps.add_argument("--db-path", dest="db_path", required=True)
    p_ch_gaps.add_argument("--week-key", dest="week_key", required=True)
    p_ch_gaps.add_argument("--subject", required=True)
    p_ch_fb = ch_sub.add_parser(
        "feedback", help="record the published outcome (adopted/parked/rejected)"
    )
    p_ch_fb.add_argument("--db-path", dest="db_path", required=True)
    p_ch_fb.add_argument("--week-key", dest="week_key", required=True)
    p_ch_fb.add_argument("--subject", required=True)
    p_ch_fb.add_argument("--decision", required=True)
    p_ch_fb.add_argument("--reason", required=True)
    p_ch_fb.add_argument("--audience", dest="audience")
    p_ch_fb.add_argument("--angle", dest="angle")
    p_ch_fb.add_argument("--usefulness", dest="usefulness", type=int)
    p_ch_fb.add_argument("--published-url", dest="published_url")
    p_ch_list = ch_sub.add_parser("list", help="list weekly choices")
    p_ch_list.add_argument("--db-path", dest="db_path", required=True)
    p_ch_list.add_argument("--week-key", dest="week_key")
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
    if not args.inbox_dir and not args.content_dir:
        out.write("need --inbox-dir and/or --content-dir\n")
        return EXIT_CONFIG_ERROR

    try:
        sqlite_storage.initialize_database(args.db_path)
    except sqlite_storage.StorageError:
        out.write("database error\n")
        return EXIT_DB_ERROR

    conn = sqlite_storage._open(args.db_path)
    try:
        conn.execute("BEGIN")
        result = sync_feedback(
            conn,
            Path(args.inbox_dir) if args.inbox_dir else None,
            Path(args.content_dir) if args.content_dir else None,
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


def cmd_research(args, out) -> int:
    from pathlib import Path

    from .pipeline.collect import CollectionPolicyError
    from .pipeline.research import ResearchError, build_github_dossier
    from .storage import sqlite as sqlite_storage
    from .storage.research_repositories import (
        EditorialDecisionRepository,
        ResearchDossierRepository,
        ResearchFactRepository,
    )

    def _hex64(value):
        return (
            isinstance(value, str)
            and len(value) == 64
            and all(ch in "0123456789abcdef" for ch in value)
        )

    if args.research_command == "add-note":
        from .domain.models import ResearchFact
        from .pipeline.research import ensure_dossier
        from .storage.research_repositories import ResearchFactRepository

        if not _hex64(args.event_id):
            out.write("config error: invalid event id\n")
            return EXIT_CONFIG_ERROR
        if args.kind not in ("fact", "official_claim", "contradiction", "unknown"):
            out.write("config error: invalid fact kind\n")
            return EXIT_CONFIG_ERROR
        try:
            sqlite_storage.initialize_database(args.db_path)
            conn = sqlite_storage._open(args.db_path)
            try:
                conn.execute("BEGIN")
                dossier = ensure_dossier(conn, args.event_id)
                fact = ResearchFactRepository(conn).insert_or_get(ResearchFact(
                    dossier_id=dossier.id,
                    kind=args.kind,
                    text=args.text,
                    source_kind="manual",
                    source_url=args.url,
                ))
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
        out.write("fact_id: %s\n" % fact.id)
        out.write("kind: %s\n" % fact.kind)
        return EXIT_OK

    if args.research_command == "add-evidence":
        from .pipeline.research import attach_official_evidence, ensure_dossier
        from .sources.official_http import OfficialHttpError

        if not _hex64(args.event_id):
            out.write("config error: invalid event id\n")
            return EXIT_CONFIG_ERROR
        if isinstance(args.timeout, bool) or not isinstance(args.timeout, int) or not 1 <= args.timeout <= 30:
            out.write("invalid timeout\n")
            return EXIT_CONFIG_ERROR
        try:
            sqlite_storage.initialize_database(args.db_path)
            conn = sqlite_storage._open(args.db_path)
            try:
                conn.execute("BEGIN")
                dossier = ensure_dossier(conn, args.event_id)
                fact = attach_official_evidence(
                    conn,
                    dossier.id,
                    args.url,
                    allow_network=bool(args.allow_network),
                    timeout_seconds=args.timeout,
                    kind=args.kind,
                )
                conn.execute("COMMIT")
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                raise
            finally:
                conn.close()
        except CollectionPolicyError as exc:
            out.write("policy error: %s\n" % exc)
            return EXIT_SECURITY
        except OfficialHttpError as exc:
            out.write("evidence error: %s\n" % exc.code)
            return EXIT_CAPABILITY
        except ResearchError as exc:
            out.write("config error: %s\n" % exc)
            return EXIT_CONFIG_ERROR
        except ValueError as exc:
            out.write("config error: %s\n" % exc)
            return EXIT_CONFIG_ERROR
        except sqlite_storage.StorageError:
            out.write("database error\n")
            return EXIT_DB_ERROR
        out.write("fact_id: %s\n" % fact.id)
        out.write("kind: %s\n" % fact.kind)
        out.write("chars: %d\n" % len(fact.text))
        return EXIT_OK

    if args.research_command == "show":
        if not _hex64(args.event_id):
            out.write("config error: invalid event id\n")
            return EXIT_CONFIG_ERROR
        if not Path(args.db_path).exists():
            out.write("database does not exist\n")
            return EXIT_DB_ERROR
        try:
            with sqlite_storage.connect(args.db_path) as conn:
                dossiers = ResearchDossierRepository(conn).list_for_event(args.event_id)
        except sqlite_storage.StorageError:
            out.write("database error\n")
            return EXIT_DB_ERROR
        if not dossiers:
            out.write("no dossier for this event\n")
            return EXIT_OK
        dossier = dossiers[0]
        out.write("dossier_id: %s\n" % dossier.id)
        out.write("status: %s\n" % dossier.status)
        out.write("summary: %s\n" % dossier.summary_judgment)
        out.write("needs_testing: %s\n" % ("yes" if dossier.needs_testing else "no"))
        try:
            with sqlite_storage.connect(args.db_path) as conn:
                facts = ResearchFactRepository(conn).list_for_dossier(dossier.id)
                decisions = EditorialDecisionRepository(conn).list_for_dossier(dossier.id)
        except sqlite_storage.StorageError:
            out.write("database error\n")
            return EXIT_DB_ERROR
        out.write("facts: %d\n" % len(facts))
        for fact in facts:
            source = fact.source_url or ""
            out.write("  [%s] %s %s\n" % (fact.kind, fact.text[:120], source[:80]))
        if decisions:
            latest = decisions[0]
            out.write("editorial: %s (%s)\n" % (
                latest.decision, ",".join(latest.reason_codes)))
        return EXIT_OK

    # build
    if not _hex64(args.event_id):
        out.write("config error: invalid event id\n")
        return EXIT_CONFIG_ERROR
    if isinstance(args.timeout, bool) or not isinstance(args.timeout, int) or not 1 <= args.timeout <= 30:
        out.write("invalid timeout\n")
        return EXIT_CONFIG_ERROR
    try:
        sqlite_storage.initialize_database(args.db_path)
        conn = sqlite_storage._open(args.db_path)
        try:
            conn.execute("BEGIN")
            result = build_github_dossier(
                conn,
                args.event_id,
                allow_network=bool(args.allow_network),
                timeout_seconds=args.timeout,
            )
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()
    except CollectionPolicyError as exc:
        out.write("policy error: %s\n" % exc)
        return EXIT_SECURITY
    except ResearchError as exc:
        out.write("config error: %s\n" % exc)
        return EXIT_CONFIG_ERROR
    except sqlite_storage.StorageError:
        out.write("database error\n")
        return EXIT_DB_ERROR
    out.write("dossier_id: %s\n" % result.dossier.id)
    out.write("status: %s\n" % result.status)
    out.write("facts: %d\n" % len(result.facts))
    out.write("fetch_failures: %d\n" % result.fetch_failures)
    out.write("needs_testing: %s\n" % ("yes" if result.dossier.needs_testing else "no"))
    return EXIT_OK


def cmd_official(args, out) -> int:
    from pathlib import Path

    from .pipeline.collect import CollectionPolicyError
    from .pipeline.official_discovery import collect_official_announcements
    from .storage import sqlite as sqlite_storage
    from .storage.official_repositories import (
        OfficialAnnouncementCandidateRepository,
    )

    if args.official_command == "list":
        if not Path(args.db_path).exists():
            out.write("database does not exist\n")
            return EXIT_DB_ERROR
        try:
            sqlite_storage.initialize_database(args.db_path)
            with sqlite_storage.connect(args.db_path) as conn:
                rows = OfficialAnnouncementCandidateRepository(conn).list(
                    args.status, args.limit
                )
        except ValueError as exc:
            out.write("config error: %s\n" % exc)
            return EXIT_CONFIG_ERROR
        except sqlite_storage.StorageError:
            out.write("database error\n")
            return EXIT_DB_ERROR
        for row in rows:
            out.write(
                "[%s] %s | %s | %s\n"
                % (row.source_name, row.published_at[:10],
                   row.title[:80], row.url[:70])
            )
        return EXIT_OK

    # collect
    if isinstance(args.timeout, bool) or not isinstance(args.timeout, int) or not 1 <= args.timeout <= 30:
        out.write("invalid timeout\n")
        return EXIT_CONFIG_ERROR
    try:
        sqlite_storage.initialize_database(args.db_path)
        conn = sqlite_storage._open(args.db_path)
        try:
            conn.execute("BEGIN")
            result = collect_official_announcements(
                conn,
                allow_network=bool(args.allow_network),
                timeout_seconds=args.timeout,
            )
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()
    except CollectionPolicyError as exc:
        out.write("policy error: %s\n" % exc)
        return EXIT_SECURITY
    except sqlite_storage.StorageError:
        out.write("database error\n")
        return EXIT_DB_ERROR
    out.write("sources_attempted: %d\n" % result.sources_attempted)
    out.write("sources_failed: %d\n" % result.sources_failed)
    out.write("created: %d\n" % result.created)
    out.write("existing: %d\n" % result.existing)
    out.write("skipped_unsafe: %d\n" % result.skipped_unsafe)
    if result.sources_failed == result.sources_attempted:
        return EXIT_CAPABILITY
    return EXIT_OK


def cmd_editorial(args, out) -> int:
    from pathlib import Path

    from .pipeline.editorial import EditorialError, decide_editorial
    from .storage import sqlite as sqlite_storage
    from .storage.research_repositories import ResearchDossierRepository

    if not isinstance(args.event_id, str) or len(args.event_id) != 64:
        out.write("config error: invalid event id\n")
        return EXIT_CONFIG_ERROR
    if not Path(args.db_path).exists():
        out.write("database does not exist\n")
        return EXIT_DB_ERROR
    try:
        sqlite_storage.initialize_database(args.db_path)
        conn = sqlite_storage._open(args.db_path)
        try:
            dossiers = ResearchDossierRepository(conn).list_for_event(args.event_id)
            if not dossiers:
                out.write("no dossier for this event\n")
                return EXIT_OK
            conn.execute("BEGIN")
            decision = decide_editorial(conn, dossiers[0].id)
            conn.execute("COMMIT")
        finally:
            conn.close()
    except EditorialError as exc:
        out.write("config error: %s\n" % exc)
        return EXIT_CONFIG_ERROR
    except sqlite_storage.StorageError:
        out.write("database error\n")
        return EXIT_DB_ERROR
    out.write("dossier_id: %s\n" % decision.dossier_id)
    out.write("decision: %s\n" % decision.decision)
    out.write("reason_codes: %s\n" % ",".join(decision.reason_codes))
    return EXIT_OK


def cmd_content(args, out) -> int:
    from pathlib import Path

    from .outputs.content_brief import ContentBriefError, publish_content_brief
    from .outputs.platform_drafts import (
        DraftPublishError,
        publish_platform_skeleton,
        publish_prompt_pack,
    )
    from .outputs.platform_templates import PlatformTemplateError
    from .storage import sqlite as sqlite_storage
    from .storage.event_candidate_repositories import EventCandidateRepository
    from .storage.research_repositories import (
        EditorialDecisionRepository,
        ResearchDossierRepository,
        ResearchFactRepository,
    )

    if not _WEEK_KEY_RE.match(args.week_key):
        out.write("invalid week key: expected YYYY-Www\n")
        return EXIT_CONFIG_ERROR
    if not isinstance(args.event_id, str) or len(args.event_id) != 64:
        out.write("config error: invalid event id\n")
        return EXIT_CONFIG_ERROR
    if not args.allow_output_write:
        out.write("output write not allowed: --allow-output-write is required\n")
        return EXIT_SECURITY
    if not Path(args.db_path).exists():
        out.write("database does not exist\n")
        return EXIT_DB_ERROR
    try:
        with sqlite_storage.connect(args.db_path) as conn:
            event = EventCandidateRepository(conn).get(args.event_id)
            dossiers = ResearchDossierRepository(conn).list_for_event(args.event_id)
            if event is None or not dossiers:
                out.write("no dossier for this event\n")
                return EXIT_CONFIG_ERROR
            dossier = dossiers[0]
            facts = ResearchFactRepository(conn).list_for_dossier(dossier.id)
            decisions = EditorialDecisionRepository(conn).list_for_dossier(dossier.id)
            if not decisions:
                out.write("no editorial decision; run `editorial decide` first\n")
                return EXIT_CONFIG_ERROR
            decision = decisions[0]
    except sqlite_storage.StorageError:
        out.write("database error\n")
        return EXIT_DB_ERROR
    try:
        if args.content_command == "prompt-pack":
            path = publish_prompt_pack(
                Path(args.output_root), args.week_key, event, dossier,
                facts, decision, args.platform,
            )
        elif args.content_command == "platform-draft":
            path = publish_platform_skeleton(
                Path(args.output_root), args.week_key, event, dossier,
                facts, decision, args.platform,
            )
        else:
            path = publish_content_brief(
                Path(args.output_root), args.week_key, event, dossier, facts, decision
            )
    except (ContentBriefError, DraftPublishError):
        out.write("output error\n")
        return EXIT_CAPABILITY
    except PlatformTemplateError as exc:
        out.write("config error: %s\n" % exc)
        return EXIT_CONFIG_ERROR
    out.write("written: %s\n" % path.name)
    return EXIT_OK


def cmd_weekly(args, out) -> int:
    from .pipeline.collect import CollectionPolicyError
    from .pipeline.weekly import WeeklyError, run_weekly
    from .storage import sqlite as sqlite_storage

    if not _WEEK_KEY_RE.match(args.week_key):
        out.write("invalid week key: expected YYYY-Www\n")
        return EXIT_CONFIG_ERROR
    if isinstance(args.timeout, bool) or not isinstance(args.timeout, int) or not 1 <= args.timeout <= 30:
        out.write("invalid timeout\n")
        return EXIT_CONFIG_ERROR
    try:
        report = run_weekly(
            args.db_path,
            args.week_key,
            allow_network=bool(args.allow_network),
            output_root=args.output_root,
            allow_output_write=bool(args.allow_output_write),
            research_limit=args.research_limit,
            timeout_seconds=args.timeout,
        )
    except CollectionPolicyError as exc:
        out.write("policy error: %s\n" % exc)
        return EXIT_SECURITY
    except WeeklyError as exc:
        out.write("config error: %s\n" % exc)
        return EXIT_CONFIG_ERROR
    except sqlite_storage.StorageError:
        out.write("database error\n")
        return EXIT_DB_ERROR
    out.write("discovery_policies: %d\n" % report.discovery_policies)
    out.write("discovery_success: %d\n" % report.discovery_success)
    out.write("discovery_degraded: %d\n" % report.discovery_degraded)
    out.write("official_created: %d\n" % report.official_created)
    out.write("official_sources_failed: %d\n" % report.official_sources_failed)
    out.write("promoted: %d\n" % report.promoted)
    out.write("already_promoted: %d\n" % report.already_promoted)
    out.write("research_attempted: %d\n" % report.research_attempted)
    out.write("dossiers_built: %d\n" % report.dossiers_built)
    out.write("research_failures: %d\n" % report.research_failures)
    out.write("decisions_ready: %d\n" % report.decisions_ready)
    out.write("decisions_needs_testing: %d\n" % report.decisions_needs_testing)
    out.write("decisions_watch: %d\n" % report.decisions_watch)
    out.write("briefs_written: %d\n" % report.briefs_written)
    if report.discovery_degraded or report.research_failures:
        return EXIT_CAPABILITY
    return EXIT_OK


def cmd_choice(args, out) -> int:
    from pathlib import Path

    from .domain.models import FEEDBACK_DECISIONS
    from .pipeline.creator import (
        CreatorChoiceRepository,
        assess_choice_gaps,
        record_choice,
        record_choice_feedback,
    )
    from .storage import sqlite as sqlite_storage

    if args.week_key is not None and not _WEEK_KEY_RE.match(args.week_key):
        out.write("invalid week key: expected YYYY-Www\n")
        return EXIT_CONFIG_ERROR
    if not Path(args.db_path).exists():
        out.write("database does not exist\n")
        return EXIT_DB_ERROR
    try:
        sqlite_storage.initialize_database(args.db_path)
        conn = sqlite_storage._open(args.db_path)
        try:
            repo = CreatorChoiceRepository(conn)

            if args.choice_command == "pick":
                conn.execute("BEGIN")
                choice = record_choice(
                    conn, args.week_key, args.subject, args.event_id
                )
                conn.execute("COMMIT")
                out.write("choice_id: %s\n" % choice.id)
                out.write("subject: %s\n" % choice.subject)
                out.write("status: %s\n" % choice.status)
                out.write("next: 运行 `choice gaps` 看证据缺口清单\n")
                return EXIT_OK

            if args.choice_command == "list":
                rows = repo.list(args.week_key)
                for row in rows:
                    out.write("[%s] %s | %s\n" % (row.week_key, row.status, row.subject))
                return EXIT_OK

            choices = [
                c for c in repo.list(args.week_key) if c.subject == args.subject
            ]

            if not choices:
                out.write("no choice for this week/subject; run `choice pick` first\n")
                return EXIT_CONFIG_ERROR
            choice = choices[0]

            if args.choice_command == "gaps":
                report = assess_choice_gaps(conn, choice)
                if not report.linked_event:
                    out.write("未关联事件：请先 pick 时给 --event-id，或直接按主题研究\n")
                elif not report.has_dossier:
                    out.write("已关联事件但无档案：先 `research build --event-id %s`\n"
                              % choice.event_candidate_id)
                out.write("证据缺口清单：\n")
                for gap, label, how in report.gaps:
                    out.write("  - [%s] %s —— %s\n" % (gap, label, how))
                return EXIT_OK

            if args.choice_command == "feedback":
                if args.decision not in FEEDBACK_DECISIONS:
                    out.write("config error: decision 必须是 adopted/parked/rejected\n")
                    return EXIT_CONFIG_ERROR
                if (
                    args.usefulness is not None
                    and (isinstance(args.usefulness, bool)
                         or not isinstance(args.usefulness, int)
                         or not 1 <= args.usefulness <= 5)
                ):
                    out.write("config error: usefulness 必须是 1-5\n")
                    return EXIT_CONFIG_ERROR
                conn.execute("BEGIN")
                feedback = record_choice_feedback(
                    conn,
                    choice.id,
                    args.decision,
                    args.reason,
                    audience=args.audience,
                    angle=args.angle,
                    usefulness=args.usefulness,
                    published_url=args.published_url,
                )
                new_status = "published" if args.decision == "adopted" else "parked"
                repo.update_status(choice.id, new_status)
                conn.execute("COMMIT")
                out.write("feedback_id: %s\n" % feedback.id)
                out.write("choice_status: %s\n" % new_status)
                return EXIT_OK
        finally:
            conn.close()
    except ValueError as exc:
        out.write("config error: %s\n" % exc)
        return EXIT_CONFIG_ERROR
    except sqlite_storage.StorageError:
        out.write("database error\n")
        return EXIT_DB_ERROR
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
    if args.command == "research":
        return cmd_research(args, stream)
    if args.command == "official":
        return cmd_official(args, stream)
    if args.command == "choice":
        return cmd_choice(args, stream)
    if args.command == "editorial":
        return cmd_editorial(args, stream)
    if args.command == "content":
        return cmd_content(args, stream)
    if args.command == "weekly":
        return cmd_weekly(args, stream)

    parser.print_help(stream)
    return EXIT_OK
