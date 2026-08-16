-- AI Signal Agent: official announcement candidates (migration 0008)
--
-- Phase 2D3-B: the Official sensor. Consumer-level AI changes (product
-- launches, pricing, usage limits) surface first on official channels, not
-- GitHub; this table records first-party announcement CANDIDATES discovered
-- from official feeds/pages, ready to be referenced by event_candidate and
-- quoted as evidence in research dossiers.
--
-- Purely additive; must NOT modify 0001-0007.

CREATE TABLE IF NOT EXISTS official_announcement_candidate (
    id            TEXT PRIMARY KEY,  -- deterministic(source_name, url)
    source_name   TEXT NOT NULL CHECK (length(trim(source_name)) > 0),
    title         TEXT NOT NULL CHECK (length(trim(title)) > 0),
    url           TEXT NOT NULL,     -- first-party https, no credentials
    published_at  TEXT NOT NULL,     -- ISO-ish date/time from the feed
    summary       TEXT NOT NULL,     -- cleaned excerpt (untrusted, capped)
    status        TEXT NOT NULL CHECK (
        status IN ('new', 'researched', 'rejected')
    ),
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    UNIQUE (source_name, url)
);
CREATE INDEX IF NOT EXISTS idx_official_candidate_status
    ON official_announcement_candidate(status, published_at DESC);
