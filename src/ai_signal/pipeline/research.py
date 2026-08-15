"""Phase 2D2 GitHub research pipeline (deterministic, quote-only, no LLM).

:func:`build_github_dossier` turns one source-independent ``event_candidate``
whose references include GitHub repository candidates into a
:class:`ResearchDossier` with auditable :class:`ResearchFact` rows:

* repository metadata (stars/forks/language/pushed, from the stored
  whitelisted snapshot attributes - never raw payloads);
* the latest release (tag/date/body excerpt, first-party URL) and the
  previous release when available;
* a bounded README excerpt (untrusted external text: control characters
  stripped, injection markers sanitized, length capped, never executed).

No LLM, no fabricated prose: every fact quotes a first-party source, the
summary is an explicit machine draft, unknowns are listed as unknowns, and a
``needs_testing`` flag is derived from experience-claim markers found in
release/README text.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Tuple

from ..domain.models import (
    ResearchDossier,
    ResearchFact,
    now_utc,
    sha256_hex,
)
from ..sources.github_rest import (
    GitHubClientError,
    GitHubReadmeClient,
    GitHubReleasesClient,
    UrllibTransport,
)
from ..storage.event_candidate_repositories import (
    EventCandidateRepository,
    EventCandidateSourceRefRepository,
)
from ..storage.research_repositories import (
    ResearchDossierRepository,
    ResearchFactRepository,
)
from ..storage.sqlite import StorageError


class ResearchError(ValueError):
    """Raised when research cannot proceed (configuration / data)."""


# Experience-claim markers: their presence in release/README text means the
# core proposition is an experience claim and needs personal testing.
_EXPERIENCE_MARKERS = (
    "faster", "better", "stable", "easier", "seamless", "secure", "scales",
    "更稳", "更好", "更快", "提升", "效率", "省时", "省心", "一键", "开箱即用",
)

_INJECTION_MARKERS = (
    "ignore previous instructions",
    "system prompt",
    "reveal secrets",
    "<script",
    "javascript:",
)

_README_EXCERPT_CAP = 800
_RELEASE_BODY_CAP = 600
_MAX_REFS = 3  # bound network requests per dossier

_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def attach_official_evidence(
    conn,
    dossier_id: str,
    url: str,
    *,
    allow_network: bool = False,
    transport_factory: Optional[Callable[[], Any]] = None,
    clock: Optional[Callable[[], Any]] = None,
    timeout_seconds: int = 10,
    kind: str = "fact",
) -> ResearchFact:
    """Attach one official first-party page as evidence to a dossier (2D3).

    The page is fetched read-only (https only, bounded, tag-stripped) and
    stored as an ``official_page`` fact. ``kind`` defaults to ``fact``; use
    ``official_claim`` for promotional claims that must stay attributed to
    the vendor. Re-running the editorial gate afterwards yields a new
    decision revision because the input hash covers all fact identities.
    """
    from ..domain.models import FACT_KINDS
    from ..sources.official_http import (
        OfficialHttpClient,
        OfficialHttpError,
        UrllibOfficialTransport,
    )

    if kind not in FACT_KINDS:
        raise ResearchError("invalid fact kind")
    if allow_network is False:
        from ..pipeline.collect import CollectionPolicyError

        raise CollectionPolicyError("network not allowed: --allow-network is required")

    dossier = ResearchDossierRepository(conn).get(dossier_id)
    if dossier is None:
        raise ResearchError("unknown dossier")

    ts = clock if clock is not None else now_utc
    client = OfficialHttpClient(
        transport_factory() if transport_factory is not None else UrllibOfficialTransport(),
        timeout_seconds=timeout_seconds,
    )
    text = client.fetch(url)
    if not text:
        raise ResearchError("official page returned no usable text")
    fact = ResearchFact(
        dossier_id=dossier_id,
        kind=kind,
        text=text,
        source_kind="official_page",
        source_url=url,
        created_at=ts(),
    )
    stored = ResearchFactRepository(conn).insert_or_get(fact)
    if stored is None:  # pragma: no cover - defensive
        raise StorageError("official evidence upsert failed")
    return stored


def _clean_external_text(text: str, cap: int) -> Tuple[str, bool]:
    """Strip control characters and cap untrusted external text.

    Returns ``(cleaned, injection)``; when injection markers are found the
    text is replaced by a safe placeholder (never rendered, never executed).
    """
    cleaned = _CTRL_RE.sub(" ", text or "")
    lowered = cleaned.lower()
    if any(marker in lowered for marker in _INJECTION_MARKERS):
        return "（内容含可疑注入标记，已脱敏）", True
    return cleaned[:cap].strip(), False


@dataclass(frozen=True)
class DossierBuildResult:
    """Safe summary of one dossier build."""

    dossier: ResearchDossier
    facts: Tuple[ResearchFact, ...]
    status: str
    fetch_failures: int


def build_github_dossier(
    conn,
    event_candidate_id: str,
    *,
    allow_network: bool = False,
    transport_factory: Optional[Callable[[], Any]] = None,
    clock: Optional[Callable[[], Any]] = None,
    timeout_seconds: int = 10,
) -> DossierBuildResult:
    """Build (or rebuild) the dossier for one event candidate (caller owns txn).

    Network fetches happen only with ``allow_network=True`` and only against
    api.github.com (the clients enforce the same firewall as 2B1). Failures
    are recorded as ``unknown`` facts and never abort the build.
    """
    ts = clock if clock is not None else now_utc
    event_repo = EventCandidateRepository(conn)
    ref_repo = EventCandidateSourceRefRepository(conn)
    event = event_repo.get(event_candidate_id)
    if event is None:
        raise ResearchError("unknown event candidate")

    github_refs = [
        ref
        for ref in ref_repo.list_for_candidate(event_candidate_id)
        if ref.source_kind == "github_repository_candidate"
    ]
    if not github_refs:
        raise ResearchError("event candidate has no GitHub repository references")
    if allow_network is False:
        from ..pipeline.collect import CollectionPolicyError

        raise CollectionPolicyError("network not allowed: --allow-network is required")

    readme_client = GitHubReadmeClient(
        transport_factory() if transport_factory is not None else UrllibTransport(),
        timeout_seconds=timeout_seconds,
    )
    releases_client = GitHubReleasesClient(
        transport_factory() if transport_factory is not None else UrllibTransport(),
        timeout_seconds=timeout_seconds,
    )

    timeline: List[str] = []
    fact_specs: List[dict] = []  # {kind, text, source_kind, source_url}
    fetch_failures = 0
    needs_testing = False
    latest_tag = None
    latest_date = None

    for ref in github_refs[:_MAX_REFS]:
        row = conn.execute(
            "SELECT c.title, c.url, "
            "       (SELECT a.attributes FROM candidate_assessment a "
            "        JOIN candidate_discovery cd ON cd.id = a.candidate_discovery_id "
            "        WHERE cd.candidate_id = c.id "
            "        ORDER BY a.assessed_at DESC, a.id ASC LIMIT 1) AS attrs "
            "FROM candidate c WHERE c.id = ?",
            (ref.ref_id,),
        ).fetchone()
        if row is None:
            fetch_failures += 1
            continue
        title = row["title"]
        repo_url = row["url"]

        if row["attrs"]:
            try:
                attrs = json.loads(row["attrs"])
                fact_specs.append({
                    "kind": "fact",
                    "text": (
                        "GitHub 快照：%s；stars=%s, forks=%s, language=%s, "
                        "pushed_at=%s"
                        % (
                            title,
                            attrs.get("stargazers_count"),
                            attrs.get("forks_count"),
                            attrs.get("language"),
                            (attrs.get("pushed_at") or "")[:10],
                        )
                    ),
                    "source_kind": "github_metadata",
                    "source_url": repo_url,
                })
            except (ValueError, TypeError):
                pass

        try:
            readme = readme_client.fetch(title)
        except GitHubClientError:
            readme = None
            fetch_failures += 1
        if readme:
            excerpt, _ = _clean_external_text(readme, _README_EXCERPT_CAP)
            if excerpt:
                fact_specs.append({
                    "kind": "official_claim",
                    "text": "README 摘要：%s" % excerpt,
                    "source_kind": "github_readme",
                    "source_url": "https://github.com/%s" % title,
                })
                lowered = readme.lower()
                if any(marker in lowered for marker in _EXPERIENCE_MARKERS):
                    needs_testing = True
        else:
            fact_specs.append({
                "kind": "unknown",
                "text": "README 不可读或缺失（%s）" % title,
                "source_kind": "github_readme",
                "source_url": "https://github.com/%s" % title,
            })

        try:
            releases = releases_client.fetch(title)
        except GitHubClientError:
            releases = []
            fetch_failures += 1
        if releases:
            latest = releases[0]
            latest_tag = latest["tag"]
            latest_date = latest["published_at"][:10]
            body, _ = _clean_external_text(latest["body"], _RELEASE_BODY_CAP)
            fact_specs.append({
                "kind": "fact",
                "text": "发布 %s（%s）：%s" % (latest_tag, latest_date, body),
                "source_kind": "github_release",
                "source_url": latest["html_url"],
            })
            if any(marker in latest["body"].lower() for marker in _EXPERIENCE_MARKERS):
                needs_testing = True
            for release in releases[:3]:
                timeline.append(
                    "%s %s: %s (%s)"
                    % (
                        release["published_at"][:10],
                        release["tag"],
                        release["name"] or "release",
                        release["html_url"],
                    )
                )
            if len(releases) > 1:
                prev = releases[1]
                fact_specs.append({
                    "kind": "fact",
                    "text": "上一版本 %s（%s）" % (prev["tag"], prev["published_at"][:10]),
                    "source_kind": "github_release",
                    "source_url": prev["html_url"],
                })
        else:
            fact_specs.append({
                "kind": "unknown",
                "text": "无 Release 记录（%s）" % title,
                "source_kind": "github_release",
                "source_url": "https://github.com/%s/releases" % title,
            })

    if latest_tag:
        summary = "机器草案：%s 于 %s 发布 %s；具体价值判断待人工确认。" % (
            event.subject, latest_date, latest_tag
        )
    else:
        summary = "机器草案：%s 无近期 Release，仅有元数据与 README；具体变化待人工确认。" % event.subject

    limits = [
        "仅第一方 GitHub 快照，无独立评测",
        "未本人实测",
        "单周快照，无趋势数据",
        "README/Release 正文为外部不可信输入，已做长度与字符清理",
    ]
    forbidden = [
        "爆火/快速增长（无时间序列证据）",
        "更好用/更稳/提效（未实测）",
        "生产可用/行业领先",
        "普通用户普遍采用（仅 GitHub 单一来源）",
    ]
    test_plan = []
    if needs_testing:
        test_plan = [
            "安装并运行 %s（明确版本与环境）" % event.subject,
            "完成一个真实任务，记录成功率、耗时、人工接管次数与失败点",
            "与替换前的工具做同一任务的对照",
        ]

    bundle = sha256_hex(
        json.dumps(
            {
                "subject": event.subject,
                "facts": [
                    {k: f[k] for k in ("kind", "source_kind", "source_url", "text")}
                    for f in fact_specs
                ],
                "needs_testing": needs_testing,
                "timeline": timeline,
            },
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    dossier = ResearchDossier(
        event_candidate_id=event_candidate_id,
        summary_judgment=summary,
        timeline=tuple(timeline),
        target_audience=event.affected_audience,
        job_to_be_done=event.work_impact_hypothesis,
        limits_unknowns=tuple(limits),
        forbidden_claims=tuple(forbidden),
        needs_testing=needs_testing,
        test_plan=tuple(test_plan),
        status="partial" if fetch_failures else "complete",
        bundle_hash=bundle,
        created_at=ts(),
        updated_at=ts(),
    )
    stored_dossier = ResearchDossierRepository(conn).insert_or_get(dossier)
    fact_repo = ResearchFactRepository(conn)
    stored_facts = tuple(
        fact_repo.insert_or_get(
            ResearchFact(
                dossier_id=stored_dossier.id,
                kind=f["kind"],
                text=f["text"],
                source_kind=f["source_kind"],
                source_url=f["source_url"],
                created_at=ts(),
            )
        )
        for f in fact_specs
    )
    return DossierBuildResult(
        dossier=stored_dossier,
        facts=stored_facts,
        status=stored_dossier.status,
        fetch_failures=fetch_failures,
    )
