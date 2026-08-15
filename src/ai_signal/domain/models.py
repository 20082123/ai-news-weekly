"""Domain models for AI Signal Agent.

Every model is a plain ``dataclass`` built only on the Python standard
library. The rules enforced across the whole layer are:

* timestamps are always timezone-aware UTC - naive datetimes are rejected;
* run records use random UUID4 ids;
* stable entities use deterministic SHA-256 ids derived from their
  canonical parts, so re-running the pipeline yields identical ids;
* JSON payloads must be JSON-serializable mappings;
* models never carry secrets (no api keys, cookies, authorization headers,
  request headers, etc.);
* URLs, numbers and dates are never fabricated - they are always supplied
  by the caller.

The models perform validation and normalization in ``__post_init__``.
Because the dataclasses are frozen, normalization uses
``object.__setattr__``.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Tuple


class SensitiveDataError(ValueError):
    """Raised when a payload contains a credential-bearing field."""


_SENSITIVE_PAYLOAD_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "authorization",
        "client_secret",
        "cookie",
        "cookies",
        "credential",
        "credentials",
        "email_pass",
        "env",
        "environment",
        "headers",
        "password",
        "passwd",
        "private_key",
        "refresh_token",
        "request_headers",
        "secret",
        "set_cookie",
        "smtp_password",
        "token",
    }
)


# --------------------------------------------------------------------------- #
# Time helpers
# --------------------------------------------------------------------------- #
def ensure_aware_utc(value: datetime) -> datetime:
    """Return ``value`` normalized to aware UTC.

    Naive datetimes are rejected: there is no silent "assume local" or
    "assume UTC" conversion, so mistakes surface immediately and
    consistently.
    """
    if not isinstance(value, datetime):
        raise TypeError("a timezone-aware datetime is required")
    if value.tzinfo is None:
        raise ValueError("naive datetimes are not allowed; pass a tz-aware datetime")
    return value.astimezone(timezone.utc)


def now_utc() -> datetime:
    """Current time as aware UTC."""
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Identifier helpers
# --------------------------------------------------------------------------- #
def generate_run_id() -> str:
    """Random UUID4 for run-scoped records."""
    return str(uuid.uuid4())


def deterministic_id(*parts: Any) -> str:
    """Deterministic SHA-256 id from canonical parts.

    Parts are joined with a unit separator so that ``("a", "b")`` and
    ``("ab",)`` cannot collide. ``None`` parts are treated as empty.
    """
    joined = "\x1f".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def raw_signal_id(source: str, external_id: str, payload_sha256: str) -> str:
    return deterministic_id("raw_signal", source, external_id, payload_sha256)


def signal_entity_id(source: str, canonical_key: str) -> str:
    return deterministic_id("signal", source, canonical_key)


def event_entity_id(canonical_key: str) -> str:
    return deterministic_id("event", canonical_key)


def event_member_entity_id(event_id: str, signal_id: str) -> str:
    return deterministic_id("event_member", event_id, signal_id)


def evidence_entity_id(source: str, url: Optional[str], snippet: str) -> str:
    return deterministic_id("evidence", source, url or "", snippet)


def claim_evidence_entity_id(claim_id: str, evidence_id: str) -> str:
    return deterministic_id("claim_evidence", claim_id, evidence_id)


def material_pack_entity_id(week_key: str, bundle_hash: str) -> str:
    return deterministic_id("material_pack", week_key, bundle_hash)


def metric_snapshot_entity_id(publication_id: str, measurement_window: str) -> str:
    return deterministic_id("metric_snapshot", publication_id, measurement_window)


def delivery_run_entity_id(
    week_key: str, bundle_hash: str, policy_version_id: str, channel: str
) -> str:
    return deterministic_id(
        "delivery_run", week_key, bundle_hash, policy_version_id, channel
    )


def policy_version_entity_id(version: str) -> str:
    return deterministic_id("policy_version", version)


# --------------------------------------------------------------------------- #
# Payload / sequence helpers
# --------------------------------------------------------------------------- #
def validate_payload(payload: Any) -> Mapping[str, Any]:
    """Validate that ``payload`` is a JSON-serializable mapping."""
    if not isinstance(payload, Mapping):
        raise TypeError("payload must be a mapping, got %s" % type(payload).__name__)
    _reject_sensitive_fields(payload)
    # Raises TypeError/ValueError if the mapping is not JSON serializable.
    json.dumps(payload)
    return payload


def _reject_sensitive_fields(value: Any, path: Tuple[str, ...] = ()) -> None:
    """Reject credential-bearing keys before data can reach SQLite.

    The check is recursive and key-based. It deliberately allows harmless
    capability flags such as ``email_enabled`` while blocking complete
    environment/header containers and conventional credential field names.
    """
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if normalized in _SENSITIVE_PAYLOAD_KEYS:
                location = ".".join(path + (str(key),))
                raise SensitiveDataError(
                    "credential-bearing field is not allowed in payload: %s" % location
                )
            _reject_sensitive_fields(item, path + (str(key),))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _reject_sensitive_fields(item, path + (str(index),))


def as_tuple(value: Any) -> Tuple[Any, ...]:
    """Coerce a list/set/tuple into a tuple (empty tuple for ``None``)."""
    if value is None:
        return ()
    if isinstance(value, tuple):
        return value
    if isinstance(value, (list, set, frozenset)):
        return tuple(value)
    raise TypeError("expected a sequence, got %s" % type(value).__name__)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalize_datetimes(instance: Any, names: Tuple[str, ...]) -> None:
    """Normalize each named datetime field to aware UTC (skip ``None``)."""
    for name in names:
        value = getattr(instance, name)
        if value is not None:
            object.__setattr__(instance, name, ensure_aware_utc(value))


# Controlled vocabularies shared with the storage CHECK constraints.
COLLECTION_RUN_STATUSES = ("running", "success", "partial", "failed")
DELIVERY_RUN_STATUSES = ("running", "success", "partial", "failed")
PUBLICATION_STATUSES = ("published", "failed")
SOURCE_BATCH_STATUSES = ("success", "partial", "unavailable", "failed")
FEEDBACK_DECISIONS = ("adopted", "parked", "rejected")
SIGNAL_STATES = (
    "collected",
    "normalized",
    "clustered",
    "verified",
    "insufficient_evidence",
    "packaged",
    "adopted",
    "parked",
    "rejected",
    "published",
    "measured",
    "failed",
    "quarantined",
)
CLAIM_EVIDENCE_RELATIONS = ("supports", "contradicts", "contextual")


# --------------------------------------------------------------------------- #
# Policy
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class PolicyVersion:
    """An immutable version of the scoring/decision policy."""

    version: str
    created_at: datetime
    rules: Mapping[str, Any] = field(default_factory=dict)
    is_active: bool = False
    id: str = ""

    def __post_init__(self) -> None:
        if not self.version or not self.version.strip():
            raise ValueError("policy version label must not be empty")
        if self.id == "":
            object.__setattr__(self, "id", policy_version_entity_id(self.version))
        object.__setattr__(self, "rules", validate_payload(self.rules))
        _normalize_datetimes(self, ("created_at",))


# --------------------------------------------------------------------------- #
# Collection run
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class CollectionRun:
    """One execution of the collection pipeline for a given week."""

    week_key: str
    started_at: datetime
    config_snapshot: Mapping[str, Any]
    policy_version_id: Optional[str] = None
    finished_at: Optional[datetime] = None
    status: str = "running"
    created_at: Optional[datetime] = None
    id: str = ""

    def __post_init__(self) -> None:
        if not self.week_key or not self.week_key.strip():
            raise ValueError("week_key must not be empty")
        if self.status not in COLLECTION_RUN_STATUSES:
            raise ValueError("invalid collection run status: %r" % self.status)
        if self.id == "":
            object.__setattr__(self, "id", generate_run_id())
        if self.created_at is None:
            object.__setattr__(self, "created_at", now_utc())
        object.__setattr__(self, "config_snapshot", validate_payload(self.config_snapshot))
        _normalize_datetimes(self, ("started_at", "finished_at", "created_at"))


# --------------------------------------------------------------------------- #
# Raw signal
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RawSignal:
    """An immutable, exactly-once raw record captured from a source."""

    collection_run_id: str
    source: str
    external_id: str
    payload: Mapping[str, Any]
    payload_sha256: str
    collected_at: datetime
    source_version: str = "0"
    created_at: Optional[datetime] = None
    id: str = ""

    def __post_init__(self) -> None:
        if not self.source or not self.external_id:
            raise ValueError("source and external_id must not be empty")
        if self.id == "":
            object.__setattr__(
                self,
                "id",
                raw_signal_id(self.source, self.external_id, self.payload_sha256),
            )
        if self.created_at is None:
            object.__setattr__(self, "created_at", now_utc())
        object.__setattr__(self, "payload", validate_payload(self.payload))
        _normalize_datetimes(self, ("collected_at", "created_at"))


# --------------------------------------------------------------------------- #
# Signal (normalized entity)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Signal:
    """A normalized signal entity, deduplicated by canonical key."""

    collection_run_id: str
    source: str
    canonical_key: str
    raw_signal_id: str
    first_seen_at: datetime
    signal_type: str
    title: Optional[str] = None
    url: Optional[str] = None
    state: str = "collected"
    last_seen_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    payload: Mapping[str, Any] = field(default_factory=dict)
    id: str = ""

    def __post_init__(self) -> None:
        if not self.source or not self.canonical_key:
            raise ValueError("source and canonical_key must not be empty")
        if self.state not in SIGNAL_STATES:
            raise ValueError("invalid signal state: %r" % self.state)
        if self.id == "":
            object.__setattr__(
                self, "id", signal_entity_id(self.source, self.canonical_key)
            )
        if self.last_seen_at is None:
            object.__setattr__(self, "last_seen_at", self.first_seen_at)
        if self.created_at is None:
            object.__setattr__(self, "created_at", self.first_seen_at)
        if self.updated_at is None:
            object.__setattr__(self, "updated_at", self.first_seen_at)
        object.__setattr__(self, "payload", validate_payload(self.payload))
        _normalize_datetimes(
            self, ("first_seen_at", "last_seen_at", "created_at", "updated_at")
        )


# --------------------------------------------------------------------------- #
# Event + members
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Event:
    """A cluster of related signals forming a news event."""

    canonical_key: str
    title: str
    created_at: datetime
    summary: Optional[str] = None
    occurred_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    cluster_signal_ids: Tuple[str, ...] = ()
    payload: Mapping[str, Any] = field(default_factory=dict)
    id: str = ""

    def __post_init__(self) -> None:
        if not self.canonical_key or not self.title.strip():
            raise ValueError("canonical_key and title must not be empty")
        if self.id == "":
            object.__setattr__(self, "id", event_entity_id(self.canonical_key))
        if self.updated_at is None:
            object.__setattr__(self, "updated_at", self.created_at)
        object.__setattr__(self, "cluster_signal_ids", as_tuple(self.cluster_signal_ids))
        object.__setattr__(self, "payload", validate_payload(self.payload))
        _normalize_datetimes(self, ("created_at", "occurred_at", "updated_at"))


@dataclass(frozen=True)
class EventMember:
    """Membership link between an event and a signal (unique pair)."""

    event_id: str
    signal_id: str
    created_at: datetime
    role: str = "related"
    id: str = ""

    def __post_init__(self) -> None:
        if not self.event_id or not self.signal_id:
            raise ValueError("event_id and signal_id must not be empty")
        if self.id == "":
            object.__setattr__(
                self, "id", event_member_entity_id(self.event_id, self.signal_id)
            )
        _normalize_datetimes(self, ("created_at",))


# --------------------------------------------------------------------------- #
# Claims + evidence
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Claim:
    """A verifiable claim extracted from an event."""

    text: str
    created_at: datetime
    event_id: Optional[str] = None
    claim_type: str = "factual"
    state: str = "collected"
    updated_at: Optional[datetime] = None
    id: str = ""

    def __post_init__(self) -> None:
        if not self.text or not self.text.strip():
            raise ValueError("claim text must not be empty")
        if self.id == "":
            object.__setattr__(
                self,
                "id",
                deterministic_id("claim", self.event_id or "", self.text),
            )
        if self.updated_at is None:
            object.__setattr__(self, "updated_at", self.created_at)
        _normalize_datetimes(self, ("created_at", "updated_at"))


@dataclass(frozen=True)
class Evidence:
    """A piece of source evidence supporting or contradicting a claim."""

    source: str
    snippet: str
    collected_at: datetime
    url: Optional[str] = None
    evidence_type: str = "reference"
    payload: Mapping[str, Any] = field(default_factory=dict)
    created_at: Optional[datetime] = None
    payload_sha256: str = ""
    id: str = ""

    def __post_init__(self) -> None:
        if not self.source:
            raise ValueError("evidence source must not be empty")
        if not self.snippet or not self.snippet.strip():
            raise ValueError("evidence snippet must not be empty")
        object.__setattr__(self, "payload", validate_payload(self.payload))
        if self.payload_sha256 == "":
            canonical = json.dumps(self.payload, sort_keys=True, ensure_ascii=False)
            object.__setattr__(self, "payload_sha256", sha256_hex(canonical))
        if self.created_at is None:
            object.__setattr__(self, "created_at", self.collected_at)
        if self.id == "":
            object.__setattr__(
                self, "id", evidence_entity_id(self.source, self.url, self.snippet)
            )
        _normalize_datetimes(self, ("collected_at", "created_at"))


@dataclass(frozen=True)
class ClaimEvidence:
    """A link between a claim and a piece of evidence (unique pair)."""

    claim_id: str
    evidence_id: str
    created_at: datetime
    relation: str = "supports"
    weight: Optional[float] = None
    id: str = ""

    def __post_init__(self) -> None:
        if not self.claim_id or not self.evidence_id:
            raise ValueError("claim_id and evidence_id must not be empty")
        if self.relation not in CLAIM_EVIDENCE_RELATIONS:
            raise ValueError("invalid claim-evidence relation: %r" % self.relation)
        if self.weight is not None:
            weight = float(self.weight)
            if not (0.0 <= weight <= 1.0):
                raise ValueError("evidence weight must be between 0.0 and 1.0")
            object.__setattr__(self, "weight", weight)
        if self.id == "":
            object.__setattr__(
                self,
                "id",
                claim_evidence_entity_id(self.claim_id, self.evidence_id),
            )
        _normalize_datetimes(self, ("created_at",))


# --------------------------------------------------------------------------- #
# Material pack
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class MaterialPack:
    """The packaged weekly material derived from events and claims."""

    week_key: str
    content: Mapping[str, Any]
    bundle_hash: str
    created_at: datetime
    event_id: Optional[str] = None
    claim_ids: Tuple[str, ...] = ()
    id: str = ""

    def __post_init__(self) -> None:
        if not self.week_key or not self.bundle_hash:
            raise ValueError("week_key and bundle_hash must not be empty")
        if self.id == "":
            object.__setattr__(
                self, "id", material_pack_entity_id(self.week_key, self.bundle_hash)
            )
        object.__setattr__(self, "claim_ids", as_tuple(self.claim_ids))
        object.__setattr__(self, "content", validate_payload(self.content))
        _normalize_datetimes(self, ("created_at",))


# --------------------------------------------------------------------------- #
# Feedback
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Feedback:
    """A human decision on a signal/event/claim/pack."""

    target_type: str
    target_id: str
    decision: str
    created_at: datetime
    reason: Optional[str] = None
    audience: Optional[str] = None
    angle: Optional[str] = None
    usefulness: Optional[int] = None
    published_url: Optional[str] = None
    policy_version_id: Optional[str] = None
    id: str = ""

    def __post_init__(self) -> None:
        if not self.target_type or not self.target_id:
            raise ValueError("target_type and target_id must not be empty")
        if self.decision not in FEEDBACK_DECISIONS:
            raise ValueError("invalid feedback decision: %r" % self.decision)
        if self.usefulness is not None:
            if isinstance(self.usefulness, bool) or not isinstance(self.usefulness, int):
                raise TypeError("usefulness must be an integer between 1 and 5")
            if not (1 <= self.usefulness <= 5):
                raise ValueError("usefulness must be between 1 and 5")
        if self.id == "":
            object.__setattr__(self, "id", generate_run_id())
        _normalize_datetimes(self, ("created_at",))


# --------------------------------------------------------------------------- #
# Publication + metrics
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Publication:
    """A published material pack on a given channel."""

    material_pack_id: str
    channel: str
    published_at: datetime
    status: str = "published"
    target: Optional[str] = None
    payload: Mapping[str, Any] = field(default_factory=dict)
    id: str = ""

    def __post_init__(self) -> None:
        if not self.material_pack_id or not self.channel:
            raise ValueError("material_pack_id and channel must not be empty")
        if self.status not in PUBLICATION_STATUSES:
            raise ValueError("invalid publication status: %r" % self.status)
        object.__setattr__(self, "payload", validate_payload(self.payload))
        _normalize_datetimes(self, ("published_at",))
        if self.id == "":
            object.__setattr__(
                self,
                "id",
                deterministic_id(
                    "publication",
                    self.material_pack_id,
                    self.channel,
                    self.published_at.isoformat(),
                ),
            )


@dataclass(frozen=True)
class MetricSnapshot:
    """Engagement/readership metrics for one publication window."""

    publication_id: str
    measurement_window: str
    measured_at: datetime
    metrics: Mapping[str, Any]
    created_at: Optional[datetime] = None
    id: str = ""

    def __post_init__(self) -> None:
        if not self.publication_id or not self.measurement_window:
            raise ValueError("publication_id and measurement_window must not be empty")
        if self.created_at is None:
            object.__setattr__(self, "created_at", self.measured_at)
        if self.id == "":
            object.__setattr__(
                self,
                "id",
                metric_snapshot_entity_id(self.publication_id, self.measurement_window),
            )
        object.__setattr__(self, "metrics", validate_payload(self.metrics))
        _normalize_datetimes(self, ("measured_at", "created_at"))


# --------------------------------------------------------------------------- #
# Scoring + delivery
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ScoreLog:
    """An append-only scoring record for a target entity."""

    target_type: str
    target_id: str
    score: float
    components: Mapping[str, Any]
    created_at: datetime
    policy_version_id: Optional[str] = None
    id: str = ""

    def __post_init__(self) -> None:
        if not self.target_type or not self.target_id:
            raise ValueError("target_type and target_id must not be empty")
        if self.id == "":
            object.__setattr__(self, "id", generate_run_id())
        object.__setattr__(self, "components", validate_payload(self.components))
        object.__setattr__(self, "score", float(self.score))
        _normalize_datetimes(self, ("created_at",))


@dataclass(frozen=True)
class DeliveryRun:
    """One delivery attempt of a bundle through a channel (idempotent)."""

    week_key: str
    bundle_hash: str
    policy_version_id: str
    channel: str
    started_at: datetime
    status: str = "running"
    finished_at: Optional[datetime] = None
    warnings: Tuple[str, ...] = ()
    created_at: Optional[datetime] = None
    id: str = ""

    def __post_init__(self) -> None:
        if not self.week_key or not self.bundle_hash or not self.policy_version_id:
            raise ValueError("week_key, bundle_hash and policy_version_id must not be empty")
        if self.status not in DELIVERY_RUN_STATUSES:
            raise ValueError("invalid delivery run status: %r" % self.status)
        if self.created_at is None:
            object.__setattr__(self, "created_at", self.started_at)
        if self.id == "":
            object.__setattr__(
                self,
                "id",
                delivery_run_entity_id(
                    self.week_key, self.bundle_hash, self.policy_version_id, self.channel
                ),
            )
        object.__setattr__(self, "warnings", as_tuple(self.warnings))
        _normalize_datetimes(self, ("started_at", "finished_at", "created_at"))


# --------------------------------------------------------------------------- #
# Source contract value objects
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SourceItem:
    """One item returned by a source ``collect`` call."""

    external_id: str
    collected_at: datetime
    payload: Mapping[str, Any]
    url: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.external_id:
            raise ValueError("external_id must not be empty")
        object.__setattr__(self, "payload", validate_payload(self.payload))
        _normalize_datetimes(self, ("collected_at",))


@dataclass(frozen=True)
class SourceBatch:
    """The result of a single source collection call.

    ``status`` is the only way a source communicates a partial/unavailable
    failure to the pipeline; a single failing source must never force the
    whole pipeline to abort.
    """

    source: str
    status: str
    started_at: datetime
    finished_at: datetime
    source_version: str = "0"
    items: Tuple[SourceItem, ...] = ()
    warnings: Tuple[str, ...] = ()
    next_cursor: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.source:
            raise ValueError("source name must not be empty")
        if self.status not in SOURCE_BATCH_STATUSES:
            raise ValueError("invalid source batch status: %r" % self.status)
        object.__setattr__(self, "items", as_tuple(self.items))
        object.__setattr__(self, "warnings", as_tuple(self.warnings))
        _normalize_datetimes(self, ("started_at", "finished_at"))
        if self.finished_at < self.started_at:
            raise ValueError("finished_at must not be earlier than started_at")
        if not all(isinstance(item, SourceItem) for item in self.items):
            raise TypeError("items must contain SourceItem values")
        if not all(isinstance(warning, str) for warning in self.warnings):
            raise TypeError("warnings must contain strings")


# --------------------------------------------------------------------------- #
# Source collection run + cursor (phase 2A)
# --------------------------------------------------------------------------- #
# A ``scope_key`` is a stable logical collection scope (for example
# ``ai-agents-v1``). It isolates per-scope cursor progress so that different
# GitHub query scopes never overwrite each other. It is deliberately NOT the
# raw query string, a URL, a file path or a date, and it must never carry
# credentials. When the query semantics change incompatibly the scope version
# is bumped (``ai-agents-v1`` -> ``ai-agents-v2``).
SCOPE_KEY_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,63}$"
_SCOPE_KEY_RE = re.compile(SCOPE_KEY_PATTERN)


def validate_scope_key(value: Any) -> str:
    """Validate a stable logical scope key.

    The value is never echoed back in the error, so an accidentally-supplied
    raw query cannot leak through an exception message.
    """
    if not isinstance(value, str):
        raise TypeError("scope_key must be a string")
    if _SCOPE_KEY_RE.fullmatch(value) is None:
        raise ValueError("invalid scope_key")
    return value


def _non_negative_int(value: Any, name: str) -> int:
    """Validate that ``value`` is a non-negative integer (booleans rejected)."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("%s must be a non-negative integer" % name)
    if value < 0:
        raise ValueError("%s must be a non-negative integer" % name)
    return value


