from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppPaths:
    repo_root: Path
    state_dir: Path
    cache_dir: Path
    logs_dir: Path
    db_path: Path
