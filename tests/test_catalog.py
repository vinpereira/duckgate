from unittest.mock import patch

import boto3
import duckdb
import pytest
from moto import mock_aws

from duckgate.catalog import (
    TableSpec,
    _detect_format,
    _make_view_sql,
    describe_table,
    discover_catalog,
    ensure_registered,
    run_query,
)
from duckgate.config import AwsConfig, Config, GlueConfig, TableConfig


def test_make_view_sql_parquet():
    sql = _make_view_sql("my_table", "s3://bucket/prefix/**/*.parquet", "parquet")
    assert 'CREATE OR REPLACE VIEW "my_table"' in sql
    assert "read_parquet" in sql
    assert "s3://bucket/prefix/**/*.parquet" in sql


def test_make_view_sql_quotes_hyphenated_name():
    # Glue database/table names commonly contain hyphens, which DuckDB's
    # unquoted identifier syntax parses as the subtraction operator
    sql = _make_view_sql("gate-nonprod-eu-db__my-table", "s3://bucket/prefix/*.parquet", "parquet")
    assert '"gate-nonprod-eu-db__my-table"' in sql


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


def test_discover_catalog_local_tables_only():
    config = Config(
        aws=AwsConfig(profile="test", region="eu-central-1"),
        glue=GlueConfig(enabled=False),
        tables=[TableConfig(name="my_table", path="s3://bucket/data/*.parquet", format="parquet")],
    )
    catalog = discover_catalog(config)
    assert catalog == {"my_table": TableSpec(path="s3://bucket/data/*.parquet", format="parquet")}


@mock_aws
def test_discover_catalog_glue_tables():
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
    with patch("duckgate.catalog.boto3.Session", return_value=boto3.Session()):
        catalog = discover_catalog(config)
    assert catalog == {"locations": TableSpec(path="s3://bucket/locations/", format="parquet")}


@mock_aws
def test_discover_catalog_local_overrides_glue():
    glue = boto3.client("glue", region_name="eu-central-1")
    glue.create_database(DatabaseInput={"Name": "my_db"})
    glue.create_table(
        DatabaseName="my_db",
        TableInput=_glue_table_input("locations", "s3://bucket/locations/"),
    )
    config = Config(
        aws=AwsConfig(profile="test", region="eu-central-1"),
        glue=GlueConfig(enabled=True, databases=["my_db"]),
        tables=[TableConfig(name="locations", path="s3://override/data/*.parquet", format="csv")],
    )
    with patch("duckgate.catalog.boto3.Session", return_value=boto3.Session()):
        catalog = discover_catalog(config)
    assert catalog["locations"] == TableSpec(path="s3://override/data/*.parquet", format="csv")


@mock_aws
def test_discover_catalog_collision_uses_double_underscore():
    config = Config(
        aws=AwsConfig(profile="test", region="eu-central-1"),
        glue=GlueConfig(enabled=True, databases=["db_a", "db_b"]),
    )
    glue = boto3.client("glue", region_name="eu-central-1")
    for db in ["db_a", "db_b"]:
        glue.create_database(DatabaseInput={"Name": db})
        glue.create_table(
            DatabaseName=db,
            TableInput=_glue_table_input("events", f"s3://bucket/{db}/events/"),
        )
    with patch("duckgate.catalog.boto3.Session", return_value=boto3.Session()):
        catalog = discover_catalog(config)
    assert "db_a__events" in catalog
    assert "db_b__events" in catalog
    assert "events" not in catalog


@mock_aws
def test_discover_catalog_skips_table_without_location():
    glue = boto3.client("glue", region_name="eu-central-1")
    glue.create_database(DatabaseInput={"Name": "my_db"})
    glue.create_table(
        DatabaseName="my_db",
        TableInput=_glue_table_input("empty_table", ""),
    )
    config = Config(
        aws=AwsConfig(profile="test", region="eu-central-1"),
        glue=GlueConfig(enabled=True, databases=["my_db"]),
    )
    with patch("duckgate.catalog.boto3.Session", return_value=boto3.Session()):
        catalog = discover_catalog(config)
    assert "empty_table" not in catalog


def test_discover_catalog_glue_disabled_skips_glue_entirely():
    config = Config(
        aws=AwsConfig(profile="test", region="eu-central-1"),
        glue=GlueConfig(enabled=False, databases=["my_db"]),
    )
    with patch("duckgate.catalog.boto3.Session") as mock_session:
        catalog = discover_catalog(config)
    mock_session.assert_not_called()
    assert catalog == {}


