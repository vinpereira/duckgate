# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.8.0] - 2026-09-22

### Breaking

- `[glue] enabled` now defaults to `false` (was `true`). Any `duckgate.toml` that omits the
  `[glue]` section, or has `[glue]` without an explicit `enabled` key, no longer discovers
  Glue tables — add `enabled = true` to keep the previous behavior. Motivation: Glue
  discovery alone measured ~1.5–1.9s against a real 4-database config, roughly doubling wall
  time for a direct S3 query that never touches the catalog (this project's own README-
  highlighted "querying S3 without any config" use case) — see
  `docs/duckgate-vs-athena-performance.md` and
  `docs/superpowers/specs/2026-09-22-glue-optout-and-iceberg-version-hint-design.md`.

### Fixed

- Iceberg tables with no `version-hint.text` now readable — `engine.py` sets
  `unsafe_enable_version_guessing = true` on every connection. Hit for real on a production
  Glue-cataloged table during the same benchmark.

## [0.7.0] - 2026-09-20

### Added

- `[query] timeout_seconds` config (default 0, disabled), `--timeout N` CLI flag, and
  `\timeout N`/`\notimeout` shell meta-commands. Cancels a running query after N seconds via
  `conn.interrupt()` on a background timer — verified this reliably raises
  `duckdb.InterruptException` mid-scan, is a harmless no-op when nothing is running, and
  leaves the connection usable afterward. Unlike the default row LIMIT (v0.6.0), this bounds
  wall-clock time for aggregates too, since it cancels the query outright rather than
  wrapping it.

## [0.6.0] - 2026-09-13

### Added

- `[query] default_limit` config (default 100), `--limit N` CLI flag, and `\limit N`/
  `\nolimit` shell meta-commands. A `SELECT`/`WITH` query with no `LIMIT` is now capped by
  default instead of just warned about — verified via `EXPLAIN` that wrapping it as
  `SELECT * FROM (<query>) LIMIT N` doesn't defeat DuckDB's limit pushdown. Still doesn't
  reduce scan cost for aggregates (`COUNT(*)`, `GROUP BY`) — those still get the plain
  stderr warning instead of a silently-wrong sense of safety.

## [0.5.0] - 2026-09-07

### Added

- `format = "json"` for `[[sources]]`/Glue-detected tables, via `read_json`. Detected from a
  Glue table's JSON SerDe (`org.openx.data.jsonserde.JsonSerDe` and similar). A bare-prefix
  JSON location globs as `**/*.json*` (not `**/*.json`) so it also matches gzipped files like
  CloudTrail's `*.json.gz`.
- `spatial` extension loaded on every connection — enables `ST_*` geo functions directly on
  position/GPS data.

### Changed

- Credentials are now injected via `CREATE SECRET` instead of four `SET s3_*` statements.
  Avoids interpolating the access key/secret/session token into SQL text.

## [0.4.0] - 2026-09-05

### Breaking

- Renamed the `[[tables]]` config section to `[[sources]]`, and `TableConfig` to
  `SourceConfig`. Existing `duckgate.toml` files using `[[tables]]` need updating — the
  section is no longer recognized. Motivation: `duckgate` isn't Glue-specific — a "source" is
  just a named piece of S3 data, whether or not it corresponds to a Glue table.

### Added

- Documented querying S3 directly with no config at all (`read_parquet`/`iceberg_scan` with
  a literal path, in `-q` or the shell) — this already worked, it just wasn't written down.
  README now leads with this and de-emphasizes Glue as a hard requirement.
- `duckgate init`'s generated template explains the dual role of `[[sources]]`: overrides a
  Glue table by name if one matches, otherwise it's just a name for an S3 location.

## [0.3.1] - 2026-09-04

### Fixed

- `ensure_registered`'s query-text scan used a plain substring check, which false-positived
  on tables whose name is a literal prefix of another (common with Glue's hierarchical
  naming, e.g. `..._root_table` vs. `..._root_table_accumulateddata_batterystatus`) —
  querying only the longer table also registered the shorter one unnecessarily. Now uses a
  word-boundary-aware match.

