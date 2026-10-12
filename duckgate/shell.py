import re
import signal
from pathlib import Path

import click
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.history import FileHistory, InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings

from duckgate.catalog import apply_default_limit, describe_table, run_query_with_timeout

HISTORY_PATH = Path.home() / ".duckgate" / "history"

_EXIT_WORDS = ("\\q", "exit", "quit")
# the table name being typed, including an opening quote if there is one
_PREFIX = re.compile(r'"?[\w-]*$')
_PLAIN_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

_BANNER = (
    "duckgate  •  end SQL with ; to run  •  Tab completes table names  •  \\q to exit\n"
    "\\tables  •  \\d <table>  •  \\limit N / \\nolimit  •  \\timeout N / \\notimeout"
)


def run_shell(
    conn,
    catalog,
    default_limit,
    default_timeout,
    credentials=None,
    history_path=HISTORY_PATH,
):
    shell = Shell(conn, catalog, default_limit, default_timeout, credentials)
    session = PromptSession(
        history=_make_history(history_path),
        multiline=True,
        key_bindings=_key_bindings(),
        completer=TableCompleter(catalog),
        complete_while_typing=False,
        prompt_continuation="     ...> ",
    )
    click.echo(_BANNER)

    while True:
        try:
            text = session.prompt("duckgate> ")
        except KeyboardInterrupt:
            continue  # Ctrl+C discards the query being typed, like psql
        except EOFError:
            break
        if not shell.handle(text):
            break

    click.echo("Bye!")


class Shell:
    def __init__(self, conn, catalog, default_limit, default_timeout, credentials=None):
        self.conn = conn
        self.catalog = catalog
        self.limit = default_limit
        self.timeout = default_timeout
        self.credentials = credentials
        self.registered = set()

    def handle(self, text: str) -> bool:
        """Run one submitted input. Returns False when the shell should exit."""
        text = text.strip()
        # meta-commands and exit words also accept a trailing ";"
        command = text.rstrip(";").strip()
        if not command:
            return True
        if command.lower() in _EXIT_WORDS:
            return False
        if command.startswith("\\"):
            self._meta(command)
        else:
            self._query(text)
        return True

    def _meta(self, command: str) -> None:
        name, *args = command.split()
        name = name.lower()
        if name == "\\nolimit":
            self.limit = 0
            click.echo("Row limit disabled for this session.")
        elif name == "\\limit":
            if len(args) == 1 and args[0].isdigit():
                self.limit = int(args[0])
                click.echo(f"Row limit set to {self.limit}.")
            else:
                click.echo("Usage: \\limit N", err=True)
        elif name == "\\notimeout":
            self.timeout = 0
            click.echo("Query timeout disabled for this session.")
        elif name == "\\timeout":
            if len(args) == 1 and args[0].isdigit():
                self.timeout = int(args[0])
                click.echo(f"Query timeout set to {self.timeout}s.")
            else:
                click.echo("Usage: \\timeout N", err=True)
        elif name == "\\tables":
            for table in sorted(self.catalog):
                click.echo(table)
        elif name == "\\d":
            self._describe(args)
        else:
            click.echo(f"Unknown command: {command.split()[0]}", err=True)

    def _describe(self, args: list[str]) -> None:
        if len(args) != 1:
            click.echo("Usage: \\d <table>", err=True)
            return
        table = args[0].strip('"')
        if table not in self.catalog:
            click.echo(f"Table '{table}' not found. Use \\tables to list them.", err=True)
            return
        try:
            self._apply_credentials()
            df = describe_table(self.conn, self.catalog[table]).fetchdf()
            click.echo(df.to_string(index=False))
        except Exception as e:
            click.echo(f"Error: {e}", err=True)

    def _query(self, text: str) -> None:
        try:
            self._apply_credentials()
            sql = apply_default_limit(text, self.limit)
            df = _execute(self.conn, self.catalog, self.registered, sql, self.timeout)
            click.echo("(0 rows)" if df.empty else df.to_string(index=False))
        except Exception as e:
            click.echo(f"Error: {e}", err=True)

    def _apply_credentials(self) -> None:
        if self.credentials is not None:
            self.credentials.apply(self.conn)


class TableCompleter(Completer):
    def __init__(self, names):
        self.names = sorted(names)

    def get_completions(self, document, complete_event):
        prefix = _PREFIX.search(document.text_before_cursor).group()
        typed = prefix.lstrip('"').lower()
        for name in self.names:
            if name.lower().startswith(typed):
                # unquoted hyphens parse as subtraction, so insert those names quoted
                text = name if _PLAIN_IDENTIFIER.fullmatch(name) else f'"{name}"'
                yield Completion(text, start_position=-len(prefix), display=name)


def _is_complete(text: str) -> bool:
    """Whether Enter should submit the input instead of starting a new line."""
    text = text.strip()
    return not text or text.endswith(";") or text.startswith("\\") or text.lower() in _EXIT_WORDS


def _key_bindings() -> KeyBindings:
    bindings = KeyBindings()

    @bindings.add("enter")
    def _(event):
        buffer = event.current_buffer
        if buffer.complete_state:
            buffer.complete_state = None  # Enter just closes the completion menu
        elif _is_complete(buffer.text):
            buffer.validate_and_handle()
        else:
            buffer.insert_text("\n")

    return bindings


def _make_history(path: Path):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
    except OSError:
        return InMemoryHistory()
    return FileHistory(str(path))


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
