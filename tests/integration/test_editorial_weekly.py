"""Integration tests for 2E editorial gate, 2F briefs and Phase 3 weekly run.

Offline only: a routing fake transport answers search, repos, readme and
releases endpoints, so the full weekly flow runs end to end without network.
"""

import base64
import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from ai_signal.domain.models import (  # noqa: E402
    ResearchDossier,
    ResearchFact,
)
from ai_signal.outputs.content_brief import (  # noqa: E402
    ContentBriefError,
    publish_content_brief,
)
from ai_signal.pipeline.editorial import decide_editorial  # noqa: E402
from ai_signal.pipeline.weekly import run_weekly  # noqa: E402
from ai_signal.sources.github_rest import HttpResponse  # noqa: E402
from ai_signal.storage import sqlite as S  # noqa: E402
from ai_signal.storage.research_repositories import (  # noqa: E402
    EditorialDecisionRepository,
    ResearchDossierRepository,
    ResearchFactRepository,
)

TS = "2026-08-15T08:00:00+00:00"


def _b64(text):
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _repo_item(repo_id, full_name=None, description=None):
    return {
        "id": repo_id,
        "full_name": full_name or ("example-org/repo-%d" % repo_id),
        "html_url": "https://github.com/" + (full_name or "example-org/repo-%d" % repo_id),
        "description": description or (
            "An AI agent harness that wraps multiple models for real task "
            "automation with a substantive enough description for the gate."
        ),
        "topics": ["agent", "harness"],
        "language": "Rust",
        "stargazers_count": 150,
        "forks_count": 12,
        "created_at": "2026-06-01T00:00:00+00:00",
        "updated_at": "2026-08-14T00:00:00+00:00",
        "pushed_at": "2026-08-14T00:00:00+00:00",
        "homepage": None,
        "fork": False,
        "archived": False,
        "disabled": False,
        "is_template": False,
    }


class _WeeklyFakeTransport:
    """Answers every endpoint the weekly flow touches."""

    def __init__(self):
        self.calls = []

    def get(self, url, headers, timeout_seconds, max_response_bytes):
        self.calls.append(url)
        path = urlparse(url).path
        parts = [p for p in path.split("/") if p]
        if path == "/search/repositories":
            query = parse_qs(urlparse(url).query).get("q", [""])[0]
            repo_id = 200 + abs(hash(query)) % 100
            body = json.dumps(
                {"total_count": 1, "items": [_repo_item(repo_id)]}
            ).encode("utf-8")
        elif path.startswith("/repos/") and path.endswith("/readme"):
            full_name = parts[1] + "/" + parts[2]
            text = "# %s\nA faster, minimal agent harness for terminal work." % full_name
            body = json.dumps({"content": _b64(text)}).encode("utf-8")
        elif path.startswith("/repos/") and path.endswith("/releases"):
            body = json.dumps([
                {
                    "tag_name": "v0.9.0",
                    "name": "v0.9.0",
                    "body": "Weekly release with improvements.",
                    "published_at": "2026-08-12T14:08:30Z",
                    "html_url": "https://github.com/x/y/releases/tag/v0.9.0",
                },
                {
                    "tag_name": "v0.8.0",
                    "name": "v0.8.0",
                    "body": "Previous release.",
                    "published_at": "2026-08-05T14:08:30Z",
                    "html_url": "https://github.com/x/y/releases/tag/v0.8.0",
                },
            ]).encode("utf-8")
        elif path.startswith("/repos/"):
            full_name = parts[1] + "/" + parts[2]
            repo_id = 100 + abs(hash(full_name)) % 100
            body = json.dumps(_repo_item(repo_id, full_name=full_name)).encode("utf-8")
        else:
            raise RuntimeError("unexpected path")
        return HttpResponse(
            status=200,
            headers={"Content-Type": "application/json"},
            body=body,
            final_url="https://api.github.com" + path,
        )


class EditorialGateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ai_editorial_")
        self.db = os.path.join(self.tmp, "test.db")
        S.initialize_database(self.db)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed_dossier(self, facts, needs_testing=False):
        event_id = "e" * 64
        dossier = ResearchDossier(
            event_candidate_id=event_id,
            summary_judgment="机器草案",
            timeline=(),
            target_audience="开发者",
            job_to_be_done="选工具",
            limits_unknowns=("无独立评测",),
            forbidden_claims=("爆火",),
            needs_testing=needs_testing,
            test_plan=(),
            bundle_hash="b" * 64,
        )
        conn = S._open(self.db)
        conn.execute("BEGIN")
        # The dossier has an FK to event_candidate: seed the parent row.
        conn.execute(
            "INSERT INTO event_candidate (id, signal_type, subject, "
            "change_summary, affected_audience, work_impact_hypothesis, "
            "missing_evidence, research_priority, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (event_id, "tool_workflow_change", "测试事件", "变化待研究",
             "开发者", "选工具", "[]", 50, TS, TS),
        )
        stored = ResearchDossierRepository(conn).insert_or_get(dossier)
        for spec in facts:
            ResearchFactRepository(conn).insert_or_get(ResearchFact(
                dossier_id=stored.id,
                kind=spec[0],
                text=spec[1],
                source_kind=spec[2],
                source_url=spec[3] if len(spec) > 3 else None,
            ))
        conn.execute("COMMIT")
        conn.close()
        return stored.id

    def _decide(self, dossier_id):
        conn = S._open(self.db)
        conn.execute("BEGIN")
        decision = decide_editorial(conn, dossier_id)
        conn.execute("COMMIT")
        conn.close()
        return decision

    def test_ready_to_write_with_substantive_release(self):
        dossier_id = self._seed_dossier([
            ("fact", "发布 v0.3.0（2026-08-12）：" + "内容" * 30,
             "github_release", "https://github.com/x/y/releases/tag/v0.3.0"),
        ])
        decision = self._decide(dossier_id)
        self.assertEqual(decision.decision, "ready_to_write")
        self.assertIn("first_party_change_evidence", decision.reason_codes)

    def test_needs_testing_with_experience_claim(self):
        dossier_id = self._seed_dossier([
            ("fact", "发布 v0.3.0（2026-08-12）：" + "内容" * 30,
             "github_release", "https://github.com/x/y/releases/tag/v0.3.0"),
        ], needs_testing=True)
        decision = self._decide(dossier_id)
        self.assertEqual(decision.decision, "needs_testing")
        self.assertIn("experience_claim_detected", decision.reason_codes)

    def test_watch_without_change_evidence(self):
        dossier_id = self._seed_dossier([
            ("unknown", "README 不可读", "github_readme",
             "https://github.com/x/y"),
        ])
        decision = self._decide(dossier_id)
        self.assertEqual(decision.decision, "watch")
        self.assertIn("no_first_party_change_evidence", decision.reason_codes)

    def test_thin_evidence_watch(self):
        dossier_id = self._seed_dossier([
            ("fact", "发布 v0.3.0", "github_release",
             "https://github.com/x/y/releases/tag/v0.3.0"),
        ])
        decision = self._decide(dossier_id)
        self.assertEqual(decision.decision, "watch")
        self.assertIn("thin_evidence", decision.reason_codes)

    def test_new_fact_yields_new_revision(self):
        dossier_id = self._seed_dossier([
            ("fact", "发布 v0.3.0（2026-08-12）：" + "内容" * 30,
             "github_release", "https://github.com/x/y/releases/tag/v0.3.0"),
        ])
        first = self._decide(dossier_id)
        # Add a new fact: the input hash changes -> a second revision.
        conn = S._open(self.db)
        conn.execute("BEGIN")
        ResearchFactRepository(conn).insert_or_get(ResearchFact(
            dossier_id=dossier_id, kind="fact",
            text="上一版本 v0.2.0（2026-08-01）",
            source_kind="github_release",
            source_url="https://github.com/x/y/releases/tag/v0.2.0",
        ))
        conn.execute("COMMIT")
        conn.close()
        second = self._decide(dossier_id)
        self.assertNotEqual(first.input_hash, second.input_hash)
        conn = S._open(self.db)
        try:
            rows = EditorialDecisionRepository(conn).list_for_dossier(dossier_id)
        finally:
            conn.close()
        self.assertEqual(len(rows), 2)

    def test_official_evidence_updates_decision(self):
        from ai_signal.pipeline.research import attach_official_evidence
        from ai_signal.sources.github_rest import HttpResponse

        class _FakeOfficialTransport:
            def __init__(self):
                self.calls = []

            def get(self, url, headers, timeout_seconds, max_response_bytes):
                self.calls.append(url)
                return HttpResponse(
                    status=200,
                    headers={"Content-Type": "text/html"},
                    body=b"<html><body>Official announcement with new pricing "
                         b"table and effective date.</body></html>",
                    final_url=url,
                )

        dossier_id = self._seed_dossier([
            ("fact", "发布 v0.3.0（2026-08-12）：" + "内容" * 30,
             "github_release", "https://github.com/x/y/releases/tag/v0.3.0"),
        ])
        first = self._decide(dossier_id)
        transport = _FakeOfficialTransport()
        conn = S._open(self.db)
        conn.execute("BEGIN")
        fact = attach_official_evidence(
            conn,
            dossier_id,
            "https://example.com/announcement",
            allow_network=True,
            transport_factory=lambda: transport,
        )
        conn.execute("COMMIT")
        conn.close()
        self.assertEqual(fact.source_kind, "official_page")
        self.assertEqual(fact.source_url, "https://example.com/announcement")
        second = self._decide(dossier_id)
        self.assertNotEqual(first.input_hash, second.input_hash)
        self.assertEqual(second.decision, "ready_to_write")
        conn = S._open(self.db)
        try:
            rows = EditorialDecisionRepository(conn).list_for_dossier(dossier_id)
        finally:
            conn.close()
        self.assertEqual(len(rows), 2)


class WeeklyPipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ai_weekly_")
        self.db = os.path.join(self.tmp, "test.db")
        self.out = os.path.join(self.tmp, "out")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_weekly_end_to_end(self):
        transport = _WeeklyFakeTransport()
        report = run_weekly(
            self.db,
            "2026-W33",
            allow_network=True,
            output_root=self.out,
            allow_output_write=True,
            research_limit=2,
            transport_factory=lambda: transport,
        )
        self.assertEqual(report.discovery_policies, 4)
        self.assertEqual(report.discovery_success, 4)
        self.assertEqual(report.discovery_degraded, 0)
        self.assertGreaterEqual(report.promoted, 1)
        self.assertEqual(report.research_attempted, 2)
        self.assertEqual(report.dossiers_built, 2)
        self.assertEqual(report.research_failures, 0)
        # Readme carries "faster" -> needs_testing for every researched draft.
        self.assertEqual(report.decisions_needs_testing, 2)
        # needs_testing dossiers DO get briefs (only watch is skipped).
        self.assertEqual(report.briefs_written, 2)
        files = list(Path(self.out).glob("Content/*.md"))
        self.assertEqual(len(files), 2)
        text = files[0].read_text(encoding="utf-8")
        self.assertIn("editorial: needs_testing", text)
        self.assertIn("禁说清单", text)
        self.assertIn("本人测试计划", text)
        # The four-question angle section is part of the draft-ready brief.
        self.assertIn("角度（四问翻译草稿）", text)
        self.assertIn("标题草稿（人工）", text)

    def test_weekly_requires_network(self):
        from ai_signal.pipeline.collect import CollectionPolicyError

        with self.assertRaises(CollectionPolicyError):
            run_weekly(self.db, "2026-W33")
        self.assertFalse(os.path.exists(self.db))


class ContentBriefTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ai_brief_")
        self.out = Path(self.tmp) / "out"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _dossier_and_event(self):
        from ai_signal.domain.models import EditorialDecision, EventCandidate

        event = EventCandidate(
            signal_type="economics_access",
            subject="DeepSeek V4 API",
            change_summary="峰谷计价生效",
            affected_audience="开发者",
            work_impact_hypothesis="成本重算",
            research_priority=90,
        )
        dossier = ResearchDossier(
            event_candidate_id=event.id,
            summary_judgment="机器草案",
            timeline=("2026-08-16 峰谷计价生效 (https://api-docs.deepseek.com)",),
            target_audience="开发者",
            job_to_be_done="成本重算",
            limits_unknowns=("无独立评测",),
            forbidden_claims=("爆火",),
            needs_testing=False,
            test_plan=(),
            bundle_hash="b" * 64,
        )
        decision = EditorialDecision(
            dossier_id=dossier.id,
            policy_version="editorial-v1",
            input_hash="a" * 64,
            decision="ready_to_write",
            reason_codes=("first_party_change_evidence",),
        )
        facts = [
            ResearchFact(
                dossier_id=dossier.id, kind="fact",
                text="官方价目表：off-peak $0.66/$1.98，peak $1.32/$3.96",
                source_kind="official_page",
                source_url="https://api-docs.deepseek.com/quick_start/pricing",
            )
        ]
        return event, dossier, facts, decision

    def test_publish_brief_safe_name_and_content(self):
        event, dossier, facts, decision = self._dossier_and_event()
        path = publish_content_brief(self.out, "2026-W33", event, dossier, facts, decision)
        self.assertTrue(path.exists())
        self.assertIn("2026-W33", path.name)
        self.assertIn("DeepSeek V4 API", path.name)
        resolved = path.resolve()
        self.assertTrue(str(resolved).startswith(str(self.out.resolve())))
        text = path.read_text(encoding="utf-8")
        self.assertIn("editorial: ready_to_write", text)
        self.assertIn("api-docs.deepseek.com", text)

    def test_weird_subject_sanitized(self):
        from ai_signal.domain.models import EditorialDecision, EventCandidate

        event = EventCandidate(
            signal_type="economics_access",
            subject='bad/../../name:*?',
            change_summary="x",
            affected_audience="开发者",
            work_impact_hypothesis="y",
            research_priority=10,
        )
        dossier = ResearchDossier(
            event_candidate_id=event.id,
            summary_judgment="草案", timeline=(),
            target_audience="开发者", job_to_be_done="y",
            limits_unknowns=(), forbidden_claims=(),
            needs_testing=False, test_plan=(), bundle_hash="b" * 64,
        )
        decision = EditorialDecision(
            dossier_id=dossier.id, policy_version="editorial-v1",
            input_hash="a" * 64, decision="watch",
            reason_codes=("no_first_party_change_evidence",),
        )
        path = publish_content_brief(self.out, "2026-W33", event, dossier, [], decision)
        self.assertTrue(path.exists())
        self.assertNotIn("..", path.name)
        self.assertTrue(str(path.resolve()).startswith(str(self.out.resolve())))


if __name__ == "__main__":
    unittest.main()
