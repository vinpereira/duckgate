from unittest.mock import MagicMock, patch

import pytest
from botocore.credentials import Credentials, ReadOnlyCredentials
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
    creds = MagicMock(spec=Credentials)
    creds.get_frozen_credentials.return_value = ReadOnlyCredentials(
        access_key="AKIATEST", secret_key="secret", token=None, account_id=None
    )
    session = MagicMock()
    session.get_credentials.return_value = creds
    return session


# All fixtures disable Glue, so only engine.py's boto3.Session needs patching.
ENGINE_SESSION = "duckgate.engine.boto3.Session"


def test_cli_help(runner):
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "SQL" in result.output


def test_cli_version_constant():
    assert duckgate.__version__ == "0.4.0"


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


def test_tables_command_does_not_create_duckdb_connection(runner, config_toml):
    with patch("duckgate.cli.create_connection", side_effect=AssertionError("should not run")):
        result = runner.invoke(cli, ["tables"])
    assert result.exit_code == 0


def test_describe_command_unknown_table(runner, config_toml):
    result = runner.invoke(cli, ["describe", "does_not_exist"])
    assert result.exit_code != 0
    assert "does_not_exist" in result.output
    assert "duckgate tables" in result.output


def test_init_command_creates_file(runner, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli, ["init", "--path", "duckgate.toml"])
    assert result.exit_code == 0
    assert (tmp_path / "duckgate.toml").exists()
    content = (tmp_path / "duckgate.toml").read_text()
    assert "[aws]" in content
    assert "profile" in content


def test_init_command_refuses_overwrite(runner, tmp_path, monkeypatch):
    (tmp_path / "duckgate.toml").write_text("[aws]\nprofile='x'\nregion='y'\n")
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli, ["init", "--path", "duckgate.toml"])
    assert result.exit_code != 0


def test_init_command_prompts_with_home_default(runner, tmp_path, monkeypatch):
    monkeypatch.setattr("duckgate.cli.Path.home", lambda: tmp_path)
    result = runner.invoke(cli, ["init"], input="\n")
    assert result.exit_code == 0
    expected = tmp_path / ".duckgate" / "config.toml"
    assert f"[{expected}]" in result.output
    assert expected.exists()
    assert "[aws]" in expected.read_text()


def test_init_command_prompt_accepts_custom_path(runner, tmp_path, monkeypatch):
    monkeypatch.setattr("duckgate.cli.Path.home", lambda: tmp_path)
    custom = tmp_path / "custom.toml"
    result = runner.invoke(cli, ["init"], input=f"{custom}\n")
    assert result.exit_code == 0
    assert custom.exists()
    assert not (tmp_path / ".duckgate" / "config.toml").exists()


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
