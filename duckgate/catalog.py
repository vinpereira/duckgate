from dataclasses import dataclass

import boto3
import click
import duckdb

from duckgate.config import Config, TableConfig


@dataclass
class TableSpec:
    path: str
    format: str


def discover_catalog(config: Config) -> dict[str, TableSpec]:
    catalog: dict[str, TableSpec] = {}
    for table in config.tables:
        catalog[table.name] = TableSpec(path=table.path, format=table.format)
    if config.glue.enabled:
        _discover_glue(config, catalog)
    return catalog


def _discover_glue(config: Config, catalog: dict[str, TableSpec]) -> None:
    session = boto3.Session(profile_name=config.aws.profile)
    glue = session.client("glue", region_name=config.aws.region)

    dbs = _list_databases(glue, config.glue.databases)
    rows = []  # (db, name, location, fmt)
    name_dbs: dict[str, list[str]] = {}

    for db in dbs:
        pager = glue.get_paginator("get_tables")
        for page in pager.paginate(DatabaseName=db):
            for t in page["TableList"]:
                loc = t.get("StorageDescriptor", {}).get("Location", "")
                rows.append((db, t["Name"], loc, _detect_format(t)))
                name_dbs.setdefault(t["Name"], []).append(db)

    for db, name, loc, fmt in rows:
        if not loc:
            continue
        view_name = f"{db}__{name}" if len(name_dbs[name]) > 1 else name
        if view_name in catalog:
            continue
        catalog[view_name] = TableSpec(path=loc, format=fmt)


def ensure_registered(
    conn: duckdb.DuckDBPyConnection,
    catalog: dict[str, TableSpec],
    sql: str,
    registered: set[str],
) -> None:
    for name, spec in catalog.items():
        if name in registered:
            continue
        if name in sql or f'"{name}"' in sql:
            _try_register(conn, name, spec.path, spec.format)
            registered.add(name)


def register_local_tables(
    conn: duckdb.DuckDBPyConnection,
    tables: list[TableConfig],
) -> list[str]:
    registered: list[str] = []
    total = len(tables)
    for i, table in enumerate(tables, start=1):
        _report_progress(i, total)
        if not _try_register(conn, table.name, table.path, table.format):
            continue
        registered.append(table.name)
    if total:
        click.echo(err=True)
    return registered


def _report_progress(i: int, total: int) -> None:
    click.echo(f"\rRegistering tables... {i}/{total}", nl=False, err=True)


def _try_register(conn: duckdb.DuckDBPyConnection, name: str, path: str, fmt: str) -> bool:
    try:
        conn.execute(_make_view_sql(name, path, fmt))
    except duckdb.Error as e:
        click.echo(f"\nWarning: skipping table '{name}': {e}", err=True)
        return False
    return True


def _make_view_sql(name: str, path: str, format: str) -> str:
    # Glue database/table names often contain hyphens, which DuckDB's unquoted
    # identifier syntax parses as the subtraction operator — always quote.
    name = '"' + name.replace('"', '""') + '"'
    if format == "iceberg":
        return f"CREATE OR REPLACE VIEW {name} AS SELECT * FROM iceberg_scan('{path}')"
    path = _with_glob(path, format)
    if format == "csv":
        return f"CREATE OR REPLACE VIEW {name} AS SELECT * FROM read_csv('{path}')"
    return f"CREATE OR REPLACE VIEW {name} AS SELECT * FROM read_parquet('{path}')"


def _with_glob(path: str, format: str) -> str:
    # Glue table locations are usually a bare folder prefix (no wildcard) —
    # read_parquet/read_csv won't expand that on their own, so add one.
    if "*" in path:
        return path
    return f"{path.rstrip('/')}/**/*.{format}"


def _detect_format(table: dict) -> str:
    if table.get("TableType") == "ICEBERG":
        return "iceberg"
    input_format = table.get("StorageDescriptor", {}).get("InputFormat", "")
    if "csv" in input_format.lower() and "parquet" not in input_format.lower():
        return "csv"
    return "parquet"


def register_glue_tables(
    conn: duckdb.DuckDBPyConnection,
    config: Config,
    already_registered: list[str],
) -> list[str]:
    session = boto3.Session(profile_name=config.aws.profile)
    glue = session.client("glue", region_name=config.aws.region)

    dbs = _list_databases(glue, config.glue.databases)
    rows = []  # (db, name, location, fmt)
    name_dbs: dict[str, list[str]] = {}  # name -> [db, ...] — used to detect collisions

    for db in dbs:
        pager = glue.get_paginator("get_tables")
        for page in pager.paginate(DatabaseName=db):
            for t in page["TableList"]:
                loc = t.get("StorageDescriptor", {}).get("Location", "")
                rows.append((db, t["Name"], loc, _detect_format(t)))
                name_dbs.setdefault(t["Name"], []).append(db)

    registered = []
    total = len(rows)
    for i, (db, name, loc, fmt) in enumerate(rows, start=1):
        _report_progress(i, total)
        if not loc:
            continue
        view_name = f"{db}__{name}" if len(name_dbs[name]) > 1 else name
        if view_name in already_registered:
            continue
        if not _try_register(conn, view_name, loc, fmt):
            continue
        registered.append(view_name)

    if total:
        click.echo(err=True)
    return registered


def _list_databases(glue, databases):
    if databases:
        return databases
    pager = glue.get_paginator("get_databases")
    return [db["Name"] for page in pager.paginate() for db in page["DatabaseList"]]
