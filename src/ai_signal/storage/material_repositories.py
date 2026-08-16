"""Idempotent repositories for the phase 2B2 materialization stage.

Every repository here is *scope-aware and deterministic*: the same inputs
produce the same ids and re-running the pipeline never creates duplicate
Event / EventMember / Claim / Evidence / ClaimEvidence / MaterialPack /
Feedback rows. Repositories never call ``COMMIT`` - the caller owns the
transaction so a failure rolls back every write atomically.

Rules (mirroring :mod:`ai_signal.storage.repositories`):

* all SQL is parameterized;
* JSON is serialized with ``sort_keys=True, ensure_ascii=False, separators``
  so payloads and content are byte-stable across runs;
* every database error is surfaced as :class:`StorageError`;
* upserts use ``ON CONFLICT(...) DO NOTHING`` then re-read by the natural key.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, List, Mapping, Optional, Tuple

from ..domain.models import (
    Claim,
    ClaimEvidence,
    Event,
    EventMember,
    Evidence,
    Feedback,
    MaterialPack,
    ensure_aware_utc,
)
from .sqlite import StorageError


def _iso(value) -> Optional[str]:
    if value is None:
        return None
    return ensure_aware_utc(value).isoformat()


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if value is None:
        return None
    return datetime.fromisoformat(value)


def _dumps(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _wrap(operation: str) -> StorageError:
    return StorageError("material storage %s failed" % operation)


def _safe_execute(conn, sql: str, params, operation: str):
    try:
        return conn.execute(sql, params)
    except StorageError:
        raise
    except Exception as exc:  # noqa: BLE001 - surface as StorageError
        raise _wrap(operation) from exc


# --------------------------------------------------------------------------- #
# Raw signal selection (scope-aware)
# --------------------------------------------------------------------------- #
def select_github_raw_signals(
    conn,
    week_key: str,
    scope_key: str,
    limit: int,
) -> List[Mapping[str, Any]]:
    """Select the *latest* GitHub ``raw_signal`` per repository for a scope/week.

    Attribution flows through ``raw_signal_observation -> source_run ->
    collection_run -> raw_signal``, so a content-addressed snapshot that was
    collected by several scopes or several weeks is visible to each of them
    (and different scopes never mix). Within one ``(week_key, scope_key)`` the
    same repository may have several observations; only the newest snapshot is
    returned, chosen by a deterministic ``(updated_at|pushed_at, observed_at,
    raw_signal.id)`` ordering. ``limit`` is applied *after* deduplication, so
    it bounds the number of distinct repositories, not observation rows.
    """
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 50:
        raise ValueError("limit must be between 1 and 50")
    sql = (
        "SELECT rs.id, sr.collection_run_id, rs.source, rs.external_id, "
        "       rs.payload, rs.payload_sha256, rs.source_version, "
        "       obs.observed_at AS collected_at, rs.created_at "
        "FROM raw_signal_observation obs "
        "JOIN source_run sr ON obs.source_run_id = sr.id "
        "JOIN collection_run cr ON sr.collection_run_id = cr.id "
        "JOIN raw_signal rs ON obs.raw_signal_id = rs.id "
        "WHERE rs.source = 'github' "
        "  AND cr.week_key = ? "
        "  AND sr.scope_key = ? "
        "  AND cr.status IN ('success', 'partial') "
        "  AND sr.status IN ('success', 'partial') "
        "ORDER BY rs.id ASC"
    )
    rows = _safe_execute(conn, sql, (week_key, scope_key), "raw select").fetchall()

    # Decode payloads, then keep the newest snapshot per repository.
    decoded = []
    for row in rows:
        item = dict(row)
        item["payload"] = json.loads(item["payload"])
        decoded.append(item)

    latest: dict = {}
    for item in decoded:
        repo_key = item["external_id"]
        if repo_key not in latest or _is_newer(item, latest[repo_key]):
            latest[repo_key] = item

    # Deterministic display order: newest repo first (by updated/pushed time),
    # ties broken by ascending external_id (stable two-pass sort).
    ordered = sorted(latest.values(), key=lambda item: item["external_id"])
    ordered.sort(key=lambda item: _time_key(item["payload"]), reverse=True)
    return ordered[:limit]


def _time_key(payload: Mapping[str, Any]):
    """Return a comparable key for the repo's latest activity time."""
    updated = payload.get("updated_at")
    pushed = payload.get("pushed_at")
    value = updated or pushed or ""
    # Normalize the common trailing "Z" so ISO strings sort consistently.
    if isinstance(value, str):
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return value
    return ""


