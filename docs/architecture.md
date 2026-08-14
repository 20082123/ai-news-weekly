# Architecture

This document describes the **AI Signal Agent** shadow rewrite and how it
coexists with the legacy weekly pipeline.

## Two paths, one repository

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
  Agent-Reach. A real GitHub client is deferred to a later phase.
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

* Signal cards, claim/evidence, multi-angle packs, Markdown, Obsidian,
  feedback sync, X, Reddit, Agent-Reach, auto-publish and email switching.
* `main.py` and the GitHub Actions workflow remain unchanged; production still
  runs `python main.py`. A real GitHub smoke run is performed manually only.

### What is deliberately not in 2A

* Network collection and a real GitHub HTTP client.
* Agent-Reach doctor / reachability checks.
* A real read-only smoke run against live data.

These belong to **2A-2** (Agent-Reach doctor and a real read-only smoke), which
runs only after the offline path is verified end to end.

## Rollback

Phase 1 is purely additive: it introduces `src/ai_signal/**`, `tests/**`,
`docs/**`, `pyproject.toml` and extends `.gitignore`. It touches no legacy
file. Rolling back phase 1 therefore means simply removing these additions;
the legacy pipeline keeps working unmodified because it never depended on
them.

To undo the shadow foundation:

1. delete `src/ai_signal/`, `tests/`, `docs/`, `pyproject.toml`;
2. revert the appended section of `README.md` and the appended lines in
   `.gitignore`;
3. remove any local `*.db` / `*.sqlite` artifacts created by `db init`
   (they are git-ignored and never referenced by `main.py`).

No `main.py`, workflow, secret or schedule needs to change to roll back.
