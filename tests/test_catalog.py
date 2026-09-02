from unittest.mock import patch

import boto3
from moto import mock_aws

from duckgate.catalog import (
    _detect_format,
    _make_view_sql,
    register_glue_tables,
    register_local_tables,
)
from duckgate.config import AwsConfig, Config, GlueConfig, TableConfig


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


def test_make_view_sql_parquet_adds_glob_to_bare_folder():
    # Glue table locations are usually a bare folder prefix, no wildcard
    sql = _make_view_sql("locations", "s3://bucket/locations/", "parquet")
    assert "s3://bucket/locations/**/*.parquet" in sql


def test_make_view_sql_iceberg_keeps_bare_folder_unchanged():
    sql = _make_view_sql("locations", "s3://bucket/locations/", "iceberg")
    assert "iceberg_scan('s3://bucket/locations/')" in sql


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


def _glue_table_input(name, location, input_format=""):
    return {
        "Name": name,
        "StorageDescriptor": {
            "Location": location,
            "InputFormat": input_format
            or "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat",
            "OutputFormat": "",
            "SerdeInfo": {
                "SerializationLibrary": "org.apache.hadoop.hive.serde2.lazy.LazySimpleSerDe"
            },
            "Columns": [],
        },
        "TableType": "EXTERNAL_TABLE",
    }


def _register_glue(conn, config, already_registered):
    # register_glue_tables resolves a real boto3.Session(profile_name=...), which
    # fails locally since no such AWS profile exists — swap in a profile-less
    # session; @mock_aws still intercepts the API calls it makes.
    with patch("duckgate.catalog.boto3.Session", return_value=boto3.Session()):
        return register_glue_tables(conn, config, already_registered)


def _configure_duck_s3(duck_conn, moto_server):
    duck_conn.execute("SET s3_region='eu-central-1'")
    duck_conn.execute("SET s3_access_key_id='test'")
    duck_conn.execute("SET s3_secret_access_key='test'")
    duck_conn.execute(f"SET s3_endpoint='{moto_server}'")
    duck_conn.execute("SET s3_use_ssl=false")
    duck_conn.execute("SET s3_url_style='path'")


@mock_aws
def test_register_glue_tables_parquet(duck_conn, moto_server, sample_parquet_bytes):
    glue = boto3.client("glue", region_name="eu-central-1")
    glue.create_database(DatabaseInput={"Name": "my_db"})
    glue.create_table(
        DatabaseName="my_db",
        TableInput=_glue_table_input("locations", "s3://bucket/locations/"),
    )

    s3 = boto3.client(
        "s3",
        region_name="eu-central-1",
        endpoint_url=f"http://{moto_server}",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    s3.create_bucket(
        Bucket="bucket", CreateBucketConfiguration={"LocationConstraint": "eu-central-1"}
    )
    s3.put_object(Bucket="bucket", Key="locations/part-0.parquet", Body=sample_parquet_bytes)
    _configure_duck_s3(duck_conn, moto_server)

    config = Config(
        aws=AwsConfig(profile="test", region="eu-central-1"),
        glue=GlueConfig(enabled=True, databases=["my_db"]),
    )
    registered = _register_glue(duck_conn, config, already_registered=[])
    assert "locations" in registered
    count = duck_conn.execute("SELECT COUNT(*) FROM locations").fetchone()[0]
    assert count == 3


@mock_aws
def test_register_glue_tables_skips_already_registered(duck_conn):
    glue = boto3.client("glue", region_name="eu-central-1")
    glue.create_database(DatabaseInput={"Name": "my_db"})
    glue.create_table(
        DatabaseName="my_db",
        TableInput=_glue_table_input("locations", "s3://bucket/locations/"),
    )
    config = Config(
        aws=AwsConfig(profile="test", region="eu-central-1"),
        glue=GlueConfig(enabled=True, databases=["my_db"]),
    )
    registered = _register_glue(duck_conn, config, already_registered=["locations"])
    assert "locations" not in registered


@mock_aws
def test_register_glue_tables_name_collision_uses_double_underscore(
    duck_conn, moto_server, sample_parquet_bytes
):
    config = Config(
        aws=AwsConfig(profile="test", region="eu-central-1"),
        glue=GlueConfig(enabled=True, databases=["db_a", "db_b"]),
    )
    glue = boto3.client("glue", region_name="eu-central-1")
    s3 = boto3.client(
        "s3",
        region_name="eu-central-1",
        endpoint_url=f"http://{moto_server}",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    s3.create_bucket(
        Bucket="bucket", CreateBucketConfiguration={"LocationConstraint": "eu-central-1"}
    )
    for db in ["db_a", "db_b"]:
        glue.create_database(DatabaseInput={"Name": db})
        glue.create_table(
            DatabaseName=db,
            TableInput=_glue_table_input("events", f"s3://bucket/{db}/events/"),
        )
        s3.put_object(Bucket="bucket", Key=f"{db}/events/part-0.parquet", Body=sample_parquet_bytes)
    _configure_duck_s3(duck_conn, moto_server)

    registered = _register_glue(duck_conn, config, already_registered=[])
    assert "db_a__events" in registered
    assert "db_b__events" in registered
    assert "events" not in registered


@mock_aws
def test_register_glue_tables_skips_table_without_location(duck_conn):
    glue = boto3.client("glue", region_name="eu-central-1")
    glue.create_database(DatabaseInput={"Name": "my_db"})
    glue.create_table(
        DatabaseName="my_db",
        TableInput=_glue_table_input("empty_table", "", input_format=""),
    )
    config = Config(
        aws=AwsConfig(profile="test", region="eu-central-1"),
        glue=GlueConfig(enabled=True, databases=["my_db"]),
    )
    registered = _register_glue(duck_conn, config, already_registered=[])
    assert "empty_table" not in registered
