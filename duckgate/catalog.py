import hashlib
import json
import os
import re
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import boto3
import click
import duckdb

from duckgate.config import Config

CACHE_DIR = Path.home() / ".duckgate" / "cache"
# bump whenever discovery would produce different TableSpecs for the same Glue tables,
# so caches written by older versions are ignored instead of served for up to a TTL
_CACHE_VERSION = 2


@dataclass
class TableSpec:
    path: str
    format: str


def discover_catalog(config: Config, refresh: bool = False) -> dict[str, TableSpec]:
    catalog: dict[str, TableSpec] = {}
    if config.glue.enabled:
        catalog.update(_cached_glue_tables(config, refresh))
    # local sources override Glue tables with the same name, and are never cached —
    # editing [[sources]] takes effect on the next run
    for source in config.sources:
        catalog[source.name] = TableSpec(path=source.path, format=source.format)
    return catalog


def glue_cache_age(config: Config) -> float | None:
    """Seconds since the cached Glue catalog was written, or None if there's no valid one."""
    entry = _read_glue_cache(config)
    return None if entry is None else time.time() - entry[0]


def _cached_glue_tables(config: Config, refresh: bool) -> dict[str, TableSpec]:
    # Glue discovery costs ~1.5-1.9s per run against a real multi-database config,
    # paid on every -q invocation — cache it per profile/region/databases
    if config.glue.cache_ttl_seconds > 0 and not refresh:
        entry = _read_glue_cache(config)
        if entry is not None:
            return entry[1]
    tables = _discover_glue(config)
    if config.glue.cache_ttl_seconds > 0:
        _write_glue_cache(config, tables)
    return tables


def _glue_cache_path(config: Config) -> Path:
    key = json.dumps(
        [_CACHE_VERSION, config.aws.profile, config.aws.region, sorted(config.glue.databases)]
    )
    return CACHE_DIR / f"glue-{hashlib.sha256(key.encode()).hexdigest()[:16]}.json"


def _read_glue_cache(config: Config) -> tuple[float, dict[str, TableSpec]] | None:
    try:
        data = json.loads(_glue_cache_path(config).read_text())
        created_at = float(data["created_at"])
        tables = {name: TableSpec(**spec) for name, spec in data["tables"].items()}
    except (OSError, ValueError, KeyError, TypeError):
        return None  # missing or unreadable cache is just a miss
    if not 0 <= time.time() - created_at <= config.glue.cache_ttl_seconds:
        return None
    return created_at, tables


def _write_glue_cache(config: Config, tables: dict[str, TableSpec]) -> None:
    path = _glue_cache_path(config)
    data = {"created_at": time.time(), "tables": {n: asdict(t) for n, t in tables.items()}}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        os.replace(tmp, path)  # atomic, so a concurrent run never reads a half-written file
    except OSError:
        pass  # caching is best-effort; discovery already succeeded


def _discover_glue(config: Config) -> dict[str, TableSpec]:
    tables: dict[str, TableSpec] = {}
    session = boto3.Session(profile_name=config.aws.profile)
    glue = session.client("glue", region_name=config.aws.region)

    dbs = _list_databases(glue, config.glue.databases)
    rows = []  # (db, name, location, fmt)
    name_dbs: dict[str, list[str]] = {}

    for db in dbs:
        pager = glue.get_paginator("get_tables")
        for page in pager.paginate(DatabaseName=db):
            for t in page["TableList"]:
                fmt = _detect_format(t)
                loc = t.get("StorageDescriptor", {}).get("Location", "")
                if fmt == "iceberg":
                    # the exact current snapshot, no version-hint.text or guessing needed
                    loc = t.get("Parameters", {}).get("metadata_location") or loc
                rows.append((db, t["Name"], loc, fmt))
                name_dbs.setdefault(t["Name"], []).append(db)

    for db, name, loc, fmt in rows:
        if not loc:
            continue
        view_name = f"{db}__{name}" if len(name_dbs[name]) > 1 else name
        tables[view_name] = TableSpec(path=loc, format=fmt)
    return tables


def ensure_registered(
    conn: duckdb.DuckDBPyConnection,
    catalog: dict[str, TableSpec],
    sql: str,
    registered: set[str],
) -> None:
    for name, spec in catalog.items():
        if name in registered:
            continue
        if re.search(rf"\b{re.escape(name)}\b", sql):
            _try_register(conn, name, spec.path, spec.format)
            registered.add(name)


def run_query(
    conn: duckdb.DuckDBPyConnection,
    catalog: dict[str, TableSpec],
    sql: str,
    registered: set[str],
) -> duckdb.DuckDBPyRelation:
    ensure_registered(conn, catalog, sql, registered)
    for _ in range(len(catalog) + 1):
        try:
            return conn.execute(sql)
        except duckdb.CatalogException as e:
            match = re.search(r"Table with name (\S+) does not exist", str(e))
            missing = match.group(1) if match else None
            if missing is None or missing not in catalog or missing in registered:
                raise
            spec = catalog[missing]
            _try_register(conn, missing, spec.path, spec.format)
            registered.add(missing)
    raise RuntimeError("could not resolve all tables referenced by the query")


class QueryTimeoutError(Exception):
    pass


