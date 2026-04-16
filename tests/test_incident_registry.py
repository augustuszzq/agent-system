import json
from pathlib import Path

from autoresearch.db import init_db
from autoresearch.incidents.registry import IncidentRegistry


def test_upsert_incident_reuses_existing_row_for_same_job_category_fingerprint(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"
    init_db(db_path)
    registry = IncidentRegistry(db_path)

    created = registry.upsert_incident(
        run_id="run_demo",
        job_id="job_demo",
        severity="HIGH",
        category="ENV_IMPORT_ERROR",
        fingerprint="no module named nonexistent_package",
        evidence={
            "scan_time": "2026-04-16T00:00:00+00:00",
            "snapshot_dir": "/tmp/scan-a",
            "classifier_rule": "import-error",
            "matched_lines": ["ModuleNotFoundError: No module named 'nonexistent_package'"],
        },
    )
    updated = registry.upsert_incident(
        run_id="run_demo",
        job_id="job_demo",
        severity="HIGH",
        category="ENV_IMPORT_ERROR",
        fingerprint="no module named nonexistent_package",
        evidence={
            "scan_time": "2026-04-16T00:05:00+00:00",
            "snapshot_dir": "/tmp/scan-b",
            "classifier_rule": "import-error",
            "matched_lines": ["ModuleNotFoundError: No module named 'nonexistent_package'"],
        },
    )

    assert created.incident_id == updated.incident_id
    assert created.created_at == updated.created_at
    assert updated.updated_at == "2026-04-16T00:05:00+00:00"


def test_list_open_incidents_returns_newest_first(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"
    init_db(db_path)
    registry = IncidentRegistry(db_path)

    registry.upsert_incident(
        run_id="run_1",
        job_id="job_1",
        severity="MEDIUM",
        category="UNKNOWN",
        fingerprint="a",
        evidence={"scan_time": "2026-04-16T00:00:00+00:00", "snapshot_dir": "/tmp/a", "classifier_rule": "fallback", "matched_lines": ["a"]},
    )
    registry.upsert_incident(
        run_id="run_2",
        job_id="job_2",
        severity="CRITICAL",
        category="RESOURCE_OOM",
        fingerprint="oom",
        evidence={"scan_time": "2026-04-16T00:10:00+00:00", "snapshot_dir": "/tmp/b", "classifier_rule": "oom-line", "matched_lines": ["out of memory"]},
    )

    rows = registry.list_open_incidents()

    assert [row.job_id for row in rows] == ["job_2", "job_1"]


def test_summarize_open_incidents_groups_by_category_and_severity(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"
    init_db(db_path)
    registry = IncidentRegistry(db_path)

    registry.upsert_incident(
        run_id="run_1",
        job_id="job_1",
        severity="CRITICAL",
        category="RESOURCE_OOM",
        fingerprint="oom",
        evidence={"scan_time": "2026-04-16T00:10:00+00:00", "snapshot_dir": "/tmp/a", "classifier_rule": "oom-line", "matched_lines": ["out of memory"]},
    )
    registry.upsert_incident(
        run_id="run_2",
        job_id="job_2",
        severity="HIGH",
        category="ENV_IMPORT_ERROR",
        fingerprint="import",
        evidence={"scan_time": "2026-04-16T00:11:00+00:00", "snapshot_dir": "/tmp/b", "classifier_rule": "import-error", "matched_lines": ["ModuleNotFoundError"]},
    )

    summary = registry.summarize_open_incidents(limit=3)

    assert summary.counts["RESOURCE_OOM"] == 1
    assert summary.counts["ENV_IMPORT_ERROR"] == 1
    assert summary.top_incidents[0].job_id == "job_1"
