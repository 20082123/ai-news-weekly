"""Phase 2C3 source-independent event-candidate recording.

:func:`record_event_candidate` persists one :class:`EventCandidate` together
with its source references inside the caller's transaction. The identity is
source-independent: the same ``(signal_type, subject, change_summary)`` from
different sources lands on one row, and every source-specific candidate
reference is stored as ``(source_kind, ref_id, ref_label)`` pointers.

This layer only RECORDS candidates - it does not confirm events, does not
read source payloads, and never reuses the legacy 2B ``event`` table.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Tuple

from ..domain.models import (
    EventCandidate,
    EventCandidateSourceRef,
    now_utc,
)
from ..storage.event_candidate_repositories import (
    EventCandidateRepository,
    EventCandidateSourceRefRepository,
)


class EventCandidateError(ValueError):
    """Raised when an event candidate record is invalid."""


@dataclass(frozen=True)
class EventCandidateRecord:
    """The canonical stored rows produced by one recording."""

    candidate: EventCandidate
    refs: Tuple[EventCandidateSourceRef, ...]


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
