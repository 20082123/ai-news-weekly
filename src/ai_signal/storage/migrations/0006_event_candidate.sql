-- AI Signal Agent: source-independent Event Candidate contract (migration 0006)
--
-- Phase 2C3 establishes the THIN source-independent contract on top of the
-- source-specific candidate layers (2C1/2C2 GitHubRepositoryCandidate today;
-- OfficialAnnouncementCandidate and others later):
--
--   event_candidate            one candidate description of a real AI change,
--                              typed by the five global Signal Types
--   event_candidate_source_ref source-specific candidate references
--                              (kind + id + safe label, no cross-source FK)
--
-- This is a CANDIDATE layer only: it does not confirm events and it does not
-- reuse the legacy 2B ``event`` table (that stays a compatibility artifact).
--
-- Purely additive; must NOT modify 0001-0005.

CREATE TABLE IF NOT EXISTS event_candidate (
    id                      TEXT PRIMARY KEY,
    signal_type             TEXT NOT NULL CHECK (
        signal_type IN (
            'capability_change',
            'tool_workflow_change',
            'user_reality',
            'economics_access',
            'ecosystem_market_shift'
        )
    ),
    subject                 TEXT NOT NULL CHECK (length(trim(subject)) > 0),
    change_summary          TEXT NOT NULL CHECK (length(trim(change_summary)) > 0),
    affected_audience       TEXT NOT NULL CHECK (length(trim(affected_audience)) > 0),
    work_impact_hypothesis  TEXT NOT NULL CHECK (length(trim(work_impact_hypothesis)) > 0),
    missing_evidence        TEXT NOT NULL,   -- JSON array of stable codes
    research_priority       INTEGER NOT NULL CHECK (research_priority BETWEEN 0 AND 100),
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    UNIQUE (signal_type, subject, change_summary)
);
CREATE INDEX IF NOT EXISTS idx_event_candidate_type
    ON event_candidate(signal_type, research_priority);

-- Source references are (kind, id) TEXT pointers WITHOUT cross-source foreign
-- keys: the referenced row lives in a source-specific table (e.g. 2C1
-- ``candidate`` for github_repository_candidate), which may not even exist
-- yet (official_announcement_candidate). ``ref_label`` is a safe,
-- pre-validated human-readable label only - never a URL or raw payload.
CREATE TABLE IF NOT EXISTS event_candidate_source_ref (
    id                  TEXT PRIMARY KEY,
    event_candidate_id  TEXT NOT NULL,
    source_kind         TEXT NOT NULL CHECK (
        source_kind IN (
            'github_repository_candidate',
            'official_announcement_candidate'
        )
    ),
    ref_id              TEXT NOT NULL CHECK (length(trim(ref_id)) > 0),
    ref_label           TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    FOREIGN KEY (event_candidate_id) REFERENCES event_candidate(id),
    UNIQUE (event_candidate_id, source_kind, ref_id)
);
CREATE INDEX IF NOT EXISTS idx_event_candidate_ref_candidate
    ON event_candidate_source_ref(event_candidate_id);
