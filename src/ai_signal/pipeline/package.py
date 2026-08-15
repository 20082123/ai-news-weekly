"""Phase 2B2/2B3 A-F material packaging (deterministic, offline, no LLM).

Builds a structured ``MaterialPack`` (``material-pack-v2``) per Event from the
Claims and Evidence produced by :mod:`ai_signal.pipeline.materialize`. The pack
contains six fixed angles (A-F, in Chinese) plus a deterministic score.
``bundle_hash`` is computed from canonical JSON of the content so the same
inputs always yield the same hash and pack id.

Only the claims/evidence *touched by the current snapshot* are used: the caller
passes them in explicitly (``ClaimTouch`` records returned by materialize), so
historical claims/evidence for the same event never leak into the new pack.

A final validator runs before any pack is persisted: every claim must exist,
every claim must have at least one safe evidence link, and every number / URL /
full date-time referenced in a claim must be traceable to that *same claim's
own* evidence (no cross-claim borrowing).

``verified -> packaged`` is advanced here, only after the MaterialPack row has
been persisted, in the same transaction.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlparse

from ..domain.models import MaterialPack, sha256_hex, now_utc
from ..domain.states import transition
from ..pipeline.materialize import ClaimTouch
from ..storage.material_repositories import (
    EvidenceRepository,
    MaterialPackRepository,
)
from ..storage.repositories import SignalRepository, StateTransitionRepository


SCHEMA_VERSION = "material-pack-v2"

_NUMBER_RE = re.compile(r"\d[\d,]*")
_URL_RE = re.compile(r"https?://[^\s<>\"']+")
_DATETIME_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})"
)


class PackageValidationError(Exception):
    """Raised when a pack fails final validation (never persisted on failure)."""


def _strip_trailing_punct(url: str) -> str:
    # Both ASCII and CJK sentence punctuation: Chinese claims end with "。",
    # which the URL charset would otherwise absorb into the token.
    return url.rstrip(".,;:!?)]\"'”’》）」』。！？：；，")


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _extract_tokens(text: str) -> Tuple[List[str], List[str], List[str]]:
    """Return (numbers, urls, datetimes) found in ``text``.

    URLs have trailing sentence punctuation (ASCII and CJK) stripped so a
    claim ending ``…可通过 https://github.com/x 公开访问。`` still matches
    evidence containing the bare URL.
    """
    numbers = [m.group(0).replace(",", "") for m in _NUMBER_RE.finditer(text)]
    urls = [_strip_trailing_punct(u) for u in _URL_RE.findall(text)]
    datetimes = _DATETIME_RE.findall(text)
    return numbers, urls, datetimes


def _evidence_tokens(ev: Any) -> Tuple[set, set, set]:
    parts = [ev.snippet, _canonical_json(dict(ev.payload))]
    if ev.url:
        parts.append(ev.url)
    nums, urls, dates = _extract_tokens(" ".join(parts))
    return set(nums), set(urls), set(dates)


def _is_safe_https_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except (TypeError, ValueError):
        return False
    return (
        parsed.scheme == "https"
        and bool(parsed.netloc)
        and parsed.username is None
        and parsed.password is None
        and parsed.port is None
    )


# --------------------------------------------------------------------------- #
# Angle builders (Chinese, phase 2B3)
# --------------------------------------------------------------------------- #
def _build_project_info(p: Mapping[str, Any]) -> Dict[str, Any]:
    """Project facts shown in the Markdown 项目信息 section.

    Everything comes from the collected snapshot payload; nothing is
    fabricated. Missing fields are stored as ``None`` and rendered as 暂无.
    """
    return {
        "full_name": p.get("full_name"),
        "description": p.get("description"),
        "topics": list(p.get("topics") or []),
        "language": p.get("language"),
        "stargazers_count": p.get("stargazers_count"),
        "forks_count": p.get("forks_count"),
        "updated_at": p.get("updated_at"),
        "pushed_at": p.get("pushed_at"),
        "html_url": p.get("html_url"),
    }


def _build_a(claim_ids: Tuple[str, ...], claims_text: List[str]) -> Dict[str, Any]:
    return {"claim_ids": list(claim_ids), "facts": claims_text}


def _build_b(evidence_id: str, source: str, url: str, snippet: str) -> Dict[str, Any]:
    return {
        "evidence_id": evidence_id,
        "source": source,
        "url": url,
        "snippet": snippet,
        "unknowns": [
            "仅基于 GitHub API 单次快照，无法据此证明任何趋势或增长。",
            "本阶段没有可比较的历史快照基线。",
        ],
        "limitations": [
            "stars/forks 是当前快照的瞬时计数，不代表增长速度。",
        ],
    }


def _build_c(p: Mapping[str, Any]) -> Dict[str, Any]:
    stars = p.get("stargazers_count")
    forks = p.get("forks_count")
    has_desc = "description" in p
    has_topics = bool(p.get("topics"))
    score = 0.0
    if has_desc:
        score += 0.25
    if has_topics:
        score += 0.25
    if isinstance(stars, int) and not isinstance(stars, bool) and stars > 0:
        score += 0.25
    if isinstance(forks, int) and not isinstance(forks, bool) and forks > 0:
        score += 0.25
    return {
        "heat_status": "unmeasured",
        "heat_status_label": "尚未测量",
        "notes": [
            "热度趋势尚未测量：缺少历史快照，无法判断是否升温。",
            "stars/forks 只是当前快照数值，不是增长速度。",
            "后续积累历史快照后才能比较热度变化。",
        ],
        "score_components": {
            "has_description": has_desc,
            "has_topics": has_topics,
            "stars_present": stars is not None,
            "forks_present": forks is not None,
        },
        "score": round(score, 4),
    }


def _build_d(p: Mapping[str, Any]) -> Dict[str, Any]:
    full_name = p.get("full_name") or "该仓库"
    topics = "、".join(list(p.get("topics") or [])[:5]) or "暂无 topics"
    desc = p.get("description") or "简介暂缺"
    return {
        "editorial_hypothesis": True,
        "angles": [
            {
                "who": "knowledge_workers",
                "who_label": "知识工作者",
                "note": "可以把 %s（%s）作为效率工具候选进行评估。" % (full_name, desc),
            },
            {
                "who": "companies",
                "who_label": "公司",
                "note": "若 %s 与现有技术栈（topics：%s）契合，可评估集成可行性。" % (full_name, topics),
            },
            {
                "who": "professionals",
                "who_label": "专业人员",
                "note": "可对 %s 做基准对比或二次开发参考。" % full_name,
            },
        ],
    }


def _build_e(p: Mapping[str, Any]) -> Dict[str, Any]:
    full_name = p.get("full_name") or "该仓库"
    return {
        "type": "manual_test_plan",
        "steps": [
            "阅读 %s 的官方 README、LICENSE 与 security policy。" % full_name,
            "在隔离环境中测试，不使用生产凭据。",
            "不提供任何凭据。",
            "不运行未知脚本。",
            "记录版本、输入、输出与失败条件。",
        ],
        "note": "本项目代码绝不由本程序自动执行。",
    }


def _build_f(p: Mapping[str, Any]) -> Dict[str, Any]:
    full_name = p.get("full_name") or "该仓库"
    language = p.get("language") or "未知语言"
    desc = p.get("description") or "（简介暂缺）"
    return {
        "editorial_outline": True,
        "bilibili_outline": "以「%s：%s」为题做一期上手演示视频。" % (full_name, desc),
        "xiaohongshu_points": [
            "一句话讲清 %s 解决什么问题。" % full_name,
            "配图展示 %s 项目的核心界面或目录结构。" % language,
        ],
        "douyin_hook": "3 秒钩子：这个用 %s 写的 %s，值得花 30 秒看一眼。" % (language, full_name),
        "counter_questions": ["可能出什么问题？", "结果能否复现？"],
        "resource_leads": ["官方文档", "相关仓库"],
    }


# --------------------------------------------------------------------------- #
# Validator
# --------------------------------------------------------------------------- #
def validate_pack(
    claims_by_id: Mapping[str, str],
    evidence_by_claim: Mapping[str, Sequence[Any]],
) -> None:
    """Validate claim-evidence integrity, per claim, before persistence.

    Each claim is checked against *its own* bound evidence only: numbers, URLs
    and full date/time tokens in the claim text must appear in that claim's
    evidence (snippet, payload or url). Borrowing another claim's evidence is
    rejected. Raises :class:`PackageValidationError` on any failure.
    """
    if not claims_by_id:
        raise PackageValidationError("no claims")
    for claim_id, claim_text in claims_by_id.items():
        evs = evidence_by_claim.get(claim_id)
        if not evs:
            raise PackageValidationError("claim without evidence")
        ev_nums: set = set()
        ev_urls: set = set()
        ev_dates: set = set()
        for ev in evs:
            if ev.url is not None and not _is_safe_https_url(ev.url):
                raise PackageValidationError("unsafe evidence url")
            n, u, d = _evidence_tokens(ev)
            ev_nums |= n
            ev_urls |= u
            ev_dates |= d
        c_nums, c_urls, c_dates = _extract_tokens(claim_text)
        for num in c_nums:
            if num not in ev_nums:
                raise PackageValidationError("claim number not traceable")
        for url in c_urls:
            if url not in ev_urls:
                raise PackageValidationError("claim url not traceable")
        for dt in c_dates:
            if dt not in ev_dates:
                raise PackageValidationError("claim datetime not traceable")


# --------------------------------------------------------------------------- #
# Pack builder
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class PackageResult:
    pack_id: str
    bundle_hash: str
    created: bool
    content: Mapping[str, Any]
    claim_evidence_links: Mapping[str, str]


def _advance_to_packaged(conn, signal_id: str, ts: datetime) -> None:
    """Advance ``verified -> packaged`` after the pack row has been persisted.

    Compare-and-swap: no-op if the signal is already packaged, so re-runs never
    duplicate the transition.
    """
    signal_repo = SignalRepository(conn)
    state_repo = StateTransitionRepository(conn)
    if signal_repo.advance_state(signal_id, "verified", "packaged"):
        state_repo.append(
            transition(signal_id, "verified", "packaged", timestamp=ts, stage="package")
        )


def build_and_store_pack(
    conn,
    *,
    week_key: str,
    event_id: str,
    signal_id: str,
    title: str,
    claims: Sequence[ClaimTouch],
    clock: Optional[datetime] = None,
) -> Optional[PackageResult]:
    """Build a deterministic MaterialPack for the given current-snapshot claims.

    ``claims`` are the ``ClaimTouch`` records produced by materialize for this
    snapshot; historical claims/evidence for the same event are never queried.
    Returns ``None`` if there are no claims. Raises
    :class:`PackageValidationError` if validation fails (nothing is written).
    The caller owns the transaction.
    """
    if not claims:
        return None
    ts = clock if clock is not None else now_utc()
    evidence_repo = EvidenceRepository(conn)
    pack_repo = MaterialPackRepository(conn)

    # Load only the current-snapshot evidence objects by explicit id.
    claims_by_id: Dict[str, str] = {}
    evidence_by_claim: Dict[str, List[Any]] = {}
    first_evidence = None
    for ct in claims:
        claims_by_id[ct.claim_id] = ct.text
        evs = []
        for ev_id in ct.evidence_ids:
            ev = evidence_repo.get(ev_id)
            if ev is None:
                raise PackageValidationError("claim evidence missing")
            evs.append(ev)
        evidence_by_claim[ct.claim_id] = evs
        if first_evidence is None and evs:
            first_evidence = evs[0]

    validate_pack(claims_by_id, evidence_by_claim)
    assert first_evidence is not None

    # Load the repository snapshot fields from the Signal payload for scoring
    # and for the Chinese project-info / editorial angles.
    sig = SignalRepository(conn).get(signal_id)
    repo_payload = dict(sig.payload) if sig is not None else {}

    claim_ids = tuple(claims_by_id.keys())
    claims_text = [claims_by_id[cid] for cid in claim_ids]

    content = {
        "schema_version": SCHEMA_VERSION,
        "signal_card": {"full_name": title, "event_id": event_id},
        "project_info": _build_project_info(repo_payload),
        "A_what_happened": _build_a(claim_ids, claims_text),
        "B_evidence_limits_unknowns": _build_b(
            first_evidence.id,
            first_evidence.source,
            first_evidence.url or "",
            first_evidence.snippet,
        ),
        "C_why_now_heat": _build_c(repo_payload),
        "D_audience_impacts": _build_d(repo_payload),
        "E_manual_test_plan": _build_e(repo_payload),
        "F_channel_adaptations": _build_f(repo_payload),
        "score": _build_c(repo_payload),
    }

    bundle_hash = sha256_hex(_canonical_json(content))
    pack = MaterialPack(
        week_key=week_key,
        content=content,
        bundle_hash=bundle_hash,
        created_at=ts,
        event_id=event_id,
        claim_ids=claim_ids,
    )
    created = not pack_repo.exists(pack.id)
    pack_repo.upsert(pack)

    # verified -> packaged only after the pack row is safely persisted.
    _advance_to_packaged(conn, signal_id, ts)

    # claim_id -> first evidence URL, for clickable Markdown links.
    claim_evidence_links = {
        ct.claim_id: (evidence_by_claim[ct.claim_id][0].url or "")
        for ct in claims
    }

    return PackageResult(
        pack_id=pack.id,
        bundle_hash=bundle_hash,
        created=created,
        content=content,
        claim_evidence_links=claim_evidence_links,
    )
