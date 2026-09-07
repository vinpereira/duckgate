import boto3
import duckdb

from duckgate.config import Config


def create_connection(config: Config) -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    conn.execute("INSTALL httpfs; LOAD httpfs;")
    conn.execute("INSTALL iceberg; LOAD iceberg;")
    conn.execute("INSTALL spatial; LOAD spatial;")
    # only kicks in past progress_bar_time (2s default) — quiet for fast
    # catalog registration, visible for slow scans over S3
    conn.execute("PRAGMA enable_progress_bar")

    session = boto3.Session(profile_name=config.aws.profile)
    creds = session.get_credentials().get_frozen_credentials()

    conn.execute(
        "CREATE SECRET (TYPE s3, KEY_ID ?, SECRET ?, SESSION_TOKEN ?, REGION ?)",
        [creds.access_key, creds.secret_key, creds.token or "", config.aws.region],
    )

    return conn
