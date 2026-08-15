# Architecture

> **文档职责说明（2026-08-15）：** 本文件保留各历史提交的累计实现细节，供代码
> 追溯使用，不再负责产品目标、当前状态或未来阶段定义。请先读
> [00-PRODUCT.md](./00-PRODUCT.md)、[01-ROADMAP.md](./01-ROADMAP.md)、
> [02-STATUS.md](./02-STATUS.md) 与 [03-ARCHITECTURE.md](./03-ARCHITECTURE.md)。
> 下文出现的 `current` 或“未来阶段”应按其所在历史小节理解。

This document describes the **AI Signal Agent** shadow rewrite and how it
coexists with the legacy weekly pipeline.

## Historical Phase 1 view: two paths, one repository

The repository runs two independent paths during phase 1.

### Legacy path (production)

```
GitHub Actions (.github/workflows/weekly.yml)
        │
        ▼
python main.py   →   Tavily + GitHub Trending   →   LLM (GLM / Gemini / DeepSeek)
                                                           │
                                                           ▼
                                                   Markdown → HTML email
```

The legacy path remains the **source of truth in production**. Phase 1 does
not modify `main.py`, `requirements.txt` or the weekly workflow. The GitHub
Action still runs `python main.py` exactly as before.

### Shadow path (new foundation)

```
ai_signal (offline, stdlib-only, shadow mode)
        │
        ├─ config         immutable Settings, shadow by default
        ├─ domain         models + state machine
        ├─ sources        abstract Source contract (no concrete adapters yet)
        ├─ storage        local SQLite v1 + migrations + repositories
        ├─ observability  JSON Lines redacted logging
        └─ cli            config check / db init / db status / doctor
```

The shadow path runs **only locally** during phase 1. It does not collect,
call an LLM, build a material pack, publish to a vault or send email. It
establishes the substrate that future phases will fill in.

## Phase 1 module responsibilities

| Module | Responsibility | Not yet implemented |
| --- | --- | --- |
| `ai_signal.config` | Immutable `Settings` (CLI > env > defaults); shadow mode disables network/email/publish | reading live secrets |
| `ai_signal.domain.models` | All entities as frozen dataclasses with validation and deterministic ids | persistence |
| `ai_signal.domain.states` | Signal lifecycle state machine + `StateTransitionRecord` | wiring into repos beyond audit |
| `ai_signal.sources.base` | Abstract `Source.collect(cursor, context) -> SourceBatch` | GitHub/Twitter/Reddit/Tavily/Agent-Reach |
| `ai_signal.storage.sqlite` | Connection management, migration runner, schema status | online stores |
| `ai_signal.storage.repositories` | Collection run, raw signal, signal, state-transition repositories | claim/evidence/pack/publication repos |
| `ai_signal.observability.logging` | JSON Lines output with recursive redaction | file logging, remote sinks |
| `ai_signal.cli` | Offline CLI with documented exit codes | running the pipeline |

## Local SQLite is the future source of truth

The local SQLite database (`collection_run`, `raw_signal`, `signal`, …) is
designed to become the durable source of truth for the pipeline: every
collected item is stored exactly once, every state change is audited, and
feedback can be correlated with scoring. In phase 1 this database is only
exercised by the offline CLI and the tests; nothing in production reads or
writes it yet.

The connection is opened with `foreign_keys=ON`, `journal_mode=WAL`,
`synchronous=NORMAL` and `busy_timeout=5000`. Migrations are versioned,
checksummed, idempotent and applied inside explicit transactions so a
failing migration can never leave a stale `schema_migration` row.

## Actions still run the legacy pipeline

GitHub Actions is unchanged. It continues to:

1. check out the repository,
2. install dependencies from `requirements.txt`,
3. run `python main.py`.

The shadow package is not installed or invoked by the workflow in phase 1.

## Future collector degradation and the safety boundary

When concrete sources are added in later phases, the contract is:

* a single source communicates partial/total failure through
  `SourceBatch.status` (`partial` / `unavailable` / `failed`) and **must
  not** abort the whole pipeline;