@dataclass(frozen=True)
class SourceRun:
    """One execution of a single source within a collection run.

    Mirrors the outcome of a :class:`SourceBatch` for audit and replay. It
    never carries credentials, paths or raw payloads - only counts, stable
    warning codes, cursors and timestamps.
    """

    collection_run_id: str
    source: str
    scope_key: str
    source_version: str
    status: str
    started_at: datetime
    finished_at: datetime
    cursor_in: Optional[str] = None
    cursor_out: Optional[str] = None
    item_count: int = 0
    warning_count: int = 0
    warnings: Tuple[str, ...] = ()
    cursor_advanced: bool = False
    created_at: Optional[datetime] = None
    id: str = ""

    def __post_init__(self) -> None:
        if not self.collection_run_id or not self.source or not self.source_version:
            raise ValueError(
                "collection_run_id, source and source_version must not be empty"
            )
        object.__setattr__(self, "scope_key", validate_scope_key(self.scope_key))
        if self.status not in SOURCE_BATCH_STATUSES:
            raise ValueError("invalid source run status: %r" % self.status)
        object.__setattr__(
            self, "item_count", _non_negative_int(self.item_count, "item_count")
        )
        object.__setattr__(
            self, "warning_count", _non_negative_int(self.warning_count, "warning_count")
        )
        object.__setattr__(self, "warnings", as_tuple(self.warnings))
        if not all(isinstance(warning, str) for warning in self.warnings):
            raise TypeError("warnings must contain strings")
        if self.warning_count != len(self.warnings):
            raise ValueError("warning_count must equal the number of warnings")
        for name in ("cursor_in", "cursor_out"):
            cursor = getattr(self, name)
            if cursor is not None and (
                not isinstance(cursor, str) or not cursor.strip()
            ):
                raise ValueError("%s must be a non-empty string or None" % name)
        if not isinstance(self.cursor_advanced, bool):
            raise TypeError("cursor_advanced must be a bool")
        if self.cursor_advanced and (
            self.status != "success"
            or self.cursor_out is None
            or self.cursor_out == self.cursor_in
        ):
            raise ValueError(
                "cursor_advanced requires a successful run with a changed cursor"
            )
        if self.id == "":
            object.__setattr__(self, "id", generate_run_id())
        if self.created_at is None:
            object.__setattr__(self, "created_at", now_utc())
        _normalize_datetimes(self, ("started_at", "finished_at", "created_at"))
        if self.finished_at < self.started_at:
            raise ValueError("finished_at must not be earlier than started_at")


