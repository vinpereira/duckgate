import time
from unittest.mock import patch

import boto3
import duckdb
import pytest
from moto import mock_aws

from duckgate.catalog import (
    QueryTimeoutError,
    TableSpec,
    _detect_format,
    _make_view_sql,
    _source_expr,
    apply_default_limit,
    describe_table,
    discover_catalog,
    ensure_registered,
    glue_cache_age,
    run_query,
    run_query_with_timeout,
    run_script,
)
from duckgate.config import AwsConfig, Config, GlueConfig, SourceConfig


def test_make_view_sql_parquet():
    sql = _make_view_sql("my_table", "s3://bucket/prefix/**/*.parquet", "parquet")
    assert 'CREATE OR REPLACE VIEW "my_table"' in sql
    assert "read_parquet" in sql
    assert "s3://bucket/prefix/**/*.parquet" in sql


def test_make_view_sql_quotes_hyphenated_name():
    # Glue database/table names commonly contain hyphens, which DuckDB's
    # unquoted identifier syntax parses as the subtraction operator
    sql = _make_view_sql("my-db__my-table", "s3://bucket/prefix/*.parquet", "parquet")
    assert '"my-db__my-table"' in sql


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


def test_source_expr_json_adds_glob_with_gz_wildcard():
    sql = _source_expr("s3://bucket/logs/", "json")
    assert "read_json('s3://bucket/logs/**/*.json*')" == sql


def test_source_expr_json_leaves_explicit_glob_unchanged():
    sql = _source_expr("s3://bucket/logs/**/*.json.gz", "json")
    assert sql == "read_json('s3://bucket/logs/**/*.json.gz')"


def test_apply_default_limit_passthrough_when_limit_present():
    sql = apply_default_limit("SELECT * FROM t LIMIT 10", default_limit=100)
    assert sql == "SELECT * FROM t LIMIT 10"


def test_apply_default_limit_disabled_prints_plain_warning(capsys):
    sql = apply_default_limit("SELECT * FROM t", default_limit=0)
    assert sql == "SELECT * FROM t"
    assert "Warning: no LIMIT" in capsys.readouterr().err


def test_apply_default_limit_wraps_select(capsys):
    sql = apply_default_limit("SELECT * FROM t", default_limit=100)
    assert sql == "SELECT * FROM (SELECT * FROM t\n) LIMIT 100"
    err = capsys.readouterr().err
    assert "Note: no LIMIT" in err
    assert "100" in err


def test_apply_default_limit_wraps_with_cte():
    sql = apply_default_limit("WITH x AS (SELECT 1) SELECT * FROM x", default_limit=50)
    assert sql == "SELECT * FROM (WITH x AS (SELECT 1) SELECT * FROM x\n) LIMIT 50"


def test_apply_default_limit_strips_trailing_semicolon():
    sql = apply_default_limit("SELECT * FROM t;", default_limit=100)
    assert sql == "SELECT * FROM (SELECT * FROM t\n) LIMIT 100"


def test_apply_default_limit_survives_trailing_line_comment():
    sql = apply_default_limit("SELECT 1 AS x\n-- trailing note\n;", default_limit=100)
    assert duckdb.sql(sql).fetchall() == [(1,)]


def test_apply_default_limit_skips_non_select_statements(capsys):
    sql = apply_default_limit("PRAGMA table_info('t')", default_limit=100)
    assert sql == "PRAGMA table_info('t')"
    assert "Warning: no LIMIT" in capsys.readouterr().err


def test_detect_format_json_from_serde():
    table = {
        "TableType": "",
        "StorageDescriptor": {
            "InputFormat": "org.apache.hadoop.mapred.TextInputFormat",
            "SerdeInfo": {"SerializationLibrary": "org.openx.data.jsonserde.JsonSerDe"},
        },
    }
    assert _detect_format(table) == "json"


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


