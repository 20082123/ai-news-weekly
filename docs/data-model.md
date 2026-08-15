# Data model

> **文档职责说明（2026-08-15）：** 本文件只负责数据库表、字段、约束和领域对象，
> 不负责产品目标、路线图或当前状态。项目入口见
> [00-PRODUCT.md](./00-PRODUCT.md)、[01-ROADMAP.md](./01-ROADMAP.md) 与
> [02-STATUS.md](./02-STATUS.md)。历史模型不等于未来 source-independent
> Event / Research Dossier 的最终 schema。

This document describes the v1 SQLite schema (`storage/migrations/0001_initial.sql`)
and the domain objects that map to it.

## Conventions

* **Timestamps** are stored as ISO-8601 UTC text and modeled in Python as
  timezone-aware `datetime` objects. Naive datetimes are rejected at the
  domain layer (`ensure_aware_utc`).
* **Run-scoped ids** (collection run, score log, state-transition row) use
  random UUID4 so they are globally unique per execution. A `Feedback`
  constructed directly without an id also defaults to UUID4; the Phase 2B2
  Markdown feedback-sync path explicitly supplies a deterministic id derived
  from target id + the six canonical feedback fields, making repeat sync
  idempotent.
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

### `raw_signal_observation` (migration 0003)

`raw_signal` is **content-addressed and globally deduplicated** by
`(source, external_id, payload_sha256)`; its `collection_run_id` only records
the *first* run that saw that content. `raw_signal_observation` records every
later observation of a snapshot, one row per `(source_run_id, raw_signal_id)`,
with `observed_at` (the observation time) and `created_at`. This is how a
snapshot collected by several scopes or several weeks stays attributable to
each of them - `raw_signal.collection_run_id` alone is **not** sufficient to
isolate multiple scopes.

Three distinct record types now model collection:

* **`raw_signal`** — the global, immutable, content-addressed snapshot.
* **`raw_signal_observation`** — each run/scope/week's attribution of a
  snapshot (many-to-many between `source_run` and `raw_signal`).
* **`source_cursor`** — per-`(source, scope_key)` pagination progress.

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

### Adapters and the raw-query firewall (phase 2B1)

`collection_run.config_snapshot` selects one of two adapters via
`adapter_kind`, each with its own strict key whitelist:

* `fixture` — `fixture` + `fixture_sha256` (offline; no network).
* `github-rest-v1` — `query_sha256` + `sort` + `order` + `per_page` +
  `max_pages` (read-only public GitHub Search).

For the REST adapter the raw GitHub query is used **only** to build the HTTPS
request. What is persisted is
`query_sha256 = SHA256(canonical JSON(query, sort, order, per_page, max_pages))`,
never the raw query. The cursor for the REST adapter is the stable form
`page:N` (capped at `max_pages` ≤ 3, `per_page` ≤ 25) and points at the
*next* page to request; it is never a URL and never contains the query.
`None` starts at page 1. When `max_pages` is reached (or the result set runs
out) the cursor wraps back to `page:1`, so a scope is re-scanned
periodically; SQLite dedup keeps `raw_signal` stable across re-scans.
`raw_signal.source_version` is `github-rest-v1` for this adapter.

## Phase 2B2: materialization data flow

Phase 2B2 reuses the existing `signal`, `event`, `event_member`, `claim`,
`evidence`, `claim_evidence`, `material_pack` and `feedback` tables without
any schema change. The data flow is:

* **Raw selection** — `select_github_raw_signals` joins
  `raw_signal_observation → source_run → collection_run → raw_signal` filtered
  by `(week_key, scope_key)`, so a content-addressed snapshot is visible to
  every scope/week that observed it. It keeps only the *latest* snapshot per
  repository (grouped by `external_id`, ordered by `updated_at|pushed_at`,
  `observed_at`, `raw_signal.id`) and applies `limit` after that dedup - so
  only one snapshot per repository is ever materialized and old snapshots
  never overwrite newer ones. The observation time (`observed_at`), not the
  raw_signal's first insert time, is what flows into the Signal.
* **Signal** — `canonical_key = github:repository:<numeric_id>`.
  `SignalRepository.upsert` preserves `first_seen_at` and `state` on conflict
  (never resetting a progressed Signal); `last_seen_at` only moves forward
  (driven by the observation `observed_at`, not wall-clock) and
  `raw_signal_id`/`title`/`url`/`payload` are refreshed only from a non-older
  observation.
