from typer.testing import CliRunner

from autoresearch.cli import app


runner = CliRunner()


def test_cli_help_shows_top_level_commands() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "db" in result.stdout
    assert "run" in result.stdout


def test_db_init_creates_database_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AUTORESEARCH_DB", str(tmp_path / "state" / "autoresearch.db"))
    monkeypatch.setenv("AUTORESEARCH_REPO_ROOT", str(tmp_path))
    (tmp_path / "conf").mkdir()
    (tmp_path / "conf" / "app.yaml").write_text(
        "app_name: auto-research\n"
        "paths:\n"
        "  state_dir: state\n"
        "  cache_dir: cache\n"
        "  logs_dir: logs\n"
        "  db_path: state/autoresearch.db\n"
        "remote:\n"
        "  root: /eagle/lc-mpi/Zhiqing/auto-research\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["db", "init"])

    assert result.exit_code == 0
    assert (tmp_path / "state" / "autoresearch.db").exists()


def test_run_create_and_list_round_trip(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AUTORESEARCH_DB", str(tmp_path / "state" / "autoresearch.db"))
    monkeypatch.setenv("AUTORESEARCH_REPO_ROOT", str(tmp_path))
    (tmp_path / "conf").mkdir()
    (tmp_path / "conf" / "app.yaml").write_text(
        "app_name: auto-research\n"
        "paths:\n"
        "  state_dir: state\n"
        "  cache_dir: cache\n"
        "  logs_dir: logs\n"
        "  db_path: state/autoresearch.db\n"
        "remote:\n"
        "  root: /eagle/lc-mpi/Zhiqing/auto-research\n",
        encoding="utf-8",
    )

    init_result = runner.invoke(app, ["db", "init"])
    create_result = runner.invoke(
        app,
        ["run", "create", "--kind", "local-debug", "--project", "demo", "--notes", "hello"],
    )
    list_result = runner.invoke(app, ["run", "list"])

    assert init_result.exit_code == 0
    assert create_result.exit_code == 0
    assert "local-debug" in list_result.stdout
    assert "demo" in list_result.stdout
