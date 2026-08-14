-- AI Signal Agent: raw-signal observation attribution (migration 0003)
--
-- ``raw_signal`` is content-addressed and globally deduplicated by
-- ``(source, external_id, payload_sha256)``; its ``collection_run_id`` only
-- records the *first* run that saw that content. This table records every
-- later observation of a given snapshot by a specific ``source_run``, so a
-- snapshot collected by several scopes or several weeks is still attributable
-- to each of them.
--
-- This migration is purely additive. It must NOT modify 0001_initial.sql or
-- 0002_source_collection.sql.

CREATE TABLE IF NOT EXISTS raw_signal_observation (
    source_run_id TEXT NOT NULL,
    raw_signal_id TEXT NOT NULL,
    observed_at   TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    PRIMARY KEY (source_run_id, raw_signal_id),
    FOREIGN KEY (source_run_id) REFERENCES source_run(id),
    FOREIGN KEY (raw_signal_id) REFERENCES raw_signal(id)
);
CREATE INDEX IF NOT EXISTS idx_raw_signal_observation_raw
    ON raw_signal_observation(raw_signal_id);

-- Deterministic backfill: attribute each pre-existing raw_signal to the
-- source_run (same collection_run + source) that observed it, so an upgraded
-- database can still materialize its existing data. ``INSERT OR IGNORE`` keeps
-- this idempotent and the composite primary key guarantees no duplicates.
INSERT OR IGNORE INTO raw_signal_observation
    (source_run_id, raw_signal_id, observed_at, created_at)
SELECT sr.id, rs.id, rs.collected_at, rs.created_at
FROM raw_signal rs
JOIN source_run sr
  ON sr.collection_run_id = rs.collection_run_id
 AND sr.source = rs.source;
