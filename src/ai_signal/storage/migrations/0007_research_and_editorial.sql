-- AI Signal Agent: research dossier + editorial decision (migration 0007)
--
-- Phase 2D/2E: the research layer turns one source-independent
-- ``event_candidate`` into an evidence-backed ``research_dossier``, whose
-- facts are later gated into an ``editorial_decision``. Purely additive;
-- must NOT modify 0001-0006, and must NOT reuse the legacy 2B ``event``
-- table (dossiers key off ``event_candidate`` only).

CREATE TABLE IF NOT EXISTS research_dossier (
    id                  TEXT PRIMARY KEY,  -- deterministic(event_candidate_id, bundle_hash)
    event_candidate_id  TEXT NOT NULL,
    status              TEXT NOT NULL CHECK (
        status IN ('draft', 'complete', 'partial', 'failed')
    ),
    bundle_hash         TEXT NOT NULL CHECK (length(bundle_hash) = 64),
    summary_judgment    TEXT NOT NULL CHECK (length(trim(summary_judgment)) > 0),
    timeline            TEXT NOT NULL,     -- JSON array of {at, what, source_url, source_kind}
    target_audience     TEXT NOT NULL CHECK (length(trim(target_audience)) > 0),
    job_to_be_done      TEXT NOT NULL CHECK (length(trim(job_to_be_done)) > 0),
    limits_unknowns     TEXT NOT NULL,     -- JSON array of strings
    forbidden_claims    TEXT NOT NULL,     -- JSON array of strings (禁说清单)
    needs_testing       INTEGER NOT NULL CHECK (needs_testing IN (0, 1)),
    test_plan           TEXT NOT NULL,     -- JSON array of strings
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    FOREIGN KEY (event_candidate_id) REFERENCES event_candidate(id)
);
CREATE INDEX IF NOT EXISTS idx_dossier_event
    ON research_dossier(event_candidate_id, created_at);

-- One auditable fact per row. ``source_url`` is FIRST-PARTY evidence (release
-- page, README, official page) - https only, no credentials, no control
-- characters; it is validated at the domain layer before storage.
CREATE TABLE IF NOT EXISTS research_fact (
    id           TEXT PRIMARY KEY,  -- deterministic(dossier_id, kind, source_url, text)
    dossier_id   TEXT NOT NULL,
    kind         TEXT NOT NULL CHECK (
        kind IN ('fact', 'official_claim', 'unknown', 'contradiction')
    ),
    text         TEXT NOT NULL CHECK (length(trim(text)) > 0),
    source_kind  TEXT NOT NULL CHECK (
        source_kind IN (
            'github_release',
            'github_readme',
            'github_metadata',
            'official_page',
            'manual'
        )
    ),
    source_url   TEXT,
    created_at   TEXT NOT NULL,
    FOREIGN KEY (dossier_id) REFERENCES research_dossier(id),
    UNIQUE (dossier_id, kind, source_url, text)
);
CREATE INDEX IF NOT EXISTS idx_fact_dossier
    ON research_fact(dossier_id, kind);

-- Editorial decision: deterministic per (dossier, policy_version, input_hash);
-- a changed fact set yields a new revision while history is retained.
CREATE TABLE IF NOT EXISTS editorial_decision (
    id              TEXT PRIMARY KEY,  -- deterministic(dossier_id, policy_version, input_hash)
    dossier_id      TEXT NOT NULL,
    policy_version  TEXT NOT NULL,
    input_hash      TEXT NOT NULL CHECK (length(input_hash) = 64),
    decision        TEXT NOT NULL CHECK (
        decision IN ('ready_to_write', 'needs_testing', 'watch', 'reject')
    ),
    reason_codes    TEXT NOT NULL,     -- JSON array of stable codes
    decided_at      TEXT NOT NULL,
    FOREIGN KEY (dossier_id) REFERENCES research_dossier(id),
    UNIQUE (dossier_id, policy_version, input_hash)
);
CREATE INDEX IF NOT EXISTS idx_decision_dossier
    ON editorial_decision(dossier_id, decided_at);
