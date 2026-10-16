# Roadmap

What's being considered for `duckgate`, not a commitment or a schedule. Shipped work is in
[CHANGELOG.md](CHANGELOG.md). Ideas and feedback are welcome as GitHub issues.

| Status | Meaning |
|---|---|
| **next** | scope is clear; likely one of the next releases |
| **idea** | worth having, not yet evaluated or scoped |
| **on demand** | built only once there's a real, recurring need for it |
| **blocked** | wanted, but an open problem prevents it |

## Data sources

- **Delta Lake tables** — **next**. Query Delta tables (`[[sources]] format = "delta"` and
  Delta tables in Glue) through DuckDB's `delta` extension, like Iceberg today.
- **Glue CSV tables: column names from the catalog** — **idea**. Headerless CSV tables may
  come back with generic column names (`column0`, `column1`, ...) instead of the names
  declared in Glue. Unverified — reports with a real Glue CSV table are welcome.

## Performance

- **Suggest Athena for queries DuckDB handles badly** — **next** (suggestion only). When a
  query hits the timeout or runs long, print a hint to run it on Athena instead. Typical case:
  aggregates over Iceberg tables made of many small files, where every file costs a separate
  S3 request — a table layout problem (fixed by compaction), not something DuckDB can avoid.
- **Route expensive queries to Athena automatically** — **blocked**: there's no reliable way
  yet to tell a slow query apart *before* running it. Would also need opt-in, since Athena
  bills per data scanned and `duckgate` runs entirely locally today.

## Interface

- **`duckgate ui`** — **idea**. Open DuckDB's local web UI with S3 credentials and the
  catalog already wired in. Open question: tables are registered lazily today, so the UI
  would start out empty.

## Integrations

- **dbt sources** — **on demand**. Generate a dbt `sources.yml` from the discovered catalog,
  with each table's S3 location already wrapped in the right reader.
- **dbt profile** — **on demand**, after dbt sources. Generate a dbt DuckDB target from the
  `[aws]` config.

## Out of scope

- Dedicated write/export features. Plain `COPY (query) TO 's3://...'` already works.
- Sources outside S3 (local files, RDS, DynamoDB, CloudWatch Logs' live API).
- A long-running server/daemon process.
- Turning `duckgate` into a pipeline/ETL tool.