def test_discover_catalog_local_sources_only():
    config = Config(
        aws=AwsConfig(profile="test", region="eu-central-1"),
        glue=GlueConfig(enabled=False),
        sources=[
            SourceConfig(name="my_table", path="s3://bucket/data/*.parquet", format="parquet")
        ],
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
        sources=[SourceConfig(name="locations", path="s3://override/data/*.parquet", format="csv")],
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


def test_ensure_registered_json_source_with_gzipped_records(duck_conn, moto_server):
    import gzip
    import json as json_module

    s3 = boto3.client(
        "s3",
        region_name="eu-central-1",
        endpoint_url=f"http://{moto_server}",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    s3.create_bucket(
        Bucket="json-bucket", CreateBucketConfiguration={"LocationConstraint": "eu-central-1"}
    )
    body = gzip.compress(json_module.dumps({"Records": [{"id": 1}, {"id": 2}]}).encode())
    s3.put_object(Bucket="json-bucket", Key="logs/trail.json.gz", Body=body)
    _configure_duck_s3(duck_conn, moto_server)

    catalog = {"trail": TableSpec(path="s3://json-bucket/logs/", format="json")}
    registered = set()
    ensure_registered(duck_conn, catalog, "SELECT * FROM trail, UNNEST(trail.Records)", registered)

    assert registered == {"trail"}
    count = duck_conn.execute('SELECT COUNT(*) FROM "trail", UNNEST("trail".Records)').fetchone()[0]
    assert count == 2


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


def test_run_query_with_timeout_disabled_skips_timer(duck_conn):
    with patch("duckgate.catalog.threading.Timer") as mock_timer:
        run_query_with_timeout(duck_conn, {}, "SELECT 1", set(), timeout_seconds=0)
    mock_timer.assert_not_called()


def test_run_query_with_timeout_fast_query_unaffected(duck_conn):
    result = run_query_with_timeout(duck_conn, {}, "SELECT 1", set(), timeout_seconds=30)
    assert result.fetchone() == (1,)


def test_run_query_with_timeout_cancels_slow_query(duck_conn):
    slow_sql = "SELECT count(*) FROM range(100000000) t1, range(1000) t2"
    with pytest.raises(QueryTimeoutError, match="1s timeout"):
        run_query_with_timeout(duck_conn, {}, slow_sql, set(), timeout_seconds=1)


def test_run_query_with_timeout_connection_reusable_after_timeout(duck_conn):
    slow_sql = "SELECT count(*) FROM range(100000000) t1, range(1000) t2"
    with pytest.raises(QueryTimeoutError):
        run_query_with_timeout(duck_conn, {}, slow_sql, set(), timeout_seconds=1)

    result = run_query_with_timeout(duck_conn, {}, "SELECT 42", set(), timeout_seconds=30)
    assert result.fetchone() == (42,)


def test_default_limit_integration_caps_rows(duck_conn, sample_parquet_bytes, moto_server):
    s3 = boto3.client(
        "s3",
        region_name="eu-central-1",
        endpoint_url=f"http://{moto_server}",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    s3.create_bucket(
        Bucket="limit-integration-bucket",
        CreateBucketConfiguration={"LocationConstraint": "eu-central-1"},
    )
    s3.put_object(
        Bucket="limit-integration-bucket", Key="data/file.parquet", Body=sample_parquet_bytes
    )
    _configure_duck_s3(duck_conn, moto_server)

    catalog = {
        "my_table": TableSpec(path="s3://limit-integration-bucket/data/*.parquet", format="parquet")
    }
    sql = apply_default_limit("SELECT * FROM my_table", default_limit=2)
    rows = run_query(duck_conn, catalog, sql, set()).fetchall()
    assert len(rows) == 2


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


def test_apply_default_limit_wraps_select_after_leading_comment():
    sql = apply_default_limit("-- recent rows only\n/* block */ SELECT * FROM range(10)", 3)
    assert sql.endswith("LIMIT 3")
    assert len(duckdb.sql(sql).fetchall()) == 3


def test_run_script_returns_last_statement_result(duck_conn):
    sql = "CREATE TEMP TABLE t AS SELECT 1 AS x; SELECT x FROM t;"
    assert run_script(duck_conn, {}, sql, set(), 0, 100).fetchall() == [(1,)]


def test_run_script_limits_only_the_last_statement(duck_conn):
    sql = "CREATE TEMP TABLE t AS SELECT * FROM range(10) r(x);\n-- final\nSELECT * FROM t"
    assert len(run_script(duck_conn, {}, sql, set(), 0, 3).fetchall()) == 3
    assert duck_conn.execute("SELECT count(*) FROM t").fetchone()[0] == 10


def test_run_script_handles_semicolons_in_strings(duck_conn):
    assert run_script(duck_conn, {}, "SELECT 'a;b' AS s;", set(), 0, 100).fetchall() == [("a;b",)]


def test_run_script_rejects_empty_input(duck_conn):
    with pytest.raises(ValueError, match="no SQL statement"):
        run_script(duck_conn, {}, "  -- just a comment\n", set(), 0, 100)


# --- Glue catalog cache ---


def _glue_config(ttl=3600, databases=("my_db",), profile="test", sources=()):
    return Config(
        aws=AwsConfig(profile=profile, region="eu-central-1"),
        glue=GlueConfig(enabled=True, databases=list(databases), cache_ttl_seconds=ttl),
        sources=list(sources),
    )


def _create_glue_table(name):
    glue = boto3.client("glue", region_name="eu-central-1")
    try:
        glue.create_database(DatabaseInput={"Name": "my_db"})
    except glue.exceptions.AlreadyExistsException:
        pass
    glue.create_table(DatabaseName="my_db", TableInput=_glue_table_input(name, f"s3://b/{name}/"))


def _discover(config, **kwargs):
    with patch("duckgate.catalog.boto3.Session", return_value=boto3.Session()):
        return discover_catalog(config, **kwargs)


def _discover_without_glue_access(config, **kwargs):
    with patch("duckgate.catalog.boto3.Session", side_effect=AssertionError("Glue was called")):
        return discover_catalog(config, **kwargs)


@mock_aws
def test_glue_catalog_served_from_cache():
    _create_glue_table("locations")
    first = _discover(_glue_config())
    assert _discover_without_glue_access(_glue_config()) == first


@mock_aws
def test_refresh_bypasses_and_rewrites_cache():
    _create_glue_table("locations")
    _discover(_glue_config())
    _create_glue_table("trips")
    assert "trips" in _discover(_glue_config(), refresh=True)
    assert "trips" in _discover_without_glue_access(_glue_config())


@mock_aws
def test_cache_expires_after_ttl():
    _create_glue_table("locations")
    _discover(_glue_config(ttl=60))
    _create_glue_table("trips")
    later = time.time() + 61
    with patch("duckgate.catalog.time.time", return_value=later):
        assert "trips" in _discover(_glue_config(ttl=60))


@mock_aws
def test_cache_ttl_zero_disables_cache(isolated_glue_cache):
    _create_glue_table("locations")
    _discover(_glue_config(ttl=0))
    assert not isolated_glue_cache.exists() or not any(isolated_glue_cache.iterdir())
    _create_glue_table("trips")
    assert "trips" in _discover(_glue_config(ttl=0))


@mock_aws
def test_cache_is_keyed_by_profile_and_databases():
    _create_glue_table("locations")
    _discover(_glue_config())
    with pytest.raises(AssertionError, match="Glue was called"):
        _discover_without_glue_access(_glue_config(profile="other"))
    with pytest.raises(AssertionError, match="Glue was called"):
        _discover_without_glue_access(_glue_config(databases=("my_db", "other_db")))


@mock_aws
def test_corrupt_cache_is_ignored(isolated_glue_cache):
    _create_glue_table("locations")
    _discover(_glue_config())
    for cache_file in isolated_glue_cache.iterdir():
        cache_file.write_text("{not json")
    assert "locations" in _discover(_glue_config())


@mock_aws
def test_unwritable_cache_dir_still_discovers(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    monkeypatch.setattr("duckgate.catalog.CACHE_DIR", blocker / "cache")
    _create_glue_table("locations")
    assert "locations" in _discover(_glue_config())


@mock_aws
def test_local_sources_are_never_cached():
    _create_glue_table("locations")
    first = SourceConfig(name="mine", path="s3://one/*.parquet")
    _discover(_glue_config(sources=[first]))
    second = SourceConfig(name="mine", path="s3://two/*.parquet")
    catalog = _discover_without_glue_access(_glue_config(sources=[second]))
    assert catalog["mine"].path == "s3://two/*.parquet"


@mock_aws
def test_glue_cache_age():
    config = _glue_config()
    assert glue_cache_age(config) is None
    _create_glue_table("locations")
    _discover(config)
    age = glue_cache_age(config)
    assert age is not None and 0 <= age < 5
    with patch("duckgate.catalog.time.time", return_value=time.time() + 3601):
        assert glue_cache_age(config) is None


# --- Glue Iceberg tables as Glue actually reports them ---


@pytest.mark.parametrize("marker", ["ICEBERG", "iceberg"])
def test_detect_format_iceberg_from_table_type_parameter(marker):
    # Glue reports Iceberg tables as TableType EXTERNAL_TABLE + Parameters.table_type
    table = {"TableType": "EXTERNAL_TABLE", "Parameters": {"table_type": marker}}
    assert _detect_format(table) == "iceberg"


def _glue_iceberg_table_input(name, location, metadata_location=None):
    params = {"table_type": "ICEBERG"}
    if metadata_location:
        params["metadata_location"] = metadata_location
    return {
        "Name": name,
        "StorageDescriptor": {"Location": location, "Columns": []},
        "TableType": "EXTERNAL_TABLE",
        "Parameters": params,
    }


@mock_aws
def test_glue_iceberg_table_reads_current_metadata_file():
    glue = boto3.client("glue", region_name="eu-central-1")
    glue.create_database(DatabaseInput={"Name": "my_db"})
    metadata = "s3://b/events/metadata/00042-abc.metadata.json"
    glue.create_table(
        DatabaseName="my_db",
        TableInput=_glue_iceberg_table_input("events", "s3://b/events", metadata),
    )
    catalog = _discover(_glue_config(ttl=0))
    assert catalog["events"] == TableSpec(path=metadata, format="iceberg")


@mock_aws
def test_glue_iceberg_table_without_metadata_location_uses_table_location():
    glue = boto3.client("glue", region_name="eu-central-1")
    glue.create_database(DatabaseInput={"Name": "my_db"})
    glue.create_table(
        DatabaseName="my_db", TableInput=_glue_iceberg_table_input("events", "s3://b/events")
    )
    catalog = _discover(_glue_config(ttl=0))
    assert catalog["events"] == TableSpec(path="s3://b/events", format="iceberg")


@mock_aws
def test_cache_from_an_older_format_version_is_ignored(monkeypatch):
    _create_glue_table("locations")
    _discover(_glue_config())
    monkeypatch.setattr("duckgate.catalog._CACHE_VERSION", 999)
    with pytest.raises(AssertionError, match="Glue was called"):
        _discover_without_glue_access(_glue_config())
