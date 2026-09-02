# duckgate

Interactive SQL shell over S3 data (Parquet, Iceberg, CSV) using DuckDB.

## Install

`duckgate` is a CLI tool, so an isolated install is recommended over a bare `pip install`:

```bash
uv tool install duckgate
# or
pipx install duckgate
```

## Quick start

```bash
duckgate init          # create duckgate.toml in current directory
duckgate tables        # list available tables
duckgate               # open interactive shell
duckgate -q "SELECT COUNT(*) FROM my_table"  # one-shot query
```

## Configuration

`duckgate.toml` (current directory) or `~/.duckgate/config.toml`:

```toml
[aws]
profile = "my-aws-profile"
region  = "eu-central-1"

[glue]
enabled   = true
databases = []  # empty = all databases

[[tables]]
name   = "fis_location"
path   = "s3://my-bucket/structured/sqlserver/dev/fis/location/**/*.parquet"
format = "parquet"  # parquet | iceberg | csv
```

### Table resolution

- **Glue tables** are discovered automatically from the configured databases. A bare Glue
  table location (no wildcard) is read as `location/**/*.<format>`.
- **Local `[[tables]]`** entries override Glue tables with the same name — use this to point
  to a specific environment prefix.
- If two Glue tables from different databases share a name, they are registered as
  `database__table_name`.

## AWS credentials

Uses the AWS profile from `[aws] profile`. Works with SSO (`aws sso login`, Granted `assume`)
and static credentials alike.

## Supported formats

| Format  | How                                                  |
| ------- | ----------------------------------------------------- |
| Parquet | `read_parquet('s3://...')` via httpfs                |
| Iceberg | `iceberg_scan('s3://...')` via the iceberg extension  |
| CSV     | `read_csv('s3://...')` via httpfs                     |

## Development

```bash
uv sync
uv run pytest
uv run ruff check --fix .
uv run ruff format .
```

## Release

Pushing a `v*` tag (e.g. `v0.1.0`) builds and publishes to PyPI via GitHub Actions, using
PyPI Trusted Publishing (see `.github/workflows/publish.yml`). Bump `__version__` in
`duckgate/__init__.py` to match before tagging.

## License

Apache License 2.0 — see [LICENSE](LICENSE).