@dataclass(frozen=True)
class SourceCursor:
    """The current pagination cursor for a single ``(source, scope_key)``.

    Cursor progress is isolated per scope: each scope keeps its own row, so
    different GitHub query scopes advance independently. The cursor is only
    advanced by the pipeline, and only on a fully successful batch that
    supplies a distinct, non-null next cursor.
    """

    source: str
    scope_key: str
    source_version: str
    last_run_id: str
    updated_at: datetime
    cursor: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.source or not self.source_version or not self.last_run_id:
            raise ValueError(
                "source, source_version and last_run_id must not be empty"
            )
        object.__setattr__(self, "scope_key", validate_scope_key(self.scope_key))
        if not isinstance(self.cursor, str) or not self.cursor.strip():
            raise ValueError("cursor must be a non-empty string")
        _normalize_datetimes(self, ("updated_at",))


# --------------------------------------------------------------------------- #
# Candidate qualification (phase 2C1, identity v2)
# --------------------------------------------------------------------------- #
# Three layers:
#   Candidate          - stable identity: "who this object is"
#   CandidateDiscovery - provenance: one (week, scope, lane, snapshot) context
#   CandidateAssessment - deterministic decision per discovery
# A Candidate is deliberately NOT an Event and never becomes a MaterialPack.
CANDIDATE_LANES = ("watchlist", "mature", "emerging", "ecosystem")
CANDIDATE_DECISIONS = ("research", "watch", "reject")
CANDIDATE_TRIGGER_KINDS = ("repository_snapshot",)