* only genuinely unrecoverable problems raise `SourceError`;
* no source reads cookies, tokens or other credentials from global state —
  any credential is passed in explicitly via the run context;
* the default `shadow` run mode keeps network, email and publishing
  disabled; switching to `live` is an explicit, deliberate configuration
  change.

Credential-bearing payload keys are rejected before persistence. The
redacted logger is the second boundary that prevents tokens, cookies,
authorization headers, SMTP details, emails and sensitive URL query
parameters from reaching stdout or a future log sink.

## Phase 2A: offline GitHub fixture collection

Phase 2A adds the first concrete collection path, still entirely offline:

```
GitHub JSON fixture ──▶ FixtureGitHubClient ──▶ GitHubSource.collect
                                                     │
                                                     ▼
                                               SourceBatch
                                                     │
                       collect_source_once (pipeline/collect.py)
                                                     │
                    ┌────────────────┬───────────────┴───────────────┐
                    ▼                ▼                               ▼
              CollectionRun    SourceRun                     RawSignal (upsert)
              (own txn)        ──────────────────── single final transaction ───────────
                               SourceCursor (advance: success only, per (source, scope_key))
                               + CollectionRun status
```

Key points:

* **Offline only.** The client is `FixtureGitHubClient`, which reads a local
  JSON file. There is no online/real/live network mode in 2A and no call to
  Agent-Reach. A real GitHub client is deferred to a later phase. *(The real
  GitHub client was subsequently implemented as **2B1** - see below; this
  paragraph records the original 2A boundary only.)*
* **Progress is isolated by `(source, scope_key)`.** A `scope_key` is a stable
  logical collection alias (for example `ai-agents-v1`,
  `github-fixture-v1`) - never the raw GitHub query, a URL, a file path or a
  date. Each scope keeps its own `SourceCursor` row and its own `SourceRun`
  rows, so running several GitHub query scopes never overwrites another
  scope's cursor or trips a uniqueness clash within a collection run. When the
  query semantics change incompatibly the scope version is bumped
  (`ai-agents-v1` -> `ai-agents-v2`). The raw GitHub query never reaches the
  cursor table.
* **Cursor advances on a new successful position only.** `SourceCursor` is
  updated inside the final transaction only when `SourceBatch.status ==
  "success"` and the source returns a distinct, non-null next cursor. A
  terminal null cursor or repeated cursor does not clear or falsely advance
  the stored value. `partial`, `unavailable` and `failed` batches are still
  recorded (as a `SourceRun` and raw signals for the items that did parse) but
  never move the cursor, so the next run retries the same page.
* **One atomic final transaction.** `SourceRun`, all `RawSignal` upserts, the
  optional cursor advance and the `CollectionRun` status update share a single
  `BEGIN/COMMIT`. If it fails it is rolled back and the run is best-effort
  marked `failed` without masking the original `StorageError`.
* **Deterministic dedup.** Each item payload is hashed with canonical JSON
  (`sort_keys=True`, `ensure_ascii=False`, `separators=(",", ":")`) → SHA-256,
  feeding the existing `raw_signal(source, external_id, payload_sha256)`
  uniqueness key, so a repeated item never creates a duplicate row.
* **Safe config.** `config_snapshot` may carry only `run_mode=shadow`,
  `source=github`, `scope_key`, `adapter_kind`, and adapter-specific fields
  (`fixture`/`fixture_sha256` for the fixture adapter, or `query_sha256`/
  `sort`/`order`/`per_page`/`max_pages` for the REST adapter); never a path,
  an env var, a credential, or the raw GitHub query.

## Phase 2B1: read-only public GitHub Search collection

Phase 2B1 adds a second, opt-in adapter that reaches the real public GitHub
Search API on top of the exact same pipeline:

```
GitHubSearchSpec -> GitHubRestClient -> GitHubSource -> SourceBatch
                                          -> collect_source_once
                                             -> source_run / raw_signal / source_cursor
```

