# duckgate

Interactive SQL shell over S3 data (Parquet, Iceberg, CSV) using DuckDB.

## What duckgate adds on top of DuckDB

The SQL engine is unmodified DuckDB — every query, join, and function runs exactly as it
would in the `duckdb` CLI, including querying S3 data ad hoc (see below). Glue is **optional**:
`duckgate` is a thin orchestration layer for querying S3 as SQL tables, with or without a
Glue Data Catalog behind it.

| | Plain DuckDB | duckgate |
|---|---|---|
| Query S3 data directly | `SELECT * FROM read_parquet('s3://bucket/prefix/**/*.parquet')` — works today, no setup | Same, plus you can give it a short reusable name (see `[[sources]]` below) |
| Discover tables | Write the full S3 path per table, by hand | `duckgate tables` lists everything from Glue + your named sources |
| AWS credentials | `SET s3_access_key_id=...` or `CREATE SECRET`, manually | Resolved automatically from an AWS profile (SSO or static) |
| Hyphenated names | You quote the identifier yourself | Quoted automatically (`CREATE VIEW "name-with-hyphens"`) |
| Glue location with no wildcard | `read_parquet` won't expand it — "no files found" | `**/*.<format>` appended automatically |
| Point at another environment | Rewrite the query with a different path | `[[sources]]` overrides a Glue table by name, no catalog edits |
| Registering many tables | N/A — you only use what you write | Lazy, on-demand, once per session |
| One broken table (bad perms, empty) | You just don't query what you don't know about | Rest of the catalog keeps working, warning printed |
| Query with no `LIMIT` | No warning | Warned on stderr (`-q` and the shell) |

Where plain DuckDB already holds its own: the `duckdb` CLI has its own polished interactive
shell (history, autocomplete, `.mode`) and a `-c "query"` one-shot flag. `duckgate`'s shell is
simpler — its value is coming with S3/Glue already wired in, not out-shelling DuckDB.

### Querying S3 without any config

Because the connection already has `httpfs` loaded and your AWS credentials set, you can
query any S3 path directly — no Glue, no `duckgate.toml` entry, nothing pre-registered:

```sql
SELECT COUNT(*) FROM read_parquet('s3://bucket/prefix/**/*.parquet')
SELECT * FROM iceberg_scan('s3://bucket/some-iceberg-table/')
```

This works in both `-q` and the shell. It's the closest thing to Athena's "query an S3
location directly" — the trade-off is you type the full path every time and it won't show up
in `duckgate tables`/`describe`. Give it a name via `[[sources]]` (below) when you want either
of those.

## Install

`duckgate` is a CLI tool, so an isolated install is recommended over a bare `pip install`:

```bash
uv tool install duckgate
# or
pipx install duckgate
```

## Quick start

```bash
duckgate init          # prompts for a config path (default: ~/.duckgate/config.toml)
duckgate tables        # list available tables
duckgate describe my_table                   # show a table's schema, no registration
duckgate               # open interactive shell
duckgate -q "SELECT COUNT(*) FROM my_table"  # one-shot query
```

`duckgate init` accepts Enter to take the suggested default, or a path you type (e.g. a local
`duckgate.toml` for a per-project override). Pass `-p`/`--path` to skip the prompt entirely —
handy in scripts: `duckgate init --path duckgate.toml`.

## Configuration

`duckgate.toml` in the current directory, falling back to `~/.duckgate/config.toml`:

```toml
[aws]
profile = "my-aws-profile"
region  = "eu-central-1"

[glue]
enabled   = true
databases = []  # empty = all databases

# [[sources]] name a piece of S3 data you want to query as `name`.
# - If it matches a Glue table name, it overrides that table (e.g. to
#   point at a different environment) without touching the catalog.
# - Without Glue, a source is just a name for wherever your data lives
#   in S3 — a bucket + prefix, no catalog needed.
[[sources]]
name   = "fis_location"
path   = "s3://my-bucket/structured/sqlserver/dev/fis/location/**/*.parquet"
format = "parquet"  # parquet | iceberg | csv
```

Set `[glue] enabled = false` to use `duckgate` purely as a named-S3-sources tool, no Glue
Data Catalog required at all — `[[sources]]` is all you need.

### Source resolution

- **Glue tables** are discovered automatically from the configured databases (skip this
  entirely with `[glue] enabled = false`). A bare Glue table location (no wildcard) is read
  as `location/**/*.<format>`.
- **Local `[[sources]]`** entries override Glue tables with the same name — use this to point
  to a specific environment prefix, or to name S3 data that isn't in Glue at all.
- If two Glue tables from different databases share a name, they are registered as
  `database__table_name`.
- `duckgate tables` lists what the catalog *discovers* (what Glue/your `[[sources]]`
  advertise), not just tables that are actually queryable. A table with no matching files or
  bad permissions will still show up in the list and only fails when you query it.
- Tables are registered lazily: a table's DuckDB view is created only the first time a query
  references it in a session (`-q` runs once per process; the shell keeps this per session,
  so a repeated reference to the same table costs nothing).

### Query safety

`-q` and the shell print a warning to stderr when a query has no `LIMIT` clause. This only
flags the risk — it doesn't rewrite the query, and it can't help an aggregate
(`COUNT(*)`, `GROUP BY`) that has to scan the whole table regardless of any `LIMIT`.

## AWS credentials

Uses the AWS profile from `[aws] profile`, resolved via `boto3.Session(profile_name=...)`.
Works with `aws sso login` and static credentials directly. Tools that only export temporary
credentials into your shell's environment (e.g. Granted `assume`) are **not** picked up,
since a named profile lookup ignores ambient environment variables — configure the profile's
`credential_process` if you want a tool like that to work transparently.

## Supported formats

| Format  | How                                                  |
| ------- | ----------------------------------------------------- |
| Parquet | `read_parquet('s3://...')` via httpfs                |
| Iceberg | `iceberg_scan('s3://...')` via the iceberg extension  |
| CSV     | `read_csv('s3://...')` via httpfs                     |

## Development

```bash
make sync    # uv sync
make test    # run the test suite
make lint    # ruff check --fix
make format  # ruff format
make check   # lint + format + test + clean (run before every commit)
```

## Release

Pushing a `v*` tag (e.g. `v0.1.0`) builds and publishes to PyPI via GitHub Actions, using
PyPI Trusted Publishing (see `.github/workflows/publish.yml`). Bump `__version__` in
`duckgate/__init__.py` to match before tagging.

## License

Apache License 2.0 — see [LICENSE](LICENSE).