def candidate_entity_id(source: str, canonical_key: str) -> str:
    """Stable global identity: same source + canonical_key -> same id."""
    return deterministic_id("candidate-v2", source, canonical_key)


def candidate_discovery_entity_id(
    candidate_id: str,
    week_key: str,
    scope_key: str,
    lane: str,
    raw_signal_id: str,
) -> str:
    return deterministic_id(
        "candidate-discovery-v2",
        candidate_id, week_key, scope_key, lane, raw_signal_id,
    )


def candidate_assessment_entity_id(
    candidate_discovery_id: str, input_hash: str, policy_version: str
) -> str:
    return deterministic_id(
        "candidate-assessment-v2", candidate_discovery_id, input_hash, policy_version
    )


@dataclass(frozen=True)
class Candidate:
    """A stable global candidate identity (one row per source object).

    The id depends only on ``(source, canonical_key)``: the same repository
    across any week, scope or lane is exactly one Candidate. Discovery
    contexts live in :class:`CandidateDiscovery`.
    """

    source: str
    canonical_key: str
    title: str
    url: str
    first_seen_at: datetime
    last_seen_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    id: str = ""

    def __post_init__(self) -> None:
        for name in ("canonical_key", "title"):
            if not str(getattr(self, name)).strip():
                raise ValueError("%s must not be empty" % name)
        if self.source != "github":
            raise ValueError("source must be github")
        if not isinstance(self.url, str) or not self.url.startswith("https://"):
            raise ValueError("url must be pre-validated https")
        if self.last_seen_at is None:
            object.__setattr__(self, "last_seen_at", self.first_seen_at)
        if self.created_at is None:
            object.__setattr__(self, "created_at", self.first_seen_at)
        if self.updated_at is None:
            object.__setattr__(self, "updated_at", self.first_seen_at)
        if self.id == "":
            object.__setattr__(
                self, "id", candidate_entity_id(self.source, self.canonical_key)
            )
        _normalize_datetimes(
            self, ("first_seen_at", "last_seen_at", "created_at", "updated_at")
        )
        if self.first_seen_at > self.last_seen_at:
            raise ValueError("first_seen_at must not be later than last_seen_at")


