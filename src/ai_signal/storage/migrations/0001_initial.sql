-- AI Signal Agent: initial schema (migration 0001)
--
-- Conventions:
--   * all timestamps are stored as ISO-8601 UTC text (e.g. 2026-08-13T12:00:00+00:00)
--   * original JSON payloads are stored as TEXT alongside a SHA-256 digest
--   * raw records are append-only and never overwritten or deleted by later
--     failures
--   * business state is validated in code, not by SQL triggers; CHECK
--     constraints here only guard controlled vocabularies

CREATE TABLE IF NOT EXISTS schema_migration (
    version     INTEGER PRIMARY KEY,
    filename    TEXT NOT NULL UNIQUE,
    checksum    CHAR(64) NOT NULL,
    applied_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS policy_version (
    id          TEXT PRIMARY KEY,
    version     TEXT NOT NULL UNIQUE,
    rules       TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    is_active   INTEGER NOT NULL CHECK (is_active IN (0, 1))
);

CREATE TABLE IF NOT EXISTS collection_run (
    id                 TEXT PRIMARY KEY,
    week_key           TEXT NOT NULL,
    started_at         TEXT NOT NULL,
    finished_at        TEXT,
    status             TEXT NOT NULL CHECK (status IN ('running', 'success', 'partial', 'failed')),
    config_snapshot    TEXT NOT NULL,
    policy_version_id  TEXT,
    created_at         TEXT NOT NULL,
    FOREIGN KEY (policy_version_id) REFERENCES policy_version(id)
);
CREATE INDEX IF NOT EXISTS idx_collection_run_week ON collection_run(week_key);

CREATE TABLE IF NOT EXISTS raw_signal (
    id                TEXT PRIMARY KEY,
    collection_run_id TEXT NOT NULL,
    source            TEXT NOT NULL,
    external_id       TEXT NOT NULL,
    payload           TEXT NOT NULL,
    payload_sha256    CHAR(64) NOT NULL,
    source_version    TEXT NOT NULL,
    collected_at      TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    FOREIGN KEY (collection_run_id) REFERENCES collection_run(id),
    UNIQUE (source, external_id, payload_sha256)
);
CREATE INDEX IF NOT EXISTS idx_raw_signal_run ON raw_signal(collection_run_id);

CREATE TABLE IF NOT EXISTS signal (
    id                TEXT PRIMARY KEY,
    collection_run_id TEXT NOT NULL,
    source            TEXT NOT NULL,
    canonical_key     TEXT NOT NULL,
    raw_signal_id     TEXT NOT NULL,
    title             TEXT,
    url               TEXT,
    signal_type       TEXT NOT NULL,
    state             TEXT NOT NULL CHECK (state IN (
        'collected', 'normalized', 'clustered', 'verified',
        'insufficient_evidence', 'packaged', 'adopted', 'parked',
        'rejected', 'published', 'measured', 'failed', 'quarantined'
    )),
    first_seen_at     TEXT NOT NULL,
    last_seen_at      TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    payload           TEXT NOT NULL,
    FOREIGN KEY (collection_run_id) REFERENCES collection_run(id),
    FOREIGN KEY (raw_signal_id) REFERENCES raw_signal(id),
    UNIQUE (source, canonical_key)
);
CREATE INDEX IF NOT EXISTS idx_signal_run ON signal(collection_run_id);

CREATE TABLE IF NOT EXISTS state_transition (
    id                TEXT PRIMARY KEY,
    entity_id         TEXT NOT NULL,
    from_state        TEXT NOT NULL,
    to_state          TEXT NOT NULL,
    timestamp         TEXT NOT NULL,
    reason            TEXT,
    stage             TEXT,
    policy_version_id TEXT,
    created_at        TEXT NOT NULL,
    FOREIGN KEY (policy_version_id) REFERENCES policy_version(id)
);
CREATE INDEX IF NOT EXISTS idx_state_transition_entity ON state_transition(entity_id, timestamp);

CREATE TABLE IF NOT EXISTS event (
    id            TEXT PRIMARY KEY,
    canonical_key TEXT NOT NULL UNIQUE,
    title         TEXT NOT NULL,
    summary       TEXT,
    occurred_at   TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    payload       TEXT
);

CREATE TABLE IF NOT EXISTS event_member (
    id         TEXT PRIMARY KEY,
    event_id   TEXT NOT NULL,
    signal_id  TEXT NOT NULL,
    role       TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (event_id) REFERENCES event(id),
    FOREIGN KEY (signal_id) REFERENCES signal(id),
    UNIQUE (event_id, signal_id)
);
CREATE INDEX IF NOT EXISTS idx_event_member_signal ON event_member(signal_id);

CREATE TABLE IF NOT EXISTS claim (
    id         TEXT PRIMARY KEY,
    event_id   TEXT,
    text       TEXT NOT NULL,
    claim_type TEXT NOT NULL,
    state      TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (event_id) REFERENCES event(id)
);
CREATE INDEX IF NOT EXISTS idx_claim_event ON claim(event_id);

CREATE TABLE IF NOT EXISTS evidence (
    id             TEXT PRIMARY KEY,
    source         TEXT NOT NULL,
    url            TEXT,
    snippet        TEXT NOT NULL,
    evidence_type  TEXT NOT NULL,
    collected_at   TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    payload        TEXT NOT NULL,
    payload_sha256 CHAR(64) NOT NULL
);

CREATE TABLE IF NOT EXISTS claim_evidence (
    id          TEXT PRIMARY KEY,
    claim_id    TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    relation    TEXT NOT NULL CHECK (relation IN ('supports', 'contradicts', 'contextual')),
    weight      REAL,
    created_at  TEXT NOT NULL,
    FOREIGN KEY (claim_id) REFERENCES claim(id),
    FOREIGN KEY (evidence_id) REFERENCES evidence(id),
    UNIQUE (claim_id, evidence_id)
);
CREATE INDEX IF NOT EXISTS idx_claim_evidence_claim ON claim_evidence(claim_id);
CREATE INDEX IF NOT EXISTS idx_claim_evidence_evidence ON claim_evidence(evidence_id);

CREATE TABLE IF NOT EXISTS material_pack (
    id          TEXT PRIMARY KEY,
    week_key    TEXT NOT NULL,
    event_id    TEXT,
    claim_ids   TEXT NOT NULL,
    content     TEXT NOT NULL,
    bundle_hash CHAR(64) NOT NULL,
    created_at  TEXT NOT NULL,
    FOREIGN KEY (event_id) REFERENCES event(id)
);
CREATE INDEX IF NOT EXISTS idx_material_pack_week ON material_pack(week_key);

CREATE TABLE IF NOT EXISTS feedback (
    id                TEXT PRIMARY KEY,
    target_type       TEXT NOT NULL,
    target_id         TEXT NOT NULL,
    decision          TEXT NOT NULL CHECK (decision IN ('adopted', 'parked', 'rejected')),
    reason            TEXT,
    audience          TEXT,
    angle             TEXT,
    usefulness        INTEGER CHECK (usefulness IS NULL OR (usefulness BETWEEN 1 AND 5)),
    published_url     TEXT,
    created_at        TEXT NOT NULL,
    policy_version_id TEXT,
    FOREIGN KEY (policy_version_id) REFERENCES policy_version(id)
);
CREATE INDEX IF NOT EXISTS idx_feedback_target ON feedback(target_type, target_id);

CREATE TABLE IF NOT EXISTS publication (
    id              TEXT PRIMARY KEY,
    material_pack_id TEXT NOT NULL,
    channel         TEXT NOT NULL,
    target          TEXT,
    published_at    TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('published', 'failed')),
    payload         TEXT NOT NULL,
    FOREIGN KEY (material_pack_id) REFERENCES material_pack(id)
);

