from unittest.mock import MagicMock

import duckdb
import pytest
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document
from prompt_toolkit.history import FileHistory, InMemoryHistory
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from duckgate.catalog import TableSpec
from duckgate.engine import S3Credentials
from duckgate.shell import Shell, TableCompleter, _is_complete, _key_bindings, _make_history


@pytest.fixture
def catalog(tmp_path):
    data = tmp_path / "people"
    data.mkdir()
    duckdb.sql(
        "COPY (SELECT * FROM (VALUES (1, 'a'), (2, 'b'), (3, 'c')) t(id, name)) "
        f"TO '{data / 'part.parquet'}' (FORMAT PARQUET)"
    )
    path = str(data / "*.parquet")
    return {
        "people": TableSpec(path=path, format="parquet"),
        "my-db__people": TableSpec(path=path, format="parquet"),
    }


@pytest.fixture
def shell(catalog):
    conn = duckdb.connect(":memory:")
    yield Shell(conn, catalog, default_limit=100, default_timeout=0)
    conn.close()


# --- query execution ---


def test_query_prints_rows(shell, capsys):
    assert shell.handle("SELECT name FROM people ORDER BY id;") is True
    out = capsys.readouterr().out
    assert "a" in out and "c" in out


def test_multiline_query_with_trailing_comment(shell, capsys):
    assert shell.handle("SELECT count(*) AS n\nFROM people\n-- all rows\n;") is True
    captured = capsys.readouterr()
    assert "Error" not in captured.err
    assert "3" in captured.out


def test_multiple_statements_in_one_input(shell, capsys):
    shell.handle("CREATE TEMP TABLE t AS SELECT 7 AS seven; SELECT * FROM t;")
    captured = capsys.readouterr()
    assert "Error" not in captured.err
    assert "seven" in captured.out


def test_query_against_hyphenated_table(shell, capsys):
    shell.handle('SELECT count(*) AS n FROM "my-db__people";')
    assert "3" in capsys.readouterr().out


def test_query_error_is_printed_and_shell_continues(shell, capsys):
    assert shell.handle("SELECT * FROM nope;") is True
    assert "Error:" in capsys.readouterr().err


def test_empty_result(shell, capsys):
    shell.handle("SELECT * FROM people WHERE id > 99;")
    assert "(0 rows)" in capsys.readouterr().out


def test_lone_semicolon_is_ignored(shell, capsys):
    assert shell.handle(";") is True
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


def test_credentials_applied_before_query(catalog, capsys):
    conn = duckdb.connect(":memory:")
    credentials = MagicMock(spec=S3Credentials)
    shell = Shell(conn, catalog, default_limit=100, default_timeout=0, credentials=credentials)
    shell.handle("SELECT 1;")
    credentials.apply.assert_called_once_with(conn)
    conn.close()


def test_credential_refresh_failure_is_printed(catalog, capsys):
    conn = duckdb.connect(":memory:")
    credentials = MagicMock(spec=S3Credentials)
    credentials.apply.side_effect = RuntimeError("Token has expired")
    shell = Shell(conn, catalog, default_limit=100, default_timeout=0, credentials=credentials)
    assert shell.handle("SELECT 1;") is True
    assert "Error: Token has expired" in capsys.readouterr().err
    conn.close()


# --- exit ---


@pytest.mark.parametrize("text", ["\\q", "exit", "QUIT", "exit;", "\\q;"])
def test_exit_words(shell, text):
    assert shell.handle(text) is False


# --- meta-commands ---


def test_limit_and_nolimit(shell, capsys):
    shell.handle("\\limit 2")
    assert shell.limit == 2
    capsys.readouterr()
    shell.handle("SELECT * FROM people;")
    assert capsys.readouterr().out.count("\n") == 3  # header + 2 rows
    shell.handle("\\nolimit;")
    assert shell.limit == 0


def test_limit_usage(shell, capsys):
    shell.handle("\\limit abc")
    assert "Usage: \\limit N" in capsys.readouterr().err


