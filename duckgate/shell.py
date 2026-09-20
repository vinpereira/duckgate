import signal

import click
from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory

from duckgate.catalog import apply_default_limit, run_query_with_timeout


def run_shell(conn, catalog, default_limit, default_timeout):
    session = PromptSession(history=InMemoryHistory())
    registered = set()
    limit = default_limit
    timeout = default_timeout
    click.echo(
        "duckgate  •  type SQL and Enter to run  •  \\q to exit  •  "
        "\\limit N / \\nolimit  •  \\timeout N / \\notimeout"
    )

    while True:
        try:
            text = session.prompt("duckgate> ")
        except (EOFError, KeyboardInterrupt):
            break

        text = text.strip()
        if not text:
            continue
        if text.lower() in ("\\q", "exit", "quit"):
            break
        if text.lower() == "\\nolimit":
            limit = 0
            click.echo("Row limit disabled for this session.")
            continue
        if text.lower().startswith("\\limit"):
            parts = text.split()
            if len(parts) == 2 and parts[1].isdigit():
                limit = int(parts[1])
                click.echo(f"Row limit set to {limit}.")
            else:
                click.echo("Usage: \\limit N", err=True)
            continue
        if text.lower() == "\\notimeout":
            timeout = 0
            click.echo("Query timeout disabled for this session.")
            continue
        if text.lower().startswith("\\timeout"):
            parts = text.split()
            if len(parts) == 2 and parts[1].isdigit():
                timeout = int(parts[1])
                click.echo(f"Query timeout set to {timeout}s.")
            else:
                click.echo("Usage: \\timeout N", err=True)
            continue

        try:
            sql = apply_default_limit(text, limit)
            df = _execute(conn, catalog, registered, sql, timeout)
            click.echo("(0 rows)" if df.empty else df.to_string(index=False))
        except Exception as e:
            click.echo(f"Error: {e}", err=True)

    click.echo("Bye!")


def _execute(conn, catalog, registered, text, timeout):
    # prompt_toolkit's terminal handling can leave Ctrl+C unable to reach
    # DuckDB's own interrupt handling — wire it up explicitly for the
    # duration of the query so a long-running SELECT can actually be cancelled.
    # The timeout timer (inside run_query_with_timeout) calls the same
    # conn.interrupt(), just from a different trigger — whichever fires first wins,
    # the other is a harmless no-op.
    previous = signal.signal(signal.SIGINT, lambda *_: conn.interrupt())
    try:
        return run_query_with_timeout(conn, catalog, text, registered, timeout).fetchdf()
    finally:
        signal.signal(signal.SIGINT, previous)
