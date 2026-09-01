from unittest.mock import MagicMock, patch

import duckdb
import pytest

from duckgate.config import AwsConfig, Config
from duckgate.engine import create_connection


@pytest.fixture
def minimal_config():
    return Config(aws=AwsConfig(profile="test", region="eu-central-1"))


def _mock_credentials():
    creds = MagicMock()
    creds.access_key = "AKIATEST"
    creds.secret_key = "secret"
    creds.token = "token"
    return creds


def test_create_connection_returns_duckdb_connection(minimal_config):
    mock_session = MagicMock()
    mock_session.get_credentials.return_value.resolve.return_value = _mock_credentials()
    with patch("duckgate.engine.boto3.Session", return_value=mock_session):
        conn = create_connection(minimal_config)
    assert isinstance(conn, duckdb.DuckDBPyConnection)
    conn.close()


def test_create_connection_injects_region(minimal_config):
    mock_session = MagicMock()
    mock_session.get_credentials.return_value.resolve.return_value = _mock_credentials()
    with patch("duckgate.engine.boto3.Session", return_value=mock_session):
        conn = create_connection(minimal_config)
    result = conn.execute("SELECT current_setting('s3_region')").fetchone()[0]
    assert result == "eu-central-1"
    conn.close()


def test_create_connection_without_session_token(minimal_config):
    creds = MagicMock()
    creds.access_key = "AKIA"
    creds.secret_key = "secret"
    creds.token = None
    mock_session = MagicMock()
    mock_session.get_credentials.return_value.resolve.return_value = creds
    with patch("duckgate.engine.boto3.Session", return_value=mock_session):
        conn = create_connection(minimal_config)
    assert isinstance(conn, duckdb.DuckDBPyConnection)
    conn.close()
