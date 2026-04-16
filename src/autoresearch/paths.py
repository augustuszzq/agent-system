from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppPaths:
    repo_root: Path
    state_dir: Path
    cache_dir: Path
    logs_dir: Path
    db_path: Path


def incident_state_dir(paths: AppPaths, job_id: str) -> Path:
    return paths.state_dir / "incidents" / job_id


def incident_snapshot_dir(paths: AppPaths, job_id: str, scan_time: str) -> Path:
    return incident_state_dir(paths, job_id) / scan_time
