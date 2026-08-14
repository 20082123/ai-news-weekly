"""Phase 2B2 materialization pipeline (deterministic, offline, no LLM).

Transforms collected GitHub ``raw_signal`` rows into normalized Signals,
Events, Claims, Evidence and ClaimEvidence links - all deterministically and
idempotently. The same inputs always produce the same ids and re-running
never duplicates rows.

Data flow::

    select_github_raw_signals(week_key, scope_key, limit)   # latest snapshot per repo
        -> normalize each raw_signal into a Signal (canonical_key stable)
        -> one deterministic Event per repository
        -> EventMember link (idempotent)
        -> factual Claims derived ONLY from snapshot fields
        -> Evidence anchored to the raw_signal (id includes raw_signal_id)
        -> ClaimEvidence (supports) links
        -> Signal state: collected -> normalized -> clustered -> verified

The ``verified -> packaged`` transition is deliberately NOT done here: it is
performed by the packaging stage only after a MaterialPack has been persisted.

Everything is written in a single transaction owned by the caller; on failure
nothing is committed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, List, Mapping, Optional, Tuple
from urllib.parse import urlparse

from ..domain.models import (
    Claim,
    ClaimEvidence,
    Event,
    EventMember,
    Evidence,
    Signal,
    deterministic_id,
    now_utc,
    validate_scope_key,
)
from ..domain.states import can_transition, transition
from ..storage.material_repositories import select_github_raw_signals
from ..storage.repositories import SignalRepository, StateTransitionRepository


class MaterializeError(Exception):
    """Raised when materialization cannot proceed (configuration / data)."""


# Stable canonical keys.
_REPO_PREFIX = "github:repository:"
_EVENT_PREFIX = "github:event:repository:"

# Warning codes for untrusted / anomalous data.
WARN_PROMPT_INJECTION = "UNTRUSTED_PROMPT_INJECTION"
WARN_QUARANTINED = "RECORD_QUARANTINED"

# Hosts allowed for repository URLs. Real GitHub REST data must use
# github.com; tests opt in to the reserved example domains via an explicit
# ``safe_hosts`` argument - arbitrary hosts are never silently accepted.
_GITHUB_HOSTS = frozenset({"github.com"})
FIXTURE_HOSTS = frozenset({"example.com", "example.org", "example.net"})

_INJECTION_MARKERS = (
    "ignore previous instructions",
    "system prompt",
    "reveal secrets",
    "<script",
    "</script",
    "<!--",
    "javascript:",
)

_DESC_MAX = 500
_TOPIC_MAX_LEN = 64
_TOPIC_MAX_COUNT = 20
_SNIPPET_MAX = 280
_FULL_NAME_MAX = 256


@dataclass(frozen=True)
class ClaimTouch:
    """One claim produced for a repository during this materialization."""

    claim_id: str
    text: str
    evidence_ids: Tuple[str, ...]


@dataclass(frozen=True)
class EventTouch:
    """The entities touched for one repository during this materialization."""

    event_id: str
    signal_id: str
    title: str
    claims: Tuple[ClaimTouch, ...]

    @property
    def claim_ids(self) -> Tuple[str, ...]:
        return tuple(c.claim_id for c in self.claims)

    @property
    def evidence_ids(self) -> Tuple[str, ...]:
        out: List[str] = []
        for c in self.claims:
            for ev in c.evidence_ids:
                if ev not in out:
                    out.append(ev)
        return tuple(out)


@dataclass(frozen=True)
class MaterializeResult:
    """Safe, payload-free summary of one materialization run."""

    processed: int
    signals_created: int
    signals_updated: int
    events_created: int
    claims_created: int
    evidence_created: int
    quarantined: int
    warnings: Tuple[str, ...] = ()
    events: Tuple[EventTouch, ...] = ()


def _has_control_chars(text: str) -> bool:
    return any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text)


def _clean_text(value: Any, max_len: int) -> Optional[str]:
    """Sanitize untrusted text. Returns ``None`` if the value is unusable."""
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    if _has_control_chars(value):
        return None
    text = value.strip()
    if not text:
        return None
    if len(text) > max_len:
        text = text[:max_len]
    return text


def _clean_topics(raw: Any) -> Tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    topics: List[str] = []
    for item in raw[:_TOPIC_MAX_COUNT]:
        clean = _clean_text(item, _TOPIC_MAX_LEN)
        if clean:
            topics.append(clean)
    return tuple(topics)


def _is_injection(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _INJECTION_MARKERS)


def _is_safe_https_url(value: Any, safe_hosts: frozenset) -> bool:
    """A URL is safe only when https, on an allowed host, no creds, no port."""
    if not isinstance(value, str):
        return False
    try:
        parsed = urlparse(value)
    except (TypeError, ValueError):
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname in safe_hosts
        and parsed.username is None
        and parsed.password is None
        and parsed.port is None
    )


def _parse_iso_timestamp(value: Any, field_name: str) -> str:
    """Validate a tz-aware ISO-8601 timestamp and return a normalized form."""
    if not isinstance(value, str):
        raise MaterializeError("invalid %s" % field_name)
    text = value.strip()
    if not text or _has_control_chars(text):
        raise MaterializeError("invalid %s" % field_name)
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise MaterializeError("invalid %s" % field_name) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MaterializeError("naive %s" % field_name)
    return normalized


def _non_negative_int(raw_payload: Mapping[str, Any], key: str) -> Optional[int]:
    val = raw_payload.get(key)
    if val is None:
        return None
    if isinstance(val, bool) or not isinstance(val, int):
        raise MaterializeError("invalid %s" % key)
    if val < 0:
        raise MaterializeError("negative %s" % key)
    return val


@dataclass
class _NormalizedRepo:
    """Internal value object holding a cleaned repository snapshot."""

    repo_id: str
    canonical_key: str
    event_key: str
    full_name: str
    html_url: str
    description: Optional[str]
    language: Optional[str]
    stargazers_count: Optional[int]
    forks_count: Optional[int]
    topics: Tuple[str, ...]
    pushed_at: Optional[str]
    updated_at: str
    payload: Mapping[str, Any]
    warnings: List[str] = field(default_factory=list)


def _normalize_repo(raw_payload: Mapping[str, Any], safe_hosts: frozenset) -> _NormalizedRepo:
    """Build a cleaned, whitelist-only snapshot from a raw payload.

    Raises :class:`MaterializeError` (-> quarantine) when essential fields are
    missing, malformed, unsafe, or carry prompt-injection markers. A poisoned
    description is replaced with a stable placeholder (not quarantined).
    """
    warnings: List[str] = []

    raw_id = raw_payload.get("id")
    if isinstance(raw_id, bool) or not isinstance(raw_id, int):
        raise MaterializeError("repository id missing or invalid")
    repo_id = str(raw_id)

    full_name = _clean_text(raw_payload.get("full_name"), _FULL_NAME_MAX)
    html_url = raw_payload.get("html_url")
    if not full_name or not isinstance(html_url, str):
        raise MaterializeError("required repository fields missing")

    # Essential fields with injection markers quarantine the whole record.
    if _is_injection(full_name) or _is_injection(html_url):
        raise MaterializeError("prompt-injection in essential field")

    if not _is_safe_https_url(html_url, safe_hosts):
        raise MaterializeError("unsafe repository url")

    updated_at = _parse_iso_timestamp(raw_payload.get("updated_at"), "updated_at")
    if _is_injection(updated_at):
        raise MaterializeError("prompt-injection in timestamp")

    pushed_at_raw = raw_payload.get("pushed_at")
    pushed_at = (
        _parse_iso_timestamp(pushed_at_raw, "pushed_at")
        if pushed_at_raw is not None
        else None
    )

    # stars / forks must be non-negative integers (absent -> None).
    stargazers_count = _non_negative_int(raw_payload, "stargazers_count")
    forks_count = _non_negative_int(raw_payload, "forks_count")

    description = _clean_text(raw_payload.get("description"), _DESC_MAX)
    if description and _is_injection(description):
        warnings.append(WARN_PROMPT_INJECTION)
        description = "[description withheld: untrusted content]"

    language = _clean_text(raw_payload.get("language"), 64)
    topics = _clean_topics(raw_payload.get("topics"))

    canonical_key = _REPO_PREFIX + repo_id
    event_key = _EVENT_PREFIX + repo_id

    payload = {
        "id": repo_id,
        "full_name": full_name,
        "html_url": html_url,
        "updated_at": updated_at,
    }
    if description is not None:
        payload["description"] = description
    if language is not None:
        payload["language"] = language
    if stargazers_count is not None:
        payload["stargazers_count"] = stargazers_count
    if forks_count is not None:
        payload["forks_count"] = forks_count
    if topics:
        payload["topics"] = list(topics)
    if pushed_at is not None:
        payload["pushed_at"] = pushed_at

    return _NormalizedRepo(
        repo_id=repo_id,
        canonical_key=canonical_key,
        event_key=event_key,
        full_name=full_name,
        html_url=html_url,
        description=description,
        language=language,
        stargazers_count=stargazers_count,
        forks_count=forks_count,
        topics=topics,
        pushed_at=pushed_at,
        updated_at=updated_at,
        payload=payload,
        warnings=warnings,
    )


def _make_evidence(
    raw: _NormalizedRepo,
    raw_signal_id: str,
    source_version: str,
    collected_at: datetime,
) -> Evidence:
    snippet = "%s - updated %s" % (raw.full_name, raw.updated_at)
    payload = {
        "raw_signal_id": raw_signal_id,
        "source_version": source_version,
        "html_url": raw.html_url,
        "full_name": raw.full_name,
        "updated_at": raw.updated_at,
    }
    # All claim-referenced fields are included so numbers/dates/URLs in claim
    # text are traceable to the bound evidence payload.
    if raw.stargazers_count is not None:
        payload["stargazers_count"] = raw.stargazers_count
    if raw.forks_count is not None:
        payload["forks_count"] = raw.forks_count
    if raw.language is not None:
        payload["language"] = raw.language
    if raw.pushed_at is not None:
        payload["pushed_at"] = raw.pushed_at

    # The evidence id must change when the raw snapshot changes.
    evidence_id = deterministic_id("evidence", raw_signal_id, raw.html_url, snippet)

    return Evidence(
        source="github",
        url=raw.html_url,
        snippet=snippet[:_SNIPPET_MAX],
        evidence_type="github_repository_snapshot",
        payload=payload,
        collected_at=collected_at,
        id=evidence_id,
    )


def _advance_to_verified(
    signal_repo: SignalRepository,
    state_repo: StateTransitionRepository,
    signal_id: str,
    ts: datetime,
) -> int:
    """Advance a Signal along ``collected -> normalized -> clustered -> verified``.

    Returns the number of *new* transitions appended. Uses compare-and-swap so
    already-passed states are skipped and a packaged/terminal Signal is never
    regressed.
    """
    appended = 0
    current = signal_repo.get_state(signal_id) or "collected"
    for target in ("normalized", "clustered", "verified"):
        if current == target:
            continue
        if not can_transition(current, target):
            break
        if signal_repo.advance_state(signal_id, current, target):
            state_repo.append(
                transition(signal_id, current, target, timestamp=ts, stage="materialize")
            )
            appended += 1
            current = target
        else:
            # Another writer advanced concurrently: re-read and continue.
            current = signal_repo.get_state(signal_id) or current
    return appended


def materialize_github(
    conn,
    week_key: str,
    scope_key: str,
    limit: int,
    *,
    clock: Optional[datetime] = None,
    safe_hosts: frozenset = _GITHUB_HOSTS,
) -> MaterializeResult:
    """Materialize the latest GitHub snapshot per repository for a scope.

    ``conn`` is an open connection; the caller wraps this call in its own
    transaction. ``safe_hosts`` is an explicit host allow-list (defaults to
    ``github.com``; tests pass ``_FIXTURE_HOSTS``). Raises
    :class:`StorageError` on database failure (caller rolls back).
    """
    validate_scope_key(scope_key)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 50:
        raise MaterializeError("limit must be between 1 and 50")

    raw_rows = select_github_raw_signals(conn, week_key, scope_key, limit)
    ts = clock if clock is not None else now_utc()

    signal_repo = SignalRepository(conn)
    state_repo = StateTransitionRepository(conn)
    from ..storage.material_repositories import (
        ClaimEvidenceRepository,
        ClaimRepository,
        EventMemberRepository,
        EventRepository,
        EvidenceRepository,
    )

    event_repo = EventRepository(conn)
    member_repo = EventMemberRepository(conn)
    claim_repo = ClaimRepository(conn)
    evidence_repo = EvidenceRepository(conn)
    ce_repo = ClaimEvidenceRepository(conn)

    processed = 0
    signals_created = 0
    signals_updated = 0
    events_created = 0
    claims_created = 0
    evidence_created = 0
    quarantined = 0
    all_warnings: List[str] = []
    touches: List[EventTouch] = []

    for row in raw_rows:
        processed += 1
        try:
            raw = _normalize_repo(row["payload"], safe_hosts)
        except MaterializeError:
            quarantined += 1
            all_warnings.append(WARN_QUARANTINED)
            continue

        raw_signal_id = row["id"]
        source_version = row["source_version"]
        collected_at = datetime.fromisoformat(row["collected_at"])

        # --- Signal upsert (monotonic last_seen = collected_at) ------------
        signal = Signal(
            collection_run_id=row["collection_run_id"],
            source="github",
            canonical_key=raw.canonical_key,
            raw_signal_id=raw_signal_id,
            first_seen_at=collected_at,
            signal_type="github_repository",
            title=raw.full_name,
            url=raw.html_url,
            payload=raw.payload,
            last_seen_at=collected_at,
            updated_at=collected_at,
        )
        existing_state = signal_repo.get_state(signal.id)
        signal_repo.upsert(signal)
        if existing_state is None:
            signals_created += 1
        else:
            signals_updated += 1

        _advance_to_verified(signal_repo, state_repo, signal.id, ts)

        # --- Event (one per repository, deterministic) ----------------------
        event = Event(
            canonical_key=raw.event_key,
            title=raw.full_name,
            created_at=ts,
            summary=raw.description,
            occurred_at=None,
            payload={"repository_id": raw.repo_id},
        )
        if not event_repo.exists_by_key(event.canonical_key):
            events_created += 1
        event_repo.upsert(event)

        member_repo.upsert(
            EventMember(event_id=event.id, signal_id=signal.id, created_at=ts)
        )

        # --- Evidence anchored to the raw snapshot -------------------------
        evidence = _make_evidence(raw, raw_signal_id, source_version, collected_at)
        if not evidence_repo.exists(evidence.id):
            evidence_created += 1
        evidence_repo.upsert(evidence)

        # --- Deterministic factual claims (each bound to evidence) ----------
        claim_specs: List[str] = [
            "Repository %s is publicly accessible at %s." % (raw.full_name, raw.html_url),
            "The repository was last updated at %s." % raw.updated_at,
        ]
        if raw.stargazers_count is not None:
            claim_specs.append("The repository has %d stargazers." % raw.stargazers_count)
        if raw.forks_count is not None:
            claim_specs.append("The repository has %d forks." % raw.forks_count)
        if raw.language is not None:
            claim_specs.append("The primary language is %s." % raw.language)
        if raw.pushed_at is not None:
            claim_specs.append("The repository was last pushed to at %s." % raw.pushed_at)

        claim_touches: List[ClaimTouch] = []
        for claim_text in claim_specs:
            claim = Claim(
                text=claim_text,
                created_at=ts,
                event_id=event.id,
                claim_type="factual",
                state="verified",
            )
            if not claim_repo.exists(claim.id):
                claims_created += 1
            claim_repo.upsert(claim)
            ce_repo.upsert(
                ClaimEvidence(
                    claim_id=claim.id,
                    evidence_id=evidence.id,
                    created_at=ts,
                    relation="supports",
                )
            )
            claim_touches.append(
                ClaimTouch(claim_id=claim.id, text=claim_text, evidence_ids=(evidence.id,))
            )

        all_warnings.extend(raw.warnings)
        touches.append(
            EventTouch(
                event_id=event.id,
                signal_id=signal.id,
                title=raw.full_name,
                claims=tuple(claim_touches),
            )
        )

    return MaterializeResult(
        processed=processed,
        signals_created=signals_created,
        signals_updated=signals_updated,
        events_created=events_created,
        claims_created=claims_created,
        evidence_created=evidence_created,
        quarantined=quarantined,
        warnings=tuple(all_warnings),
        events=tuple(touches),
    )
