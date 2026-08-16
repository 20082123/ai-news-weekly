"""Phase 2C2 GitHub Discovery Policy Catalog (immutable, versioned, in-code).

A :class:`GitHubDiscoveryPolicy` is a plain Python frozen object - there is no
YAML, no DSL and no plugin system. Policies live in :data:`POLICY_CATALOG` and
are addressed by their ``id`` (for example ``emerging-v1``). Changing the
semantics of a probe REQUIRES bumping its scope version (and therefore its
``scope_key``), because the scope/spec binding (migration 0005) refuses a
scope that would run under a different ``spec_hash``.

Budget table (agreed limits, see 2C2 plan):

=====================  ===============  ================
Policy                 candidate_limit  research_budget
=====================  ===============  ================
watchlist-v1           20               5
mature-v1              50               5
emerging-v1            100              8
ecosystem-v1           50               5
=====================  ===============  ================

Safety rules inherited from 2B1/2C1:

* the raw GitHub query lives only in memory inside the probe spec (it is the
  only thing that can build the HTTPS request); only ``spec_hash`` is ever
  persisted or compared;
* search specs keep ``per_page <= 25`` and ``max_pages <= 3``;
* date qualifiers inside queries are FIXED constants; moving a time window is
  a semantic change and must bump the scope version.

``watchlist-v1`` and ``ecosystem-v1`` carry DRAFT first-user lists
(2026-08-16, not yet confirmed): the engineering is complete and the entries
are concentrated in one clearly-marked section at the bottom of this file so
the first user can swap them without touching anything else.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Tuple

from ..domain.models import (
    GITHUB_DISCOVERY_LANES,
    GITHUB_PROBE_KINDS,
    sha256_hex,
    validate_scope_key,
)
from .relation import EcosystemTargetSpec

# probe_id / policy_id share the scope-key naming discipline: lowercase,
# stable alias, never a query, URL, path or credential.
_POLICY_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

_SEARCH_SPEC_KEYS = frozenset({"query", "sort", "order", "per_page", "max_pages"})
_WATCHLIST_SPEC_KEYS = frozenset({"full_name"})
_ECOSYSTEM_SPEC_KEYS = frozenset({"target", "aliases"})

_FULL_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class DiscoveryPolicyError(ValueError):
    """Raised when a discovery policy definition is invalid."""


def _has_control_characters(text: str) -> bool:
    return any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _require_label(label: str, name: str) -> None:
    if not isinstance(label, str) or _POLICY_ID_RE.fullmatch(label) is None:
        raise DiscoveryPolicyError("invalid %s" % name)


def _non_negative_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DiscoveryPolicyError("%s must be a non-negative integer" % name)
    return value


def _bounded_int(value: Any, low: int, high: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DiscoveryPolicyError("%s must be an integer" % name)
    if not (low <= value <= high):
        raise DiscoveryPolicyError("%s must be between %d and %d" % (name, low, high))
    return value


def _validate_search_spec(spec: Mapping[str, Any]) -> Mapping[str, Any]:
    """Validate a GitHub repository-search probe spec (whitelist only)."""
    if not isinstance(spec, Mapping):
        raise DiscoveryPolicyError("search spec must be a mapping")
    extra = set(spec.keys()) - _SEARCH_SPEC_KEYS
    if extra:
        raise DiscoveryPolicyError("search spec contains disallowed keys")
    query = spec.get("query")
    if (
        not isinstance(query, str)
        or not 1 <= len(query.strip()) <= 256
        or _has_control_characters(query)
    ):
        raise DiscoveryPolicyError("invalid search query")
    if spec.get("sort") not in ("updated", "stars"):
        raise DiscoveryPolicyError("sort must be 'updated' or 'stars'")
    if spec.get("order") not in ("asc", "desc"):
        raise DiscoveryPolicyError("order must be 'asc' or 'desc'")
    per_page = spec.get("per_page")
    if isinstance(per_page, bool) or not isinstance(per_page, int) or not 1 <= per_page <= 25:
        raise DiscoveryPolicyError("per_page must be between 1 and 25")
    max_pages = spec.get("max_pages")
    if isinstance(max_pages, bool) or not isinstance(max_pages, int) or not 1 <= max_pages <= 3:
        raise DiscoveryPolicyError("max_pages must be between 1 and 3")
    return {
        "query": query,
        "sort": spec["sort"],
        "order": spec["order"],
        "per_page": per_page,
        "max_pages": max_pages,
    }


def _validate_watchlist_spec(spec: Mapping[str, Any]) -> Mapping[str, Any]:
    """Validate a watchlist direct-snapshot probe spec (single repository)."""
    if not isinstance(spec, Mapping):
        raise DiscoveryPolicyError("watchlist spec must be a mapping")
    extra = set(spec.keys()) - _WATCHLIST_SPEC_KEYS
    if extra:
        raise DiscoveryPolicyError("watchlist spec contains disallowed keys")
    full_name = spec.get("full_name")
    if (
        not isinstance(full_name, str)
        or _FULL_NAME_RE.fullmatch(full_name.strip()) is None
        or _has_control_characters(full_name)
    ):
        raise DiscoveryPolicyError("invalid watchlist full_name")
    return {"full_name": full_name.strip()}


def _validate_ecosystem_spec(spec: Mapping[str, Any]) -> Mapping[str, Any]:
    """Validate an ecosystem target probe spec (core project + strong aliases)."""
    if not isinstance(spec, Mapping):
        raise DiscoveryPolicyError("ecosystem spec must be a mapping")
    extra = set(spec.keys()) - _ECOSYSTEM_SPEC_KEYS
    if extra:
        raise DiscoveryPolicyError("ecosystem spec contains disallowed keys")
    target = spec.get("target")
    if (
        not isinstance(target, str)
        or _FULL_NAME_RE.fullmatch(target.strip()) is None
        or _has_control_characters(target)
    ):
        raise DiscoveryPolicyError("invalid ecosystem target")
    aliases = spec.get("aliases", [])
    if not isinstance(aliases, list):
        raise DiscoveryPolicyError("ecosystem aliases must be a list")
    cleaned = []
    for alias in aliases:
        if (
            not isinstance(alias, str)
            or not alias.strip()
            or _has_control_characters(alias)
        ):
            raise DiscoveryPolicyError("invalid ecosystem alias")
        if len(cleaned) >= 32:
            continue
        cleaned.append(alias.strip())
    return {"target": target.strip(), "aliases": cleaned}


_SPEC_VALIDATORS = {
    "search": _validate_search_spec,
    "watchlist_target": _validate_watchlist_spec,
    "ecosystem_target": _validate_ecosystem_spec,
}


@dataclass(frozen=True)
class GitHubDiscoveryProbe:
    """One collection probe inside a policy.

    Each probe owns exactly one ``scope_key`` (one query = one cursor) and a
    deterministic ``spec_hash`` over ``(kind, spec)``. The raw query never
    leaves this object; only the hash is persisted or compared.
    """

    probe_id: str
    kind: str
    scope_key: str
    spec: Mapping[str, Any]
    priority: int

    def __post_init__(self) -> None:
        _require_label(self.probe_id, "probe_id")
        if self.kind not in GITHUB_PROBE_KINDS:
            raise DiscoveryPolicyError("invalid probe kind: %r" % self.kind)
        object.__setattr__(self, "scope_key", validate_scope_key(self.scope_key))
        _non_negative_int(self.priority, "priority")
        validated = _SPEC_VALIDATORS[self.kind](self.spec)
        object.__setattr__(self, "spec", validated)

    @property
    def spec_hash(self) -> str:
        """SHA-256 over ``(kind, spec)`` - the scope binding key.

        The raw query is part of the hashed input but is never itself
        persisted or logged anywhere.
        """
        return sha256_hex(_canonical({"kind": self.kind, "spec": self.spec}))


@dataclass(frozen=True)
class GitHubDiscoveryPolicy:
    """An immutable, versioned GitHub discovery policy.

    ``policy_hash`` covers the complete policy (id, lane, probes, budgets,
    ecosystem targets) and is persisted on every run for audit. Probe ids and
    scope keys must be unique inside one policy: one query, one scope, one
    cursor. ``ecosystem_targets`` (phase 2C2-C) lists the first-user-confirmed
    core projects for the ecosystem lane; it must be empty on other lanes.
    """

    id: str
    lane: str
    probes: Tuple[GitHubDiscoveryProbe, ...]
    candidate_limit: int
    research_budget: int
    ecosystem_targets: Tuple["EcosystemTargetSpec", ...] = ()

    def __post_init__(self) -> None:
        _require_label(self.id, "policy_id")
        if self.lane not in GITHUB_DISCOVERY_LANES:
            raise DiscoveryPolicyError("invalid lane: %r" % self.lane)
        probes = tuple(self.probes)
        if not all(isinstance(probe, GitHubDiscoveryProbe) for probe in probes):
            raise DiscoveryPolicyError("probes must contain GitHubDiscoveryProbe values")
        probe_ids = [probe.probe_id for probe in probes]
        scope_keys = [probe.scope_key for probe in probes]
        if len(set(probe_ids)) != len(probe_ids):
            raise DiscoveryPolicyError("probe_ids must be unique within a policy")
        if len(set(scope_keys)) != len(scope_keys):
            raise DiscoveryPolicyError("scope_keys must be unique within a policy")
        # Deterministic processing order: priorities must be unique so the
        # numeric order (not tuple order) decides which probe wins a candidate.
        priorities = [probe.priority for probe in probes]
        if len(set(priorities)) != len(priorities):
            raise DiscoveryPolicyError("probe priorities must be unique within a policy")
        object.__setattr__(self, "probes", probes)
        targets = tuple(self.ecosystem_targets)
        if not all(isinstance(target, EcosystemTargetSpec) for target in targets):
            raise DiscoveryPolicyError(
                "ecosystem_targets must contain EcosystemTargetSpec values"
            )
        if targets and self.lane != "ecosystem":
            raise DiscoveryPolicyError("ecosystem_targets are only valid on the ecosystem lane")
        if len({target.target for target in targets}) != len(targets):
            raise DiscoveryPolicyError("ecosystem targets must be unique")
        object.__setattr__(self, "ecosystem_targets", targets)
        # Budget boundaries: candidate_limit 1..100; research_budget must be
        # within 0..candidate_limit (0 = nothing may be queued).
        _bounded_int(self.candidate_limit, 1, 100, "candidate_limit")
        limit = self.candidate_limit
        _bounded_int(self.research_budget, 0, limit, "research_budget")

    @property
    def policy_hash(self) -> str:
        """SHA-256 over the complete canonical policy definition."""
        payload = {
            "id": self.id,
            "lane": self.lane,
            "probes": [
                {
                    "probe_id": probe.probe_id,
                    "kind": probe.kind,
                    "scope_key": probe.scope_key,
                    "spec": probe.spec,
                    "priority": probe.priority,
                }
                for probe in self.probes
            ],
            "candidate_limit": self.candidate_limit,
            "research_budget": self.research_budget,
            "ecosystem_targets": [
                {"target": target.target, "aliases": list(target.aliases)}
                for target in self.ecosystem_targets
            ],
        }
        return sha256_hex(_canonical(payload))


def _search_probe(probe_id: str, scope_key: str, query: str, priority: int) -> GitHubDiscoveryProbe:
    """Build a default search probe (updated/desc, 25 per page, 3 pages)."""
    return GitHubDiscoveryProbe(
        probe_id=probe_id,
        kind="search",
        scope_key=scope_key,
        spec={
            "query": query,
            "sort": "updated",
            "order": "desc",
            "per_page": 25,
            "max_pages": 3,
        },
        priority=priority,
    )


# --------------------------------------------------------------------------- #
# Catalog
# --------------------------------------------------------------------------- #

# Mature recall: adopted agent-related projects. stars only de-noise; the
# qualification gate still decides. Fixed date windows: moving a window is a
# semantic change -> scope version bump.
_POLICY_MATURE = GitHubDiscoveryPolicy(
    id="mature-v1",
    lane="mature",
    probes=(
        _search_probe(
            "mature-v1-q1",
            "ghp-mature-v1-q1",
            "topic:ai-agent stars:>=100 pushed:>2026-07-01",
            0,
        ),
        _search_probe(
            "mature-v1-q2",
            "ghp-mature-v1-q2",
            "agentic stars:>=100 pushed:>2026-07-01",
            1,
        ),
        _search_probe(
            "mature-v1-q3",
            "ghp-mature-v1-q3",
            "topic:mcp stars:>=100 pushed:>2026-07-01",
            2,
        ),
    ),
    candidate_limit=50,
    research_budget=5,
)

# Emerging recall: widest net, no minimum stars; creation age, recent push and
# agent relevance are all judged by the local qualification gate.
_POLICY_EMERGING = GitHubDiscoveryPolicy(
    id="emerging-v1",
    lane="emerging",
    probes=(
        _search_probe(
            "emerging-v1-q1",
            "ghp-emerging-v1-q1",
            "topic:ai-agent pushed:>2026-07-01",
            0,
        ),
        _search_probe(
            "emerging-v1-q2",
            "ghp-emerging-v1-q2",
            "agentic pushed:>2026-07-01",
            1,
        ),
        _search_probe(
            "emerging-v1-q3",
            "ghp-emerging-v1-q3",
            "topic:ai-agents pushed:>2026-07-01",
            2,
        ),
    ),
    candidate_limit=100,
    research_budget=8,
)

# --------------------------------------------------------------------------- #
# First-user lists (DRAFT, 2026-08-16 - NOT yet confirmed by the first user).
# The engineering is complete; only these catalog entries are provisional.
# To change the watchlist or the ecosystem core project, edit ONLY this
# section: probe scope_keys must stay unique and semantic changes must bump
# the scope version (the binding firewall enforces it automatically).
# --------------------------------------------------------------------------- #

# Watchlist: human-confirmed core repositories, one direct-snapshot probe
# each (no search, no stars/topic heuristics - Case D).
_POLICY_WATCHLIST = GitHubDiscoveryPolicy(
    id="watchlist-v1",
    lane="watchlist",
    probes=(
        GitHubDiscoveryProbe(
            probe_id="watchlist-v1-r1",
            kind="watchlist_target",
            scope_key="ghp-watchlist-v1-r1",
            spec={"full_name": "paul-gauthier/aider"},
            priority=0,
        ),
        GitHubDiscoveryProbe(
            probe_id="watchlist-v1-r2",
            kind="watchlist_target",
            scope_key="ghp-watchlist-v1-r2",
            spec={"full_name": "langchain-ai/langgraph"},
            priority=1,
        ),
        GitHubDiscoveryProbe(
            probe_id="watchlist-v1-r3",
            kind="watchlist_target",
            scope_key="ghp-watchlist-v1-r3",
            spec={"full_name": "microsoft/autogen"},
            priority=2,
        ),
        GitHubDiscoveryProbe(
            probe_id="watchlist-v1-r4",
            kind="watchlist_target",
            scope_key="ghp-watchlist-v1-r4",
            spec={"full_name": "openai/openai-agents-python"},
            priority=3,
        ),
        GitHubDiscoveryProbe(
            probe_id="watchlist-v1-r5",
            kind="watchlist_target",
            scope_key="ghp-watchlist-v1-r5",
            spec={"full_name": "OleksandrChekhovskyi/hax"},
            priority=4,
        ),
        GitHubDiscoveryProbe(
            probe_id="watchlist-v1-r6",
            kind="watchlist_target",
            scope_key="ghp-watchlist-v1-r6",
            spec={"full_name": "vava-nessa/free-coding-models"},
            priority=5,
        ),
    ),
    candidate_limit=20,
    research_budget=5,
)

# Ecosystem: one monitored core project (LangGraph) with strong aliases; the
# search probes recall wrappers/UIs/plugins/MCP servers and the relation
# resolver (full_name/description/topics, word-boundary matching) decides
# whether the metadata actually relates to the core.
_POLICY_ECOSYSTEM = GitHubDiscoveryPolicy(
    id="ecosystem-v1",
    lane="ecosystem",
    probes=(
        _search_probe(
            "ecosystem-v1-q1",
            "ghp-eco-v1-q1",
            "langgraph in:name,description pushed:>2026-07-01",
            0,
        ),
        _search_probe(
            "ecosystem-v1-q2",
            "ghp-eco-v1-q2",
            "topic:langgraph pushed:>2026-07-01",
            1,
        ),
    ),
    candidate_limit=50,
    research_budget=5,
    ecosystem_targets=(
        EcosystemTargetSpec(
            target="langchain-ai/langgraph",
            aliases=("langgraph", "langgraphjs", "langgraph-mcp", "langchain"),
        ),
    ),
)

POLICY_CATALOG = {
    policy.id: policy
    for policy in (
        _POLICY_WATCHLIST,
        _POLICY_MATURE,
        _POLICY_EMERGING,
        _POLICY_ECOSYSTEM,
    )
}


def get_policy(policy_id: str) -> GitHubDiscoveryPolicy:
    """Return the catalog policy for ``policy_id`` (KeyError when unknown)."""
    if not isinstance(policy_id, str) or policy_id not in POLICY_CATALOG:
        raise DiscoveryPolicyError("unknown policy")
    return POLICY_CATALOG[policy_id]


def list_policies():
    """Catalog summary: safe metadata only - never a query, spec or hash."""
    return [
        {
            "id": policy.id,
            "lane": policy.lane,
            "probe_count": len(policy.probes),
            "candidate_limit": policy.candidate_limit,
            "research_budget": policy.research_budget,
        }
        for policy in POLICY_CATALOG.values()
    ]
