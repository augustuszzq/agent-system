from dataclasses import dataclass
import os
from pathlib import Path

import yaml

from autoresearch.paths import AppPaths


@dataclass(frozen=True)
class BridgeSettings:
    alias: str
    host: str
    user: str
    control_path: str
    server_alive_interval: int
    server_alive_count_max: int
    connect_timeout: int


@dataclass(frozen=True)
class ProbeSettings:
    project: str
    queue: str
    walltime: str


@dataclass(frozen=True)
class Settings:
    app_name: str
    paths: AppPaths
    remote_root: str
    bridge: BridgeSettings
    probe: ProbeSettings


def _resolve_path(repo_root: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return repo_root / path


def resolve_repo_root(repo_root: Path | None = None) -> Path:
    explicit = os.getenv("AUTORESEARCH_REPO_ROOT")
    if explicit:
        return Path(explicit).resolve()
    return (repo_root or Path(__file__).resolve().parents[2]).resolve()


def load_settings(repo_root: Path | None = None) -> Settings:
    resolved_root = resolve_repo_root(repo_root=repo_root)
    app_config = yaml.safe_load((resolved_root / "conf" / "app.yaml").read_text(encoding="utf-8"))
    bridge_config = yaml.safe_load((resolved_root / "conf" / "polaris.yaml").read_text(encoding="utf-8"))

    state_dir = _resolve_path(resolved_root, app_config["paths"]["state_dir"])
    cache_dir = _resolve_path(resolved_root, app_config["paths"]["cache_dir"])
    logs_dir = _resolve_path(resolved_root, app_config["paths"]["logs_dir"])
    db_path = _resolve_path(resolved_root, app_config["paths"]["db_path"])

    override_db = os.getenv("AUTORESEARCH_DB")
    if override_db:
        db_path = _resolve_path(resolved_root, override_db)

    return Settings(
        app_name=app_config["app_name"],
        paths=AppPaths(
            repo_root=resolved_root,
            state_dir=state_dir,
            cache_dir=cache_dir,
            logs_dir=logs_dir,
            db_path=db_path,
        ),
        remote_root=app_config["remote"]["root"],
        bridge=BridgeSettings(
            alias=bridge_config["bridge"]["alias"],
            host=bridge_config["bridge"]["host"],
            user=bridge_config["bridge"]["user"],
            control_path=bridge_config["bridge"]["control_path"],
            server_alive_interval=bridge_config["bridge"]["server_alive_interval"],
            server_alive_count_max=bridge_config["bridge"]["server_alive_count_max"],
            connect_timeout=bridge_config["bridge"]["connect_timeout"],
        ),
        probe=ProbeSettings(
            project=bridge_config["probe"]["project"],
            queue=bridge_config["probe"]["queue"],
            walltime=bridge_config["probe"]["walltime"],
        ),
    )
