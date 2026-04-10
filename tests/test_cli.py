from typer.testing import CliRunner

from autoresearch.cli import app


runner = CliRunner()


def test_cli_help_shows_top_level_commands() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "db" in result.stdout
    assert "run" in result.stdout
