import boto3
import duckdb

from duckgate.config import Config


def create_connection(config: Config) -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    conn.execute("INSTALL httpfs; LOAD httpfs;")
    conn.execute("INSTALL iceberg; LOAD iceberg;")
    conn.execute("INSTALL spatial; LOAD spatial;")
    # Iceberg tables without a version-hint.text (some in this platform's Glue catalog)
    # otherwise fail outright — see docs/duckgate-vs-athena-performance.md for a real
    # example hit during a benchmark. Accepted trade-off: this only changes behavior when
    # no authoritative version-hint exists, and duckgate is a best-effort ad hoc query
    # tool, not a system anything else depends on for correctness.
    conn.execute("SET unsafe_enable_version_guessing = true;")
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
