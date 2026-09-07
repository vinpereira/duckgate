from unittest.mock import MagicMock, patch

import duckdb
import pytest
from botocore.credentials import Credentials, ReadOnlyCredentials

from duckgate.config import AwsConfig, Config
from duckgate.engine import create_connection


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
