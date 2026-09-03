import boto3
import duckdb

from duckgate.config import Config


def create_connection(config: Config) -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    conn.execute("INSTALL httpfs; LOAD httpfs;")
    conn.execute("INSTALL iceberg; LOAD iceberg;")
    # only kicks in past progress_bar_time (2s default) — quiet for fast
    # catalog registration, visible for slow scans over S3
    conn.execute("PRAGMA enable_progress_bar")

    session = boto3.Session(profile_name=config.aws.profile)
    creds = session.get_credentials().get_frozen_credentials()

    conn.execute(f"SET s3_region='{config.aws.region}'")
    conn.execute(f"SET s3_access_key_id='{creds.access_key}'")
    conn.execute(f"SET s3_secret_access_key='{creds.secret_key}'")
    if creds.token:
        conn.execute(f"SET s3_session_token='{creds.token}'")

    return conn
