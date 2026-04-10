from pathlib import Path

from typer.testing import CliRunner

from autoresearch.cli import app


runner = CliRunner()


def _write_bridge_config(conf_dir: Path) -> None:
    (conf_dir / "polaris.yaml").write_text(
        "bridge:\n"
        "  alias: polaris-relay\n"
        "  host: polaris-login-04.hsn.cm.polaris.alcf.anl.gov\n"
        "  user: zzq\n"
        "  control_path: ~/.ssh/cm-%C\n"
        "  server_alive_interval: 60\n"
        "  server_alive_count_max: 3\n"
        "  connect_timeout: 15\n",
        encoding="utf-8",
    )


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
    _write_bridge_config(tmp_path / "conf")

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
    _write_bridge_config(tmp_path / "conf")

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


def test_repository_docs_exist() -> None:
    repo_root = Path(__file__).resolve().parents[1]

    assert (repo_root / "README.md").exists()
    assert (repo_root / "AGENTS.md").exists()
    assert (repo_root / "PLANS.md").exists()
    assert (repo_root / "SESSION_RESUME.md").exists()
