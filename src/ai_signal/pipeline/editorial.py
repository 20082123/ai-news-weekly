"""Phase 2E editorial decision gate (deterministic, editorial-v1, no LLM).

:func:`decide_editorial` maps one dossier revision to exactly one decision:

    ready_to_write  first-party change evidence present (release/official
                    fact with a substantive body) and no experience claim
                    detected -> release facts can be reported responsibly;
    needs_testing   first-party evidence present BUT the release/README text
                    carries experience-claim markers ("faster", "更稳", ...)
                    -> the core proposition is an experience claim and must
                    be personally tested before writing it;
    watch           no first-party change evidence (no release/official fact)
                    or evidence too thin;
    reject          NOT produced by editorial-v1 (rejection belongs to
                    earlier qualification and human feedback).

The decision input hash covers the dossier id, its fact identities and the
needs_testing flag: any evidence change yields a new auditable revision.
"""

from __future__ import annotations

from typing import Callable, Optional

from ..domain.models import (
    EDITORIAL_POLICY_VERSION,
    EditorialDecision,
    now_utc,
    sha256_hex,
)
from ..storage.research_repositories import (
    EditorialDecisionRepository,
    ResearchDossierRepository,
    ResearchFactRepository,
)
from ..storage.sqlite import StorageError

import json

# Stable reason codes (payload-free).
R_FIRST_PARTY_EVIDENCE = "first_party_change_evidence"
R_EXPERIENCE_CLAIM = "experience_claim_detected"
R_THIN_EVIDENCE = "thin_evidence"
R_NO_CHANGE_EVIDENCE = "no_first_party_change_evidence"

_SUBSTANTIVE_BODY_MIN = 40  # characters of quoted release/official text


class EditorialError(ValueError):
    """Raised when an editorial decision cannot be computed."""


def _decision_input_hash(dossier_id: str, fact_ids, needs_testing: bool) -> str:
    payload = {
        "dossier_id": dossier_id,
        "fact_ids": sorted(fact_ids),
        "needs_testing": needs_testing,
    }
    return sha256_hex(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    )


def decide_editorial(
    conn,
    dossier_id: str,
    *,
    clock: Optional[Callable[[], "object"]] = None,
) -> EditorialDecision:
    """Compute and persist the editorial decision for a dossier (caller owns txn)."""
    ts = clock if clock is not None else now_utc
    dossier = ResearchDossierRepository(conn).get(dossier_id)
    if dossier is None:
        raise EditorialError("unknown dossier")
    facts = ResearchFactRepository(conn).list_for_dossier(dossier_id)

    change_facts = [
        f
        for f in facts
        if f.source_kind in ("github_release", "official_page") and f.kind in ("fact", "official_claim")
    ]
    substantive = any(
        len(f.text) >= _SUBSTANTIVE_BODY_MIN for f in change_facts
    )

    if not change_facts:
        decision = "watch"
        codes = (R_NO_CHANGE_EVIDENCE,)
    elif not substantive:
        decision = "watch"
        codes = (R_THIN_EVIDENCE,)
    elif dossier.needs_testing:
        decision = "needs_testing"
        codes = (R_FIRST_PARTY_EVIDENCE, R_EXPERIENCE_CLAIM)
    else:
        decision = "ready_to_write"
        codes = (R_FIRST_PARTY_EVIDENCE,)

    input_hash = _decision_input_hash(
        dossier_id,
        [f.id for f in facts],
        dossier.needs_testing,
    )
    record = EditorialDecision(
        dossier_id=dossier_id,
        policy_version=EDITORIAL_POLICY_VERSION,
        input_hash=input_hash,
        decision=decision,
        reason_codes=codes,
        decided_at=ts(),
    )
    stored = EditorialDecisionRepository(conn).insert_or_get(record)
    if stored is None:  # pragma: no cover - defensive
        raise StorageError("editorial decision upsert failed")
    return stored