def _is_newer(candidate: Mapping[str, Any], current: Mapping[str, Any]) -> bool:
    """True if ``candidate`` is a newer snapshot than ``current``.

    Deterministic tie-breakers, in order: (updated_at|pushed_at), collected_at,
    then raw_signal.id (descending), so the choice is stable even when two
    snapshots share the same timestamps.
    """
    cand_payload = candidate["payload"]
    cur_payload = current["payload"]
    cand_time = _time_key(cand_payload)
    cur_time = _time_key(cur_payload)
    if cand_time != cur_time:
        return cand_time > cur_time
    if candidate["collected_at"] != current["collected_at"]:
        return candidate["collected_at"] > current["collected_at"]
    return candidate["id"] > current["id"]


# --------------------------------------------------------------------------- #
# Event + members
# --------------------------------------------------------------------------- #
class EventRepository:
    def __init__(self, conn):
        self.conn = conn

    def upsert(self, event: Event) -> Event:
        _safe_execute(
            self.conn,
            "INSERT INTO event "
            "(id, canonical_key, title, summary, occurred_at, created_at, "
            " updated_at, payload) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(canonical_key) DO UPDATE SET "
            " title = excluded.title, "
            " summary = excluded.summary, "
            " updated_at = excluded.updated_at, "
            " payload = excluded.payload",
            (
                event.id,
                event.canonical_key,
                event.title,
                event.summary,
                _iso(event.occurred_at),
                _iso(event.created_at),
                _iso(event.updated_at),
                _dumps(event.payload),
            ),
            "event upsert",
        )
        return self.get_by_canonical_key(event.canonical_key)

    def get_by_canonical_key(self, canonical_key: str) -> Optional[Event]:
        row = _safe_execute(
            self.conn,
            "SELECT * FROM event WHERE canonical_key = ?",
            (canonical_key,),
            "event read",
        ).fetchone()
        return self._from_row(row) if row else None

    def exists_by_key(self, canonical_key: str) -> bool:
        row = _safe_execute(
            self.conn,
            "SELECT 1 FROM event WHERE canonical_key = ?",
            (canonical_key,),
            "event exists",
        ).fetchone()
        return row is not None

    @staticmethod
    def _from_row(row) -> Event:
        payload = json.loads(row["payload"]) if row["payload"] else {}
        return Event(
            id=row["id"],
            canonical_key=row["canonical_key"],
            title=row["title"],
            summary=row["summary"],
            occurred_at=_parse_dt(row["occurred_at"]),
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
            payload=payload,
        )


class EventMemberRepository:
    def __init__(self, conn):
        self.conn = conn

    def upsert(self, member: EventMember) -> EventMember:
        _safe_execute(
            self.conn,
            "INSERT INTO event_member (id, event_id, signal_id, role, created_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(event_id, signal_id) DO NOTHING",
            (
                member.id,
                member.event_id,
                member.signal_id,
                member.role,
                _iso(member.created_at),
            ),
            "event_member upsert",
        )
        row = _safe_execute(
            self.conn,
            "SELECT * FROM event_member WHERE event_id = ? AND signal_id = ?",
            (member.event_id, member.signal_id),
            "event_member read",
        ).fetchone()
        return EventMember(
            id=row["id"],
            event_id=row["event_id"],
            signal_id=row["signal_id"],
            role=row["role"],
            created_at=_parse_dt(row["created_at"]),
        )


