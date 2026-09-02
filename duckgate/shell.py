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
            df = conn.execute(text).fetchdf()
            click.echo("(0 rows)" if df.empty else df.to_string(index=False))
        except Exception as e:
            click.echo(f"Error: {e}", err=True)

    click.echo("Bye!")
