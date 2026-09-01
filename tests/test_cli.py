from click.testing import CliRunner

import duckgate
from duckgate.cli import cli


def test_cli_help_exits_zero():
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "SQL" in result.output


def test_version_string_set():
    assert duckgate.__version__ == "0.1.0"