The two adapters are selected by `config_snapshot["adapter_kind"]`:

* `fixture` - offline JSON fixture (phase 2A, no network);
* `github-rest-v1` - read-only public GitHub Search API (phase 2B1).

Network boundary (hard rules):

* the production transport (`UrllibTransport`) is the only module allowed to
  import `urllib.request` / `urllib.error`; no other production module may
  import a network or subprocess client, and no test ever makes a real call;
* a real request happens **only** for `collect github-live` when
  `--allow-network` is explicitly passed, after all parameter validation and
  while `run_mode` stays `shadow`;
* the only reachable host is `https://api.github.com/search/repositories`;
  redirects away from it are blocked and the response final URL is re-checked
  (https, `api.github.com`, no credentials, no alternate port);
* no `Authorization` / `Cookie` is sent - anonymous public data only.

Query and cursor safety:

* the raw GitHub query is used solely to build the HTTPS request; only
  `query_sha256 = SHA256(canonical JSON(query, sort, order, per_page, max_pages))`
  is persisted in `config_snapshot`. The raw query never reaches
  `scope_key`, the cursor table, logs, warnings or exception messages.
* cursors are the stable form `page:N` (never a URL or the query) and point
  at the *next* page to request. The cursor advances only on a successful
  batch with a distinct next page within `max_pages` (1–3); `per_page` is
  capped at 25. When `max_pages` is reached (or the result set runs out) the
  cursor wraps back to `page:1`, so a scope is re-scanned periodically rather
  than terminating; SQLite dedup keeps `raw_signal` stable across re-scans.
* every transport/response failure is mapped to a stable `GitHubClientError`
  code and then to a stable batch warning code (e.g. `GITHUB_RATE_LIMITED`,
  `GITHUB_INVALID_RESPONSE`), with no sensitive data in the warning.

### What is still deliberately out of scope

* X, Reddit, Agent-Reach, Tavily, LLM-based summarization, auto-publish and
  email switching.
* `main.py` and the GitHub Actions workflow remain unchanged; production still
  runs `python main.py`. A real GitHub smoke run is performed manually only.

## Phase 2B2: deterministic materialization (offline, no LLM)

Phase 2B2 closes the loop from collected `raw_signal` to human-editable
Markdown inbox and feedback sync, entirely deterministically and offline:

```
select_github_raw_signals(week_key, scope_key, limit)
  → normalize/dedup → Signal (canonical_key = github:repository:<id>)
  → one deterministic Event per repository
  → factual Claims from snapshot fields only
  → Evidence anchored to raw_signal
  → ClaimEvidence (supports)
  → A–F MaterialPack (canonical JSON → stable bundle_hash → stable pack id)
  → Markdown Inbox/<pack_id>.md (atomic write, frontmatter preserved)
  → feedback sync: parse frontmatter → deterministic Feedback row
```

Key design rules:

* **Scope isolation via observation attribution.** `raw_signal` is
  content-addressed and globally deduplicated, so its `collection_run_id`
  alone cannot isolate multiple scopes. Each collection writes a
  `raw_signal_observation` row linking the `source_run` to the (possibly
  shared) snapshot, and `select_github_raw_signals` joins
  `raw_signal_observation → source_run → collection_run → raw_signal` filtered
  by `(week_key, scope_key)`. Different scopes never mix, an overlapping
  snapshot is visible to every scope that observed it, and within one scope
  only the *latest* snapshot per repository is kept (deterministic
  `(updated_at|pushed_at, observed_at, raw_signal.id)` ordering), with `limit`
  applied after that deduplication.
* **No LLM, no fabricated facts.** Claims are generated only from fields
  present in the single API snapshot. "Trending" / "growing" claims are
  forbidden. Each Claim must bind at least one Evidence; the validator checks
  every claim *against its own* evidence only - numbers, full date/time tokens
  and URLs must be traceable to that claim's bound evidence, never another
  claim's.
