import signal

import click
from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory


def run_shell(conn):
    session = PromptSession(history=InMemoryHistory())
    click.echo("duckgate  •  type SQL and Enter to run  •  \\q to exit")

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

        try:
            df = _execute(conn, text)
            click.echo("(0 rows)" if df.empty else df.to_string(index=False))
        except Exception as e:
            click.echo(f"Error: {e}", err=True)

    click.echo("Bye!")


def _execute(conn, text):
    # prompt_toolkit's terminal handling can leave Ctrl+C unable to reach
    # DuckDB's own interrupt handling — wire it up explicitly for the
    # duration of the query so a long-running SELECT can actually be cancelled
    previous = signal.signal(signal.SIGINT, lambda *_: conn.interrupt())
    try:
        return conn.execute(text).fetchdf()
    finally:
        signal.signal(signal.SIGINT, previous)