@dataclass(frozen=True)
class CandidateDiscovery:
    """One discovery context of a Candidate: (week, scope, lane, snapshot).

    The same repository discovered in different lanes, scopes or weeks
    produces different Discovery rows that share the same Candidate. The id
    is deterministic over the full context including ``raw_signal_id``, so a
    new snapshot in the same context forms a new Discovery.
    """

    candidate_id: str
    week_key: str
    scope_key: str
    lane: str
    raw_signal_id: str
    observed_at: datetime
    created_at: Optional[datetime] = None
    id: str = ""

    def __post_init__(self) -> None:
        if not self.candidate_id:
            raise ValueError("candidate_id must not be empty")
        if not str(self.week_key).strip():
            raise ValueError("week_key must not be empty")
        object.__setattr__(self, "scope_key", validate_scope_key(self.scope_key))
        if self.lane not in CANDIDATE_LANES:
            raise ValueError("invalid lane: %r" % self.lane)
        if not self.raw_signal_id:
            raise ValueError("raw_signal_id must not be empty")
        if self.created_at is None:
            object.__setattr__(self, "created_at", self.observed_at)
        if self.id == "":
            object.__setattr__(
                self,
                "id",
                candidate_discovery_entity_id(
                    self.candidate_id, self.week_key, self.scope_key,
                    self.lane, self.raw_signal_id,
                ),
            )
        _normalize_datetimes(self, ("observed_at", "created_at"))


@dataclass(frozen=True)
class CandidateAssessment:
    """One deterministic qualification decision for a CandidateDiscovery.

    The id is derived from ``(candidate_discovery_id, input_hash,
    policy_version)``, so re-running on identical input is idempotent while a
    changed snapshot (new ``input_hash``) yields a new assessment revision
    kept for audit.
    """

    candidate_discovery_id: str
    policy_version: str
    input_hash: str
    decision: str
    trigger_kind: str
    trigger_summary: str
    reason_codes: Tuple[str, ...]
    missing_evidence: Tuple[str, ...]
    attributes: Mapping[str, Any] = field(default_factory=dict)
    assessed_at: Optional[datetime] = None
    id: str = ""

    def __post_init__(self) -> None:
        if not self.candidate_discovery_id or not self.policy_version or not self.input_hash:
            raise ValueError(
                "candidate_discovery_id, policy_version and input_hash must not be empty"
            )
        if not isinstance(self.input_hash, str) or len(self.input_hash) != 64:
            raise ValueError("input_hash must be a 64-character hex string")
        try:
            int(self.input_hash, 16)
        except ValueError as exc:
            raise ValueError("input_hash must be a hex string") from exc
        if self.decision not in CANDIDATE_DECISIONS:
            raise ValueError("invalid decision: %r" % self.decision)
        if self.trigger_kind not in CANDIDATE_TRIGGER_KINDS:
            raise ValueError("invalid trigger_kind: %r" % self.trigger_kind)
        if not isinstance(self.trigger_summary, str) or not self.trigger_summary.strip():
            raise ValueError("trigger_summary must not be empty")
        object.__setattr__(self, "reason_codes", as_tuple(self.reason_codes))
        object.__setattr__(self, "missing_evidence", as_tuple(self.missing_evidence))
        if not all(isinstance(code, str) for code in self.reason_codes):
            raise TypeError("reason_codes must contain strings")
        if not all(isinstance(item, str) for item in self.missing_evidence):
            raise TypeError("missing_evidence must contain strings")
        object.__setattr__(self, "attributes", validate_payload(self.attributes))
        if self.id == "":
            object.__setattr__(
                self,
                "id",
                candidate_assessment_entity_id(
                    self.candidate_discovery_id, self.input_hash, self.policy_version
                ),
            )
        if self.assessed_at is None:
            object.__setattr__(self, "assessed_at", now_utc())
        _normalize_datetimes(self, ("assessed_at",))


# --------------------------------------------------------------------------- #
# GitHub discovery policy (phase 2C2, GitHub-specific)
# --------------------------------------------------------------------------- #
# These tables/objects are deliberately GitHub-specific. They orchestrate
# GitHub collection + candidate qualification + per-candidate dedup + the
# research budget, and produce a GitHub Research Queue - NOT global Signals,
# NOT Events, NOT A-F MaterialPacks.
GITHUB_DISCOVERY_LANES = ("watchlist", "mature", "emerging", "ecosystem")
GITHUB_DISCOVERY_RUN_STATUSES = ("running", "success", "partial", "failed")
GITHUB_PROBE_KINDS = ("search", "watchlist_target", "ecosystem_target")
GITHUB_PROBE_RUN_STATUSES = ("running", "success", "partial", "failed", "blocked")
GITHUB_QUEUE_STATES = ("queued", "over_budget")
GITHUB_RELATION_KINDS = ("full_name_match", "description_mention", "topic_match")