* **Event** — one per repository: `canonical_key =
  github:event:repository:<numeric_id>`.
* **EventMember** — links Event ↔ Signal, unique per pair.
* **Claim / Evidence / ClaimEvidence** — factual claims derived only from
  snapshot fields. A claim id is `deterministic_id("claim", event_id, text)`
  (so changing stars/date yields a new claim); an evidence id includes the
  `raw_signal_id` (so a new snapshot yields new evidence). Each Claim binds at
  least one Evidence via `supports`; historical claims/evidence stay auditable
  but are never included in the current pack.
* **MaterialPack** — `bundle_hash = SHA256(canonical JSON(content))`; pack id
  = `deterministic_id("material_pack", week_key, bundle_hash)`. The same
  inputs always produce the same hash, pack id and Markdown filename.
* **Feedback** — id = `deterministic_id("feedback", target_id, canonical
  JSON(six feedback fields))`. Re-syncing the same file is idempotent; a
  changed decision appends a new row.

Signal state advances `collected → normalized → clustered → verified` during
materialization, then `verified → packaged` after the MaterialPack row is
persisted - all recorded in `state_transition` (compare-and-swap keeps
re-runs idempotent).

### Phase 2B3 deltas (no schema change)

* **Claims are natural Chinese sentences** quoting only snapshot fields
  (URL, updated/pushed timestamps, stars/forks, description, topics,
  language). Claim ids keep the `deterministic_id("claim", event_id, text)`
  semantics, so any quoted-value change yields a new claim row while
  historical claims stay auditable.
* **Evidence payload** now also carries `description` and `topics`, and
  evidence ids use the versioned scheme
  `deterministic_id("evidence-v2", raw_signal_id, SHA256(canonical payload))`.
  Old evidence rows are immutable and untouched; a changed payload yields a
  fresh evidence row, and the current `EventTouch` only references the
  current version.
* **MaterialPack content schema** is `material-pack-v2` (Chinese A–F angles
  plus a `project_info` section sourced from the snapshot payload).
  `bundle_hash`/pack-id determinism is unchanged.
* **Markdown filenames** are the tentative readable form
  `<week_key> - GitHub - <repo> (<owner>).md`; `pack_id`/`event_id` live in
  frontmatter and SQLite only. A rebuild for the same `(event_id, week_key)`
  updates the existing Inbox file in place, refreshing `target_id` to the new
  pack id while preserving the six human feedback fields.

## Phase 2C1: `candidate`, `candidate_discovery`, `candidate_assessment`

Migration `0004_candidate_qualification.sql` (rewritten for v2) adds three
tables (purely additive; 0001-0003 are immutable).

### `candidate` - stable global identity

One row per `(source, canonical_key)` (`UNIQUE`). `source` is CHECK-constrained
to `github`. The row carries `title`, `url`, `first_seen_at`, `last_seen_at`
(no week/scope/lane/raw_signal_id - those are discovery-level). Re-runs are
monotonic: `first_seen_at` becomes the historical minimum observation time,
`last_seen_at` the historical maximum (never moving backward), and an older
snapshot never overwrites a newer `title`/`url`.

### `candidate_discovery` - provenance

One deterministic row per `(candidate_id, week_key, scope_key, lane,
raw_signal_id)` (`UNIQUE`). `lane` is CHECK-constrained to `watchlist |
mature | emerging | ecosystem`. A new snapshot (different `raw_signal_id`)
in the same context forms a new discovery; the shared Candidate identity
never changes. `FK candidate_id -> candidate(id)`,
`FK raw_signal_id -> raw_signal(id)`.

### `candidate_assessment` - deterministic decision revision

One row per `(candidate_discovery_id, input_hash, policy_version)`
(`UNIQUE`). `input_hash = SHA-256(canonical JSON(whitelist attributes))`,
`decision` CHECK `research | watch | reject`, `trigger_kind` fixed to
`repository_snapshot`. `attributes` carry only reviewed whitelist fields
(plus `homepage_present: true|false`, never the homepage URL itself) -
never the raw payload, never credentials, never quarantined content.
`policy_version` is `candidate-gate-v2`.

### Relationship to the legacy tables

A candidate is neither a `signal`, an `event` nor a `material_pack`: the
qualification path writes only these three tables. The 2B `materialize` flow
remains as a legacy compatibility path.

## Phase 2C2: GitHub discovery policy tables (migration 0005)

