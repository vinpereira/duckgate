import boto3
import duckdb

from duckgate.config import AwsConfig, Config


class S3Credentials:
    """Keeps a connection's S3 secret in sync with the AWS profile's credentials.

    SSO and assumed-role credentials expire (typically after 1h). boto3 refreshes them
    on its own, so apply() runs before each shell query and re-creates the secret only
    when boto3 hands back different credentials — otherwise it's an in-memory compare.
    """

    def __init__(self, aws: AwsConfig):
        self._region = aws.region
        self._credentials = boto3.Session(profile_name=aws.profile).get_credentials()
        self._applied = None

    def apply(self, conn: duckdb.DuckDBPyConnection) -> None:
        frozen = self._credentials.get_frozen_credentials()
        if frozen == self._applied:
            return
        conn.execute(
            "CREATE OR REPLACE SECRET duckgate_s3 "
            "(TYPE s3, KEY_ID ?, SECRET ?, SESSION_TOKEN ?, REGION ?)",
            [frozen.access_key, frozen.secret_key, frozen.token or "", self._region],
        )
        self._applied = frozen


def create_connection(
    config: Config, credentials: S3Credentials | None = None
) -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    conn.execute("INSTALL httpfs; LOAD httpfs;")
    conn.execute("INSTALL iceberg; LOAD iceberg;")
    conn.execute("INSTALL spatial; LOAD spatial;")
    # Iceberg tables without a version-hint.text (common in Glue catalogs) otherwise
    # fail outright. Accepted trade-off: this only changes behavior when
    # no authoritative version-hint exists, and duckgate is a best-effort ad hoc query
    # tool, not a system anything else depends on for correctness.
    conn.execute("SET unsafe_enable_version_guessing = true;")
    # only kicks in past progress_bar_time (2s default) — quiet for fast
    # catalog registration, visible for slow scans over S3
    conn.execute("PRAGMA enable_progress_bar")

    if credentials is None:
        credentials = S3Credentials(config.aws)
    credentials.apply(conn)

    return conn
