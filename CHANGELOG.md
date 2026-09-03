# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Changed

- `duckgate init` now prompts for the config path (default `~/.duckgate/config.toml`,
  Enter accepts it) instead of always writing `./duckgate.toml`. Pass `-p`/`--path` to skip
  the prompt for scripts and CI.

### Fixed

- Quote view names in `CREATE VIEW` — Glue database/table names commonly contain hyphens,
  which DuckDB's unquoted identifier syntax parsed as subtraction, breaking every hyphenated
  Glue table.

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
