# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

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
