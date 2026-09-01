import click


@click.group(invoke_without_command=True)
@click.option("-q", "--query", "query_str", default=None, help="Run a SQL query and exit")
@click.pass_context
def cli(ctx, query_str):
    """Interactive SQL shell over S3 data using DuckDB."""
    if ctx.invoked_subcommand is None and query_str is None:
        click.echo("Shell coming soon. Use -q to run a query.")
