import boto3

from duckgate.catalog import _detect_format, _make_view_sql, register_local_tables
from duckgate.config import TableConfig


def test_make_view_sql_parquet():
    sql = _make_view_sql("my_table", "s3://bucket/prefix/**/*.parquet", "parquet")
    assert "CREATE OR REPLACE VIEW my_table" in sql
    assert "read_parquet" in sql
    assert "s3://bucket/prefix/**/*.parquet" in sql


def test_make_view_sql_iceberg():
    sql = _make_view_sql("my_table", "s3://bucket/prefix/", "iceberg")
    assert "iceberg_scan" in sql


def test_make_view_sql_csv():
    sql = _make_view_sql("my_table", "s3://bucket/prefix/*.csv", "csv")
    assert "read_csv" in sql


def test_detect_format_iceberg():
    assert _detect_format({"TableType": "ICEBERG", "StorageDescriptor": {}}) == "iceberg"


def test_detect_format_parquet_from_input_format():
    table = {
        "TableType": "",
        "StorageDescriptor": {
            "InputFormat": "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
        },
    }
    assert _detect_format(table) == "parquet"


def test_detect_format_csv():
    table = {
        "TableType": "",
        "StorageDescriptor": {"InputFormat": "org.apache.hadoop.mapred.TextInputFormat"},
    }
    # CSV detection: falls back to parquet (safe default) when no CSV marker
    assert _detect_format(table) == "parquet"


def test_detect_format_csv_explicit():
    table = {
        "TableType": "",
        "StorageDescriptor": {"InputFormat": "org.apache.hadoop.mapred.CsvInputFormat"},
    }
    assert _detect_format(table) == "csv"


def test_register_local_tables_returns_names(duck_conn, sample_parquet_bytes, moto_server):
    s3 = boto3.client(
        "s3",
        region_name="eu-central-1",
        endpoint_url=f"http://{moto_server}",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    s3.create_bucket(
        Bucket="test-bucket",
        CreateBucketConfiguration={"LocationConstraint": "eu-central-1"},
    )
    s3.put_object(Bucket="test-bucket", Key="data/file.parquet", Body=sample_parquet_bytes)

    duck_conn.execute("SET s3_region='eu-central-1'")
    duck_conn.execute("SET s3_access_key_id='test'")
    duck_conn.execute("SET s3_secret_access_key='test'")
    duck_conn.execute(f"SET s3_endpoint='{moto_server}'")
    duck_conn.execute("SET s3_use_ssl=false")
    duck_conn.execute("SET s3_url_style='path'")

    tables = [
        TableConfig(name="test_table", path="s3://test-bucket/data/*.parquet", format="parquet")
    ]
    registered = register_local_tables(duck_conn, tables)
    assert registered == ["test_table"]

    count = duck_conn.execute("SELECT COUNT(*) FROM test_table").fetchone()[0]
    assert count == 3
