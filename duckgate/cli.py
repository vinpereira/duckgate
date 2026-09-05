from pathlib import Path

import click

from duckgate.catalog import describe_table, discover_catalog, run_query
from duckgate.config import find_config, load_config
from duckgate.engine import create_connection


def _build_conn(config):
    conn = create_connection(config)
    catalog = discover_catalog(config)
    return conn, catalog


@click.group(invoke_without_command=True)
@click.option("-q", "--query", "query_str", default=None, help="Run a SQL query and exit")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "csv", "json"]),
    default="table",
    help="Output format for -q (default: table)",
)
@click.pass_context
def cli(ctx, query_str, output_format):
    """Interactive SQL shell over S3 data using DuckDB."""
    if ctx.invoked_subcommand is not None:
        return
    try:
        config = load_config(find_config())
    except FileNotFoundError as e:
        click.echo(str(e), err=True)
        raise SystemExit(1) from e

    conn, catalog = _build_conn(config)

    if query_str:
        try:
            df = run_query(conn, catalog, query_str, set()).fetchdf()
            if output_format == "csv":
                click.echo(df.to_csv(index=False), nl=False)
            elif output_format == "json":
                click.echo(df.to_json(orient="records", indent=2))
            else:
                click.echo(df.to_string(index=False))
        except Exception as e:
            click.echo(f"Error: {e}", err=True)
            raise SystemExit(1) from e
    else:
        from duckgate.shell import run_shell

        run_shell(conn, catalog)


@cli.command()
def tables():
    """List available tables."""
    try:
        config = load_config(find_config())
    except FileNotFoundError as e:
        click.echo(str(e), err=True)
        raise SystemExit(1) from e

    catalog = discover_catalog(config)
    for name in sorted(catalog):
        click.echo(name)


@cli.command()
@click.argument("name")
def describe(name):
    """Show a table's schema without registering it."""
    try:
        config = load_config(find_config())
    except FileNotFoundError as e:
        click.echo(str(e), err=True)
        raise SystemExit(1) from e

    catalog = discover_catalog(config)
    if name not in catalog:
        click.echo(
            f"Table '{name}' not found. Run `duckgate tables` to see what's available.",
            err=True,
        )
        raise SystemExit(1)

    conn = create_connection(config)
    try:
        df = describe_table(conn, catalog[name]).fetchdf()
        click.echo(df.to_string(index=False))
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1) from e


@cli.command("init")
@click.option(
    "-p",
    "--path",
    "path_str",
    default=None,
    help="Config file path (skips the interactive prompt)",
)
def init_cmd(path_str):
    """Create a duckgate.toml config template."""
    if path_str:
        path = Path(path_str)
    else:
        default = Path.home() / ".duckgate" / "config.toml"
        path = Path(click.prompt("Config file path", default=str(default)))

    if path.exists():
        click.echo(f"{path} already exists", err=True)
        raise SystemExit(1)

    path.parent.mkdir(parents=True, exist_ok=True)
    template = (
        "[aws]\n"
        'profile = "my-aws-profile"\n'
        'region  = "eu-central-1"\n'
        "\n"
        "[glue]\n"
        "enabled   = true\n"
        "databases = []  # empty = all databases\n"
        "\n"
        "# [[sources]] name a piece of S3 data you want to query as `name`.\n"
        "# - If it matches a Glue table name, it overrides that table (e.g. to\n"
        "#   point at a different environment) without touching the catalog.\n"
        "# - Without Glue, a source is just a name for wherever your data lives\n"
        "#   in S3 — a bucket + prefix, no catalog needed.\n"
        "# [[sources]]\n"
        '# name   = "my_table"\n'
        '# path   = "s3://my-bucket/prefix/**/*.parquet"\n'
        '# format = "parquet"  # parquet | iceberg | csv\n'
    )
    path.write_text(template)
    click.echo(f"Created {path}")