CREATE TABLE IF NOT EXISTS metric_snapshot (
    id               TEXT PRIMARY KEY,
    publication_id   TEXT NOT NULL,
    measurement_window TEXT NOT NULL,
    measured_at      TEXT NOT NULL,
    metrics          TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    FOREIGN KEY (publication_id) REFERENCES publication(id),
    UNIQUE (publication_id, measurement_window)
);

CREATE TABLE IF NOT EXISTS score_log (
    id                TEXT PRIMARY KEY,
    target_type       TEXT NOT NULL,
    target_id         TEXT NOT NULL,
    score             REAL NOT NULL,
    components        TEXT NOT NULL,
    policy_version_id TEXT,
    created_at        TEXT NOT NULL,
    FOREIGN KEY (policy_version_id) REFERENCES policy_version(id)
);
CREATE INDEX IF NOT EXISTS idx_score_log_target ON score_log(target_type, target_id);

CREATE TABLE IF NOT EXISTS delivery_run (
    id                TEXT PRIMARY KEY,
    week_key          TEXT NOT NULL,
    bundle_hash       CHAR(64) NOT NULL,
    policy_version_id TEXT NOT NULL,
    channel           TEXT NOT NULL,
    started_at        TEXT NOT NULL,
    finished_at       TEXT,
    status            TEXT NOT NULL CHECK (status IN ('running', 'success', 'partial', 'failed')),
    warnings          TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    FOREIGN KEY (policy_version_id) REFERENCES policy_version(id),
    UNIQUE (week_key, bundle_hash, policy_version_id, channel)
);
CREATE INDEX IF NOT EXISTS idx_delivery_run_week ON delivery_run(week_key);
