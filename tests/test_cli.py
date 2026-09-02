from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

import duckgate
from duckgate.cli import cli


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def config_toml(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    toml = tmp_path / "duckgate.toml"
    toml.write_text('[aws]\nprofile = "test"\nregion = "eu-central-1"\n[glue]\nenabled = false\n')
    return tmp_path


def _mock_session_no_glue():
    creds = MagicMock()
    creds.access_key = "AKIATEST"
    creds.secret_key = "secret"
    creds.token = None
    session = MagicMock()
    session.get_credentials.return_value.resolve.return_value = creds
    return session


# All fixtures disable Glue, so only engine.py's boto3.Session needs patching.
ENGINE_SESSION = "duckgate.engine.boto3.Session"


def test_cli_help(runner):
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "SQL" in result.output


def test_cli_version_constant():
    assert duckgate.__version__ == "0.1.0"


def test_tables_command_no_config(runner, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli, ["tables"])
    assert result.exit_code != 0
    assert "duckgate init" in result.output


def test_tables_command_empty(runner, config_toml):
    mock = _mock_session_no_glue()
    with patch(ENGINE_SESSION, return_value=mock):
        result = runner.invoke(cli, ["tables"])
    assert result.exit_code == 0


def test_init_command_creates_file(runner, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli, ["init"])
    assert result.exit_code == 0
    assert (tmp_path / "duckgate.toml").exists()
    content = (tmp_path / "duckgate.toml").read_text()
    assert "[aws]" in content
    assert "profile" in content


def test_init_command_refuses_overwrite(runner, tmp_path, monkeypatch):
    (tmp_path / "duckgate.toml").write_text("[aws]\nprofile='x'\nregion='y'\n")
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli, ["init"])
    assert result.exit_code != 0


def test_one_shot_query_table_format(runner, config_toml):
    mock = _mock_session_no_glue()
    with patch(ENGINE_SESSION, return_value=mock):
        result = runner.invoke(cli, ["-q", "SELECT 42 AS answer"])
    assert result.exit_code == 0
    assert "42" in result.output


def test_one_shot_query_csv_format(runner, config_toml):
    mock = _mock_session_no_glue()
    with patch(ENGINE_SESSION, return_value=mock):
        result = runner.invoke(cli, ["-q", "SELECT 42 AS answer", "--format", "csv"])
    assert result.exit_code == 0
    assert "answer" in result.output
    assert "42" in result.output


def test_one_shot_query_json_format(runner, config_toml):
    mock = _mock_session_no_glue()
    with patch(ENGINE_SESSION, return_value=mock):
        result = runner.invoke(cli, ["-q", "SELECT 42 AS answer", "--format", "json"])
    assert result.exit_code == 0
    assert "42" in result.output