def run_query_with_timeout(
    conn: duckdb.DuckDBPyConnection,
    catalog: dict[str, TableSpec],
    sql: str,
    registered: set[str],
    timeout_seconds: int,
) -> duckdb.DuckDBPyRelation:
    if timeout_seconds <= 0:
        return run_query(conn, catalog, sql, registered)
    timer = threading.Timer(timeout_seconds, conn.interrupt)
    timer.start()
    try:
        return run_query(conn, catalog, sql, registered)
    except duckdb.InterruptException as e:
        raise QueryTimeoutError(f"query canceled: exceeded {timeout_seconds}s timeout") from e
    finally:
        timer.cancel()


# "-- line" and "/* block */" comments (plus whitespace) before a statement's first keyword
_LEADING_COMMENTS = re.compile(r"\A(?:\s+|--[^\n]*(?:\n|\Z)|/\*.*?\*/)*", re.DOTALL)


def run_script(
    conn: duckdb.DuckDBPyConnection,
    catalog: dict[str, TableSpec],
    sql: str,
    registered: set[str],
    timeout_seconds: int,
    default_limit: int,
) -> duckdb.DuckDBPyRelation:
    """Run every statement in sql and return the last one's result.

    Earlier statements (SET, CREATE TEMP TABLE, COPY, ...) run as-is; the default row
    limit only applies to the last one, whose result is what gets printed. The timeout
    applies to each statement separately.
    """
    statements = [s.query for s in duckdb.extract_statements(sql)]
    if not statements:
        raise ValueError("no SQL statement to run")
    for statement in statements[:-1]:
        run_query_with_timeout(conn, catalog, statement, registered, timeout_seconds)
    last = apply_default_limit(statements[-1], default_limit)
    return run_query_with_timeout(conn, catalog, last, registered, timeout_seconds)


def apply_default_limit(sql: str, default_limit: int) -> str:
    if re.search(r"\blimit\b", sql, re.IGNORECASE):
        return sql
    if default_limit <= 0:
        click.echo("Warning: no LIMIT — this query may scan a lot of data.", err=True)
        return sql
    stripped = sql.strip().rstrip(";").strip()
    if not re.match(r"(?is)^(select|with)\b", _LEADING_COMMENTS.sub("", stripped)):
        click.echo("Warning: no LIMIT — this query may scan a lot of data.", err=True)
        return sql
    click.echo(
        f"Note: no LIMIT — capping output at {default_limit} rows "
        "(doesn't reduce scan time for aggregates). Use --limit 0 to disable.",
        err=True,
    )
    # the newline before ")" keeps a trailing "-- comment" from swallowing the wrapper
    return f"SELECT * FROM ({stripped}\n) LIMIT {default_limit}"


def _try_register(conn: duckdb.DuckDBPyConnection, name: str, path: str, fmt: str) -> bool:
    try:
        conn.execute(_make_view_sql(name, path, fmt))
    except duckdb.Error as e:
        click.echo(f"\nWarning: skipping table '{name}': {e}", err=True)
        return False
    return True


def _source_expr(path: str, format: str) -> str:
    if format == "iceberg":
        return f"iceberg_scan('{path}')"
    path = _with_glob(path, format)
    if format == "csv":
        return f"read_csv('{path}')"
    if format == "json":
        return f"read_json('{path}')"
    return f"read_parquet('{path}')"


def _make_view_sql(name: str, path: str, format: str) -> str:
    # Glue database/table names often contain hyphens, which DuckDB's unquoted
    # identifier syntax parses as the subtraction operator — always quote.
    name = '"' + name.replace('"', '""') + '"'
    return f"CREATE OR REPLACE VIEW {name} AS SELECT * FROM {_source_expr(path, format)}"


def describe_table(conn: duckdb.DuckDBPyConnection, spec: TableSpec) -> duckdb.DuckDBPyRelation:
    return conn.execute(f"DESCRIBE SELECT * FROM {_source_expr(spec.path, spec.format)}")


_GLOB_EXTENSIONS = {"parquet": "parquet", "csv": "csv", "json": "json*"}


def _with_glob(path: str, format: str) -> str:
    # Glue table locations are usually a bare folder prefix (no wildcard) —
    # read_parquet/read_csv/read_json won't expand that on their own, so add
    # one. JSON needs "json*" (not "json") to also match gzipped files like
    # CloudTrail's "*.json.gz".
    if "*" in path:
        return path
    ext = _GLOB_EXTENSIONS.get(format, format)
    return f"{path.rstrip('/')}/**/*.{ext}"


def _detect_format(table: dict) -> str:
    # Glue reports Iceberg tables as TableType EXTERNAL_TABLE with Parameters.table_type
    # ICEBERG; misreading them as Parquet globs every data file ever written, including
    # ones no longer in the current snapshot — stale and duplicated rows
    if table.get("TableType") == "ICEBERG":
        return "iceberg"
    if table.get("Parameters", {}).get("table_type", "").upper() == "ICEBERG":
        return "iceberg"
    storage = table.get("StorageDescriptor", {})
    serde_lib = storage.get("SerdeInfo", {}).get("SerializationLibrary", "")
    if "json" in serde_lib.lower():
        return "json"
    input_format = storage.get("InputFormat", "")
    if "csv" in input_format.lower() and "parquet" not in input_format.lower():
        return "csv"
    return "parquet"


def _list_databases(glue, databases):
    if databases:
        return databases
    pager = glue.get_paginator("get_databases")
    return [db["Name"] for page in pager.paginate() for db in page["DatabaseList"]]
