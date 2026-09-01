import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AwsConfig:
    profile: str
    region: str = "eu-central-1"


@dataclass
class GlueConfig:
    enabled: bool = True
    databases: list[str] = field(default_factory=list)


@dataclass
class TableConfig:
    name: str
    path: str
    format: str = "parquet"


@dataclass
class Config:
    aws: AwsConfig
    glue: GlueConfig = field(default_factory=GlueConfig)
    tables: list[TableConfig] = field(default_factory=list)


def find_config() -> Path:
    local = Path("duckgate.toml")
    if local.exists():
        return local.resolve()
    home = Path.home() / ".duckgate" / "config.toml"
    if home.exists():
        return home
    raise FileNotFoundError(
        "No duckgate.toml found in current directory or ~/.duckgate/config.toml. "
        "Run `duckgate init` to create one."
    )


def load_config(path: Path) -> Config:
    with open(path, "rb") as f:
        data = tomllib.load(f)
    aws = AwsConfig(**data["aws"])
    glue_data = data.get("glue", {})
    glue = GlueConfig(
        enabled=glue_data.get("enabled", True),
        databases=glue_data.get("databases", []),
    )
    tables = [TableConfig(**t) for t in data.get("tables", [])]
    return Config(aws=aws, glue=glue, tables=tables)
