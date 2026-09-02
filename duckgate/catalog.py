import duckdb

from duckgate.config import TableConfig


def register_local_tables(
    conn: duckdb.DuckDBPyConnection,
    tables: list[TableConfig],
) -> list[str]:
    registered: list[str] = []
    for table in tables:
        conn.execute(_make_view_sql(table.name, table.path, table.format))
        registered.append(table.name)
    return registered


def _make_view_sql(name: str, path: str, format: str) -> str:
    if format == "iceberg":
        return f"CREATE OR REPLACE VIEW {name} AS SELECT * FROM iceberg_scan('{path}')"
    if format == "csv":
        return f"CREATE OR REPLACE VIEW {name} AS SELECT * FROM read_csv('{path}')"
    return f"CREATE OR REPLACE VIEW {name} AS SELECT * FROM read_parquet('{path}')"


def _detect_format(table: dict) -> str:
    if table.get("TableType") == "ICEBERG":
        return "iceberg"
    input_format = table.get("StorageDescriptor", {}).get("InputFormat", "")
    if "csv" in input_format.lower() and "parquet" not in input_format.lower():
        return "csv"
    return "parquet"
