from pathlib import Path

from autoresearch.settings import load_settings


def test_load_settings_reads_yaml_and_derives_paths(tmp_path: Path) -> None:
    repo_root = tmp_path
    conf_dir = repo_root / "conf"
    conf_dir.mkdir()
    (conf_dir / "app.yaml").write_text(
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

    settings = load_settings(repo_root=repo_root)

    assert settings.app_name == "auto-research"
    assert settings.paths.state_dir == repo_root / "state"
    assert settings.paths.db_path == repo_root / "state" / "autoresearch.db"
    assert settings.remote_root == "/eagle/lc-mpi/Zhiqing/auto-research"


def test_env_override_replaces_db_path(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path
    conf_dir = repo_root / "conf"
    conf_dir.mkdir()
    (conf_dir / "app.yaml").write_text(
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
    monkeypatch.setenv("AUTORESEARCH_DB", str(repo_root / "custom.db"))

    settings = load_settings(repo_root=repo_root)

    assert settings.paths.db_path == repo_root / "custom.db"