# Stable warning codes for probe runs (payload-free).
WARN_SCOPE_SPEC_MISMATCH = "SCOPE_SPEC_MISMATCH"
WARN_SCOPE_UNBOUND_CURSOR = "SCOPE_UNBOUND_CURSOR"
WARN_PROBE_UNSUPPORTED = "PROBE_UNSUPPORTED"

# Stable budget reason code for research candidates beyond the budget.
BUDGET_REASON_EXCEEDED = "RESEARCH_BUDGET_EXCEEDED"


def _require_hex64(value: Any, name: str) -> None:
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError("%s must be a 64-character hex string" % name)
    try:
        int(value, 16)
    except ValueError as exc:
        raise ValueError("%s must be a hex string" % name) from exc


@dataclass(frozen=True)
class GitHubDiscoveryRun:
    """One execution of one :class:`GitHubDiscoveryPolicy` (run-scoped)."""

    policy_id: str
    policy_hash: str
    week_key: str
    lane: str
    candidate_limit: int
    research_budget: int
    started_at: datetime
    status: str = "running"
    finished_at: Optional[datetime] = None
    warnings: Tuple[str, ...] = ()
    created_at: Optional[datetime] = None
    id: str = ""

    def __post_init__(self) -> None:
        if not self.policy_id.strip():
            raise ValueError("policy_id must not be empty")
        _require_hex64(self.policy_hash, "policy_hash")
        if not str(self.week_key).strip():
            raise ValueError("week_key must not be empty")
        if self.lane not in GITHUB_DISCOVERY_LANES:
            raise ValueError("invalid lane: %r" % self.lane)
        for name in ("candidate_limit", "research_budget"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("%s must be a non-negative integer" % name)
        if self.status not in GITHUB_DISCOVERY_RUN_STATUSES:
            raise ValueError("invalid discovery run status: %r" % self.status)
        object.__setattr__(self, "warnings", as_tuple(self.warnings))
        if not all(isinstance(warning, str) for warning in self.warnings):
            raise TypeError("warnings must contain strings")
        if self.id == "":
            object.__setattr__(self, "id", generate_run_id())
        if self.created_at is None:
            object.__setattr__(self, "created_at", now_utc())
        _normalize_datetimes(self, ("started_at", "finished_at", "created_at"))
        if self.finished_at is not None and self.finished_at < self.started_at:
            raise ValueError("finished_at must not be earlier than started_at")


@dataclass(frozen=True)
class GitHubDiscoveryProbeRun:
    """One probe execution inside a :class:`GitHubDiscoveryRun` (run-scoped).

    A probe is blocked BEFORE any network request when its scope_key is bound
    to a different spec_hash or already owns an unbound legacy cursor; the
    run keeps going with ``status = blocked``.
    """

    discovery_run_id: str
    probe_id: str
    kind: str
    lane: str
    scope_key: str
    spec_hash: str
    priority: int
    started_at: datetime
    status: str = "running"
    collection_run_id: Optional[str] = None
    item_count: int = 0
    warning_count: int = 0
    warnings: Tuple[str, ...] = ()
    finished_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    id: str = ""

    def __post_init__(self) -> None:
        if not self.discovery_run_id:
            raise ValueError("discovery_run_id must not be empty")
        if not self.probe_id.strip():
            raise ValueError("probe_id must not be empty")
        if self.kind not in GITHUB_PROBE_KINDS:
            raise ValueError("invalid probe kind: %r" % self.kind)
        if self.lane not in GITHUB_DISCOVERY_LANES:
            raise ValueError("invalid lane: %r" % self.lane)
        object.__setattr__(self, "scope_key", validate_scope_key(self.scope_key))
        _require_hex64(self.spec_hash, "spec_hash")
        for name in ("priority", "item_count", "warning_count"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("%s must be a non-negative integer" % name)
        if self.status not in GITHUB_PROBE_RUN_STATUSES:
            raise ValueError("invalid probe run status: %r" % self.status)
        object.__setattr__(self, "warnings", as_tuple(self.warnings))
        if not all(isinstance(warning, str) for warning in self.warnings):
            raise TypeError("warnings must contain strings")
        if self.warning_count != len(self.warnings):
            raise ValueError("warning_count must equal the number of warnings")
        if self.collection_run_id is not None and not self.collection_run_id:
            raise ValueError("collection_run_id must be a non-empty string or None")
        if self.id == "":
            object.__setattr__(self, "id", generate_run_id())
        if self.created_at is None:
            object.__setattr__(self, "created_at", now_utc())
        _normalize_datetimes(self, ("started_at", "finished_at", "created_at"))
        if self.finished_at is not None and self.finished_at < self.started_at:
            raise ValueError("finished_at must not be earlier than started_at")


@dataclass(frozen=True)
class GitHubScopeBinding:
    """Immutable claim of a scope_key by one probe spec.

    The binding is recorded before the first network request for a scope.
    ``spec_hash`` never changes afterwards: a later run with a different spec
    hash for the same scope is refused before networking.
    """

    scope_key: str
    probe_id: str
    policy_id: str
    spec_hash: str
    created_at: datetime
    updated_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "scope_key", validate_scope_key(self.scope_key))
        if not self.probe_id.strip():
            raise ValueError("probe_id must not be empty")
        if not self.policy_id.strip():
            raise ValueError("policy_id must not be empty")
        _require_hex64(self.spec_hash, "spec_hash")
        if self.updated_at is None:
            object.__setattr__(self, "updated_at", self.created_at)
        _normalize_datetimes(self, ("created_at", "updated_at"))


def github_candidate_selection_entity_id(
    discovery_run_id: str, candidate_id: str
) -> str:
    """Deterministic selection id: one final selection per (run, candidate)."""
    return deterministic_id(
        "github-candidate-selection", discovery_run_id, candidate_id
    )


@dataclass(frozen=True)
class GitHubCandidateSelection:
    """The final per-run selection of a research candidate into the queue.

    Only ``qualification_decision = research`` candidates are selected. The
    budget only changes ``queue_state``; the qualification decision is stored
    verbatim and is never downgraded by ``over_budget``.
    """

    discovery_run_id: str
    candidate_id: str
    winning_discovery_id: str
    winning_assessment_id: str
    selection_rank: int
    qualification_decision: str
    queue_state: str
    created_at: datetime
    budget_reason: Optional[str] = None
    ecosystem_target: Optional[str] = None
    relation_kind: Optional[str] = None
    relation_field: Optional[str] = None
    relation_raw_signal_id: Optional[str] = None
    id: str = ""

    def __post_init__(self) -> None:
        for name in (
            "discovery_run_id",
            "candidate_id",
            "winning_discovery_id",
            "winning_assessment_id",
        ):
            if not getattr(self, name):
                raise ValueError("%s must not be empty" % name)
        if isinstance(self.selection_rank, bool) or not isinstance(
            self.selection_rank, int
        ):
            raise TypeError("selection_rank must be a non-negative integer")
        if self.selection_rank < 0:
            raise ValueError("selection_rank must be a non-negative integer")
        if self.qualification_decision not in CANDIDATE_DECISIONS:
            raise ValueError(
                "invalid qualification_decision: %r" % self.qualification_decision
            )
        if self.queue_state not in GITHUB_QUEUE_STATES:
            raise ValueError("invalid queue_state: %r" % self.queue_state)
        for name in (
            "budget_reason",
            "ecosystem_target",
            "relation_kind",
            "relation_field",
            "relation_raw_signal_id",
        ):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError("%s must be a non-empty string or None" % name)
        if self.relation_kind is not None and self.relation_kind not in GITHUB_RELATION_KINDS:
            raise ValueError("invalid relation_kind: %r" % self.relation_kind)
        if self.id == "":
            object.__setattr__(
                self,
                "id",
                github_candidate_selection_entity_id(
                    self.discovery_run_id, self.candidate_id
                ),
            )
        _normalize_datetimes(self, ("created_at",))


# --------------------------------------------------------------------------- #
# Event candidate (phase 2C3, source-independent)
# --------------------------------------------------------------------------- #
# The five global Signal Types are product-wide and source-agnostic. They are
# deliberately NOT GitHub lanes; GitHub's watchlist/mature/emerging/ecosystem
# remain GitHub-specific Discovery Lanes.
SIGNAL_TYPES = (
    "capability_change",
    "tool_workflow_change",
    "user_reality",
    "economics_access",
    "ecosystem_market_shift",
)

# Source-specific candidate kinds a reference may point at. Only the GitHub
# repository candidate exists today; the official announcement candidate is
# reserved for phase 2D3.
EVENT_CANDIDATE_SOURCE_KINDS = (
    "github_repository_candidate",
    "official_announcement_candidate",
)


def event_candidate_entity_id(signal_type: str, subject: str, change_summary: str) -> str:
    """Deterministic source-independent identity of one candidate change."""
    return deterministic_id("event-candidate", signal_type, subject, change_summary)


def event_candidate_source_ref_entity_id(
    event_candidate_id: str, source_kind: str, ref_id: str
) -> str:
    return deterministic_id(
        "event-candidate-source-ref", event_candidate_id, source_kind, ref_id
    )


@dataclass(frozen=True)
class EventCandidate:
    """One source-independent candidate description of a real AI change.

    The identity is derived from ``(signal_type, subject, change_summary)``:
    the same change recorded from different sources lands on the same row.
    This is a CANDIDATE layer only - it never confirms an event, never reuses
    the legacy 2B ``event`` table, and never carries raw payloads, URLs,
    credentials or source-specific metadata.
    """

    signal_type: str
    subject: str
    change_summary: str
    affected_audience: str
    work_impact_hypothesis: str
    research_priority: int
    missing_evidence: Tuple[str, ...] = ()
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    id: str = ""

    def __post_init__(self) -> None:
        if self.signal_type not in SIGNAL_TYPES:
            raise ValueError("invalid signal_type: %r" % self.signal_type)
        for name in (
            "subject",
            "change_summary",
            "affected_audience",
            "work_impact_hypothesis",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError("%s must be a non-empty string" % name)
            object.__setattr__(self, name, value.strip())
        if (
            isinstance(self.research_priority, bool)
            or not isinstance(self.research_priority, int)
            or not 0 <= self.research_priority <= 100
        ):
            raise ValueError("research_priority must be an integer between 0 and 100")
        object.__setattr__(self, "missing_evidence", as_tuple(self.missing_evidence))
        if not all(
            isinstance(code, str) and code.strip() for code in self.missing_evidence
        ):
            raise TypeError("missing_evidence must contain non-empty strings")
        if self.id == "":
            object.__setattr__(
                self,
                "id",
                event_candidate_entity_id(
                    self.signal_type, self.subject, self.change_summary
                ),
            )
        if self.created_at is None:
            object.__setattr__(self, "created_at", now_utc())
        if self.updated_at is None:
            object.__setattr__(self, "updated_at", self.created_at)
        _normalize_datetimes(self, ("created_at", "updated_at"))


@dataclass(frozen=True)
class EventCandidateSourceRef:
    """A reference from an EventCandidate to a source-specific candidate.

    ``ref_id`` is a TEXT pointer into the source-specific table (no
    cross-source FK, by design). ``ref_label`` is a safe, pre-validated
    human-readable label - never a URL, payload or credential.
    """

    event_candidate_id: str
    source_kind: str
    ref_id: str
    ref_label: str
    created_at: Optional[datetime] = None
    id: str = ""

    def __post_init__(self) -> None:
        if not self.event_candidate_id:
            raise ValueError("event_candidate_id must not be empty")
        if self.source_kind not in EVENT_CANDIDATE_SOURCE_KINDS:
            raise ValueError("invalid source_kind: %r" % self.source_kind)
        if not isinstance(self.ref_id, str) or not self.ref_id.strip():
            raise ValueError("ref_id must be a non-empty string")
        object.__setattr__(self, "ref_id", self.ref_id.strip())
        if not isinstance(self.ref_label, str) or not self.ref_label.strip():
            raise ValueError("ref_label must be a non-empty string")
        object.__setattr__(self, "ref_label", self.ref_label.strip())
        if self.id == "":
            object.__setattr__(
                self,
                "id",
                event_candidate_source_ref_entity_id(
                    self.event_candidate_id, self.source_kind, self.ref_id
                ),
            )
        if self.created_at is None:
            object.__setattr__(self, "created_at", now_utc())
        _normalize_datetimes(self, ("created_at",))


# --------------------------------------------------------------------------- #
# Research dossier + editorial decision (phases 2D/2E)
# --------------------------------------------------------------------------- #
DOSSIER_STATUSES = ("draft", "complete", "partial", "failed")
FACT_KINDS = ("fact", "official_claim", "unknown", "contradiction")
FACT_SOURCE_KINDS = (
    "github_release",
    "github_readme",
    "github_metadata",
    "official_page",
    "manual",
)
EDITORIAL_DECISIONS = ("ready_to_write", "needs_testing", "watch", "reject")
EDITORIAL_POLICY_VERSION = "editorial-v1"


def research_dossier_entity_id(event_candidate_id: str, bundle_hash: str) -> str:
    """Deterministic dossier id: same content for the same candidate."""
    return deterministic_id("research-dossier", event_candidate_id, bundle_hash)


def research_fact_entity_id(
    dossier_id: str, kind: str, source_url: Optional[str], text: str
) -> str:
    return deterministic_id("research-fact", dossier_id, kind, source_url or "", text)


def editorial_decision_entity_id(
    dossier_id: str, policy_version: str, input_hash: str
) -> str:
    return deterministic_id("editorial-decision", dossier_id, policy_version, input_hash)


def _require_json_text_array(value: Any, name: str) -> Tuple[str, ...]:
    """Validate a tuple of non-empty strings (stored as a JSON array)."""
    items = as_tuple(value)
    if not all(isinstance(item, str) and item.strip() for item in items):
        raise TypeError("%s must contain non-empty strings" % name)
    return tuple(item.strip() for item in items)


def _validate_evidence_url(value: Optional[str], name: str) -> Optional[str]:
    """First-party evidence URLs: https only, no credentials, no control chars.

    This is deliberately looser than the repository IDENTITY allow-list: an
    evidence URL may point at an official page anywhere, but it must be a
    clean https URL with no embedded credentials. Untrusted user metadata
    (e.g. GitHub ``homepage``) still never reaches storage; evidence URLs
    come from first-party API responses and are syntax-checked here.
    """
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("%s must be a non-empty https URL or None" % name)
    text = value.strip()
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text):
        raise ValueError("%s contains control characters" % name)
    from urllib.parse import urlparse

    try:
        parsed = urlparse(text)
        port = parsed.port
    except (TypeError, ValueError):
        raise ValueError("%s is not a valid URL" % name)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("%s must be a clean https URL" % name)
    return text


@dataclass(frozen=True)
class ResearchDossier:
    """One evidence-backed research record for an event candidate.

    The identity is ``(event_candidate_id, bundle_hash)``: new evidence
    yields a new revision while history stays auditable. All prose fields
    carry only quoted first-party facts plus explicit unknown/limit lists -
    never fabricated conclusions.
    """

    event_candidate_id: str
    summary_judgment: str
    timeline: Tuple[str, ...]
    target_audience: str
    job_to_be_done: str
    limits_unknowns: Tuple[str, ...]
    forbidden_claims: Tuple[str, ...]
    needs_testing: bool
    test_plan: Tuple[str, ...]
    status: str = "complete"
    bundle_hash: str = ""
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    id: str = ""

    def __post_init__(self) -> None:
        if not self.event_candidate_id:
            raise ValueError("event_candidate_id must not be empty")
        if self.status not in DOSSIER_STATUSES:
            raise ValueError("invalid dossier status: %r" % self.status)
        for name in ("summary_judgment", "target_audience", "job_to_be_done"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError("%s must be a non-empty string" % name)
            object.__setattr__(self, name, value.strip())
        object.__setattr__(self, "timeline", _require_json_text_array(self.timeline, "timeline"))
        object.__setattr__(self, "limits_unknowns", _require_json_text_array(self.limits_unknowns, "limits_unknowns"))
        object.__setattr__(self, "forbidden_claims", _require_json_text_array(self.forbidden_claims, "forbidden_claims"))
        object.__setattr__(self, "test_plan", _require_json_text_array(self.test_plan, "test_plan"))
        if not isinstance(self.needs_testing, bool):
            raise TypeError("needs_testing must be a bool")
        if self.bundle_hash == "":
            # Computed by the pipeline from the canonical fact set; a bare
            # placeholder keeps direct construction possible for tests.
            object.__setattr__(self, "bundle_hash", deterministic_id("dossier", self.event_candidate_id))
        if not isinstance(self.bundle_hash, str) or len(self.bundle_hash) != 64:
            raise ValueError("bundle_hash must be a 64-character hex string")
        if self.id == "":
            object.__setattr__(
                self,
                "id",
                research_dossier_entity_id(self.event_candidate_id, self.bundle_hash),
            )
        if self.created_at is None:
            object.__setattr__(self, "created_at", now_utc())
        if self.updated_at is None:
            object.__setattr__(self, "updated_at", self.created_at)
        _normalize_datetimes(self, ("created_at", "updated_at"))


@dataclass(frozen=True)
class ResearchFact:
    """One auditable fact bound to a dossier (quoted, never fabricated)."""

    dossier_id: str
    kind: str
    text: str
    source_kind: str
    source_url: Optional[str] = None
    created_at: Optional[datetime] = None
    id: str = ""

    def __post_init__(self) -> None:
        if not self.dossier_id:
            raise ValueError("dossier_id must not be empty")
        if self.kind not in FACT_KINDS:
            raise ValueError("invalid fact kind: %r" % self.kind)
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("fact text must be a non-empty string")
        object.__setattr__(self, "text", self.text.strip())
        if self.source_kind not in FACT_SOURCE_KINDS:
            raise ValueError("invalid fact source_kind: %r" % self.source_kind)
        object.__setattr__(
            self, "source_url", _validate_evidence_url(self.source_url, "source_url")
        )
        if self.id == "":
            object.__setattr__(
                self,
                "id",
                research_fact_entity_id(
                    self.dossier_id, self.kind, self.source_url, self.text
                ),
            )
        if self.created_at is None:
            object.__setattr__(self, "created_at", now_utc())
        _normalize_datetimes(self, ("created_at",))


@dataclass(frozen=True)
class EditorialDecision:
    """One deterministic editorial decision for a dossier revision."""

    dossier_id: str
    policy_version: str
    input_hash: str
    decision: str
    reason_codes: Tuple[str, ...]
    decided_at: Optional[datetime] = None
    id: str = ""

    def __post_init__(self) -> None:
        if not self.dossier_id or not self.policy_version:
            raise ValueError("dossier_id and policy_version must not be empty")
        if not isinstance(self.input_hash, str) or len(self.input_hash) != 64:
            raise ValueError("input_hash must be a 64-character hex string")
        try:
            int(self.input_hash, 16)
        except ValueError as exc:
            raise ValueError("input_hash must be a hex string") from exc
        if self.decision not in EDITORIAL_DECISIONS:
            raise ValueError("invalid editorial decision: %r" % self.decision)
        object.__setattr__(self, "reason_codes", as_tuple(self.reason_codes))
        if not all(isinstance(code, str) and code.strip() for code in self.reason_codes):
            raise TypeError("reason_codes must contain non-empty strings")
        if self.id == "":
            object.__setattr__(
                self,
                "id",
                editorial_decision_entity_id(
                    self.dossier_id, self.policy_version, self.input_hash
                ),
            )
        if self.decided_at is None:
            object.__setattr__(self, "decided_at", now_utc())
        _normalize_datetimes(self, ("decided_at",))