Migration `0005_github_discovery_policy.sql` adds four GitHub-specific tables
(purely additive; 0001-0004 are immutable). They orchestrate GitHub discovery
only - they do NOT model global Signals or Events.

### `github_discovery_run`

One row per execution of one `GitHubDiscoveryPolicy` (run-scoped UUID4). It
records `policy_id`, the full canonical `policy_hash` (SHA-256 of the whole
policy definition), `week_key`, `lane`, the agreed `candidate_limit` /
`research_budget`, `status` (`running | success | partial | failed`), a JSON
array of stable warning codes and start/finish timestamps.

### `github_discovery_probe_run`

One row per probe execution inside a discovery run: `probe_id`, `kind`
(`search | watchlist_target | ecosystem_target`), `lane`, `scope_key`,
`spec_hash` (SHA-256 over `(kind, spec)` - the raw query never reaches
storage), `priority`, the linked `collection_run_id`, `status`
(`running | success | partial | failed | blocked`) and safe counters.
`blocked` means the probe was refused BEFORE any network request.

### `github_discovery_scope_binding` (scope/spec firewall)

One immutable row per `scope_key` claiming it for one probe spec. The binding
is recorded before the first network request and its `spec_hash` is never
updated. Before a probe runs, the pipeline checks:

* an existing binding with a different `spec_hash` -> `SCOPE_SPEC_MISMATCH`,
  refused before networking;
* an existing binding whose `policy_id` or `probe_id` differs (same spec) ->
  `SCOPE_BINDING_OWNER_MISMATCH`, refused before networking - one policy can
  never ride another policy's scope;
* no binding but a `source_cursor` row already exists for the scope (an
  unbound legacy scope such as `ai-agents-v1`) -> `SCOPE_UNBOUND_CURSOR`,
  refused before networking.

Changing probe semantics therefore requires bumping the scope version
(`ghp-emerging-v1-q1` -> `ghp-emerging-v2-q1`): a new scope starts a fresh
cursor and old progress is never inherited by a different query.

### `github_candidate_selection`

The final per-run selection of a research candidate into the queue. Only
`qualification_decision = research` candidates are selected. `selection_rank`
orders them; ranks below `research_budget` get `queue_state = queued`, the
rest `over_budget` with `budget_reason = RESEARCH_BUDGET_EXCEEDED`. The
qualification decision is stored verbatim and is NEVER downgraded by the
budget. Ecosystem selections additionally carry `ecosystem_target`,
`relation_kind` (`full_name_match | description_mention | topic_match`),
`relation_field` and `relation_raw_signal_id` (the snapshot that evidenced the
relation). `UNIQUE (discovery_run_id, candidate_id)` guarantees one final
selection per candidate per policy run; the row id is deterministic
(`deterministic_id("github-candidate-selection", run_id, candidate_id)`).

### 2C2-C additions

* **`github-repos-v1` collection adapter** - the watchlist lane collects a
  direct `GET /repos/{owner}/{repo}` snapshot through `GitHubReposClient`.
  Its `config_snapshot` whitelist is `run_mode | source | scope_key |
  adapter_kind | full_name_sha256`; the raw full_name never reaches storage,
  and direct snapshots never paginate (no `source_cursor` row is created).
* **Gate `candidate-gate-v3`** - qualification assessments use this policy
  version whenever an ecosystem relation resolver is supplied. A candidate
  with a resolved relation (`full_name_match | topic_match |
  description_mention`) plus a substantive description and recent push
  reaches `research`; a relation without that baseline stays `watch`, and a
  query hit without any relation keeps the v2 `watch` +
  `missing_ecosystem_relation`. Without a resolver the gate stays
  byte-for-byte v2.
* **Assessment input identity (context)** - discovery-driven assessments hash
  the whitelisted repository attributes together with the relation match
  (`target`/`kind`/`field`, or none) and the discovery policy context hash,
  so a relation or policy change yields a new auditable revision while
  historical revisions are retained. 2C1-style calls without a resolver and
  without a context keep the legacy attribute-only input hash. The stored
  assessment row returned by the repository is what
  `github_candidate_selection.winning_assessment_id` references; its decision
  always equals the selection's stored `qualification_decision`.
* **Probe status propagation** - a `partial` collection outcome keeps the
  probe run `partial` with the stable `PROBE_PARTIAL` warning (never payload,
  URL, query or exception text). The discovery run is `success` only when
  every probe succeeded, `failed` when every probe is failed/blocked, and
  `partial` otherwise.

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
