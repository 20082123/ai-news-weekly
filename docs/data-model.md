# Data model

This document describes the v1 SQLite schema (`storage/migrations/0001_initial.sql`)
and the domain objects that map to it.

## Conventions

* **Timestamps** are stored as ISO-8601 UTC text and modeled in Python as
  timezone-aware `datetime` objects. Naive datetimes are rejected at the
  domain layer (`ensure_aware_utc`).
* **Run-scoped ids** (collection run, feedback, score log, state-transition
  row) use random UUID4 so they are globally unique per execution.
* **Stable entity ids** (raw signal, signal, event, evidence, …) use
  deterministic SHA-256 ids derived from their canonical parts, so the same
  inputs always produce the same id across runs.
* **Original JSON** is stored as `TEXT` and, for raw/original payloads,
  accompanied by a SHA-256 digest column. `raw_signal.source_version`
  records which adapter/parser contract produced the capture.
* **Raw records are append-only**: later failures never overwrite or delete
  already-captured rows. Idempotent upserts reuse the existing row.
* **Credentials never enter the model**: no model field holds api keys,
  cookies, authorization headers, request headers or SMTP details, and
  nested credential-bearing payload keys are rejected before persistence.

## Tables and relationships

```
policy_version
     ▲
     │ policy_version_id
collection_run ──< raw_signal ──< signal >── event ──< event_member
                       │                │       │
                       │                │       └──< claim ──< claim_evidence >── evidence
                       │                │
                       │                └── (state tracked via signal.state)
                       │
                  state_transition (audit, by entity_id)

material_pack ──< publication ──< metric_snapshot
feedback            (target_type, target_id) -> policy_version
score_log           (target_type, target_id) -> policy_version
delivery_run        unique per (week_key, bundle_hash, policy_version_id, channel)
```

### Idempotency keys (unique constraints)

| Table | Unique key |
| --- | --- |
| `raw_signal` | `(source, external_id, payload_sha256)` |
| `signal` | `(source, canonical_key)` |
| `event_member` | `(event_id, signal_id)` |
| `claim_evidence` | `(claim_id, evidence_id)` |
| `metric_snapshot` | `(publication_id, measurement_window)` |
| `delivery_run` | `(week_key, bundle_hash, policy_version_id, channel)` |
| `policy_version` | `version` (label) |

Because `raw_signal.id` and `signal.id` are derived from the same fields as
their unique keys, re-inserting the same record always lands on the same row
and the repositories return the canonical existing row.

## Identity, time and idempotency

* **Identity** — stable entities are addressable by a deterministic SHA-256
  id; run records are addressable by a fresh UUID4.
* **Time** — every temporal column is aware UTC; the domain layer refuses to
  construct a model from a naive datetime, which keeps storage consistent.
* **Idempotency** — `RawSignalRepository.upsert` and
  `SignalRepository.upsert` use `ON CONFLICT … DO NOTHING` and then re-read
  the row by its unique key, so duplicates collapse to the original record
  and callers always receive the canonical object.

## State machine

The signal lifecycle is enforced in code (`domain/states.py`), not by SQL
triggers. CHECK constraints on `signal.state`, `feedback.decision`,
`feedback.usefulness` and the status columns only guard the controlled
vocabularies.

```
collected → normalized → clustered
                            │
        ┌───────────────────┴────────────────────┐
        ▼                                         ▼
    verified                            insufficient_evidence
        │                                         │
        └──────────────────┬──────────────────────┘
                           ▼
                       packaged
                           │
           ┌───────────────┼─────────────────┐
           ▼               ▼                 ▼
       adopted          parked            rejected
           │
           ▼
       published
           │
           ▼
       measured
```

* Any non-terminal business state may also move to `failed` or
  `quarantined`.
* Terminal states (`measured`, `parked`, `rejected`, `failed`,
  `quarantined`) have no legal outgoing transition.
* Illegal transitions raise `InvalidStateTransition`; there is no silent
  repair.
* Each transition is recorded as a `StateTransitionRecord` carrying
  `entity_id`, `from_state`, `to_state`, `timestamp`, `reason`, `stage` and
  `policy_version_id`, and appended to the `state_transition` audit table.

