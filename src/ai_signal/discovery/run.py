"""Phase 2C2-B GitHub discovery orchestration.

:func:`run_github_discovery` executes one :class:`GitHubDiscoveryPolicy` end to
end over the existing 2A/2B1 collection and 2C1 qualification machinery::

    policy catalog
      -> scope/spec firewall (binding check BEFORE any network)
      -> per-probe collect_source_once (2A/2B1)
      -> per-probe qualify_github (2C1)
      -> candidate-level dedup across probes
      -> deterministic ranking + research budget
      -> github_candidate_selection (DB-only GitHub Research Queue)

Safety rules:

* a probe whose scope_key is bound to a different spec_hash, or that already
  owns an unbound legacy cursor, is marked ``blocked`` and makes NO network
  request - a new query can never inherit an old query's pagination cursor;
* network probes require an explicit ``allow_network`` (otherwise the run is
  refused before any database work, mirroring the collect CLI's exit code 4);
* a single probe failure never aborts the run: the probe is marked ``failed``
  and the remaining probes continue;
* the budget only sets ``queue_state`` - the qualification decision is stored
  verbatim and never downgraded;
* everything logged or returned is payload-free (stable codes and counts
  only); raw queries exist only inside the policy objects.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..domain.models import (
    BUDGET_REASON_EXCEEDED,
    WARN_SCOPE_SPEC_MISMATCH,
    WARN_SCOPE_UNBOUND_CURSOR,
    GitHubCandidateSelection,
    GitHubDiscoveryProbeRun,
    GitHubDiscoveryRun,
    GitHubScopeBinding,
    now_utc,
)
from ..pipeline.collect import CollectionPolicyError, collect_source_once
from ..pipeline.qualify import QualifyError, qualify_github
from ..sources.github_rest import (
    GitHubReposClient,
    GitHubReposSpec,
    GitHubRestClient,
    GitHubSearchSpec,
    UrllibTransport,
)
from ..storage import sqlite as sqlite_storage
from ..storage.discovery_repositories import (
    GitHubCandidateSelectionRepository,
    GitHubDiscoveryProbeRunRepository,
    GitHubDiscoveryRunRepository,
    GitHubScopeBindingRepository,
)
from ..storage.source_repositories import SourceCursorRepository
from .policy import DiscoveryPolicyError, get_policy
from .relation import build_ecosystem_resolver

# The 2C1 raw-signal selector caps a scope at 50 repositories; a policy
# candidate_limit above that is therefore applied per scope as this cap
# (documented, never silently raised).
_SELECTOR_LIMIT_CAP = 50

WARN_PROBE_FAILED = "PROBE_FAILED"

# Probe kinds that need a real network request in phase 2C2-B/C.
_NETWORK_KINDS = ("search", "watchlist_target")

_WEEK_KEY_RE = re.compile(r"^\d{4}-W\d{2}$")


def _week_key_ok(week_key: Any) -> bool:
    return isinstance(week_key, str) and _WEEK_KEY_RE.fullmatch(week_key) is not None


@dataclass(frozen=True)
class GitHubDiscoveryRunResult:
    """Safe, payload-free summary of one discovery run."""

    run_id: str
    policy_id: str
    lane: str
    week_key: str
    status: str
    probes_total: int
    probes_blocked: int
    probes_failed: int
    processed: int
    research: int
    watch: int
    rejected: int
    quarantined: int
    selections_total: int
    queued: int
    over_budget: int
    beyond_candidate_limit: int
    warnings: Tuple[str, ...] = ()


def _build_search_spec(probe) -> GitHubSearchSpec:
    """Map a validated policy probe spec onto the 2B1 search spec."""
    spec = probe.spec
    try:
        return GitHubSearchSpec(
            query=spec["query"],
            sort=spec["sort"],
            order=spec["order"],
            per_page=spec["per_page"],
            max_pages=spec["max_pages"],
        )
    except (TypeError, ValueError) as exc:
        raise DiscoveryPolicyError("probe spec rejected by search adapter") from exc


def _build_repos_spec(probe) -> GitHubReposSpec:
    """Map a validated watchlist probe spec onto the 2C2-C repos spec."""
    try:
        return GitHubReposSpec(full_name=probe.spec["full_name"])
    except (TypeError, ValueError) as exc:
        raise DiscoveryPolicyError("probe spec rejected by repos adapter") from exc


def run_github_discovery(
    db_path: Any,
    week_key: str,
    policy_id: str,
    *,
    allow_network: bool = False,
    timeout_seconds: int = 10,
    transport_factory: Optional[Callable[[], Any]] = None,
    clock: Optional[Callable[[], Any]] = None,
    logger: Optional[Any] = None,
) -> GitHubDiscoveryRunResult:
    """Run one GitHub discovery policy (phase 2C2-B).

    ``transport_factory`` injects the HTTP transport for tests (defaults to
    :class:`UrllibTransport`); it is never used unless ``allow_network`` is
    true, and tests never instantiate the production transport.
    """
    if not _week_key_ok(week_key):
        raise DiscoveryPolicyError("invalid week key")
    policy = get_policy(policy_id)
    if not policy.probes:
        raise DiscoveryPolicyError("policy has no probes")

    ts = clock if clock is not None else now_utc
    needs_network = any(probe.kind in _NETWORK_KINDS for probe in policy.probes)
    if needs_network and not allow_network:
        # Mirrors the collect CLI: refuse before any database work (exit 4).
        raise CollectionPolicyError("network not allowed: --allow-network is required")

    # Migration runner applies 0005 (and backs up an existing database first).
    sqlite_storage.initialize_database(db_path)

    run = GitHubDiscoveryRun(
        policy_id=policy.id,
        policy_hash=policy.policy_hash,
        week_key=week_key,
        lane=policy.lane,
        candidate_limit=policy.candidate_limit,
        research_budget=policy.research_budget,
        started_at=ts(),
    )

    # ------------------------------------------------------------------ #
    # Transaction A: record the run + every probe run, apply the scope/spec
    # firewall and claim bindings for probes that may proceed. All of this
    # happens BEFORE any network request.
    # ------------------------------------------------------------------ #
    blocked: Dict[str, str] = {}  # probe_id -> stable warning code
    conn = sqlite_storage._open(db_path)
    try:
        conn.execute("BEGIN")
        GitHubDiscoveryRunRepository(conn).insert(run)
        probe_repo = GitHubDiscoveryProbeRunRepository(conn)
        binding_repo = GitHubScopeBindingRepository(conn)
        cursor_repo = SourceCursorRepository(conn)

        for probe in policy.probes:
            probe_run = GitHubDiscoveryProbeRun(
                discovery_run_id=run.id,
                probe_id=probe.probe_id,
                kind=probe.kind,
                lane=policy.lane,
                scope_key=probe.scope_key,
                spec_hash=probe.spec_hash,
                priority=probe.priority,
                started_at=ts(),
            )
            binding = binding_repo.get(probe.scope_key)
            if binding is not None:
                if binding.spec_hash != probe.spec_hash:
                    # Hard firewall: an existing cursor scope must never run
                    # under a different spec. No network, no qualification.
                    object.__setattr__(
                        probe_run,
                        "warnings",
                        (WARN_SCOPE_SPEC_MISMATCH,),
                    )
                    object.__setattr__(probe_run, "warning_count", 1)
                    object.__setattr__(probe_run, "status", "blocked")
                    object.__setattr__(probe_run, "finished_at", ts())
                    blocked[probe.probe_id] = WARN_SCOPE_SPEC_MISMATCH
                # matching binding: proceed (binding already claimed).
            else:
                legacy_cursor = cursor_repo.get("github", probe.scope_key)
                if legacy_cursor is not None:
                    # An unbound scope that already owns a cursor belongs to a
                    # pre-2C2 query: refuse instead of inheriting its progress.
                    object.__setattr__(
                        probe_run,
                        "warnings",
                        (WARN_SCOPE_UNBOUND_CURSOR,),
                    )
                    object.__setattr__(probe_run, "warning_count", 1)
                    object.__setattr__(probe_run, "status", "blocked")
                    object.__setattr__(probe_run, "finished_at", ts())
                    blocked[probe.probe_id] = WARN_SCOPE_UNBOUND_CURSOR
                else:
                    # Claim the scope for this spec before any networking.
                    binding_repo.insert_or_get(
                        GitHubScopeBinding(
                            scope_key=probe.scope_key,
                            probe_id=probe.probe_id,
                            policy_id=policy.id,
                            spec_hash=probe.spec_hash,
                            created_at=ts(),
                        )
                    )
            probe_repo.insert(probe_run)
        conn.execute("COMMIT")
    except sqlite_storage.StorageError:
        try:
            conn.execute("ROLLBACK")
        except Exception:  # pragma: no cover - never mask the original error
            pass
        conn.close()
        raise
    except Exception as exc:  # noqa: BLE001 - surface a stable storage error
        try:
            conn.execute("ROLLBACK")
        except Exception:  # pragma: no cover
            pass
        conn.close()
        raise sqlite_storage.StorageError("failed to persist discovery run") from exc
    conn.close()

    # ------------------------------------------------------------------ #
    # Probe loop: one collection per probe (outside any db transaction).
    # Per-probe outcomes are committed individually so a later crash keeps
    # earlier audit rows.
    # ------------------------------------------------------------------ #
    def _mark_probe_failed(probe_id: str) -> None:
        failed[probe_id] = WARN_PROBE_FAILED
        conn = sqlite_storage._open(db_path)
        try:
            conn.execute("BEGIN")
            GitHubDiscoveryProbeRunRepository(conn).update_outcome(
                _probe_run_id(conn, run.id, probe_id),
                status="failed",
                warnings=(WARN_PROBE_FAILED,),
                finished_at=ts(),
            )
            conn.execute("COMMIT")
        finally:
            conn.close()

    failed: Dict[str, str] = {}
    collection_attempted: Dict[str, Tuple[str, int, str]] = {}
    for probe in policy.probes:
        if probe.probe_id in blocked:
            continue
        try:
            if probe.kind == "search":
                search_spec = _build_search_spec(probe)
                transport = (
                    transport_factory()
                    if transport_factory is not None
                    else UrllibTransport()
                )
                client = GitHubRestClient(
                    search_spec, transport, timeout_seconds=timeout_seconds
                )
                config_snapshot = {
                    "run_mode": "shadow",
                    "source": "github",
                    "scope_key": probe.scope_key,
                    "adapter_kind": "github-rest-v1",
                    "query_sha256": search_spec.query_sha256,
                    "sort": search_spec.sort,
                    "order": search_spec.order,
                    "per_page": search_spec.per_page,
                    "max_pages": search_spec.max_pages,
                }
            elif probe.kind == "watchlist_target":
                # Direct single-repository snapshot (phase 2C2-C): no
                # pagination, no search - metadata only, never README/Release.
                repos_spec = _build_repos_spec(probe)
                transport = (
                    transport_factory()
                    if transport_factory is not None
                    else UrllibTransport()
                )
                client = GitHubReposClient(
                    repos_spec, transport, timeout_seconds=timeout_seconds
                )
                config_snapshot = {
                    "run_mode": "shadow",
                    "source": "github",
                    "scope_key": probe.scope_key,
                    "adapter_kind": "github-repos-v1",
                    "full_name_sha256": repos_spec.full_name_sha256,
                }
            else:  # pragma: no cover - no other probe kinds exist
                raise DiscoveryPolicyError("probe kind not implemented yet")

            result = collect_source_once(
                db_path,
                "github",
                week_key,
                scope_key=probe.scope_key,
                client=client,
                config_snapshot=config_snapshot,
                logger=logger,
            )
        except Exception:  # noqa: BLE001 - one probe must never abort the run
            _mark_probe_failed(probe.probe_id)
            continue

        # A batch-level failure (transport mapped to a stable client error)
        # is also a failed probe: recorded as such, never silently dropped.
        if result.status == "failed":
            failed[probe.probe_id] = WARN_PROBE_FAILED

        collection_attempted[probe.probe_id] = (
            result.run_id,
            result.processed_item_count,
            result.status,
        )
        conn = sqlite_storage._open(db_path)
        try:
            conn.execute("BEGIN")
            GitHubDiscoveryProbeRunRepository(conn).update_outcome(
                _probe_run_id(conn, run.id, probe.probe_id),
                status=result.status,
                collection_run_id=result.run_id,
                item_count=result.processed_item_count,
                warnings=("PROBE_FAILED",) if result.status == "failed" else (),
                finished_at=ts(),
            )
            conn.execute("COMMIT")
        finally:
            conn.close()

    # ------------------------------------------------------------------ #
    # Final transaction: qualification per probe scope + candidate-level
    # dedup + deterministic ranking + budget + selection rows + run status.
    # ------------------------------------------------------------------ #
    qualified: List[Tuple[int, Any]] = []  # (priority, QualifiedCandidate)
    seen_candidates: Dict[str, int] = {}
    totals = {
        "processed": 0,
        "research": 0,
        "watch": 0,
        "rejected": 0,
        "quarantined": 0,
    }
    conn = sqlite_storage._open(db_path)
    try:
        conn.execute("BEGIN")
        # Ecosystem lane: relation evidence comes from the policy's confirmed
        # core-project targets (metadata only; README confirmation is 2D).
        relation_resolver = None
        if policy.lane == "ecosystem" and policy.ecosystem_targets:
            relation_resolver = build_ecosystem_resolver(policy.ecosystem_targets)
        for probe in policy.probes:
            if probe.probe_id in blocked or probe.probe_id in failed:
                continue
            scope_limit = min(policy.candidate_limit, _SELECTOR_LIMIT_CAP)
            try:
                qresult = qualify_github(
                    conn,
                    week_key,
                    probe.scope_key,
                    policy.lane,
                    scope_limit,
                    clock=ts(),
                    relation_resolver=relation_resolver,
                )
            except QualifyError as exc:  # pragma: no cover - catalog is validated
                raise sqlite_storage.StorageError(
                    "qualification failed for a policy scope"
                ) from exc
            totals["processed"] += qresult.processed
            totals["research"] += qresult.research
            totals["watch"] += qresult.watch
            totals["rejected"] += qresult.rejected
            totals["quarantined"] += qresult.quarantined
            for qc in qresult.qualified:
                if qc.candidate.id not in seen_candidates:
                    seen_candidates[qc.candidate.id] = probe.priority
                    qualified.append((probe.priority, qc))

        # Deterministic ranking: winning probe priority first, then
        # assessment recency, then candidate id. Stable multi-pass sort (each
        # pass is applied from lowest to highest precedence). No LLM ranking,
        # no composite score.
        qualified.sort(key=lambda entry: entry[1].candidate.id)
        qualified.sort(
            key=lambda entry: entry[1].assessment.assessed_at, reverse=True
        )
        qualified.sort(key=lambda entry: entry[0])

        selection_repo = GitHubCandidateSelectionRepository(conn)
        queued = 0
        over_budget = 0
        beyond_limit = 0
        for rank, (priority, qc) in enumerate(qualified):
            if rank >= policy.candidate_limit:
                beyond_limit += 1
                continue
            within_budget = rank < policy.research_budget
            relation_target = None
            relation_kind = None
            relation_field = None
            if qc.relation is not None:
                # Ecosystem relation evidence: keep the target/kind/field and
                # the raw_signal_id of the snapshot that evidenced the match.
                relation_target = qc.relation.target
                relation_kind = qc.relation.kind
                relation_field = qc.relation.field
            selection = GitHubCandidateSelection(
                discovery_run_id=run.id,
                candidate_id=qc.candidate.id,
                winning_discovery_id=qc.discovery.id,
                winning_assessment_id=qc.assessment.id,
                selection_rank=rank,
                qualification_decision=qc.assessment.decision,
                queue_state="queued" if within_budget else "over_budget",
                budget_reason=None if within_budget else BUDGET_REASON_EXCEEDED,
                created_at=ts(),
                ecosystem_target=relation_target,
                relation_kind=relation_kind,
                relation_field=relation_field,
                relation_raw_signal_id=(
                    qc.discovery.raw_signal_id if relation_target is not None else None
                ),
            )
            selection_repo.insert_or_get(selection)
            if within_budget:
                queued += 1
            else:
                over_budget += 1

        # Run status: every probe blocked/failed -> failed; any probe
        # blocked/failed -> partial (degraded but not silent); else success.
        probe_statuses: List[str] = []
        warnings: List[str] = []
        for probe in policy.probes:
            if probe.probe_id in blocked:
                probe_statuses.append("blocked")
                warnings.append(blocked[probe.probe_id])
            elif probe.probe_id in failed:
                probe_statuses.append("failed")
                warnings.append(WARN_PROBE_FAILED)
            else:
                probe_statuses.append(
                    collection_attempted[probe.probe_id][2]
                    if probe.probe_id in collection_attempted
                    else "failed"
                )
        if all(status in ("failed", "blocked") for status in probe_statuses):
            run_status = "failed"
        elif any(status in ("failed", "blocked") for status in probe_statuses):
            run_status = "partial"
        else:
            run_status = "success"

        GitHubDiscoveryRunRepository(conn).update_status(
            run.id,
            run_status,
            warnings=tuple(dict.fromkeys(warnings)),
            finished_at=ts(),
        )
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:  # pragma: no cover
            pass
        raise
    finally:
        conn.close()

    return GitHubDiscoveryRunResult(
        run_id=run.id,
        policy_id=policy.id,
        lane=policy.lane,
        week_key=week_key,
        status=run_status,
        probes_total=len(policy.probes),
        probes_blocked=len(blocked),
        probes_failed=len(failed),
        processed=totals["processed"],
        research=totals["research"],
        watch=totals["watch"],
        rejected=totals["rejected"],
        quarantined=totals["quarantined"],
        selections_total=queued + over_budget,
        queued=queued,
        over_budget=over_budget,
        beyond_candidate_limit=beyond_limit,
        warnings=tuple(dict.fromkeys(warnings)),
    )


def _probe_run_id(conn, discovery_run_id: str, probe_id: str) -> str:
    """Look up the persisted probe-run id (created in transaction A)."""
    rows = conn.execute(
        "SELECT id FROM github_discovery_probe_run "
        "WHERE discovery_run_id = ? AND probe_id = ?",
        (discovery_run_id, probe_id),
    ).fetchall()
    if not rows:
        raise sqlite_storage.StorageError("probe run row not found")
    return rows[0][0]