* **Untrusted-data handling.** `full_name`, `description`, `topics` are
  sanitized: NUL/control characters rejected, HTML escaped, prompt-injection
  markers detected. A poisoned `description` is replaced with a safe
  placeholder; injection in an essential field (`full_name`, URL, timestamp)
  quarantines the whole record while other records continue processing.
  Repository URLs must be HTTPS on an allowed host (github.com, or the
  reserved example domains via an explicit fixture policy); stars/forks must
  be non-negative integers; timestamps must be tz-aware ISO.
* **Idempotency.** All ids are deterministic (SHA-256 of canonical parts).
  Re-running never duplicates Signal / Event / EventMember / Claim / Evidence /
  ClaimEvidence / MaterialPack / Feedback rows. `SignalRepository.upsert` only
  moves `last_seen_at` forward and only refreshes mutable fields from a
  non-older observation, so it never resets a Signal that has already advanced.
* **Single transaction, two-stage.** Materialization and packaging share one
  `BEGIN/COMMIT`: Signals advance `collected → normalized → clustered →
  verified`, and only after a MaterialPack row is persisted does a Signal
  advance `verified → packaged` (compare-and-swap, so re-runs add no duplicate
  transitions). A failure rolls back everything.
* **Atomic Markdown (after commit).** Materialize + validate + persist packs +
  packaged state commit first; Markdown is then published outside the
  transaction via same-directory temp + `os.replace` (target proven inside
  `output-root/Inbox`, symlink/`..` escapes rejected). A publish failure keeps
  the committed packs and is reported as an output error, never a database
  error; a re-run补写s any missing file.
* **Feedback sync.** Scans first-level `.md` only; deterministic Feedback ids
  prevent duplicate rows on re-sync; a changed decision appends a new row. A
  database failure propagates so the whole sync transaction rolls back - a
  single invalid file is skipped, never half-committed.

## Phase 2B3: Chinese material + readable filenames

Phase 2B3 changes the presentation layer only; collection, attribution and the
packaging data flow stay exactly as in 2B2.

* **Readable tentative filenames.** ``Inbox/<week_key> - GitHub - <repo>
  (<owner>).md`` where owner/repo come from the validated GitHub
  ``full_name``. The policy is encapsulated in
  ``outputs.markdown.build_markdown_filename`` (Windows-illegal characters,
  control characters, trailing spaces/dots and reserved device names
  sanitized; length cap) so it can be swapped later. A short ``event_id``
  suffix is only appended on truncation or a collision with a different
  event. ``pack_id``/``event_id`` stay in frontmatter + SQLite only.
* **Rebuild semantics.** A rebuild finds the existing Inbox file for the same
  ``(event_id, week_key)`` (by frontmatter, not by name) and updates it in
  place: human feedback fields are preserved and ``target_id`` is refreshed to
  the new pack id after validating the old one is 64-hex. Files whose
  ``event_id``/``week_key`` do not match are never overwritten (a collision
  falls back to a suffixed name); a file with unparseable frontmatter at the
  target name is refused, not overwritten.
* **Chinese body, machine frontmatter.** The six A–F angles, the claims
  section (「事实声明与来源」) and a Chinese human-feedback guide are rendered
  in Chinese; frontmatter field names and the ``decision`` enum stay English
  so ``feedback sync`` remains compatible. Repository names, URLs, languages
  and raw topics are never translated.
* **Chinese factual claims.** Claims are natural Chinese sentences quoting
  only snapshot fields; ``description`` and ``topics`` were added to the
  evidence payload so they remain traceable. Evidence ids moved to a
  versioned scheme (``evidence-v2`` + canonical payload hash): the payload
  extension produces fresh ids while all old evidence rows stay immutable.
* **Pack schema ``material-pack-v2``.** The A–F templates now reference the
  current repository's ``full_name``/``description``/``topics``/``language``
  where possible, and a ``project_info`` section carries the snapshot facts
  shown in the Markdown. Angle C keeps heat explicitly unmeasured (single
  snapshot, no trend claims).