def test_timeout_and_notimeout(shell, capsys):
    shell.handle("\\timeout 30")
    assert shell.timeout == 30
    shell.handle("\\notimeout")
    assert shell.timeout == 0


def test_tables_lists_catalog_sorted(shell, capsys):
    shell.handle("\\tables")
    assert capsys.readouterr().out.splitlines() == ["my-db__people", "people"]


def test_describe_table(shell, capsys):
    shell.handle("\\d people")
    out = capsys.readouterr().out
    assert "id" in out and "name" in out


def test_describe_accepts_quoted_name_and_semicolon(shell, capsys):
    shell.handle('\\d "my-db__people";')
    assert "name" in capsys.readouterr().out


def test_describe_unknown_table(shell, capsys):
    shell.handle("\\d nope")
    assert "Table 'nope' not found" in capsys.readouterr().err


def test_describe_usage(shell, capsys):
    shell.handle("\\d")
    assert "Usage: \\d <table>" in capsys.readouterr().err


def test_describe_applies_credentials(catalog):
    conn = duckdb.connect(":memory:")
    credentials = MagicMock(spec=S3Credentials)
    shell = Shell(conn, catalog, default_limit=100, default_timeout=0, credentials=credentials)
    shell.handle("\\d people")
    credentials.apply.assert_called_once_with(conn)
    conn.close()


def test_unknown_meta_command(shell, capsys):
    assert shell.handle("\\wat") is True
    assert "Unknown command: \\wat" in capsys.readouterr().err


# --- multi-line submission ---


@pytest.mark.parametrize(
    "text, complete",
    [
        ("", True),
        ("   ", True),
        ("SELECT 1;", True),
        ("SELECT 1;  ", True),
        ("SELECT 1\nFROM t;", True),
        ("\\tables", True),
        ("\\d people", True),
        ("exit", True),
        ("quit", True),
        ("SELECT 1", False),
        ("SELECT 1\nFROM t", False),
    ],
)
def test_is_complete(text, complete):
    assert _is_complete(text) is complete


# --- completion ---


def _complete(names, text):
    completer = TableCompleter(names)
    event = CompleteEvent(completion_requested=True)
    return [(c.text, c.start_position) for c in completer.get_completions(Document(text), event)]


def test_completes_table_prefix():
    assert _complete(["people", "places"], "SELECT * FROM peo") == [("people", -3)]


def test_completion_is_case_insensitive():
    assert _complete(["people"], "SELECT * FROM PEO") == [("people", -3)]


def test_completion_quotes_hyphenated_names():
    assert _complete(["my-db__people"], "FROM my-d") == [('"my-db__people"', -4)]


def test_completion_replaces_an_opening_quote():
    assert _complete(["my-db__people"], 'FROM "my') == [('"my-db__people"', -3)]


def test_empty_prefix_lists_all_tables():
    assert [t for t, _ in _complete(["b", "a"], "FROM ")] == ["a", "b"]


# --- history ---


def test_history_file_created_under_missing_dir(tmp_path):
    path = tmp_path / "nested" / "history"
    history = _make_history(path)
    assert isinstance(history, FileHistory)
    assert path.parent.is_dir()


def test_history_falls_back_to_memory_when_unwritable(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    history = _make_history(blocker / "history")
    assert isinstance(history, InMemoryHistory)


# --- the real prompt, driven through a pipe ---


def _prompt(keys):
    with create_pipe_input() as pipe:
        pipe.send_text(keys)
        session = PromptSession(
            input=pipe,
            output=DummyOutput(),
            multiline=True,
            key_bindings=_key_bindings(),
        )
        return session.prompt("duckgate> ")


def test_enter_without_semicolon_continues_the_query():
    assert _prompt("SELECT 1\r;\r") == "SELECT 1\n;"


def test_enter_submits_meta_command_immediately():
    assert _prompt("\\tables\r") == "\\tables"
