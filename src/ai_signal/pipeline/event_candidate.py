"""Phase 2C3 source-independent event-candidate recording.

:func:`record_event_candidate` persists one :class:`EventCandidate` together
with its source references inside the caller's transaction. The identity is
source-independent: the same ``(signal_type, subject, change_summary)`` from
different sources lands on one row, and every source-specific candidate
reference is stored as ``(source_kind, ref_id, ref_label)`` pointers.

:func:`promote_github_queue` (phase 2C3-B) bridges the GitHub research queue
into this layer: every ``queued`` selection of a week becomes a MACHINE-DRAFT
:class:`EventCandidate` (default signal type, placeholder audience/impact,
full missing-evidence list) with a ``github_repository_candidate`` reference.
Candidates already referenced by any event candidate are skipped, so manual
seeds (e.g. the Golden Set) and earlier promotions never duplicate. Drafts
are deliberately rough - the human refines them during research; a refined
row has a new identity and the draft stays as audit history.

This layer only RECORDS candidates - it does not confirm events, does not
read source payloads, and never reuses the legacy 2B ``event`` table.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Tuple

from ..domain.models import (
    EventCandidate,
    EventCandidateSourceRef,
    now_utc,
)
from ..storage.event_candidate_repositories import (
    EventCandidateRepository,
    EventCandidateSourceRefRepository,
)

_WEEK_KEY_RE = re.compile(r"^\d{4}-W\d{2}$")

# Machine-draft defaults for promoted GitHub queue candidates. Everything
# here is explicitly a DRAFT: the human is expected to refine signal_type,
# change_summary, audience, impact and priority during research.
DRAFT_SIGNAL_TYPE = "tool_workflow_change"
DRAFT_CHANGE_SUMMARY = "具体变化待研究（机器草案）"
DRAFT_AUDIENCE = "待人工确认"
DRAFT_IMPACT = "待人工确认"
DRAFT_PRIORITY = 50
DRAFT_MISSING_EVIDENCE = (
    "specific_event",
    "readme",
    "latest_release",
    "previous_release_or_changelog",
    "working_artifact_or_demo",
    "testability",
)


class EventCandidateError(ValueError):
    """Raised when an event candidate record is invalid."""


@dataclass(frozen=True)
class EventCandidateRecord:
    """The canonical stored rows produced by one recording."""

    candidate: EventCandidate
    refs: Tuple[EventCandidateSourceRef, ...]


@dataclass(frozen=True)
class PromoteResult:
    """Safe, payload-free summary of one queue promotion."""

    processed: int
    promoted: int
    already_promoted: int
    refs_added: int


def record_event_candidate(
    conn,
    candidate: EventCandidate,
    refs: Iterable[EventCandidateSourceRef] = (),
    *,
    clock=None,
) -> EventCandidateRecord:
    """Upsert an event candidate and its source references (caller owns txn).

    All rows share the caller's transaction: any failure rolls everything
    back. Re-recording the same candidate is idempotent and returns the
    canonical stored rows.
    """
    ts = clock() if clock is not None else now_utc()
    candidate_repo = EventCandidateRepository(conn)
    ref_repo = EventCandidateSourceRefRepository(conn)

    stored_candidate = candidate_repo.insert_or_get(candidate)
    stored_refs = []
    for ref in refs:
        if ref.event_candidate_id != stored_candidate.id:
            # Normalize: refs recorded with the caller's identity guess are
            # re-pointed at the canonical stored row (identity is derived).
            ref = EventCandidateSourceRef(
                event_candidate_id=stored_candidate.id,
                source_kind=ref.source_kind,
                ref_id=ref.ref_id,
                ref_label=ref.ref_label,
                created_at=ts,
            )
        stored_refs.append(ref_repo.insert_or_get(ref))
    return EventCandidateRecord(
        candidate=stored_candidate,
        refs=tuple(stored_refs),
    )


def promote_github_queue(
    conn,
    week_key: str,
    *,
    clock=None,
) -> PromoteResult:
    """Promote a week's queued GitHub candidates into event-candidate drafts.

    ``conn`` is an open connection; the caller owns the transaction (a
    failure rolls the whole promotion back). Re-running is idempotent: every
    candidate referenced before is skipped, so no duplicate drafts appear.
    """
    if not isinstance(week_key, str) or _WEEK_KEY_RE.fullmatch(week_key) is None:
        raise EventCandidateError("invalid week key")
    ts = clock() if clock is not None else now_utc()
    ref_repo = EventCandidateSourceRefRepository(conn)

    rows = conn.execute(
        "SELECT s.selection_rank, c.id AS candidate_id, c.title "
        "FROM github_candidate_selection s "
        "JOIN github_discovery_run r ON r.id = s.discovery_run_id "
        "JOIN candidate c ON c.id = s.candidate_id "
        "WHERE r.week_key = ? AND s.queue_state = 'queued' "
        "ORDER BY r.started_at, s.selection_rank",
        (week_key,),
    ).fetchall()

    processed = 0
    promoted = 0
    already_promoted = 0
    refs_added = 0
    for row in rows:
        processed += 1
        candidate_id = row["candidate_id"]
        if ref_repo.exists_by_ref("github_repository_candidate", candidate_id):
            already_promoted += 1
            continue
        draft = EventCandidate(
            signal_type=DRAFT_SIGNAL_TYPE,
            subject=row["title"],
            change_summary=DRAFT_CHANGE_SUMMARY,
            affected_audience=DRAFT_AUDIENCE,
            work_impact_hypothesis=DRAFT_IMPACT,
            research_priority=DRAFT_PRIORITY,
            missing_evidence=DRAFT_MISSING_EVIDENCE,
            created_at=ts,
            updated_at=ts,
        )
        ref = EventCandidateSourceRef(
            event_candidate_id=draft.id,
            source_kind="github_repository_candidate",
            ref_id=candidate_id,
            ref_label=row["title"],
            created_at=ts,
        )
        record_event_candidate(conn, draft, [ref], clock=lambda: ts)
        promoted += 1
        refs_added += 1
    return PromoteResult(
        processed=processed,
        promoted=promoted,
        already_promoted=already_promoted,
        refs_added=refs_added,
    )