## Phase 2C: Candidate System

Phase 2C = Candidate System. The 2B path made a repository directly an Event
and then an A-F materialPack; 2C1 corrects the pipeline shape - **Repository
!= Event**, **search result != Candidate**, **Candidate != Material** - with a
new recommended path::

    Discovery (GitHub Search -> RawSignal)
      -> Candidate Qualification (research | watch | reject)
      -> Research (read README / releases)
      -> Editorial Decision (future phase)
      -> Content Pack (future phase)

### 2C1 (current)

* **Stable Candidate identity** - one row per `(source, canonical_key)`
  (`UNIQUE(source, canonical_key)`); the same repository across any week,
  scope or lane is exactly one Candidate. The identity carries no week/scope/
  lane - those live in discovery rows.
* **Discovery provenance** - `candidate_discovery` records each
  `(candidate, week, scope, lane, raw_signal)` context; different
  lanes/scopes/weeks produce different discoveries sharing one Candidate. A
  new snapshot in the same context forms a new discovery (raw_signal_id
  differs); an older snapshot replayed after a newer one never regresses
  `last_seen_at` / `title` / `url`.
* **Lane-aware qualification** (policy `candidate-gate-v2`, transparent integer
  constants, no composite float score):
  - common safety/junk filters: fork/archived/disabled/is_template/missing
    description/unsafe identity URL/prompt-injection -> reject;
  - `watchlist`: substantive description + push within 45 days -> research;
  - `mature`: substantive + agent relevance + stars>=100 + push within 45
    days -> research;
  - `emerging`: substantive + agent relevance + created within 180 days +
    push within 45 days -> research (no minimum stars; 1-star adhd-one-like
    qualifies);
  - `ecosystem`: metadata-only -> watch with `missing_ecosystem_relation`
    (2C2 Discovery Policy will provide monitored core projects);
  - `stars > 0` is no longer a generic research condition; a bare "ai" is not
    an agent-relevance match.
* **Homepage safety** - homepage is untrusted user metadata: it never gates
  RESEARCH, an unsafe homepage never rejects the candidate, and no homepage URL
  is stored or rendered - only `homepage_present: true|false` plus a reason
  code. The repository identity URL must still be safe github.com HTTPS.
* **Optional debug Markdown** - by default qualification writes only SQLite;
  only `--emit-candidate-markdown --output-root --allow-output-write`
  produces Chinese debug cards under `Candidates/Research/` (RESEARCH only;
  WATCH/REJECT stay database-only; `Candidates/Watch/` is never created).
* **Three decision vocabularies kept strictly separate**: Qualification
  (research|watch|reject), future Editorial Publishability
  (ready_to_write|needs_testing|watch|reject), human feedback
  (adopted|parked|rejected). Only the first is implemented.

### 2C2 (not yet implemented)

* Real Discovery Policy (Watchlist/Mature/Emerging/Ecosystem query
  strategies, larger recall pool, research budget).
* Monitored core projects and relation evidence for ecosystem lane.
* Currently absent: `--lane` is only a discovery-source label and Gate
  strategy selector.

### What 2C1 does NOT solve

* It does not improve upstream GitHub Search randomness.
* It does not solve material quality - only object identity and
  qualification-gate boundaries.
* Candidate Markdown is optional debug output, not a material Inbox; the Inbox
  will receive Editorial Results after Research in a future phase.

The existing `materialize github` / A-F flow remains as the Phase 2B legacy
compatibility path - not deleted, not the recommended entry point.

### 2C2 (in progress: GitHub-specific Discovery Policy Adapter)

2C2 (DEC-013) turns the ad-hoc `--lane` flag into a versioned, in-code
`GitHubDiscoveryPolicy` catalog (`src/ai_signal/discovery/policy.py`) with
four policies matching the four GitHub Discovery Lanes:

```
watchlist-v1  (watchlist_target probes; DRAFT first-user list filled
               2026-08-16, pending first-user confirmation/swap)
mature-v1     3 search probes, stars>=100 de-noising, budget 50/5
emerging-v1   3 search probes, no minimum stars, budget 100/8
ecosystem-v1  (search probes + ecosystem_targets aliases; DRAFT first-user
               list filled 2026-08-16, pending confirmation/swap)
```

Key rules:

* **One probe, one scope, one cursor.** Every probe owns a unique
  `scope_key`; the raw query exists only in memory and only `spec_hash` is
  persisted or compared.
* **Scope/spec firewall.** `github_discovery_scope_binding` (migration 0005)
  claims each scope for its spec before the first network request. An
  existing cursor with a different `spec_hash` (or an unbound legacy scope)
  blocks the probe BEFORE any network access - a new query can never inherit
  an old query's pagination cursor. The binding also checks its owner: a
  different `policy_id` or `probe_id` on the same spec blocks with
  `SCOPE_BINDING_OWNER_MISMATCH`. Semantic changes bump the scope version.
* **Deterministic priority.** Probes carry unique numeric priorities and run
  in ascending order; the lowest priority wins a candidate seen by several
  probes. `candidate_limit` is 1..100 and `research_budget` is 0..limit.
* **Partial stays partial.** A `partial` collection keeps the probe run
  `partial` with the stable `PROBE_PARTIAL` warning; the discovery run is
  `success` only when every probe succeeded (any degradation -> `partial`,
  all failed/blocked -> `failed`).
* **Reuse, not re-invention.** Probes drive the existing
  `collect_source_once` (2A/2B1) and `qualify_github` (2C1); qualification
  receives the policy context hash, and the assessment input identity covers
  the relation match plus that context, so relation or policy changes yield
  new auditable revisions (legacy 2C1 calls without a context keep the
  original attribute-only input hash).
* **Candidate-level dedup + budget.** The run deduplicates candidates across
  probes, ranks them deterministically (probe priority, then assessment
  recency, then candidate id - no LLM ranking), and splits the research
  queue at `research_budget`: within budget `queued`, beyond it
  `over_budget`. The qualification decision is never downgraded by budget.
* **Output is a GitHub Research Queue only** - `github_candidate_selection`
  rows, DB-only. No Events, no A-F packs, no Markdown reports, no global
  Signal taxonomy. Watchlist observes repository metadata only (no README /
  Release content) via a direct `GET /repos/{owner}/{repo}` snapshot
  (`github-repos-v1` adapter, no pagination, full_name never persisted);
  Ecosystem relations are metadata-only (`full_name | description | topics`
  matches against first-user aliases, word-boundary matching, Gate
  `candidate-gate-v3` with the relation evidenced by its `raw_signal_id`);
  README confirmation is deferred to 2D.
* **CLI (2C2-D)** - `ai-signal discover github --policy <id>` runs a policy
  (network probes require `--allow-network`), `--list-policies` prints safe
  catalog metadata and `--status` prints recent runs with safe counts.

### Historical boundary of the original 2A delivery

At the time 2A was delivered it deliberately excluded:

* network collection and a real GitHub HTTP client;
* Agent-Reach doctor / reachability checks;
* a real read-only smoke run against live data.

The GitHub HTTP client was subsequently implemented as **2B1** and has had a
controlled real run. This paragraph records the original 2A boundary; it is
not the current project status. Current facts are owned by
[02-STATUS.md](./02-STATUS.md).

## Rollback

The original Phase 1 was purely additive, but its early “delete all added
directories” rollback recipe is no longer safe: `src/ai_signal/`, `tests/`
and `docs/` now contain the later 2A–2C work and the authoritative project
control documents.

The current production safety boundary is simpler: the workflow still runs
`python main.py`, so leaving the shadow path disconnected requires no file
deletion. To undo a shadow change, identify the exact phase/commit in
[02-STATUS.md](./02-STATUS.md), use an auditable Git revert after review,
back up any local database before migration changes, and run the full test
suite plus legacy contract afterward. Never remove the whole shadow tree as a
generic rollback action.
