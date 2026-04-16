from pathlib import Path

from autoresearch.reports.daily import DailyReportBuilder


def test_daily_report_builder_module_imports() -> None:
    builder = DailyReportBuilder(
        db_path=Path("state/autoresearch.db"),
        state_dir=Path("state"),
    )
    assert builder is not None
