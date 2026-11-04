# Roadmap

What's being considered for `duckgate`, not a commitment or a schedule. Shipped work is in
[CHANGELOG.md](CHANGELOG.md). Ideas and feedback are welcome as GitHub issues.

| Status | Meaning |
|---|---|
| **idea** | worth having, not yet evaluated or scoped |
| **on demand** | built only once there's a real, recurring need for it |

## Data sources

- **Glue CSV tables: column names from the catalog** — **idea**. Headerless CSV tables may
  come back with generic column names (`column0`, `column1`, ...) instead of the names
  declared in Glue. Unverified — reports with a real Glue CSV table are welcome.
- **Delta Lake time travel** — **idea**. Query an earlier version of a Delta table (DuckDB
  2.0's `delta` extension supports it). Reading only — writing to Delta stays out of scope.

## Performance

- **Route expensive queries to Athena automatically** — **on demand**. Much less needed since
  DuckDB 2.0: its asynchronous I/O made the many-small-files case that motivated this ~6x
  faster, and queries that still time out or run long already get an Athena hint. Would also
  need a reliable way to tell a slow query apart *before* running it, and opt-in, since Athena
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

## Maintenance

- **Raise the minimum DuckDB version** — **on demand**. `duckgate` accepts DuckDB 1.3.0+ and
  CI tests that floor. DuckDB 1.4 reaches end of support on 2026-11-17 and 1.5 in March 2027,
  while 2.0 is supported until at least October 2027. Likely move: require 2.0 once 1.5 is out
  of support, or earlier if keeping 1.x working starts to cost real effort.

## Out of scope

- Dedicated write/export features. Plain `COPY (query) TO 's3://...'` already works.
- Sources outside S3 (local files, RDS, DynamoDB, CloudWatch Logs' live API).
- A long-running server/daemon process.
- Turning `duckgate` into a pipeline/ETL tool.
