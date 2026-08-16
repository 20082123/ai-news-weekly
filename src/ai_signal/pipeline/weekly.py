"""Phase 3 weekly orchestration: discovery -> research -> editorial -> brief.

:func:`run_weekly` runs the whole weekly flow end to end in one command:

    discover (4 GitHub policies, one page per probe)
      -> promote queued candidates into event-candidate drafts
      -> research the top-N drafts that have GitHub references (README +
         releases, read-only)
      -> editorial decision per new dossier
      -> content brief files (optional, requires --allow-output-write)

Every step degrades visibly: per-policy discovery failures and per-event
research failures are recorded in the report, never silently swallowed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..discovery import official_catalog as catalog_module
from ..discovery.policy import list_policies
from ..discovery.run import run_github_discovery
from ..pipeline.editorial import decide_editorial
from ..pipeline.event_candidate import promote_github_queue
from ..pipeline.official_discovery import collect_official_announcements
from ..pipeline.research import build_github_dossier
from ..storage import sqlite as sqlite_storage
from ..storage.event_candidate_repositories import EventCandidateRepository
from ..storage.research_repositories import ResearchDossierRepository


class WeeklyError(ValueError):
    """Raised when the weekly run cannot proceed (configuration)."""


@dataclass(frozen=True)
class WeeklyRunReport:
    """Safe, payload-free summary of one weekly run."""

    week_key: str
    discovery_policies: int
    discovery_success: int
    discovery_degraded: int
    official_created: int
    official_sources_failed: int
    promoted: int
    already_promoted: int
    research_attempted: int
    dossiers_built: int
    research_failures: int
    decisions_ready: int
    decisions_needs_testing: int
    decisions_watch: int
    briefs_written: int


def run_weekly(
    db_path: Any,
    week_key: str,
    *,
    allow_network: bool = False,
    output_root=None,
    allow_output_write: bool = False,
    research_limit: int = 5,
    timeout_seconds: int = 10,
    transport_factory: Optional[Callable[[], Any]] = None,
    clock: Optional[Callable[[], Any]] = None,
) -> WeeklyRunReport:
    """Run the full weekly pipeline (phase 3).

    ``transport_factory`` injects the HTTP transport for tests. All network
    use is read-only and bounded; without ``allow_network`` the run refuses
    before any database work when discovery/research would need it.
    """
    from ..pipeline.collect import CollectionPolicyError

    if allow_network is False:
        raise CollectionPolicyError("network not allowed: --allow-network is required")
    if isinstance(research_limit, bool) or not isinstance(research_limit, int) or not 1 <= research_limit <= 20:
        raise WeeklyError("research_limit must be between 1 and 20")
    if allow_output_write and output_root is None:
        raise WeeklyError("--output-root is required with --allow-output-write")

    sqlite_storage.initialize_database(db_path)

    # 1. Discovery: every catalog policy, one page per probe.
    discovery_success = 0
    discovery_degraded = 0
    for entry in list_policies():
        policy_id = entry["id"]
        if entry["probe_count"] == 0:
            # Catalog placeholders without confirmed targets are skipped.
            continue
        try:
            result = run_github_discovery(
                db_path,
                week_key,
                policy_id,
                allow_network=True,
                timeout_seconds=timeout_seconds,
                transport_factory=transport_factory,
                clock=clock,
            )
        except Exception:  # noqa: BLE001 - one policy must never abort the week
            discovery_degraded += 1
            continue
        if result.status == "success":
            discovery_success += 1
        else:
            discovery_degraded += 1

    # 1.5 Official announcements (consumer-level changes surface here).
    official_created = 0
    official_sources_failed = 0
    try:
        conn = sqlite_storage._open(db_path)
        try:
            conn.execute("BEGIN")
            off_result = collect_official_announcements(
                conn,
                allow_network=True,
                transport_factory=transport_factory,
                clock=clock,
                timeout_seconds=timeout_seconds,
            )
            conn.execute("COMMIT")
        finally:
            conn.close()
        official_created = off_result.created
        official_sources_failed = off_result.sources_failed
    except Exception:  # noqa: BLE001 - official sensor failure never blocks the week
        official_created = 0
        official_sources_failed = len(tuple(catalog_module.OFFICIAL_SOURCES))

    # 2. Promote queued candidates into event-candidate drafts.
    conn = sqlite_storage._open(db_path)
    try:
        conn.execute("BEGIN")
        promo = promote_github_queue(conn, week_key, clock=clock)
        conn.execute("COMMIT")
    finally:
        conn.close()

    # 3. Research the top-N drafts that have GitHub references.
    conn = sqlite_storage._open(db_path)
    research_attempted = 0
    dossiers_built = 0
    research_failures = 0
    researched_ids: List[str] = []
    try:
        event_repo = EventCandidateRepository(conn)
        dossier_repo = ResearchDossierRepository(conn)
        events = event_repo.list()  # priority desc
        for event in events:
            if research_attempted >= research_limit:
                break
            if dossier_repo.list_for_event(event.id):
                continue  # already researched this week's content
            has_github_ref = conn.execute(
                "SELECT 1 FROM event_candidate_source_ref "
                "WHERE event_candidate_id = ? "
                "AND source_kind = 'github_repository_candidate' LIMIT 1",
                (event.id,),
            ).fetchone() is not None
            if not has_github_ref:
                continue
            research_attempted += 1
            conn.execute("BEGIN")
            try:
                result = build_github_dossier(
                    conn,
                    event.id,
                    allow_network=True,
                    transport_factory=transport_factory,
                    clock=clock,
                    timeout_seconds=timeout_seconds,
                )
                conn.execute("COMMIT")
            except Exception:  # noqa: BLE001 - per-event failure is recorded
                try:
                    conn.execute("ROLLBACK")
                except Exception:  # pragma: no cover
                    pass
                research_failures += 1
                continue
            dossiers_built += 1
            researched_ids.append(result.dossier.id)
    finally:
        conn.close()

    # 4. Editorial decisions for the new dossiers.
    decisions = {"ready_to_write": 0, "needs_testing": 0, "watch": 0}
    conn = sqlite_storage._open(db_path)
    try:
        conn.execute("BEGIN")
        for dossier_id in researched_ids:
            decision = decide_editorial(conn, dossier_id, clock=clock)
            decisions[decision.decision] = decisions.get(decision.decision, 0) + 1
        conn.execute("COMMIT")
    finally:
        conn.close()

    # 5. Content briefs (optional, after all DB commits).
    briefs_written = 0
    if allow_output_write and output_root is not None:
        from pathlib import Path

        from ..outputs.content_brief import (
            ContentBriefError,
            publish_content_brief,
        )
        from ..storage.research_repositories import (
            EditorialDecisionRepository,
            ResearchFactRepository,
        )

        conn = sqlite_storage._open(db_path)
        try:
            event_repo = EventCandidateRepository(conn)
            fact_repo = ResearchFactRepository(conn)
            decision_repo = EditorialDecisionRepository(conn)
            # Fresh repository on THIS connection (the step-3 connection is
            # already closed and must never be reused).
            dossier_repo = ResearchDossierRepository(conn)
            for dossier_id in researched_ids:
                dossier = dossier_repo.get(dossier_id)
                event = event_repo.get(dossier.event_candidate_id)
                facts = fact_repo.list_for_dossier(dossier_id)
                decisions_rows = decision_repo.list_for_dossier(dossier_id)
                if not decisions_rows or decisions_rows[0].decision == "watch":
                    continue  # watch dossiers get no brief
                try:
                    publish_content_brief(
                        Path(output_root),
                        week_key,
                        event,
                        dossier,
                        facts,
                        decisions_rows[0],
                    )
                    briefs_written += 1
                except ContentBriefError:
                    continue  # one file failure never blocks the week
        finally:
            conn.close()

    return WeeklyRunReport(
        week_key=week_key,
        discovery_policies=len(list_policies()),
        discovery_success=discovery_success,
        discovery_degraded=discovery_degraded,
        official_created=official_created,
        official_sources_failed=official_sources_failed,
        promoted=promo.promoted,
        already_promoted=promo.already_promoted,
        research_attempted=research_attempted,
        dossiers_built=dossiers_built,
        research_failures=research_failures,
        decisions_ready=decisions.get("ready_to_write", 0),
        decisions_needs_testing=decisions.get("needs_testing", 0),
        decisions_watch=decisions.get("watch", 0),
        briefs_written=briefs_written,
    )
