-- AI Signal Agent: source collection tables (migration 0002)
--
-- Adds per-source run records and a pagination cursor isolated by
-- ``(source, scope_key)``. This migration is purely additive: it does not
-- modify or drop any table, column, index or trigger created by migration
-- 0001.
--
-- Conventions follow migration 0001:
--   * timestamps are ISO-8601 UTC text
--   * JSON arrays (warnings) are stored as TEXT
--   * business state lives in code, not in SQL triggers, and CHECK
--     constraints here only guard controlled vocabularies and non-negativity

CREATE TABLE IF NOT EXISTS source_run (
    id                TEXT PRIMARY KEY,
    collection_run_id TEXT NOT NULL,
    source            TEXT NOT NULL,
    scope_key         TEXT NOT NULL CHECK (length(trim(scope_key)) > 0),
    source_version    TEXT NOT NULL,
    status            TEXT NOT NULL CHECK (status IN ('success', 'partial', 'unavailable', 'failed')),
    started_at        TEXT NOT NULL,
    finished_at       TEXT NOT NULL,
    cursor_in         TEXT CHECK (cursor_in IS NULL OR length(trim(cursor_in)) > 0),
    cursor_out        TEXT CHECK (cursor_out IS NULL OR length(trim(cursor_out)) > 0),
    item_count        INTEGER NOT NULL CHECK (item_count >= 0),
    warning_count     INTEGER NOT NULL CHECK (warning_count >= 0),
    warnings          TEXT NOT NULL,
    cursor_advanced   INTEGER NOT NULL CHECK (
        cursor_advanced IN (0, 1)
        AND (
            cursor_advanced = 0 OR (
                status = 'success'
                AND cursor_out IS NOT NULL
                AND (cursor_in IS NULL OR cursor_out <> cursor_in)
            )
        )
    ),
    created_at        TEXT NOT NULL,
    FOREIGN KEY (collection_run_id) REFERENCES collection_run(id),
    UNIQUE (collection_run_id, source, scope_key)
);
CREATE INDEX IF NOT EXISTS idx_source_run_collection ON source_run(collection_run_id);
CREATE INDEX IF NOT EXISTS idx_source_run_source ON source_run(source, scope_key);
CREATE INDEX IF NOT EXISTS idx_source_run_status ON source_run(status);

CREATE TABLE IF NOT EXISTS source_cursor (
    source          TEXT NOT NULL,
    scope_key       TEXT NOT NULL CHECK (length(trim(scope_key)) > 0),
    cursor          TEXT NOT NULL CHECK (length(trim(cursor)) > 0),
    source_version  TEXT NOT NULL,
    last_run_id     TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    PRIMARY KEY (source, scope_key),
    FOREIGN KEY (last_run_id) REFERENCES collection_run(id)
);
