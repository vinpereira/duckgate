from unittest.mock import MagicMock, patch

import duckdb
import pytest
from botocore.credentials import Credentials, ReadOnlyCredentials

from duckgate.config import AwsConfig, Config
from duckgate.engine import S3Credentials, create_connection


@pytest.fixture
def minimal_config():
    return Config(aws=AwsConfig(profile="test", region="eu-central-1"))


def _mock_credentials(token="token"):
    # spec'd against the real class so a typo'd/nonexistent method (e.g. the
    # old .resolve()) fails loudly instead of MagicMock silently allowing it
    creds = MagicMock(spec=Credentials)
    creds.get_frozen_credentials.return_value = ReadOnlyCredentials(
        access_key="AKIATEST", secret_key="secret", token=token, account_id=None
    )
    return creds


def test_create_connection_returns_duckdb_connection(minimal_config):
    mock_session = MagicMock()
    mock_session.get_credentials.return_value = _mock_credentials()
    with patch("duckgate.engine.boto3.Session", return_value=mock_session):
        conn = create_connection(minimal_config)
    assert isinstance(conn, duckdb.DuckDBPyConnection)
    conn.close()


def test_create_connection_creates_s3_secret(minimal_config):
    mock_session = MagicMock()
    mock_session.get_credentials.return_value = _mock_credentials()
    with patch("duckgate.engine.boto3.Session", return_value=mock_session):
        conn = create_connection(minimal_config)
    secrets = conn.execute("SELECT type, secret_string FROM duckdb_secrets()").fetchall()
    assert len(secrets) == 1
    assert secrets[0][0] == "s3"
    assert "region=eu-central-1" in secrets[0][1]
    conn.close()


def test_create_connection_without_session_token(minimal_config):
    mock_session = MagicMock()
    mock_session.get_credentials.return_value = _mock_credentials(token=None)
    with patch("duckgate.engine.boto3.Session", return_value=mock_session):
        conn = create_connection(minimal_config)
    assert isinstance(conn, duckdb.DuckDBPyConnection)
    conn.close()


def test_create_connection_enables_progress_bar(minimal_config):
    mock_session = MagicMock()
    mock_session.get_credentials.return_value = _mock_credentials()
    with patch("duckgate.engine.boto3.Session", return_value=mock_session):
        conn = create_connection(minimal_config)
    result = conn.execute("SELECT current_setting('enable_progress_bar')").fetchone()[0]
    assert result is True
    conn.close()


def test_create_connection_loads_spatial_extension(minimal_config):
    mock_session = MagicMock()
    mock_session.get_credentials.return_value = _mock_credentials()
    with patch("duckgate.engine.boto3.Session", return_value=mock_session):
        conn = create_connection(minimal_config)
    result = conn.execute("SELECT ST_AsText(ST_Point(1, 1))").fetchone()[0]
    assert result == "POINT (1 1)"
    conn.close()


def test_create_connection_enables_iceberg_version_guessing(minimal_config):
    mock_session = MagicMock()
    mock_session.get_credentials.return_value = _mock_credentials()
    with patch("duckgate.engine.boto3.Session", return_value=mock_session):
        conn = create_connection(minimal_config)
    result = conn.execute("SELECT current_setting('unsafe_enable_version_guessing')").fetchone()[0]
    assert result is True
    conn.close()


def _frozen(key):
    return ReadOnlyCredentials(access_key=key, secret_key="secret", token="tok", account_id=None)


def _s3_credentials(minimal_config, *frozen):
    creds = MagicMock(spec=Credentials)
    creds.get_frozen_credentials.side_effect = list(frozen)
    mock_session = MagicMock()
    mock_session.get_credentials.return_value = creds
    with patch("duckgate.engine.boto3.Session", return_value=mock_session):
        return S3Credentials(minimal_config.aws)


def test_s3_credentials_skips_secret_when_unchanged(minimal_config):
    credentials = _s3_credentials(minimal_config, _frozen("AKIA1"), _frozen("AKIA1"))
    conn = MagicMock()
    credentials.apply(conn)
    credentials.apply(conn)
    assert conn.execute.call_count == 1


def test_s3_credentials_recreates_secret_when_refreshed(minimal_config):
    credentials = _s3_credentials(minimal_config, _frozen("AKIA1"), _frozen("AKIA2"))
    conn = MagicMock()
    credentials.apply(conn)
    credentials.apply(conn)
    assert conn.execute.call_count == 2
    assert conn.execute.call_args.args[1][0] == "AKIA2"


def test_s3_credentials_replace_keeps_a_single_secret(minimal_config):
    credentials = _s3_credentials(minimal_config, _frozen("AKIA1"), _frozen("AKIA2"))
    conn = duckdb.connect(":memory:")
    conn.execute("INSTALL httpfs; LOAD httpfs;")
    credentials.apply(conn)
    credentials.apply(conn)
    secrets = conn.execute("SELECT name, secret_string FROM duckdb_secrets()").fetchall()
    assert len(secrets) == 1
    assert secrets[0][0] == "duckgate_s3"
    assert "AKIA2" in secrets[0][1]
    conn.close()


def test_create_connection_uses_injected_credentials(minimal_config):
    credentials = MagicMock(spec=S3Credentials)
    with patch("duckgate.engine.boto3.Session", side_effect=AssertionError("unused")):
        conn = create_connection(minimal_config, credentials)
    credentials.apply.assert_called_once_with(conn)
    conn.close()
