from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DailyReportResult:
    report_date: str
    markdown: str
    output_path: Path


class DailyReportBuilder:
    def __init__(self, *, db_path: Path, state_dir: Path) -> None:
        self._db_path = db_path
        self._state_dir = state_dir
