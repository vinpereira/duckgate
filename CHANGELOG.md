# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

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
