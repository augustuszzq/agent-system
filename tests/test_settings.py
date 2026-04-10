from pathlib import Path

from autoresearch.settings import load_settings


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
    _write_bridge_config(conf_dir)

    settings = load_settings(repo_root=repo_root)

    assert settings.app_name == "auto-research"
    assert settings.paths.state_dir == repo_root / "state"
    assert settings.paths.db_path == repo_root / "state" / "autoresearch.db"
    assert settings.remote_root == "/eagle/lc-mpi/Zhiqing/auto-research"
    assert settings.bridge.server_alive_interval == 60
    assert settings.bridge.server_alive_count_max == 3
    assert settings.bridge.connect_timeout == 15


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
    _write_bridge_config(conf_dir)
    monkeypatch.setenv("AUTORESEARCH_DB", str(repo_root / "custom.db"))

    settings = load_settings(repo_root=repo_root)

    assert settings.paths.db_path == repo_root / "custom.db"


def test_load_settings_reads_bridge_config(tmp_path: Path) -> None:
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
    _write_bridge_config(conf_dir)

    settings = load_settings(repo_root=repo_root)

    assert settings.bridge.alias == "polaris-relay"
    assert settings.bridge.host == "polaris-login-04.hsn.cm.polaris.alcf.anl.gov"
    assert settings.bridge.user == "zzq"
    assert settings.bridge.control_path == "~/.ssh/cm-%C"
    assert settings.bridge.server_alive_interval == 60
    assert settings.bridge.server_alive_count_max == 3
    assert settings.bridge.connect_timeout == 15
