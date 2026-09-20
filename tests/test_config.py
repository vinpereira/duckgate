import pytest

from duckgate.config import find_config, load_config


def test_load_minimal_config(tmp_path):
    toml = tmp_path / "duckgate.toml"
    toml.write_text('[aws]\nprofile = "test-profile"\nregion = "eu-central-1"\n')
    config = load_config(toml)
    assert config.aws.profile == "test-profile"
    assert config.aws.region == "eu-central-1"
    assert config.glue.enabled is True
    assert config.glue.databases == []
    assert config.sources == []


def test_load_config_with_local_source(tmp_path):
    toml = tmp_path / "duckgate.toml"
    toml.write_text(
        '[aws]\nprofile = "p"\nregion = "eu-central-1"\n'
        '[[sources]]\nname = "fis_location"\n'
        'path = "s3://bucket/prefix/**/*.parquet"\nformat = "parquet"\n'
    )
    config = load_config(toml)
    assert len(config.sources) == 1
    assert config.sources[0].name == "fis_location"
    assert config.sources[0].format == "parquet"


def test_load_config_glue_disabled(tmp_path):
    toml = tmp_path / "duckgate.toml"
    toml.write_text('[aws]\nprofile = "p"\nregion = "r"\n[glue]\nenabled = false\n')
    config = load_config(toml)
    assert config.glue.enabled is False


def test_find_config_local(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "duckgate.toml").write_text('[aws]\nprofile = "p"\nregion = "r"\n')
    assert find_config() == tmp_path / "duckgate.toml"


def test_find_config_not_found(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(FileNotFoundError, match="duckgate init"):
        find_config()


def test_load_config_default_query_limit(tmp_path):
    toml = tmp_path / "duckgate.toml"
    toml.write_text('[aws]\nprofile = "p"\nregion = "r"\n')
    config = load_config(toml)
    assert config.query.default_limit == 100


def test_load_config_custom_query_limit(tmp_path):
    toml = tmp_path / "duckgate.toml"
    toml.write_text('[aws]\nprofile = "p"\nregion = "r"\n[query]\ndefault_limit = 25\n')
    config = load_config(toml)
    assert config.query.default_limit == 25


def test_load_config_default_query_timeout(tmp_path):
    toml = tmp_path / "duckgate.toml"
    toml.write_text('[aws]\nprofile = "p"\nregion = "r"\n')
    config = load_config(toml)
    assert config.query.timeout_seconds == 0


def test_load_config_custom_query_timeout(tmp_path):
    toml = tmp_path / "duckgate.toml"
    toml.write_text('[aws]\nprofile = "p"\nregion = "r"\n[query]\ntimeout_seconds = 30\n')
    config = load_config(toml)
    assert config.query.timeout_seconds == 30
