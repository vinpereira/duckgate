import click
import duckdb


def run_shell(conn: duckdb.DuckDBPyConnection) -> None:
    click.echo("Shell not yet implemented. Use -q to run a query.")