## Claim–evidence relationship

A `claim` belongs to an `event` (optionally) and is linked to zero or more
`evidence` rows through `claim_evidence`, whose unique key is
`(claim_id, evidence_id)`. Each link carries a `relation`
(`supports` / `contradicts` / `contextual`) and an optional `weight` in
`[0.0, 1.0]`. This many-to-many structure lets multiple pieces of evidence
support or refute the same claim and lets one piece of evidence apply to
several claims.

## Human feedback contract

The minimal feedback fields mirror the future Obsidian frontmatter sync:
`decision` (`adopted` / `parked` / `rejected`), `reason`, `audience`,
`angle`, `usefulness` (integer 1–5) and `published_url`. Feedback remains
append-only; phase 1 defines the model and schema but does not write to a
vault or implement the sync command.

## Phase 2A: `source_run` and `source_cursor`

Migration `0002_source_collection.sql` adds two tables for per-source
incremental collection. It is **purely additive**:

* migration `0001_initial.sql` is immutable and must never be edited; every
  schema change is a new numbered migration applied after it;
* `0002` does not modify or drop any existing table, column or index.

### `source_run`

One row per `(collection_run, source, scope_key)` triple, mirroring the
outcome of a `SourceBatch`:

| Column | Notes |
| --- | --- |
| `id` | UUID4 primary key |
| `collection_run_id` | FK → `collection_run(id)`; `UNIQUE(collection_run_id, source, scope_key)` |
| `source`, `scope_key`, `source_version` | which source/scope produced the batch |
| `status` | `success` / `partial` / `unavailable` / `failed` (CHECK) |
| `started_at`, `finished_at` | aware UTC; the model forbids `finished_at < started_at` |
| `cursor_in`, `cursor_out` | pagination cursors (nullable) |
| `item_count`, `warning_count` | non-negative integers (CHECK `>= 0`) |
| `warnings` | JSON array of stable warning codes; restored as a tuple of strings |
| `cursor_advanced` | 0/1 (CHECK), true only when the cursor was advanced |

### `source_cursor`

One row per `(source, scope_key)` - the composite primary key - holding the
current pagination cursor, the `source_version`, the `last_run_id`
(FK → `collection_run(id)`) and `updated_at`. `cursor` is non-null: it is only
written when a successful batch supplies a distinct, non-null next cursor.

### `scope_key` contract

`scope_key` isolates collection progress between different logical GitHub
query scopes. It is a **stable alias** (for example `ai-agents-v1`,
`github-fixture-v1`), validated against `^[a-z0-9][a-z0-9._-]{0,63}$`. It is
explicitly **not** the raw GitHub query, a URL, a file path, a date or a
credential; the raw query never reaches the cursor table. When query semantics
change incompatibly the scope version is bumped
(`ai-agents-v1` -> `ai-agents-v2`), so old and new progress coexist.

### Cursor and raw signals share one transaction

`collect_source_once` writes the `SourceRun`, all `RawSignal` upserts, the
optional `source_cursor` advance and the `collection_run` status update inside
a single `BEGIN/COMMIT`. The cursor is advanced **iff** the batch status is
`success` and the source supplies a distinct, non-null next cursor. A terminal
null cursor or repeated cursor never clears or falsely advances the stored
value. `partial` / `unavailable` / `failed` still record what was parsed but
leave the cursor untouched, so the next run re-fetches the same page. If the
final transaction fails it is rolled back, leaving neither half-written raw
signals nor a half-advanced cursor, and the run is best-effort marked `failed`
without masking the `StorageError`.

## Data retention and the credentials-must-not-enter principle

* Raw signals are immutable: the pipeline appends, it never edits or deletes
  captured data. Derived entities (signals, events, claims) can be
  re-derived from raw signals.
* No table has a column for api keys, cookies, authorization, request
  headers or SMTP credentials. Sources receive credentials only through the
  runtime context in future phases, and the redacted logger scrubs any such
  field before it can reach output.
* The database path is the only filesystem artifact. Phase 1 never writes
  into an Obsidian vault, never sends email, and never reaches the network.
