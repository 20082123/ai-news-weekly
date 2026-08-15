-- AI Signal Agent: GitHub discovery policy tables (migration 0005)
--
-- Phase 2C2 GitHub-specific Discovery Policy Adapter:
--
--   github_discovery_run            one execution of one GitHubDiscoveryPolicy
--   github_discovery_probe_run      one probe execution inside a discovery run
--   github_discovery_scope_binding  scope_key <-> spec_hash binding; the hard
--                                   guard that a scope with an existing cursor
--                                   never runs under a different probe spec
--   github_candidate_selection      the final per-run selection of a candidate
--                                   into the research queue (budget applied;
--                                   over-budget never downgrades qualification)
--
-- These tables are GitHub-specific. They do NOT model global Signals or
-- Events; those belong to later source-independent phases (2C3+). The
-- existing ``candidate`` table remains constrained to source='github'.
--
-- This migration is purely additive. It must NOT modify 0001, 0002, 0003 or
-- 0004.

CREATE TABLE IF NOT EXISTS github_discovery_run (
    id               TEXT PRIMARY KEY,
    policy_id        TEXT NOT NULL CHECK (length(trim(policy_id)) > 0),
    policy_hash      TEXT NOT NULL CHECK (length(policy_hash) = 64),
    week_key         TEXT NOT NULL,
    lane             TEXT NOT NULL CHECK (
        lane IN ('watchlist', 'mature', 'emerging', 'ecosystem')
    ),
    candidate_limit  INTEGER NOT NULL CHECK (candidate_limit >= 0),
    research_budget  INTEGER NOT NULL CHECK (research_budget >= 0),
    status           TEXT NOT NULL CHECK (
        status IN ('running', 'success', 'partial', 'failed')
    ),
    started_at       TEXT NOT NULL,
    finished_at      TEXT,
    warnings         TEXT NOT NULL,
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_github_discovery_run_policy
    ON github_discovery_run(policy_id, week_key);

CREATE TABLE IF NOT EXISTS github_discovery_probe_run (
    id                 TEXT PRIMARY KEY,
    discovery_run_id   TEXT NOT NULL,
    probe_id           TEXT NOT NULL CHECK (length(trim(probe_id)) > 0),
    kind               TEXT NOT NULL CHECK (
        kind IN ('search', 'watchlist_target', 'ecosystem_target')
    ),
    lane               TEXT NOT NULL CHECK (
        lane IN ('watchlist', 'mature', 'emerging', 'ecosystem')
    ),
    scope_key          TEXT NOT NULL CHECK (length(trim(scope_key)) > 0),
    spec_hash          TEXT NOT NULL CHECK (length(spec_hash) = 64),
    priority           INTEGER NOT NULL CHECK (priority >= 0),
    collection_run_id  TEXT,
    status             TEXT NOT NULL CHECK (
        status IN ('running', 'success', 'partial', 'failed', 'blocked')
    ),
    item_count         INTEGER NOT NULL CHECK (item_count >= 0),
    warning_count      INTEGER NOT NULL CHECK (warning_count >= 0),
    warnings           TEXT NOT NULL,
    started_at         TEXT NOT NULL,
    finished_at        TEXT,
    created_at         TEXT NOT NULL,
    FOREIGN KEY (discovery_run_id) REFERENCES github_discovery_run(id),
    FOREIGN KEY (collection_run_id) REFERENCES collection_run(id),
    UNIQUE (discovery_run_id, probe_id)
);
CREATE INDEX IF NOT EXISTS idx_github_probe_run_discovery
    ON github_discovery_probe_run(discovery_run_id, priority);

-- One binding row per scope_key, claimed before the first network request.
-- The binding is immutable: the pipeline only ever inserts, never updates the
-- spec_hash. A later run with a different spec_hash for the same scope_key is
-- refused BEFORE any network access (SCOPE_SPEC_MISMATCH). A scope_key that
-- already has a source_cursor row but no binding row is an unbound legacy
-- scope and is likewise refused (SCOPE_UNBOUND_CURSOR).
CREATE TABLE IF NOT EXISTS github_discovery_scope_binding (
    scope_key   TEXT PRIMARY KEY,
    probe_id    TEXT NOT NULL CHECK (length(trim(probe_id)) > 0),
    policy_id   TEXT NOT NULL CHECK (length(trim(policy_id)) > 0),
    spec_hash   TEXT NOT NULL CHECK (length(spec_hash) = 64),
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- The final selection of a candidate within one discovery run. Only
-- qualification=research candidates are selected; selection_rank orders them
-- and research_budget splits queued (rank < budget) from over_budget
-- (rank >= budget). The qualification decision is stored verbatim and is
-- NEVER downgraded by the budget - over_budget only changes queue_state.
-- Ecosystem relation evidence must keep its raw_signal_id for audit.
CREATE TABLE IF NOT EXISTS github_candidate_selection (
    id                       TEXT PRIMARY KEY,
    discovery_run_id         TEXT NOT NULL,
    candidate_id             TEXT NOT NULL,
    winning_discovery_id     TEXT NOT NULL,
    winning_assessment_id    TEXT NOT NULL,
    selection_rank           INTEGER NOT NULL CHECK (selection_rank >= 0),
    qualification_decision   TEXT NOT NULL CHECK (
        qualification_decision IN ('research', 'watch', 'reject')
    ),
    queue_state              TEXT NOT NULL CHECK (
        queue_state IN ('queued', 'over_budget')
    ),
    budget_reason            TEXT,
    ecosystem_target         TEXT,
    relation_kind            TEXT CHECK (
        relation_kind IS NULL OR
        relation_kind IN ('full_name_match', 'description_mention', 'topic_match')
    ),
    relation_field           TEXT,
    relation_raw_signal_id   TEXT,
    created_at               TEXT NOT NULL,
    FOREIGN KEY (discovery_run_id) REFERENCES github_discovery_run(id),
    FOREIGN KEY (candidate_id) REFERENCES candidate(id),
    FOREIGN KEY (winning_discovery_id) REFERENCES candidate_discovery(id),
    FOREIGN KEY (winning_assessment_id) REFERENCES candidate_assessment(id),
    FOREIGN KEY (relation_raw_signal_id) REFERENCES raw_signal(id),
    UNIQUE (discovery_run_id, candidate_id)
);
CREATE INDEX IF NOT EXISTS idx_github_selection_run
    ON github_candidate_selection(discovery_run_id, selection_rank);
