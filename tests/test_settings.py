from pathlib import Path

import pytest

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
        "  connect_timeout: 15\n"
        "probe:\n"
        "  project: ALCF_PROJECT\n"
        "  queue: debug\n"
        "  walltime: 00:10:00\n",
        encoding="utf-8",
    )


def _write_retry_policy(conf_dir: Path) -> None:
    (conf_dir / "retry_policy.yaml").write_text(
        "safe_retry_categories:\n"
        "  - FILESYSTEM_UNAVAILABLE\n"
        "allowed_actions:\n"
        "  - RETRY_SAME_CONFIG\n",
        encoding="utf-8",
    )


def _write_invalid_retry_policy(conf_dir: Path, *, category: str | None = None, action: str | None = None) -> None:
    safe_retry_categories = ""
    if category is not None:
        safe_retry_categories = f"  - {category}\n"

    allowed_actions = ""
    if action is not None:
        allowed_actions = f"  - {action}\n"

    (conf_dir / "retry_policy.yaml").write_text(
        "safe_retry_categories:\n"
        f"{safe_retry_categories}"
        "allowed_actions:\n"
        f"{allowed_actions}",
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
    _write_retry_policy(conf_dir)

    settings = load_settings(repo_root=repo_root)

    assert settings.app_name == "auto-research"
    assert settings.paths.state_dir == repo_root / "state"
    assert settings.paths.db_path == repo_root / "state" / "autoresearch.db"
    assert settings.remote_root == "/eagle/lc-mpi/Zhiqing/auto-research"
    assert settings.bridge.server_alive_interval == 60
    assert settings.bridge.server_alive_count_max == 3
    assert settings.bridge.connect_timeout == 15


def test_load_settings_reads_retry_policy(tmp_path: Path) -> None:
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
    _write_retry_policy(conf_dir)

    settings = load_settings(repo_root=repo_root)

    assert settings.retry_policy.safe_retry_categories == ("FILESYSTEM_UNAVAILABLE",)
    assert settings.retry_policy.allowed_actions == ("RETRY_SAME_CONFIG",)


@pytest.mark.parametrize(
    ("category", "action", "message"),
    [
        ("NOT_A_CATEGORY", "RETRY_SAME_CONFIG", "safe_retry_categories contains invalid values"),
        ("FILESYSTEM_UNAVAILABLE", "NOT_A_ACTION", "allowed_actions contains invalid values"),
    ],
)
def test_load_settings_rejects_invalid_retry_policy_values(
    tmp_path: Path,
    category: str,
    action: str,
    message: str,
) -> None:
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
    _write_invalid_retry_policy(conf_dir, category=category, action=action)

    with pytest.raises(ValueError, match=message):
        load_settings(repo_root=repo_root)


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
    _write_retry_policy(conf_dir)
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
    _write_retry_policy(conf_dir)

    settings = load_settings(repo_root=repo_root)

    assert settings.bridge.alias == "polaris-relay"
    assert settings.bridge.host == "polaris-login-04.hsn.cm.polaris.alcf.anl.gov"
    assert settings.bridge.user == "zzq"
    assert settings.bridge.control_path == "~/.ssh/cm-%C"
    assert settings.bridge.server_alive_interval == 60
    assert settings.bridge.server_alive_count_max == 3
    assert settings.bridge.connect_timeout == 15


def test_load_settings_reads_probe_defaults(tmp_path: Path) -> None:
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
    _write_retry_policy(conf_dir)

    settings = load_settings(repo_root=repo_root)

    assert settings.probe.project == "ALCF_PROJECT"
    assert settings.probe.queue == "debug"
    assert settings.probe.walltime == "00:10:00"
