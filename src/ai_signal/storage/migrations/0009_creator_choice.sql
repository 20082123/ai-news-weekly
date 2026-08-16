-- AI Signal Agent: creator choice registry (migration 0009)
--
-- Shape change (DEC-017): the hub of the system is the CREATOR'S choice of
-- what to write this week, not an accumulated pile of events. One row per
-- (week_key, subject) registers the chosen topic; evidence-gap routing and
-- research serve the choice. Feedback about published outcomes reuses the
-- existing generic ``feedback`` table with target_type = 'creator_choice'.
--
-- Purely additive; must NOT modify 0001-0008.

CREATE TABLE IF NOT EXISTS creator_choice (
    id                   TEXT PRIMARY KEY,  -- deterministic(week_key, subject)
    week_key             TEXT NOT NULL,
    subject              TEXT NOT NULL CHECK (length(trim(subject)) > 0),
    event_candidate_id   TEXT,              -- optional link to an aggregated event
    status               TEXT NOT NULL CHECK (
        status IN ('chosen', 'researched', 'drafted', 'published', 'parked')
    ),
    chosen_at            TEXT NOT NULL,
    updated_at           TEXT NOT NULL,
    FOREIGN KEY (event_candidate_id) REFERENCES event_candidate(id),
    UNIQUE (week_key, subject)
);
CREATE INDEX IF NOT EXISTS idx_creator_choice_week
    ON creator_choice(week_key, status);
