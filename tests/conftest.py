import io

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from moto.server import ThreadedMotoServer

from duckgate.config import AwsConfig, Config


@pytest.fixture
def duck_conn():
    conn = duckdb.connect(":memory:")
    conn.execute("INSTALL httpfs; LOAD httpfs;")
    conn.execute("INSTALL iceberg; LOAD iceberg;")
    yield conn
    conn.close()


@pytest.fixture
def moto_server():
    # a real HTTP server, not @mock_aws — DuckDB's httpfs makes raw HTTP calls
    # that bypass boto3's in-process mocking
    server = ThreadedMotoServer(port=0, verbose=False)
    server.start()
    _, port = server.get_host_and_port()
    yield f"localhost:{port}"
    server.stop()


@pytest.fixture
def minimal_config():
    return Config(aws=AwsConfig(profile="test", region="eu-central-1"))


@pytest.fixture
def sample_parquet_bytes():
    table = pa.table({"id": [1, 2, 3], "name": ["a", "b", "c"]})
    buf = io.BytesIO()
    pq.write_table(table, buf)
    buf.seek(0)
    return buf.read()