### Docs

- README: document `duckgate describe`, lazy registration, the LIMIT warning, and the
  Makefile targets. Correct the AWS credentials section — Granted `assume` alone does **not**
  work transparently (it only exports temporary credentials into the shell's environment,
  which a named-profile lookup ignores); it needs `credential_process` configured on the
  profile.

## [0.3.0] - 2026-09-04

### Added

- `duckgate describe <table>` — shows a table's schema (column names/types) without
  registering a view for it.
- Warn on stderr when a query has no `LIMIT` clause. Note this only flags the risk — it
  doesn't rewrite the query, and it can't help an aggregate query (`COUNT(*)`, `GROUP BY`)
  that has to scan the whole table regardless of any `LIMIT`.
- `Makefile` with `lint`/`format`/`test`/`check`/`clean`/`sync` targets for local development.

### Changed

- Table registration is now lazy: a table's DuckDB view is created only when a query
  references it, once per session (repeated references cost nothing). Previously every
  invocation — including `duckgate tables` — eagerly registered every table in scope, which
  meant a real S3 round-trip per table before anything could run.
- `duckgate tables` no longer opens a DuckDB connection or touches S3 — it lists the Glue/
  local catalog directly, so it's near-instant regardless of how many tables exist.

### Behavior change

- `duckgate tables` now lists every table the catalog *discovers* (what Glue/your local
  config advertises), not just the ones that previously *registered successfully*. A table
  with no matching files or bad permissions now appears in the list and only fails when you
  actually query it (with the existing warning), instead of being silently excluded upfront.

## [0.2.0] - 2026-09-03

### Changed

- `duckgate init` now prompts for the config path (default `~/.duckgate/config.toml`,
  Enter accepts it) instead of always writing `./duckgate.toml`. Pass `-p`/`--path` to skip
  the prompt for scripts and CI.
- Table registration now shows a single self-updating progress line (`Registering tables...
  i/N`) on stderr — with many Glue tables, each one binds its schema over the network, which
  can take a while with no feedback otherwise. Kept to one line so it doesn't duplicate the
  table list `duckgate tables` already prints at the end.
- Enabled DuckDB's native progress bar (`PRAGMA enable_progress_bar`) for query execution —
  only shows up past the 2s default threshold, so it stays quiet during fast catalog
  registration but gives feedback on slow scans in `-q` and the interactive shell.

### Fixed

- Quote view names in `CREATE VIEW` — Glue database/table names commonly contain hyphens,
  which DuckDB's unquoted identifier syntax parsed as subtraction, breaking every hyphenated
  Glue table.
- A single table that fails to register (empty prefix, no matching files, bad permissions)
  no longer aborts `duckgate tables`/`-q`/the shell — it's skipped with a warning and the
  rest of the catalog still loads.
- Ctrl+C in the interactive shell now cancels the running query — `prompt_toolkit`'s terminal
  handling was leaving Ctrl+C unable to reach DuckDB's own cancellation, so a long `SELECT`
  couldn't be interrupted. Wires up `conn.interrupt()` explicitly for the duration of query
  execution.

## [0.1.0] - 2026-09-02

### Added

- Project scaffold: `hatchling` build backend, Click entry point, `ruff` linting.
- TOML config loader (`duckgate.toml` / `~/.duckgate/config.toml`) with AWS, Glue, and
  local table settings.
- DuckDB connection factory with S3 credential injection (`httpfs`, `iceberg` extensions).
- Catalog builder: registers local `[[tables]]` entries and AWS Glue Data Catalog tables as
  DuckDB views, with local-overrides-Glue precedence and `database__table` collision handling.
- CLI commands: `duckgate tables`, `duckgate init`, and one-shot queries (`-q`) with
  `table`/`csv`/`json` output.
- Interactive SQL shell (`prompt_toolkit`).
- Apache License 2.0.
- GitHub Actions workflow to publish to PyPI via Trusted Publishing on `v*` tags.