# --------------------------------------------------------------------------- #
# Claim + Evidence + ClaimEvidence
# --------------------------------------------------------------------------- #
class ClaimRepository:
    def __init__(self, conn):
        self.conn = conn

    def upsert(self, claim: Claim) -> Claim:
        _safe_execute(
            self.conn,
            "INSERT INTO claim "
            "(id, event_id, text, claim_type, state, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            " updated_at = excluded.updated_at, state = excluded.state",
            (
                claim.id,
                claim.event_id,
                claim.text,
                claim.claim_type,
                claim.state,
                _iso(claim.created_at),
                _iso(claim.updated_at),
            ),
            "claim upsert",
        )
        return self.get(claim.id)

    def get(self, claim_id: str) -> Optional[Claim]:
        row = _safe_execute(
            self.conn, "SELECT * FROM claim WHERE id = ?", (claim_id,), "claim read"
        ).fetchone()
        if row is None:
            return None
        return Claim(
            id=row["id"],
            event_id=row["event_id"],
            text=row["text"],
            claim_type=row["claim_type"],
            state=row["state"],
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )

    def exists(self, claim_id: str) -> bool:
        row = _safe_execute(
            self.conn, "SELECT 1 FROM claim WHERE id = ?", (claim_id,), "claim exists"
        ).fetchone()
        return row is not None


class EvidenceRepository:
    def __init__(self, conn):
        self.conn = conn

    def upsert(self, evidence: Evidence) -> Evidence:
        _safe_execute(
            self.conn,
            "INSERT INTO evidence "
            "(id, source, url, snippet, evidence_type, collected_at, "
            " created_at, payload, payload_sha256) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO NOTHING",
            (
                evidence.id,
                evidence.source,
                evidence.url,
                evidence.snippet,
                evidence.evidence_type,
                _iso(evidence.collected_at),
                _iso(evidence.created_at),
                _dumps(evidence.payload),
                evidence.payload_sha256,
            ),
            "evidence upsert",
        )
        return self.get(evidence.id)

    def get(self, evidence_id: str) -> Optional[Evidence]:
        row = _safe_execute(
            self.conn,
            "SELECT * FROM evidence WHERE id = ?",
            (evidence_id,),
            "evidence read",
        ).fetchone()
        if row is None:
            return None
        return Evidence(
            id=row["id"],
            source=row["source"],
            url=row["url"],
            snippet=row["snippet"],
            evidence_type=row["evidence_type"],
            collected_at=_parse_dt(row["collected_at"]),
            created_at=_parse_dt(row["created_at"]),
            payload=json.loads(row["payload"]),
            payload_sha256=row["payload_sha256"],
        )

    def exists(self, evidence_id: str) -> bool:
        row = _safe_execute(
            self.conn, "SELECT 1 FROM evidence WHERE id = ?", (evidence_id,), "evidence exists"
        ).fetchone()
        return row is not None

    def list_for_claim(self, claim_id: str) -> List[Evidence]:
        rows = _safe_execute(
            self.conn,
            "SELECT e.* FROM evidence e "
            "JOIN claim_evidence ce ON ce.evidence_id = e.id "
            "WHERE ce.claim_id = ? ORDER BY e.id",
            (claim_id,),
            "evidence list",
        ).fetchall()
        return [
            Evidence(
                id=r["id"],
                source=r["source"],
                url=r["url"],
                snippet=r["snippet"],
                evidence_type=r["evidence_type"],
                collected_at=_parse_dt(r["collected_at"]),
                created_at=_parse_dt(r["created_at"]),
                payload=json.loads(r["payload"]),
                payload_sha256=r["payload_sha256"],
            )
            for r in rows
        ]


class ClaimEvidenceRepository:
    def __init__(self, conn):
        self.conn = conn

    def upsert(self, link: ClaimEvidence) -> ClaimEvidence:
        _safe_execute(
            self.conn,
            "INSERT INTO claim_evidence "
            "(id, claim_id, evidence_id, relation, weight, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(claim_id, evidence_id) DO NOTHING",
            (
                link.id,
                link.claim_id,
                link.evidence_id,
                link.relation,
                link.weight,
                _iso(link.created_at),
            ),
            "claim_evidence upsert",
        )
        row = _safe_execute(
            self.conn,
            "SELECT * FROM claim_evidence WHERE claim_id = ? AND evidence_id = ?",
            (link.claim_id, link.evidence_id),
            "claim_evidence read",
        ).fetchone()
        return ClaimEvidence(
            id=row["id"],
            claim_id=row["claim_id"],
            evidence_id=row["evidence_id"],
            relation=row["relation"],
            weight=row["weight"],
            created_at=_parse_dt(row["created_at"]),
        )

    def exists(self, claim_id: str, evidence_id: str) -> bool:
        row = _safe_execute(
            self.conn,
            "SELECT 1 FROM claim_evidence WHERE claim_id = ? AND evidence_id = ?",
            (claim_id, evidence_id),
            "claim_evidence exists",
        ).fetchone()
        return row is not None


