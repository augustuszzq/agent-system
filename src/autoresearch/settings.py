from dataclasses import dataclass
import os
from pathlib import Path

import yaml

from autoresearch.paths import AppPaths


@dataclass(frozen=True)
class Settings:
    app_name: str
    paths: AppPaths
    remote_root: str


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
    config_path = resolved_root / "conf" / "app.yaml"
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    state_dir = _resolve_path(resolved_root, data["paths"]["state_dir"])
    cache_dir = _resolve_path(resolved_root, data["paths"]["cache_dir"])
    logs_dir = _resolve_path(resolved_root, data["paths"]["logs_dir"])
    db_path = _resolve_path(resolved_root, data["paths"]["db_path"])

    override_db = os.getenv("AUTORESEARCH_DB")
    if override_db:
        db_path = _resolve_path(resolved_root, override_db)

    return Settings(
        app_name=data["app_name"],
        paths=AppPaths(
            repo_root=resolved_root,
            state_dir=state_dir,
            cache_dir=cache_dir,
            logs_dir=logs_dir,
            db_path=db_path,
        ),
        remote_root=data["remote"]["root"],
    )
