-- AI Signal Agent: candidate qualification v2 (migration 0004, rewritten)
--
-- Phase 2C1 three-layer candidate system:
--   candidate            - stable identity: "who this object is"
--   candidate_discovery  - provenance: each (week, scope, lane, snapshot)
--                          context in which the candidate was found
--   candidate_assessment - deterministic qualification decision per discovery
--
-- The identity is global: one repository has exactly one candidate row across
-- all weeks, scopes and lanes; every discovery context and assessment links
-- back to it. A repository is NOT an Event on this path and qualification
-- never produces A-F MaterialPacks.
--
-- This migration is purely additive. It must NOT modify 0001, 0002 or 0003.

CREATE TABLE IF NOT EXISTS candidate (
    id             TEXT PRIMARY KEY,
    source         TEXT NOT NULL CHECK (source IN ('github')),
    canonical_key  TEXT NOT NULL,
    title          TEXT NOT NULL,
    url            TEXT NOT NULL,
    first_seen_at  TEXT NOT NULL,
    last_seen_at   TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    UNIQUE (source, canonical_key)
);
CREATE INDEX IF NOT EXISTS idx_candidate_identity
    ON candidate(source, canonical_key);

CREATE TABLE IF NOT EXISTS candidate_discovery (
    id             TEXT PRIMARY KEY,
    candidate_id   TEXT NOT NULL,
    week_key       TEXT NOT NULL,
    scope_key      TEXT NOT NULL,
    lane           TEXT NOT NULL CHECK (
        lane IN ('watchlist', 'mature', 'emerging', 'ecosystem')
    ),
    raw_signal_id  TEXT NOT NULL,
    observed_at    TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    FOREIGN KEY (candidate_id) REFERENCES candidate(id),
    FOREIGN KEY (raw_signal_id) REFERENCES raw_signal(id),
    UNIQUE (candidate_id, week_key, scope_key, lane, raw_signal_id)
);
CREATE INDEX IF NOT EXISTS idx_candidate_discovery_context
    ON candidate_discovery(week_key, scope_key, lane);

CREATE TABLE IF NOT EXISTS candidate_assessment (
    id                      TEXT PRIMARY KEY,
    candidate_discovery_id  TEXT NOT NULL,
    policy_version          TEXT NOT NULL,
    input_hash              TEXT NOT NULL,
    decision                TEXT NOT NULL CHECK (decision IN ('research', 'watch', 'reject')),
    trigger_kind            TEXT NOT NULL CHECK (trigger_kind IN ('repository_snapshot')),
    trigger_summary         TEXT NOT NULL,
    reason_codes            TEXT NOT NULL,
    missing_evidence        TEXT NOT NULL,
    attributes              TEXT NOT NULL,
    assessed_at             TEXT NOT NULL,
    FOREIGN KEY (candidate_discovery_id) REFERENCES candidate_discovery(id),
    UNIQUE (candidate_discovery_id, input_hash, policy_version)
);
CREATE INDEX IF NOT EXISTS idx_candidate_assessment_discovery
    ON candidate_assessment(candidate_discovery_id, assessed_at);