def test_ensure_registered_registers_matching_bare_name(
    duck_conn, sample_parquet_bytes, moto_server
):
    s3 = boto3.client(
        "s3",
        region_name="eu-central-1",
        endpoint_url=f"http://{moto_server}",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    s3.create_bucket(
        Bucket="ensure-bucket", CreateBucketConfiguration={"LocationConstraint": "eu-central-1"}
    )
    s3.put_object(Bucket="ensure-bucket", Key="data/file.parquet", Body=sample_parquet_bytes)
    _configure_duck_s3(duck_conn, moto_server)

    catalog = {"my_table": TableSpec(path="s3://ensure-bucket/data/*.parquet", format="parquet")}
    registered = set()
    ensure_registered(duck_conn, catalog, "SELECT * FROM my_table", registered)

    assert registered == {"my_table"}
    count = duck_conn.execute("SELECT COUNT(*) FROM my_table").fetchone()[0]
    assert count == 3


def test_ensure_registered_skips_unrelated_tables(duck_conn, sample_parquet_bytes, moto_server):
    s3 = boto3.client(
        "s3",
        region_name="eu-central-1",
        endpoint_url=f"http://{moto_server}",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    s3.create_bucket(
        Bucket="ensure-bucket2", CreateBucketConfiguration={"LocationConstraint": "eu-central-1"}
    )
    s3.put_object(Bucket="ensure-bucket2", Key="data/file.parquet", Body=sample_parquet_bytes)
    _configure_duck_s3(duck_conn, moto_server)

    catalog = {
        "wanted": TableSpec(path="s3://ensure-bucket2/data/*.parquet", format="parquet"),
        "not_wanted": TableSpec(path="s3://does-not-exist/nothing/*.parquet", format="parquet"),
    }
    registered = set()
    ensure_registered(duck_conn, catalog, "SELECT * FROM wanted", registered)

    assert registered == {"wanted"}
    rows = duck_conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
    ).fetchall()
    assert [r[0] for r in rows] == ["wanted"]


def test_ensure_registered_matches_quoted_hyphenated_name(
    duck_conn, sample_parquet_bytes, moto_server
):
    s3 = boto3.client(
        "s3",
        region_name="eu-central-1",
        endpoint_url=f"http://{moto_server}",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    s3.create_bucket(
        Bucket="ensure-bucket3", CreateBucketConfiguration={"LocationConstraint": "eu-central-1"}
    )
    s3.put_object(Bucket="ensure-bucket3", Key="data/file.parquet", Body=sample_parquet_bytes)
    _configure_duck_s3(duck_conn, moto_server)

    catalog = {
        "db-a__my-table": TableSpec(path="s3://ensure-bucket3/data/*.parquet", format="parquet")
    }
    registered = set()
    ensure_registered(duck_conn, catalog, 'SELECT * FROM "db-a__my-table"', registered)

    assert registered == {"db-a__my-table"}


def test_ensure_registered_does_not_match_name_that_is_a_prefix_of_another(
    duck_conn, sample_parquet_bytes, moto_server
):
    # real-world case: Glue tables are often named hierarchically, e.g.
    # "..._root_table" and "..._root_table_accumulateddata_batterystatus" — a
    # plain substring scan would wrongly also register the shorter one
    s3 = boto3.client(
        "s3",
        region_name="eu-central-1",
        endpoint_url=f"http://{moto_server}",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    s3.create_bucket(
        Bucket="prefix-bucket", CreateBucketConfiguration={"LocationConstraint": "eu-central-1"}
    )
    s3.put_object(Bucket="prefix-bucket", Key="data/file.parquet", Body=sample_parquet_bytes)
    _configure_duck_s3(duck_conn, moto_server)

    catalog = {
        "root_table": TableSpec(path="s3://does-not-exist/nothing/*.parquet", format="parquet"),
        "root_table_child": TableSpec(path="s3://prefix-bucket/data/*.parquet", format="parquet"),
    }
    registered = set()
    ensure_registered(duck_conn, catalog, "SELECT * FROM root_table_child", registered)

    assert registered == {"root_table_child"}


def test_ensure_registered_skips_already_registered(duck_conn, capsys):
    catalog = {"broken": TableSpec(path="s3://does-not-exist/nothing/*.parquet", format="parquet")}
    registered = {"broken"}
    ensure_registered(duck_conn, catalog, "SELECT * FROM broken", registered)
    assert registered == {"broken"}
    # a real attempt against the bogus path would print a warning (see the next
    # test) — asserting there's none proves no attempt was made
    assert capsys.readouterr().err == ""


def test_ensure_registered_marks_failed_table_as_registered(duck_conn, moto_server, capsys):
    s3 = boto3.client(
        "s3",
        region_name="eu-central-1",
        endpoint_url=f"http://{moto_server}",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    s3.create_bucket(
        Bucket="ensure-bucket4", CreateBucketConfiguration={"LocationConstraint": "eu-central-1"}
    )
    _configure_duck_s3(duck_conn, moto_server)

    catalog = {
        "broken": TableSpec(path="s3://ensure-bucket4/nothing-here/*.parquet", format="parquet")
    }
    registered = set()
    ensure_registered(duck_conn, catalog, "SELECT * FROM broken", registered)

    assert registered == {"broken"}
    assert "broken" in capsys.readouterr().err


def test_run_query_registers_and_executes(duck_conn, sample_parquet_bytes, moto_server):
    s3 = boto3.client(
        "s3",
        region_name="eu-central-1",
        endpoint_url=f"http://{moto_server}",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    s3.create_bucket(
        Bucket="run-query-bucket",
        CreateBucketConfiguration={"LocationConstraint": "eu-central-1"},
    )
    s3.put_object(Bucket="run-query-bucket", Key="data/file.parquet", Body=sample_parquet_bytes)
    _configure_duck_s3(duck_conn, moto_server)

    catalog = {"my_table": TableSpec(path="s3://run-query-bucket/data/*.parquet", format="parquet")}
    registered = set()
    result = run_query(duck_conn, catalog, "SELECT COUNT(*) FROM my_table", registered)

    assert result.fetchone()[0] == 3
    assert registered == {"my_table"}


def test_run_query_falls_back_on_catalog_exception(duck_conn, sample_parquet_bytes, moto_server):
    s3 = boto3.client(
        "s3",
        region_name="eu-central-1",
        endpoint_url=f"http://{moto_server}",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    s3.create_bucket(
        Bucket="fallback-bucket",
        CreateBucketConfiguration={"LocationConstraint": "eu-central-1"},
    )
    s3.put_object(Bucket="fallback-bucket", Key="data/file.parquet", Body=sample_parquet_bytes)
    _configure_duck_s3(duck_conn, moto_server)

    catalog = {"my_table": TableSpec(path="s3://fallback-bucket/data/*.parquet", format="parquet")}
    registered = set()

    # simulate the text scan missing the table entirely — the real safety net
    # is DuckDB's own "table does not exist" error, handled inside run_query
    with patch("duckgate.catalog.ensure_registered"):
        result = run_query(duck_conn, catalog, "SELECT COUNT(*) FROM my_table", registered)

    assert result.fetchone()[0] == 3
    assert registered == {"my_table"}


def test_run_query_reraises_for_genuinely_unknown_table(duck_conn):
    with pytest.raises(duckdb.CatalogException):
        run_query(duck_conn, {}, "SELECT * FROM totally_unknown_table", set())


def test_run_query_warns_when_no_limit(duck_conn, sample_parquet_bytes, moto_server, capsys):
    s3 = boto3.client(
        "s3",
        region_name="eu-central-1",
        endpoint_url=f"http://{moto_server}",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    s3.create_bucket(
        Bucket="limit-warn-bucket",
        CreateBucketConfiguration={"LocationConstraint": "eu-central-1"},
    )
    s3.put_object(Bucket="limit-warn-bucket", Key="data/file.parquet", Body=sample_parquet_bytes)
    _configure_duck_s3(duck_conn, moto_server)

    catalog = {
        "my_table": TableSpec(path="s3://limit-warn-bucket/data/*.parquet", format="parquet")
    }
    run_query(duck_conn, catalog, "SELECT * FROM my_table", set())

    assert "LIMIT" in capsys.readouterr().err


def test_run_query_no_warning_when_limit_present(
    duck_conn, sample_parquet_bytes, moto_server, capsys
):
    s3 = boto3.client(
        "s3",
        region_name="eu-central-1",
        endpoint_url=f"http://{moto_server}",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    s3.create_bucket(
        Bucket="limit-ok-bucket",
        CreateBucketConfiguration={"LocationConstraint": "eu-central-1"},
    )
    s3.put_object(Bucket="limit-ok-bucket", Key="data/file.parquet", Body=sample_parquet_bytes)
    _configure_duck_s3(duck_conn, moto_server)

    catalog = {"my_table": TableSpec(path="s3://limit-ok-bucket/data/*.parquet", format="parquet")}
    run_query(duck_conn, catalog, "SELECT * FROM my_table LIMIT 5", set())

    assert capsys.readouterr().err == ""


def test_describe_table_shows_columns(duck_conn, sample_parquet_bytes, moto_server):
    s3 = boto3.client(
        "s3",
        region_name="eu-central-1",
        endpoint_url=f"http://{moto_server}",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    s3.create_bucket(
        Bucket="describe-bucket",
        CreateBucketConfiguration={"LocationConstraint": "eu-central-1"},
    )
    s3.put_object(Bucket="describe-bucket", Key="data/file.parquet", Body=sample_parquet_bytes)
    _configure_duck_s3(duck_conn, moto_server)

    spec = TableSpec(path="s3://describe-bucket/data/*.parquet", format="parquet")
    df = describe_table(duck_conn, spec).fetchdf()

    assert list(df["column_name"]) == ["id", "name"]
    # describe_table must not leave a registered view behind
    rows = duck_conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
    ).fetchall()
    assert rows == []


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


def _configure_duck_s3(duck_conn, moto_server):
    duck_conn.execute("SET s3_region='eu-central-1'")
    duck_conn.execute("SET s3_access_key_id='test'")
    duck_conn.execute("SET s3_secret_access_key='test'")
    duck_conn.execute(f"SET s3_endpoint='{moto_server}'")
    duck_conn.execute("SET s3_use_ssl=false")
    duck_conn.execute("SET s3_url_style='path'")
