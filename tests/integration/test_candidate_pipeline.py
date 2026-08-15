"""Integration tests for :mod:`ai_signal.pipeline.qualify` (full DB path, v2)."""

import hashlib
import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from ai_signal.pipeline import qualify as qualify_module  # noqa: E402
from ai_signal.pipeline.qualify import FIXTURE_HOSTS, qualify_github  # noqa: E402
from ai_signal.storage import sqlite as S  # noqa: E402
from ai_signal.storage.candidate_repositories import (  # noqa: E402
    CandidateAssessmentRepository,
    CandidateDiscoveryRepository,
    CandidateRepository,
)

FIXTURES = pathlib.Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "candidates"
SCOPE = "emerging-ai-agent-v1"
TS_OLD = "2026-08-13T00:00:00+00:00"
TS_NEW = "2026-08-14T00:00:00+00:00"


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _seed(db, specs, week="2026-W33"):
    """specs: list of (run_id, scope_key, collected_at, payload).

    Mirrors the real collect path: ``raw_signal`` is globally deduplicated by
    ``(source, external_id, payload_sha256)`` while every run/scope records
    its own ``raw_signal_observation`` row.
    """
    S.initialize_database(db)
    conn = S._open(db)
    conn.execute("BEGIN")
    seen = set()
    for run_id, scope_key, collected_at, payload in specs:
        if (run_id, scope_key) not in seen:
            seen.add((run_id, scope_key))
            conn.execute(
                "INSERT INTO collection_run (id, week_key, started_at, status, "
                "config_snapshot, created_at) VALUES (?,?,?,?,?,?)",
                (run_id, week, collected_at, "success", "{}", collected_at),
            )
            conn.execute(
                "INSERT INTO source_run (id, collection_run_id, source, scope_key, "
                "source_version, status, started_at, finished_at, item_count, "
                "warning_count, warnings, cursor_advanced, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("sr-" + run_id + scope_key, run_id, "github", scope_key,
                 "github-rest-v1", "success", collected_at, collected_at,
                 1, 0, "[]", 0, collected_at),
            )
        text = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        psha = _sha(text)
        external_id = str(payload.get("id", "noid-" + psha[:12]))
        raw_id = _sha("cand" + external_id + psha)
        conn.execute(
            "INSERT INTO raw_signal (id, collection_run_id, source, external_id, "
            "payload, payload_sha256, source_version, collected_at, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(source, external_id, payload_sha256) DO NOTHING",
            (raw_id, run_id, "github", external_id, text, psha,
             "github-rest-v1", collected_at, collected_at),
        )
        conn.execute(
            "INSERT INTO raw_signal_observation (source_run_id, raw_signal_id, "
            "observed_at, created_at) VALUES (?,?,?,?)",
            ("sr-" + run_id + scope_key, raw_id, collected_at, collected_at),
        )
    conn.execute("COMMIT")
    conn.close()


def _adhd_payload():
    return json.loads((FIXTURES / "adhd_one_like.json").read_text(encoding="utf-8"))["payload"]


def _variants():
    return json.loads((FIXTURES / "variants.json").read_text(encoding="utf-8"))


def _count(conn, table):
    return conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]


class CandidatePipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ai_cand_pipe_")
        self.db = os.path.join(self.tmp, "test.db")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _qualify(self, scope=SCOPE, lane="emerging", limit=50, week="2026-W33"):
        conn = S._open(self.db)
        conn.execute("BEGIN")
        result = qualify_github(conn, week, scope, lane, limit, safe_hosts=FIXTURE_HOSTS)
        conn.execute("COMMIT")
        conn.close()
        return result

    def test_adhd_one_like_emerging_no_legacy_entities(self):
        _seed(self.db, [("r1", SCOPE, TS_OLD, _adhd_payload())])
        result = self._qualify()
        self.assertEqual(result.processed, 1)
        self.assertEqual(result.research, 1)
        conn = S._open(self.db)
        try:
            for table in ("signal", "event", "event_member", "claim", "evidence",
                          "claim_evidence", "material_pack"):
                self.assertEqual(_count(conn, table), 0, table)
            self.assertEqual(_count(conn, "candidate"), 1)
            self.assertEqual(_count(conn, "candidate_discovery"), 1)
            self.assertEqual(_count(conn, "candidate_assessment"), 1)
            row = conn.execute(
                "SELECT decision, trigger_kind, reason_codes, missing_evidence, attributes "
                "FROM candidate_assessment"
            ).fetchone()
            self.assertEqual(row["decision"], "research")
            self.assertEqual(row["trigger_kind"], "repository_snapshot")
            reasons = json.loads(row["reason_codes"])
            self.assertIn("agent_relevance_match", reasons)
            for item in ("specific_event", "readme", "latest_release", "testability"):
                self.assertIn(item, json.loads(row["missing_evidence"]))
            # homepage presence flag only, no URL.
            attrs = json.loads(row["attributes"])
            self.assertIn("homepage_present", attrs)
            self.assertNotIn("homepage", attrs)
        finally:
            conn.close()

    def test_cross_context_shares_candidate(self):
        # W33/emerging, W33/ecosystem, W34/ecosystem of the same repo.
        _seed(self.db, [("r1", SCOPE, TS_OLD, _adhd_payload())])
        _seed(self.db, [("r2", SCOPE, TS_OLD, _adhd_payload())], week="2026-W34")
        self._qualify(lane="emerging", week="2026-W33")
        self._qualify(lane="ecosystem", week="2026-W33")
        self._qualify(lane="ecosystem", week="2026-W34")
        conn = S._open(self.db)
        try:
            self.assertEqual(_count(conn, "candidate"), 1)
            self.assertEqual(_count(conn, "candidate_discovery"), 3)
            self.assertEqual(_count(conn, "candidate_assessment"), 3)
            # Ecosystem contexts are watch with the missing-relation code.
            rows = conn.execute(
                "SELECT ca.decision, ca.reason_codes FROM candidate_assessment ca "
                "JOIN candidate_discovery cd ON cd.id = ca.candidate_discovery_id "
                "WHERE cd.lane = 'ecosystem'"
            ).fetchall()
            self.assertEqual(len(rows), 2)
            for row in rows:
                self.assertEqual(row["decision"], "watch")
                self.assertIn("missing_ecosystem_relation", json.loads(row["reason_codes"]))
        finally:
            conn.close()

    def test_rerun_fully_idempotent(self):
        _seed(self.db, [("r1", SCOPE, TS_OLD, _adhd_payload())])
        self._qualify()
        conn = S._open(self.db)
        before = {t: _count(conn, t) for t in
                  ("candidate", "candidate_discovery", "candidate_assessment")}
        conn.close()
        result2 = self._qualify()
        self.assertEqual(result2.candidates_created, 0)
        self.assertEqual(result2.candidates_updated, 1)
        self.assertEqual(result2.discoveries_created, 0)
        self.assertEqual(result2.discoveries_existing, 1)
        self.assertEqual(result2.assessments_created, 0)
        conn = S._open(self.db)
        after = {t: _count(conn, t) for t in before}
        conn.close()
        self.assertEqual(before, after)

    def test_new_snapshot_new_discovery_shared_candidate(self):
        payload = _adhd_payload()
        _seed(self.db, [("r1", SCOPE, TS_OLD, payload)])
        self._qualify()
        changed = dict(payload, stargazers_count=2)
        _seed(self.db, [("r2", SCOPE, TS_NEW, changed)])
        result = self._qualify()
        self.assertEqual(result.candidates_created, 0)
        self.assertEqual(result.candidates_updated, 1)
        self.assertEqual(result.discoveries_created, 1)
        conn = S._open(self.db)
        try:
            self.assertEqual(_count(conn, "candidate"), 1)
            self.assertEqual(_count(conn, "candidate_discovery"), 2)
            self.assertEqual(_count(conn, "candidate_assessment"), 2)
        finally:
            conn.close()

    def test_old_replay_never_regresses_candidate(self):
        payload = _adhd_payload()
        # Newer snapshot first, in one scope.
        _seed(self.db, [("r1", "scope-new-v1", TS_NEW, payload)])
        self._qualify(scope="scope-new-v1")
        # An OLDER observation of the same repo (different title/url) replays
        # afterwards, in a different scope: same canonical_key, earlier time.
        older = dict(
            payload,
            full_name="example-org/older-name",
            html_url="https://example.com/example-org/older-name",
        )
        _seed(self.db, [("r2", "scope-old-v1", TS_OLD, older)])
        self._qualify(scope="scope-old-v1")
        conn = S._open(self.db)
        try:
            row = conn.execute(
                "SELECT title, url, first_seen_at, last_seen_at FROM candidate"
            ).fetchone()
            # Identity text keeps the newest snapshot's values.
            self.assertEqual(row["title"], "example-org/adhd-one-like")
            self.assertEqual(
                row["url"], "https://example.com/example-org/adhd-one-like"
            )
            # first_seen_at = historical min (the old observation);
            # last_seen_at = historical max (the new observation).
            self.assertEqual(row["first_seen_at"], TS_OLD)
            self.assertEqual(row["last_seen_at"], TS_NEW)
        finally:
            conn.close()

    def test_homepage_reason_codes_recorded(self):
        variants = _variants()
        http_payload = next(v["payload"] for v in variants if v["name"] == "http_homepage")
        https_payload = next(v["payload"] for v in variants if v["name"] == "https_homepage")
        _seed(self.db, [
            ("r1", SCOPE, TS_OLD, http_payload),
            ("r2", SCOPE, TS_OLD, https_payload),
        ])
        self._qualify(lane="emerging")
        conn = S._open(self.db)
        try:
            rows = conn.execute(
                "SELECT c.canonical_key, ca.reason_codes FROM candidate_assessment ca "
                "JOIN candidate_discovery cd ON cd.id = ca.candidate_discovery_id "
                "JOIN candidate c ON c.id = cd.candidate_id "
                "ORDER BY c.canonical_key"
            ).fetchall()
            by_key = {r["canonical_key"]: json.loads(r["reason_codes"]) for r in rows}
            self.assertIn("homepage_present_unverified", by_key["github:repository:90015"])
            self.assertIn("homepage_ignored_unsafe", by_key["github:repository:90014"])
        finally:
            conn.close()

    def test_scope_isolation_via_observation(self):
        _seed(self.db, [
            ("r1", "scope-a-v1", TS_OLD, _adhd_payload()),
            ("r2", "scope-b-v1", TS_OLD, _adhd_payload()),
        ])
        result_a = self._qualify(scope="scope-a-v1")
        self.assertEqual(result_a.processed, 1)
        conn = S._open(self.db)
        try:
            self.assertEqual(_count(conn, "candidate"), 1)
            self.assertEqual(_count(conn, "candidate_discovery"), 1)
        finally:
            conn.close()

    def test_limit_after_observation_dedup(self):
        payload = _adhd_payload()
        _seed(self.db, [
            ("r1", SCOPE, TS_OLD, payload),
            ("r2", SCOPE, TS_NEW, dict(payload, stargazers_count=7)),
        ])
        result = self._qualify(limit=5)
        self.assertEqual(result.processed, 1)

    def test_quarantined_records(self):
        specs = [
            ("r1", SCOPE, TS_OLD, next(v["payload"] for v in _variants() if v["name"] == "quarantine_identity")),
            ("r1", SCOPE, TS_OLD, next(v["payload"] for v in _variants() if v["name"] == "missing_identity")),
        ]
        _seed(self.db, specs)
        result = self._qualify()
        self.assertEqual(result.quarantined, 2)
        conn = S._open(self.db)
        try:
            self.assertEqual(_count(conn, "candidate"), 0)
        finally:
            conn.close()

    def test_bad_port_identity_quarantined_no_rows(self):
        payload = _adhd_payload()
        payload["html_url"] = "https://example.com:65536/repo"
        _seed(self.db, [("r1", SCOPE, TS_OLD, payload)])
        result = self._qualify()
        self.assertEqual(result.processed, 1)
        self.assertEqual(result.quarantined, 1)
        conn = S._open(self.db)
        try:
            for table in ("candidate", "candidate_discovery", "candidate_assessment"):
                self.assertEqual(_count(conn, table), 0, table)
        finally:
            conn.close()

    def test_bad_port_homepage_still_research_with_reason(self):
        payload = _adhd_payload()
        payload["homepage"] = "https://example.org:bad/"
        _seed(self.db, [("r1", SCOPE, TS_OLD, payload)])
        result = self._qualify(lane="emerging")
        self.assertEqual(result.research, 1)
        conn = S._open(self.db)
        try:
            row = conn.execute(
                "SELECT decision, reason_codes, attributes FROM candidate_assessment"
            ).fetchone()
            self.assertEqual(row["decision"], "research")
            self.assertIn("homepage_ignored_unsafe", json.loads(row["reason_codes"]))
            attrs = json.loads(row["attributes"])
            self.assertNotIn("https://example.org:bad/", json.dumps(attrs))
            self.assertIn("homepage_present", attrs)
        finally:
            conn.close()

    def test_transaction_rollback_on_storage_failure(self):
        _seed(self.db, [("r1", SCOPE, TS_OLD, _adhd_payload())])
        original = qualify_module.CandidateRepository.upsert

        def boom(self, candidate):
            raise S.StorageError("injected failure")

        qualify_module.CandidateRepository.upsert = boom
        try:
            conn = S._open(self.db)
            conn.execute("BEGIN")
            with self.assertRaises(S.StorageError):
                qualify_github(conn, "2026-W33", SCOPE, "emerging", 50, safe_hosts=FIXTURE_HOSTS)
            conn.execute("ROLLBACK")
            conn.close()
        finally:
            qualify_module.CandidateRepository.upsert = original
        conn = S._open(self.db)
        try:
            for table in ("candidate", "candidate_discovery", "candidate_assessment"):
                self.assertEqual(_count(conn, table), 0, table)
        finally:
            conn.close()

    def test_latest_assessment_query_by_decision(self):
        _seed(self.db, [("r1", SCOPE, TS_OLD, _adhd_payload())])
        self._qualify(lane="emerging")
        self._qualify(lane="ecosystem")
        conn = S._open(self.db)
        try:
            repo = CandidateAssessmentRepository(conn)
            research = repo.list_for_context("2026-W33", SCOPE, "emerging", decision="research")
            watch = repo.list_for_context("2026-W33", SCOPE, "ecosystem", decision="watch")
            self.assertEqual(len(research), 1)
            self.assertEqual(len(watch), 1)
            cand_repo = CandidateRepository(conn)
            self.assertEqual(len(cand_repo.get_by_identity("github", "github:repository:90001").id), 64)
        finally:
            conn.close()

    def test_real_collect_to_qualify_end_to_end(self):
        """fake GitHub REST response -> GitHubRestClient -> collect_source_once
        -> raw_signal (real collect path incl. observation) -> qualify_github.

        Proves the fields collected by the real GitHubSource whitelist actually
        reach the qualification gate (instead of a hand-INSERTed payload).
        """
        import json as _json

        from ai_signal.pipeline.collect import collect_source_once
        from ai_signal.sources.github_rest import (
            GitHubRestClient,
            GitHubSearchSpec,
            HttpResponse,
        )

        def _rest_item(repo_id, fork):
            return {
                "id": repo_id,
                "full_name": "example-org/e2e-%d" % repo_id,
                "html_url": "https://example.com/example-org/e2e-%d" % repo_id,
                "description": (
                    "An agent harness with substantive length for the gate check."
                ),
                "topics": ["agent", "harness"],
                "language": "Go",
                "stargazers_count": 2,
                "forks_count": 1,
                "created_at": "2026-06-01T00:00:00+00:00",
                "updated_at": "2026-08-10T00:00:00+00:00",
                "pushed_at": "2026-08-10T00:00:00+00:00",
                "fork": fork,
                "archived": False,
                "disabled": False,
                "is_template": False,
            }

        body = _json.dumps(
            {
                "total_count": 2,
                "items": [_rest_item(1, fork=False), _rest_item(2, fork=True)],
            }
        ).encode("utf-8")

        class _FakeTransport:
            def get(self, url, headers, timeout_seconds, max_response_bytes):
                return HttpResponse(
                    status=200,
                    headers={"Content-Type": "application/json"},
                    body=body,
                    final_url="https://api.github.com/search/repositories",
                )

        spec = GitHubSearchSpec(query="topic:agent", per_page=10, max_pages=1)
        client = GitHubRestClient(spec, _FakeTransport())
        collect_result = collect_source_once(
            self.db, "github", "2026-W33", scope_key=SCOPE,
            client=client,
            config_snapshot={
                "run_mode": "shadow", "source": "github", "scope_key": SCOPE,
                "adapter_kind": "github-rest-v1",
                "query_sha256": spec.query_sha256,
                "sort": spec.sort, "order": spec.order,
                "per_page": spec.per_page, "max_pages": spec.max_pages,
            },
        )
        self.assertEqual(collect_result.status, "success")
        self.assertEqual(collect_result.processed_item_count, 2)

        # The real whitelist carried fork=true for repo 2 all the way through.
        conn = S._open(self.db)
        try:
            payloads = {
                row["external_id"]: _json.loads(row["payload"])
                for row in conn.execute("SELECT external_id, payload FROM raw_signal")
            }
            self.assertTrue(payloads["2"]["fork"])
            self.assertFalse(payloads["1"]["fork"])
        finally:
            conn.close()

        result = self._qualify(lane="emerging")
        self.assertEqual(result.processed, 2)
        # repo 1: young + recent push + agent-relevant -> research.
        # repo 2: fork=true collected by the REAL source -> reject.
        self.assertEqual(result.research, 1)
        self.assertEqual(result.rejected, 1)
        conn = S._open(self.db)
        try:
            rows = conn.execute(
                "SELECT ca.decision, ca.reason_codes FROM candidate_assessment ca "
                "JOIN candidate_discovery cd ON cd.id = ca.candidate_discovery_id "
                "JOIN candidate c ON c.id = cd.candidate_id "
                "ORDER BY c.canonical_key"
            ).fetchall()
            self.assertEqual(rows[0]["decision"], "research")
            self.assertEqual(rows[1]["decision"], "reject")
            self.assertIn(
                "agent_relevance_match", _json.loads(rows[0]["reason_codes"])
            )
            self.assertIn("repository_is_fork", _json.loads(rows[1]["reason_codes"]))
        finally:
            conn.close()

    def test_adhd_one_like_all_four_lanes(self):
        _seed(self.db, [("r1", SCOPE, TS_OLD, _adhd_payload())])
        expected = {
            "emerging": "research",
            "mature": "watch",
            "watchlist": "research",
            "ecosystem": "watch",
        }
        for lane, decision in expected.items():
            with self.subTest(lane=lane):
                result = self._qualify(lane=lane)
                self.assertEqual(result.research if decision == "research" else result.watch, 1)


if __name__ == "__main__":
    unittest.main()
