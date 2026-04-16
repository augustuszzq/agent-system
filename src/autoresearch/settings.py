from dataclasses import dataclass
import os
from pathlib import Path
from typing import get_args

import yaml

from autoresearch.paths import AppPaths
from autoresearch.schemas import IncidentCategory, RetryAction


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
class RetryPolicySettings:
    safe_retry_categories: tuple[IncidentCategory, ...]
    allowed_actions: tuple[RetryAction, ...]


@dataclass(frozen=True)
class Settings:
    app_name: str
    paths: AppPaths
    remote_root: str
    bridge: BridgeSettings
    probe: ProbeSettings
    retry_policy: RetryPolicySettings


def _resolve_path(repo_root: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return repo_root / path


def _validate_retry_policy_config(retry_policy_config: object) -> RetryPolicySettings:
    if not isinstance(retry_policy_config, dict):
        raise ValueError("conf/retry_policy.yaml must define a mapping")

    safe_retry_categories = retry_policy_config.get("safe_retry_categories")
    allowed_actions = retry_policy_config.get("allowed_actions")

    if not isinstance(safe_retry_categories, list):
        raise ValueError("conf/retry_policy.yaml safe_retry_categories must be a list")
    if not isinstance(allowed_actions, list):
        raise ValueError("conf/retry_policy.yaml allowed_actions must be a list")

    valid_incident_categories = set(get_args(IncidentCategory))
    valid_retry_actions = set(get_args(RetryAction))

    invalid_categories = [value for value in safe_retry_categories if value not in valid_incident_categories]
    if invalid_categories:
        raise ValueError(
            "conf/retry_policy.yaml safe_retry_categories contains invalid values: "
            f"{', '.join(map(str, invalid_categories))}"
        )

    invalid_actions = [value for value in allowed_actions if value not in valid_retry_actions]
    if invalid_actions:
        raise ValueError(
            "conf/retry_policy.yaml allowed_actions contains invalid values: "
            f"{', '.join(map(str, invalid_actions))}"
        )

    return RetryPolicySettings(
        safe_retry_categories=tuple(safe_retry_categories),
        allowed_actions=tuple(allowed_actions),
    )


def resolve_repo_root(repo_root: Path | None = None) -> Path:
    explicit = os.getenv("AUTORESEARCH_REPO_ROOT")
    if explicit:
        return Path(explicit).resolve()
    return (repo_root or Path(__file__).resolve().parents[2]).resolve()


def load_settings(repo_root: Path | None = None) -> Settings:
    resolved_root = resolve_repo_root(repo_root=repo_root)
    app_config = yaml.safe_load((resolved_root / "conf" / "app.yaml").read_text(encoding="utf-8"))
    bridge_config = yaml.safe_load((resolved_root / "conf" / "polaris.yaml").read_text(encoding="utf-8"))
    retry_policy_config = yaml.safe_load(
        (resolved_root / "conf" / "retry_policy.yaml").read_text(encoding="utf-8")
    )

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
        retry_policy=_validate_retry_policy_config(retry_policy_config),
    )
