"""Phase 2C1 candidate qualification pipeline (deterministic, offline, no LLM).

Answers exactly one question per discovery:

    "这个候选是否值得继续花成本读取 README、Release 等资料？"

Decisions are limited to ``research | watch | reject``. A repository is NOT an
Event on this path, no A-F MaterialPack is produced, and no single float score
is computed - every outcome is explained by stable reason codes plus a fixed
missing-evidence list.

Identity model (v2):

* ``Candidate`` - stable global identity per ``(source, canonical_key)``;
* ``CandidateDiscovery`` - one provenance row per
  ``(candidate, week, scope, lane, raw_signal)``;
* ``CandidateAssessment`` - deterministic decision revision per
  ``(discovery, input_hash, policy_version)``.

Data selection goes through the observation-attribution path
(``raw_signal_observation -> source_run -> collection_run -> raw_signal``) so
scopes never mix.

Safety:

* strict whitelist parsing of the raw payload - unknown fields never reach
  ``attributes``;
* the repository identity URL must be HTTPS on the safe-host allow-list
  (github.com in production), without credentials or non-standard ports;
* ``homepage`` is untrusted user-provided metadata: it never gates RESEARCH,
  an unsafe homepage never rejects the candidate, and no homepage URL is ever
  stored in attributes or rendered - only a ``homepage_present`` flag plus a
  reason code;
* control characters, hidden HTML, script markers and prompt-injection
  markers are quarantined/rejected and never rendered;
* numbers must be non-negative ints, booleans must be real bools, timestamps
  must be tz-aware ISO;
* nothing logged or returned carries URLs, titles, scope values or payloads.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, List, Mapping, Optional, Tuple
from urllib.parse import urlparse

from ..domain.models import (
    CANDIDATE_LANES,
    Candidate,
    CandidateAssessment,
    CandidateDiscovery,
    now_utc,
    sha256_hex,
    validate_scope_key,
)
from ..storage.candidate_repositories import (
    CandidateAssessmentRepository,
    CandidateDiscoveryRepository,
    CandidateRepository,
)
from ..storage.material_repositories import select_github_raw_signals
from ..storage.sqlite import StorageError


class QualifyError(Exception):
    """Raised when qualification cannot proceed (configuration / data)."""


POLICY_VERSION = "candidate-gate-v2"
# Phase 2C2-C: used when a relation resolver is supplied (ecosystem lane with
# metadata relation evidence). Absent a resolver the behavior is byte-for-byte
# the v2 gate.
POLICY_VERSION_V3 = "candidate-gate-v3"
TRIGGER_KIND = "repository_snapshot"
TRIGGER_SUMMARY = "发现仓库快照，但尚未确认 Release、Launch 或重大变化。"

# Transparent integer constants - no composite float score exists.
SUBSTANTIVE_DESCRIPTION_MIN = 40
ACTIVE_PUSH_MAX_DAYS = 45
EMERGING_MAX_AGE_DAYS = 180
MATURE_MIN_STARS = 100

# Fixed missing evidence for every metadata-only assessment: a search snapshot
# alone can never establish an event, README contents, releases, a working
# artifact or testability.
MISSING_EVIDENCE = (
    "specific_event",
    "readme",
    "latest_release",
    "previous_release_or_changelog",
    "working_artifact_or_demo",
    "testability",
)

# AI-agent relevance keywords checked against the cleaned full_name /
# description / topics. A bare "ai" is deliberately NOT sufficient.
AGENT_RELEVANCE_KEYWORDS = (
    "agent",
    "agentic",
    "multi-agent",
    "coding-agent",
    "coding assistant",
    "mcp",
    "harness",
    "copilot",
)

# Stable reason codes.
R_FORK = "repository_is_fork"
R_ARCHIVED = "repository_archived"
R_DISABLED = "repository_disabled"
R_TEMPLATE = "repository_is_template"
R_MISSING_DESC = "missing_description"
R_UNSAFE_URL = "unsafe_url"
R_INVALID_META = "invalid_metadata"
R_UNTRUSTED = "untrusted_content"

R_SUBSTANTIVE_DESC = "substantive_description"
R_THIN_DESC = "thin_description"
R_AGENT_RELEVANT = "agent_relevance_match"
R_NO_AGENT_RELEVANCE = "no_agent_relevance"
R_PUSH_ACTIVE = "recent_push"
R_PUSH_STALE = "stale_push"
R_RECENT_CREATED = "recent_creation"
R_TOO_OLD = "older_than_max_age"
R_STARS_MET = "mature_stars_threshold_met"
R_STARS_LOW = "mature_stars_below_threshold"
R_MISSING_ECO = "missing_ecosystem_relation"
R_ECO_RELATED = "ecosystem_relation_match"

R_HOME_PRESENT = "homepage_present_unverified"
R_HOME_UNSAFE = "homepage_ignored_unsafe"

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
_FULL_NAME_MAX = 256
_LANG_MAX = 64

_ALLOWED_FIELDS = frozenset(
    (
        "id", "full_name", "html_url", "description", "topics", "language",
        "stargazers_count", "forks_count", "created_at", "updated_at",
        "pushed_at", "homepage", "fork", "archived", "disabled", "is_template",
    )
)


@dataclass(frozen=True)
class QualifiedCandidate:
    """A candidate, its discovery and assessment, for downstream rendering."""

    candidate: Candidate
    discovery: CandidateDiscovery
    assessment: CandidateAssessment
    # Phase 2C2-C: ecosystem relation match (target/kind/field), None for
    # other lanes or when no resolver was supplied.
    relation: Optional[Any] = None


@dataclass(frozen=True)
class QualifyResult:
    """Safe, payload-free summary of one qualification run."""

    processed: int
    candidates_created: int
    candidates_updated: int
    discoveries_created: int
    discoveries_existing: int
    assessments_created: int
    research: int
    watch: int
    rejected: int
    quarantined: int
    qualified: Tuple[QualifiedCandidate, ...] = ()


def _has_control_chars(text: str) -> bool:
    return any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text)


def _clean_text(value: Any, max_len: int) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    if _has_control_chars(value):
        return None
    text = value.strip()
    if not text:
        return None
    return text[:max_len]


def _is_injection(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _INJECTION_MARKERS)


def _is_safe_https_url(value: Any, safe_hosts: frozenset) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = urlparse(value)
        # ``parsed.port`` raises ValueError for non-numeric or out-of-range
        # ports (e.g. ``:bad`` / ``:65536``); evaluate it inside the guard.
        port = parsed.port
    except (TypeError, ValueError):
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname in safe_hosts
        and parsed.username is None
        and parsed.password is None
        and port is None
    )


def _is_safe_external_homepage(value: Any) -> bool:
    """Syntax-only check for an external (non-identity) homepage URL.

    Homepage is untrusted, non-evidence metadata: it never gates the decision
    and an unsafe value never rejects the candidate. This helper only decides
    whether the value *looks like* a clean external HTTPS link, so it can be
    recorded as ``homepage_present`` + ``homepage_safe``. It deliberately does
    NOT require the host to be on any identity allow-list (``safe_hosts``) -
    a homepage may point anywhere - but it does reject dangerous schemes
    (``javascript:`` etc.), credentials, non-standard ports and control chars.
    """
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text or _has_control_chars(text):
        return False
    try:
        parsed = urlparse(text)
        # ``parsed.port`` raises ValueError for non-numeric or out-of-range
        # ports (e.g. ``:bad`` / ``:65536``); evaluate it inside the guard.
        port = parsed.port
    except (TypeError, ValueError):
        return False
    return (
        parsed.scheme == "https"
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
        and port is None
    )


def _parse_iso(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or _has_control_chars(text):
        return None
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _parse_bool(raw: Any) -> Tuple[Optional[bool], bool]:
    """Return (value, valid): booleans must be real bools."""
    if raw is None:
        return None, True
    if isinstance(raw, bool):
        return raw, True
    return None, False


def _age_days(moment: Optional[datetime], clock: datetime) -> Optional[int]:
    if moment is None:
        return None
    return (clock - moment).days


@dataclass
class _ParsedRepo:
    """Cleaned whitelist-only snapshot plus per-field validity flags."""

    repo_id: str
    full_name: str
    html_url: str
    canonical_key: str
    attributes: dict
    # Gate inputs.
    fork: Optional[bool]
    archived: Optional[bool]
    disabled: Optional[bool]
    is_template: Optional[bool]
    description: Optional[str]
    topics: Tuple[str, ...]
    stars: Optional[int]
    forks: Optional[int]
    created_at: Optional[datetime]
    updated_at: Optional[datetime]
    pushed_at: Optional[datetime]
    homepage_safe: bool
    homepage_present: bool
    # Safety outcomes.
    untrusted: bool = False
    invalid_metadata: bool = False


def _parse_repo(payload: Mapping[str, Any], safe_hosts: frozenset) -> _ParsedRepo:
    """Strict whitelist parse of a GitHub raw payload.

    Raises :class:`QualifyError` when identity-essential fields (id, full_name,
    html_url) are missing, unsafe or poisoned - the record is then quarantined
    (no Candidate row can be constructed safely). Per-field problems that do
    not break the identity are recorded as reject reasons instead. Unknown
    fields are dropped: ``attributes`` only ever contains reviewed keys.
    """
    raw_id = payload.get("id")
    if isinstance(raw_id, bool) or not isinstance(raw_id, int):
        raise QualifyError("invalid repository id")
    repo_id = str(raw_id)

    full_name = _clean_text(payload.get("full_name"), _FULL_NAME_MAX)
    if not full_name or _is_injection(full_name):
        raise QualifyError("invalid full_name")

    html_url = payload.get("html_url")
    if not isinstance(html_url, str) or _is_injection(html_url):
        raise QualifyError("invalid html_url")
    if not _is_safe_https_url(html_url, safe_hosts):
        # The identity URL cannot be stored safely at all -> quarantine.
        raise QualifyError("unsafe html_url")

    untrusted = False
    invalid_metadata = False

    description = _clean_text(payload.get("description"), _DESC_MAX)
    if description is not None and _is_injection(description):
        # Poisoned description: never stored or rendered.
        untrusted = True
        description = None

    raw_topics = payload.get("topics")
    topics: Tuple[str, ...] = ()
    if raw_topics is not None:
        if not isinstance(raw_topics, list):
            invalid_metadata = True
        else:
            cleaned = []
            for item in raw_topics:
                if not isinstance(item, str):
                    # Non-string topic: type violation -> candidate rejected.
                    invalid_metadata = True
                    continue
                if _has_control_chars(item) or _is_injection(item):
                    # Malicious/injected topic: never stored or rendered, and
                    # the whole candidate is rejected. Silently dropping the
                    # topic and continuing would let poisoned content slip
                    # into a RESEARCH decision.
                    untrusted = True
                    continue
                topic = item.strip()
                if not topic:
                    continue
                if len(cleaned) >= _TOPIC_MAX_COUNT:
                    continue
                cleaned.append(topic[:_TOPIC_MAX_LEN])
            topics = tuple(cleaned)

    language = _clean_text(payload.get("language"), _LANG_MAX)
    if language is not None and _is_injection(language):
        untrusted = True
        language = None

    def _non_neg_int(raw: Any) -> Optional[int]:
        if raw is None:
            return None
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
            return None
        return raw

    stars = payload.get("stargazers_count")
    forks = payload.get("forks_count")
    if stars is not None and _non_neg_int(stars) is None:
        invalid_metadata = True
        stars = None
    if forks is not None and _non_neg_int(forks) is None:
        invalid_metadata = True
        forks = None

    created_at = _parse_iso(payload.get("created_at"))
    if payload.get("created_at") is not None and created_at is None:
        invalid_metadata = True
    updated_at = _parse_iso(payload.get("updated_at"))
    if payload.get("updated_at") is None or updated_at is None:
        # updated_at is essential metadata; invalid -> reject reason.
        invalid_metadata = True
    pushed_at = _parse_iso(payload.get("pushed_at"))
    if payload.get("pushed_at") is not None and pushed_at is None:
        invalid_metadata = True

    # homepage: untrusted, non-evidence user metadata. It never gates the
    # decision, an unsafe value never rejects the candidate, and the URL is
    # never stored or rendered. Only two booleans survive: ``homepage_present``
    # (the raw field was non-empty) and ``homepage_safe`` (it looks like a
    # clean external HTTPS link). Neither flag sets ``untrusted`` or
    # ``invalid_metadata``, so a malicious homepage cannot cause a REJECT.
    homepage_raw = payload.get("homepage")
    homepage_present = False
    homepage_safe = False
    if homepage_raw is not None:
        if isinstance(homepage_raw, str):
            if homepage_raw.strip():
                homepage_present = True
                homepage_safe = _is_safe_external_homepage(homepage_raw)
        else:
            # Non-string homepage: presence recorded, never a reject reason.
            homepage_present = True
            homepage_safe = False

    fork, fork_ok = _parse_bool(payload.get("fork"))
    archived, archived_ok = _parse_bool(payload.get("archived"))
    disabled, disabled_ok = _parse_bool(payload.get("disabled"))
    is_template, template_ok = _parse_bool(payload.get("is_template"))
    if not (fork_ok and archived_ok and disabled_ok and template_ok):
        invalid_metadata = True

    attributes: dict = {
        "id": repo_id,
        "full_name": full_name,
        "html_url": html_url,
        "homepage_present": homepage_present,
    }
    if description is not None:
        attributes["description"] = description
    if topics:
        attributes["topics"] = list(topics)
    if language is not None:
        attributes["language"] = language
    if stars is not None:
        attributes["stargazers_count"] = stars
    if forks is not None:
        attributes["forks_count"] = forks
    if created_at is not None:
        attributes["created_at"] = created_at.isoformat()
    if updated_at is not None:
        attributes["updated_at"] = updated_at.isoformat()
    if pushed_at is not None:
        attributes["pushed_at"] = pushed_at.isoformat()

    return _ParsedRepo(
        repo_id=repo_id,
        full_name=full_name,
        html_url=html_url,
        canonical_key="github:repository:" + repo_id,
        attributes=attributes,
        fork=fork,
        archived=archived,
        disabled=disabled,
        is_template=is_template,
        description=description,
        topics=topics,
        stars=stars,
        forks=forks,
        created_at=created_at,
        updated_at=updated_at,
        pushed_at=pushed_at,
        homepage_safe=homepage_safe,
        homepage_present=homepage_present,
        untrusted=untrusted,
        invalid_metadata=invalid_metadata,
    )


def _is_agent_relevant(parsed: _ParsedRepo) -> bool:
    """Match cleaned full_name / description / topics against agent keywords.

    A bare "ai" is not a sufficient match by design.
    """
    haystack = " ".join(
        (
            parsed.full_name or "",
            parsed.description or "",
            " ".join(parsed.topics),
        )
    ).lower()
    return any(keyword in haystack for keyword in AGENT_RELEVANCE_KEYWORDS)


def _gate(
    parsed: _ParsedRepo, lane: str, clock: datetime, relation: Optional[Any] = None
) -> Tuple[str, Tuple[str, ...]]:
    """Apply the common filters, then the lane-aware qualification gate.

    Returns ``(decision, reason_codes)``. No composite float score exists by
    design: the decision is fully explained by the reason codes.

    ``relation`` (phase 2C2-C) is a metadata relation match produced by an
    ecosystem resolver (duck-typed: ``target`` / ``kind`` / ``field``). It is
    only consulted for the ecosystem lane; every other lane ignores it, so
    the v2 gate results are unchanged when no resolver is supplied.
    """
    # --- common safety / junk filters -> REJECT ---------------------------
    reject_codes: List[str] = []
    if parsed.fork is True:
        reject_codes.append(R_FORK)
    if parsed.archived is True:
        reject_codes.append(R_ARCHIVED)
    if parsed.disabled is True:
        reject_codes.append(R_DISABLED)
    if parsed.is_template is True:
        reject_codes.append(R_TEMPLATE)
    if not parsed.description:
        reject_codes.append(R_UNTRUSTED if parsed.untrusted else R_MISSING_DESC)
    if parsed.invalid_metadata:
        reject_codes.append(R_INVALID_META)
    if parsed.untrusted and R_UNTRUSTED not in reject_codes:
        reject_codes.append(R_UNTRUSTED)
    if reject_codes:
        return "reject", tuple(dict.fromkeys(reject_codes))

    # Shared facts for the lane gates.
    substantive = len(parsed.description) >= SUBSTANTIVE_DESCRIPTION_MIN
    agent_relevant = _is_agent_relevant(parsed)
    push_age = _age_days(parsed.pushed_at, clock)
    push_active = push_age is not None and 0 <= push_age <= ACTIVE_PUSH_MAX_DAYS
    created_age = _age_days(parsed.created_at, clock)

    codes: List[str] = [R_SUBSTANTIVE_DESC if substantive else R_THIN_DESC]
    if agent_relevant:
        codes.append(R_AGENT_RELEVANT)
    else:
        codes.append(R_NO_AGENT_RELEVANCE)
    codes.append(R_PUSH_ACTIVE if push_active else R_PUSH_STALE)

    if lane == "watchlist":
        # Identity is vouched for by a future Discovery Policy / human list;
        # this phase still requires confirmed recent activity.
        if substantive and push_active:
            return "research", tuple(dict.fromkeys(codes))
        return "watch", tuple(dict.fromkeys(codes))

    if lane == "mature":
        stars_met = parsed.stars is not None and parsed.stars >= MATURE_MIN_STARS
        codes.append(R_STARS_MET if stars_met else R_STARS_LOW)
        if substantive and agent_relevant and stars_met and push_active:
            return "research", tuple(dict.fromkeys(codes))
        return "watch", tuple(dict.fromkeys(codes))

    if lane == "emerging":
        young = created_age is not None and 0 <= created_age <= EMERGING_MAX_AGE_DAYS
        if young:
            codes.append(R_RECENT_CREATED)
        else:
            codes.append(R_TOO_OLD)
        # No minimum stars: a 1-star agent project can still qualify.
        if substantive and agent_relevant and young and push_active:
            return "research", tuple(dict.fromkeys(codes))
        return "watch", tuple(dict.fromkeys(codes))

    # ecosystem: metadata-only evidence can never establish a relation by
    # itself. With a resolver-supplied relation match (full_name/description/
    # topics) the candidate still needs substantive description + recent push
    # to reach RESEARCH (policy candidate-gate-v3).
    if relation is not None:
        if substantive and push_active:
            codes.append(R_ECO_RELATED)
            return "research", tuple(dict.fromkeys(codes))
        return "watch", tuple(dict.fromkeys(codes))
    codes.append(R_MISSING_ECO)
    return "watch", tuple(dict.fromkeys(codes))


def _input_hash(attributes: Mapping[str, Any]) -> str:
    return sha256_hex(
        json.dumps(attributes, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    )


def qualify_github(
    conn,
    week_key: str,
    scope_key: str,
    lane: str,
    limit: int,
    *,
    clock: Optional[datetime] = None,
    safe_hosts: frozenset = _GITHUB_HOSTS,
    relation_resolver: Optional[Callable[[_ParsedRepo], Optional[Any]]] = None,
) -> QualifyResult:
    """Qualify the latest GitHub snapshot per repository for a discovery context.

    ``conn`` is an open connection; the caller owns the transaction. Raises
    :class:`StorageError` on database failure (caller rolls back).

    ``relation_resolver`` (phase 2C2-C) turns a parsed repository into an
    ecosystem relation match; when supplied, assessments use policy
    ``candidate-gate-v3``, otherwise the original ``candidate-gate-v2``.
    """
    validate_scope_key(scope_key)
    if lane not in CANDIDATE_LANES:
        raise QualifyError("invalid lane")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise QualifyError("limit must be between 1 and 100")

    policy_version = POLICY_VERSION_V3 if relation_resolver is not None else POLICY_VERSION
    ts = clock if clock is not None else now_utc()
    raw_rows = select_github_raw_signals(conn, week_key, scope_key, limit)

    cand_repo = CandidateRepository(conn)
    disc_repo = CandidateDiscoveryRepository(conn)
    assess_repo = CandidateAssessmentRepository(conn)

    processed = 0
    candidates_created = 0
    candidates_updated = 0
    discoveries_created = 0
    discoveries_existing = 0
    assessments_created = 0
    research = 0
    watch = 0
    rejected = 0
    quarantined = 0
    qualified: List[QualifiedCandidate] = []

    for row in raw_rows:
        processed += 1
        try:
            parsed = _parse_repo(row["payload"], safe_hosts)
        except QualifyError:
            quarantined += 1
            continue

        observed_at = datetime.fromisoformat(row["collected_at"])

        # --- stable global Candidate identity (monotonic refresh) ---------
        candidate = Candidate(
            source="github",
            canonical_key=parsed.canonical_key,
            title=parsed.full_name,
            url=parsed.html_url,
            first_seen_at=observed_at,
            last_seen_at=observed_at,
            created_at=ts,
            updated_at=ts,
        )
        existed = cand_repo.get_by_identity("github", parsed.canonical_key) is not None
        stored_candidate = cand_repo.upsert(candidate)
        if existed:
            candidates_updated += 1
        else:
            candidates_created += 1

        # --- discovery provenance for this (week, scope, lane, snapshot) --
        discovery = CandidateDiscovery(
            candidate_id=stored_candidate.id,
            week_key=week_key,
            scope_key=scope_key,
            lane=lane,
            raw_signal_id=row["id"],
            observed_at=observed_at,
            created_at=ts,
        )
        discovery_existed = disc_repo.exists(discovery.id)
        stored_discovery = disc_repo.insert_or_get(discovery)
        if discovery_existed:
            discoveries_existing += 1
        else:
            discoveries_created += 1

        # --- lane-aware deterministic assessment --------------------------
        relation = relation_resolver(parsed) if relation_resolver is not None else None
        decision, reason_codes = _gate(parsed, lane, ts, relation)
        extra_codes: List[str] = []
        if parsed.homepage_present and parsed.homepage_safe:
            extra_codes.append(R_HOME_PRESENT)
        elif parsed.homepage_present:
            extra_codes.append(R_HOME_UNSAFE)
        if decision != "reject":
            reason_codes = tuple(dict.fromkeys(tuple(reason_codes) + tuple(extra_codes)))

        assessment = CandidateAssessment(
            candidate_discovery_id=stored_discovery.id,
            policy_version=policy_version,
            input_hash=_input_hash(parsed.attributes),
            decision=decision,
            trigger_kind=TRIGGER_KIND,
            trigger_summary=TRIGGER_SUMMARY,
            reason_codes=reason_codes,
            missing_evidence=MISSING_EVIDENCE,
            attributes=parsed.attributes,
            assessed_at=ts,
        )
        if not assess_repo.exists(assessment.id):
            assessments_created += 1
        assess_repo.insert_or_get(assessment)

        if decision == "research":
            research += 1
        elif decision == "watch":
            watch += 1
        else:
            rejected += 1

        # Only RESEARCH is eligible for the optional debug Markdown.
        if decision == "research":
            qualified.append(
                QualifiedCandidate(
                    candidate=stored_candidate,
                    discovery=stored_discovery,
                    assessment=assessment,
                    relation=relation,
                )
            )

    return QualifyResult(
        processed=processed,
        candidates_created=candidates_created,
        candidates_updated=candidates_updated,
        discoveries_created=discoveries_created,
        discoveries_existing=discoveries_existing,
        assessments_created=assessments_created,
        research=research,
        watch=watch,
        rejected=rejected,
        quarantined=quarantined,
        qualified=tuple(qualified),
    )
