"""Phase 2C2-C ecosystem metadata relation resolver.

The ecosystem lane looks for wrappers, UIs, plugins, MCP servers and
deployment tools around first-user-confirmed core projects. V1 establishes
ONLY an auditable metadata relation from ``full_name``, ``description`` and
``topics`` - no README content is read (README confirmation belongs to 2D).

A relation match is never fabricated:

* ``full_name_match``  - the candidate's full_name equals the core project's
  normalized full_name (case-insensitive);
* ``topic_match``      - a topic token equals the target or one of its strong
  aliases (case-insensitive exact token);
* ``description_mention`` - the target or an alias appears in the cleaned
  description with word boundaries (so ``core`` never matches ``score``).

The resolver output is duck-typed (``target`` / ``kind`` / ``field``) so the
qualification layer stays free of a discovery-layer import cycle.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Optional, Tuple

from ..domain.models import GITHUB_RELATION_KINDS

_RELATION_PRECEDENCE = ("full_name_match", "topic_match", "description_mention")

_MAX_ALIASES = 32


@dataclass(frozen=True)
class EcosystemTargetSpec:
    """One monitored core project: normalized full_name + strong aliases."""

    target: str
    aliases: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.target, str) or "/" not in self.target.strip():
            raise ValueError("target must be an owner/repo full_name")
        object.__setattr__(self, "target", self.target.strip())
        aliases = tuple(str(alias).strip() for alias in self.aliases if str(alias).strip())
        if len(aliases) > _MAX_ALIASES:
            aliases = aliases[:_MAX_ALIASES]
        object.__setattr__(self, "aliases", aliases)


@dataclass(frozen=True)
class EcosystemRelationMatch:
    """One resolved metadata relation (never carries URLs or raw payloads)."""

    target: str
    kind: str
    field: str

    def __post_init__(self) -> None:
        if self.kind not in GITHUB_RELATION_KINDS:
            raise ValueError("invalid relation kind: %r" % self.kind)
        if self.field not in ("full_name", "description", "topics"):
            raise ValueError("invalid relation field: %r" % self.field)


def _aliases_of(target_spec: EcosystemTargetSpec) -> Tuple[str, ...]:
    seen = [target_spec.target.lower(), target_spec.target.split("/", 1)[1].lower()]
    for alias in target_spec.aliases:
        lowered = alias.lower()
        if lowered and lowered not in seen:
            seen.append(lowered)
    return tuple(seen)


def resolve_relation(
    parsed: Any, targets: Tuple[EcosystemTargetSpec, ...]
) -> Optional[EcosystemRelationMatch]:
    """Match one parsed repository snapshot against the monitored targets.

    ``parsed`` is duck-typed: it must expose ``full_name`` (str | None),
    ``description`` (str | None) and ``topics`` (tuple of str). Returns the
    strongest match across all targets, or ``None``.
    """
    if not targets:
        return None
    full_name = (getattr(parsed, "full_name", None) or "").strip().lower()
    description = (getattr(parsed, "description", None) or "").strip().lower()
    topics = [str(topic).strip().lower() for topic in (getattr(parsed, "topics", None) or ())]

    found: Optional[EcosystemRelationMatch] = None

    def prefer(candidate: Optional[EcosystemRelationMatch]) -> None:
        nonlocal found
        if candidate is None:
            return
        if found is None or _RELATION_PRECEDENCE.index(candidate.kind) < _RELATION_PRECEDENCE.index(
            found.kind
        ):
            found = candidate

    for target_spec in targets:
        target = target_spec.target.strip()
        aliases = _aliases_of(target_spec)

        if full_name and full_name == target.lower():
            prefer(EcosystemRelationMatch(target=target, kind="full_name_match", field="full_name"))
            continue

        for topic in topics:
            if topic in aliases:
                prefer(EcosystemRelationMatch(target=target, kind="topic_match", field="topics"))
                break

        if description:
            for alias in aliases:
                if re.search(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(alias), description):
                    prefer(
                        EcosystemRelationMatch(
                            target=target, kind="description_mention", field="description"
                        )
                    )
                    break
    return found


def build_ecosystem_resolver(
    targets: Tuple[EcosystemTargetSpec, ...],
) -> Callable[[Any], Optional[EcosystemRelationMatch]]:
    """Build a qualification relation resolver for the given targets."""

    def resolver(parsed: Any) -> Optional[EcosystemRelationMatch]:
        return resolve_relation(parsed, targets)

    return resolver
