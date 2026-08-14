"""Integration tests for the phase 2B2 materialization + packaging pipeline."""

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

from ai_signal.pipeline import package as package_module  # noqa: E402
from ai_signal.pipeline.materialize import FIXTURE_HOSTS, materialize_github  # noqa: E402
from ai_signal.pipeline.package import PackageValidationError, build_and_store_pack  # noqa: E402
from ai_signal.storage import sqlite as S  # noqa: E402
from ai_signal.storage.repositories import SignalRepository  # noqa: E402

WEEK = "2026-W33"
SCOPE = "ai-agents-v1"
TS_OLD = "2026-08-13T00:00:00+00:00"
TS_NEW = "2026-08-14T00:00:00+00:00"


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _payload(repo_id, stars, updated_at, forks=1, language="Python"):
    return {
        "id": repo_id,
        "full_name": "example-org/example-%d" % repo_id,
        "html_url": "https://example.com/example-org/example-%d" % repo_id,
        "description": "an example repository",
        "language": language,
        "stargazers_count": stars,
        "forks_count": forks,
        "topics": ["example"],
        "pushed_at": "2026-08-01T00:00:00+00:00",
        "updated_at": updated_at,
    }


def _insert_raw(conn, run_id, repo_payload, collected_at):
    external_id = str(repo_payload["id"])
    payload = json.dumps(repo_payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    psha = _sha(payload)
    rid = _sha("github" + external_id + psha + collected_at)
    conn.execute(
        "INSERT INTO raw_signal (id, collection_run_id, source, external_id, "
        "payload, payload_sha256, source_version, collected_at, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (rid, run_id, "github", external_id, payload, psha, "github-rest-v1",
         collected_at, collected_at),
    )
    # Attribution: the source_run for this run observed this snapshot.
    conn.execute(
        "INSERT INTO raw_signal_observation (source_run_id, raw_signal_id, "
        "observed_at, created_at) VALUES (?,?,?,?)",
        ("sr-" + run_id, rid, collected_at, collected_at),
    )
    return rid


def _seed(db, specs, week=WEEK):
    """specs: list of (run_id, scope_key, collected_at, repo_payload)."""
    S.initialize_database(db)
    conn = S._open(db)
    conn.execute("BEGIN")
    seen_runs = set()
    for run_id, scope_key, collected_at, repo_payload in specs:
        if (run_id, scope_key) not in seen_runs:
            seen_runs.add((run_id, scope_key))
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
                ("sr-" + run_id, run_id, "github", scope_key, "github-rest-v1",
                 "success", collected_at, collected_at, 1, 0, "[]", 0, collected_at),
            )
        _insert_raw(conn, run_id, repo_payload, collected_at)
    conn.execute("COMMIT")
    conn.close()


def _count(conn, table):
    return conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]


def _materialize_and_pack(db, limit=10, scope=SCOPE):
    """Run materialize + pack in one transaction; return (mat_result, packs)."""
    conn = S._open(db)
    packs = []
    conn.execute("BEGIN")
    mat = materialize_github(conn, WEEK, scope, limit, safe_hosts=FIXTURE_HOSTS)
    for touch in mat.events:
        pr = build_and_store_pack(
            conn,
            week_key=WEEK,
            event_id=touch.event_id,
            signal_id=touch.signal_id,
            title=touch.title,
            claims=touch.claims,
        )
        if pr is not None:
            packs.append(pr)
    conn.execute("COMMIT")
    conn.close()
    return mat, packs


class MaterialPipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ai_signal_2b2_")
        self.db = os.path.join(self.tmp, "test.db")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed_two_repos(self):
        _seed(
            self.db,
            [
                ("r1", SCOPE, TS_OLD, _payload(2001, 42, "2026-08-10T00:00:00+00:00")),
                ("r1", SCOPE, TS_OLD, _payload(2002, 15, "2026-08-09T00:00:00+00:00", language="Rust")),
            ],
        )

    def test_full_chain_packaged_and_exact_counts(self):
        self._seed_two_repos()
        mat, packs = _materialize_and_pack(self.db)
        self.assertEqual(mat.processed, 2)
        self.assertEqual(len(packs), 2)
        conn = S._open(self.db)
        try:
            self.assertEqual(_count(conn, "signal"), 2)
            self.assertEqual(_count(conn, "event"), 2)
            self.assertEqual(_count(conn, "event_member"), 2)
            self.assertEqual(_count(conn, "evidence"), 2)
            self.assertEqual(_count(conn, "claim"), 12)  # 6 per repo
            self.assertEqual(_count(conn, "claim_evidence"), 12)
            self.assertEqual(_count(conn, "material_pack"), 2)
            self.assertEqual(_count(conn, "state_transition"), 8)  # 4 per signal
            states = [r["state"] for r in conn.execute("SELECT state FROM signal")]
            self.assertEqual(states, ["packaged", "packaged"])
        finally:
            conn.close()

    def test_rerun_exact_idempotency(self):
        self._seed_two_repos()
        _materialize_and_pack(self.db)
        conn = S._open(self.db)
        before = {t: _count(conn, t) for t in
                  ("signal", "event", "event_member", "evidence", "claim",
                   "claim_evidence", "material_pack", "state_transition")}
        conn.close()
        _materialize_and_pack(self.db)
        conn = S._open(self.db)
        after = {t: _count(conn, t) for t in before}
        conn.close()
        self.assertEqual(before, after)
        self.assertEqual(after["signal"], 2)
        self.assertEqual(after["state_transition"], 8)

    def test_latest_snapshot_wins(self):
        # Two snapshots of repo 2001: 42 stars (old), 142 stars (new).
        _seed(
            self.db,
            [
                ("r1", SCOPE, TS_OLD, _payload(2001, 42, "2026-08-10T00:00:00+00:00")),
                ("r2", SCOPE, TS_NEW, _payload(2001, 142, "2026-08-12T00:00:00+00:00")),
            ],
        )
        mat, _ = _materialize_and_pack(self.db)
        self.assertEqual(mat.processed, 1)  # deduplicated to one repository
        conn = S._open(self.db)
        try:
            self.assertEqual(_count(conn, "signal"), 1)
            row = conn.execute("SELECT raw_signal_id, last_seen_at, payload FROM signal").fetchone()
            self.assertEqual(json.loads(row["payload"])["stargazers_count"], 142)
            self.assertEqual(row["last_seen_at"], TS_NEW)
            latest_raw = conn.execute(
                "SELECT id FROM raw_signal WHERE collected_at = ?", (TS_NEW,)
            ).fetchone()["id"]
            self.assertEqual(row["raw_signal_id"], latest_raw)
        finally:
            conn.close()

    def test_old_snapshot_does_not_overwrite_new(self):
        # SignalRepository monotonic refresh: an older observation must not
        # overwrite last_seen / raw_signal_id / payload of a newer one.
        _seed(
            self.db,
            [("r1", SCOPE, TS_NEW, _payload(2001, 142, "2026-08-12T00:00:00+00:00"))],
        )
        conn = S._open(self.db)
        conn.execute("BEGIN")
        materialize_github(conn, WEEK, SCOPE, 10, safe_hosts=FIXTURE_HOSTS)
        conn.execute("COMMIT")
        conn.close()

        # Now attempt a refresh with an OLDER observation (same repo, 42 stars).
        from ai_signal.domain.models import Signal
        conn = S._open(self.db)
        raw_id = conn.execute("SELECT id FROM raw_signal LIMIT 1").fetchone()["id"]
        repo = SignalRepository(conn)
        old_signal = Signal(
            collection_run_id="r1",
            source="github",
            canonical_key="github:repository:2001",
            raw_signal_id=raw_id,
            first_seen_at=datetime.fromisoformat(TS_NEW),
            signal_type="github_repository",
            title="example-org/example-2001",
            url="https://example.com/example-org/example-2001",
            payload=_payload(2001, 42, "2026-08-10T00:00:00+00:00"),
            last_seen_at=datetime.fromisoformat(TS_OLD),
            updated_at=datetime.fromisoformat(TS_OLD),
        )
        repo.upsert(old_signal)
        row = conn.execute("SELECT last_seen_at, payload FROM signal").fetchone()
        conn.close()
        self.assertEqual(row["last_seen_at"], TS_NEW)  # unchanged (older)
        self.assertEqual(json.loads(row["payload"])["stargazers_count"], 142)

    def test_limit_applies_after_dedup(self):
        _seed(
            self.db,
            [
                ("r1", SCOPE, TS_OLD, _payload(2001, 42, "2026-08-10T00:00:00+00:00")),
                ("r2", SCOPE, TS_NEW, _payload(2001, 142, "2026-08-12T00:00:00+00:00")),
                ("r1", SCOPE, TS_OLD, _payload(2002, 15, "2026-08-09T00:00:00+00:00")),
            ],
        )
        conn = S._open(self.db)
        conn.execute("BEGIN")
        mat = materialize_github(conn, WEEK, SCOPE, limit=1, safe_hosts=FIXTURE_HOSTS)
        conn.execute("COMMIT")
        conn.close()
        # 3 raw snapshot rows but only 2 distinct repos; limit=1 -> 1 repo.
        self.assertEqual(mat.processed, 1)

    def test_scope_isolation(self):
        _seed(
            self.db,
            [
                ("r1", "scope-a-v1", TS_OLD, _payload(2001, 42, "2026-08-10T00:00:00+00:00")),
                ("r2", "scope-b-v1", TS_OLD, _payload(2002, 15, "2026-08-09T00:00:00+00:00")),
            ],
        )
        conn = S._open(self.db)
        conn.execute("BEGIN")
        mat_a = materialize_github(conn, WEEK, "scope-a-v1", 10, safe_hosts=FIXTURE_HOSTS)
        conn.execute("COMMIT")
        conn.close()
        self.assertEqual(mat_a.processed, 1)
        self.assertEqual(mat_a.events[0].title, "example-org/example-2001")

    def test_overlap_scope_materialize_both_scopes(self):
        # Same snapshot collected by scope-a and scope-b must be visible to
        # both scopes when materialized, without duplicating raw_signal.
        from ai_signal.pipeline.collect import collect_source_once
        from ai_signal.sources.github_fixture import FixtureGitHubClient

        fixture = pathlib.Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "github" / "pages.json"
        for scope in ("scope-a-v1", "scope-b-v1"):
            client = FixtureGitHubClient(fixture)
            collect_source_once(
                self.db, "github", WEEK, scope_key=scope, client=client,
                config_snapshot={
                    "run_mode": "shadow", "source": "github", "scope_key": scope,
                    "adapter_kind": "fixture", "fixture": True,
                    "fixture_sha256": client.fixture_sha256,
                },
            )
        conn = S._open(self.db)
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM raw_signal").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM raw_signal_observation").fetchone()[0], 4)
        finally:
            conn.close()
        for scope in ("scope-a-v1", "scope-b-v1"):
            conn = S._open(self.db)
            conn.execute("BEGIN")
            mat = materialize_github(conn, WEEK, scope, 10, safe_hosts=FIXTURE_HOSTS)
            conn.execute("COMMIT")
            conn.close()
            self.assertEqual(mat.processed, 2)

    def test_cross_week_new_observation_updates_last_seen_and_pack(self):
        # One identical snapshot observed in week W32 and again in week W33.
        S.initialize_database(self.db)
        payload = _payload(2001, 42, "2026-08-10T00:00:00+00:00")
        payload_json = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        psha = _sha(payload_json)
        rs_id = _sha("github" + "2001" + psha + "x")
        conn = S._open(self.db)
        conn.execute("BEGIN")
        for run_id, week, ts in (("r1", "2026-W32", TS_OLD), ("r2", "2026-W33", TS_NEW)):
            conn.execute(
                "INSERT INTO collection_run (id, week_key, started_at, status, "
                "config_snapshot, created_at) VALUES (?,?,?,?,?,?)",
                (run_id, week, ts, "success", "{}", ts),
            )
            conn.execute(
                "INSERT INTO source_run (id, collection_run_id, source, scope_key, "
                "source_version, status, started_at, finished_at, item_count, "
                "warning_count, warnings, cursor_advanced, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("sr-" + run_id, run_id, "github", "scope-a-v1", "github-rest-v1",
                 "success", ts, ts, 1, 0, "[]", 0, ts),
            )
            if run_id == "r1":
                # First observation also creates the content-addressed snapshot.
                conn.execute(
                    "INSERT INTO raw_signal (id, collection_run_id, source, external_id, "
                    "payload, payload_sha256, source_version, collected_at, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (rs_id, run_id, "github", "2001", payload_json, psha,
                     "github-rest-v1", ts, ts),
                )
            conn.execute(
                "INSERT INTO raw_signal_observation (source_run_id, raw_signal_id, "
                "observed_at, created_at) VALUES (?,?,?,?)",
                ("sr-" + run_id, rs_id, ts, ts),
            )
        conn.execute("COMMIT")
        conn.close()

        # raw_signal stays globally deduplicated.
        conn = S._open(self.db)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM raw_signal").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM raw_signal_observation").fetchone()[0], 2)
        conn.close()

        def materialize(week):
            c = S._open(self.db)
            c.execute("BEGIN")
            mat = materialize_github(c, week, "scope-a-v1", 10, safe_hosts=FIXTURE_HOSTS)
            touch = mat.events[0]
            build_and_store_pack(
                c, week_key=week, event_id=touch.event_id, signal_id=touch.signal_id,
                title=touch.title, claims=touch.claims,
            )
            c.execute("COMMIT")
            c.close()
            return mat

        materialize("2026-W32")
        conn = S._open(self.db)
        last_seen_w32 = conn.execute("SELECT last_seen_at FROM signal").fetchone()[0]
        conn.close()
        self.assertEqual(last_seen_w32, TS_OLD)

        materialize("2026-W33")
        conn = S._open(self.db)
        self.assertEqual(conn.execute("SELECT last_seen_at FROM signal").fetchone()[0], TS_NEW)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM signal").fetchone()[0], 1)  # no dup
        # A pack was generated for the new week.
        weeks = {r["week_key"] for r in conn.execute("SELECT week_key FROM material_pack")}
        conn.close()
        self.assertIn("2026-W33", weeks)

    def test_pack_build_failure_does_not_reach_packaged(self):
        self._seed_two_repos()
        # materialize (committed) -> signals at "verified".
        conn = S._open(self.db)
        conn.execute("BEGIN")
        mat = materialize_github(conn, WEEK, SCOPE, 10, safe_hosts=FIXTURE_HOSTS)
        conn.execute("COMMIT")
        conn.close()

        # Force packaging validation to fail; verified -> packaged must not run.
        original = package_module.validate_pack
        package_module.validate_pack = lambda *a, **k: (_ for _ in ()).throw(
            PackageValidationError("boom")
        )
        try:
            conn = S._open(self.db)
            conn.execute("BEGIN")
            with self.assertRaises(PackageValidationError):
                for touch in mat.events:
                    build_and_store_pack(
                        conn, week_key=WEEK, event_id=touch.event_id,
                        signal_id=touch.signal_id, title=touch.title,
                        claims=touch.claims,
                    )
            conn.execute("ROLLBACK")
            conn.close()
        finally:
            package_module.validate_pack = original

        conn = S._open(self.db)
        try:
            states = [r["state"] for r in conn.execute("SELECT state FROM signal")]
            self.assertEqual(states, ["verified", "verified"])
            self.assertEqual(_count(conn, "material_pack"), 0)
        finally:
            conn.close()

    def test_transaction_rollback_no_half_written(self):
        self._seed_two_repos()
        conn = S._open(self.db)
        conn.execute("BEGIN")
        mat = materialize_github(conn, WEEK, SCOPE, 10, safe_hosts=FIXTURE_HOSTS)
        for touch in mat.events:
            build_and_store_pack(
                conn, week_key=WEEK, event_id=touch.event_id, signal_id=touch.signal_id,
                title=touch.title, claims=touch.claims,
            )
        conn.execute("ROLLBACK")
        conn.close()
        conn = S._open(self.db)
        try:
            for table in ("signal", "event", "event_member", "claim", "evidence",
                          "claim_evidence", "material_pack", "state_transition"):
                self.assertEqual(_count(conn, table), 0, table)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