# --------------------------------------------------------------------------- #
# MaterialPack
# --------------------------------------------------------------------------- #
class MaterialPackRepository:
    def __init__(self, conn):
        self.conn = conn

    def upsert(self, pack: MaterialPack) -> MaterialPack:
        """Insert ``pack`` idempotently and return the stored record.

        The deterministic pack id is the primary key, so ``ON CONFLICT(id) DO
        NOTHING`` makes re-runs safe without a separate existence check.
        """
        _safe_execute(
            self.conn,
            "INSERT INTO material_pack "
            "(id, week_key, event_id, claim_ids, content, bundle_hash, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO NOTHING",
            (
                pack.id,
                pack.week_key,
                pack.event_id,
                json.dumps(list(pack.claim_ids), ensure_ascii=False),
                _dumps(pack.content),
                pack.bundle_hash,
                _iso(pack.created_at),
            ),
            "material_pack upsert",
        )
        return self.get(pack.id)

    def get(self, pack_id: str) -> MaterialPack:
        row = _safe_execute(
            self.conn,
            "SELECT * FROM material_pack WHERE id = ?",
            (pack_id,),
            "material_pack read",
        ).fetchone()
        return self._from_row(row)

    def exists(self, pack_id: str) -> bool:
        row = _safe_execute(
            self.conn,
            "SELECT 1 FROM material_pack WHERE id = ?",
            (pack_id,),
            "material_pack exists",
        ).fetchone()
        return row is not None

    @staticmethod
    def _from_row(row) -> MaterialPack:
        return MaterialPack(
            id=row["id"],
            week_key=row["week_key"],
            event_id=row["event_id"],
            claim_ids=tuple(json.loads(row["claim_ids"])),
            content=json.loads(row["content"]),
            bundle_hash=row["bundle_hash"],
            created_at=_parse_dt(row["created_at"]),
        )


# --------------------------------------------------------------------------- #
# Feedback
# --------------------------------------------------------------------------- #
class FeedbackRepository:
    def __init__(self, conn):
        self.conn = conn

    def exists(self, feedback_id: str) -> bool:
        row = _safe_execute(
            self.conn,
            "SELECT 1 FROM feedback WHERE id = ?",
            (feedback_id,),
            "feedback exists",
        ).fetchone()
        return row is not None

    def insert(self, feedback: Feedback) -> Feedback:
        _safe_execute(
            self.conn,
            "INSERT INTO feedback "
            "(id, target_type, target_id, decision, reason, audience, angle, "
            " usefulness, published_url, published_at, outcome, lesson, "
            " created_at, policy_version_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                feedback.id,
                feedback.target_type,
                feedback.target_id,
                feedback.decision,
                feedback.reason,
                feedback.audience,
                feedback.angle,
                feedback.usefulness,
                feedback.published_url,
                feedback.published_at,
                feedback.outcome,
                feedback.lesson,
                _iso(feedback.created_at),
                feedback.policy_version_id,
            ),
            "feedback insert",
        )
        return feedback

    def target_exists(self, target_type: str, target_id: str) -> bool:
        if target_type == "content_brief":
            # DEC-018: a brief is anchored to its event candidate. Callers
            # pass the EVENT id here (the stored Feedback target_id is the
            # deterministic brief id, checked by the sync layer itself).
            row = _safe_execute(
                self.conn,
                "SELECT 1 FROM event_candidate WHERE id = ?",
                (target_id,),
                "feedback target check",
            ).fetchone()
            return row is not None
        if target_type != "material_pack":
            return False
        row = _safe_execute(
            self.conn,
            "SELECT 1 FROM material_pack WHERE id = ?",
            (target_id,),
            "feedback target check",
        ).fetchone()
        return row is not None
