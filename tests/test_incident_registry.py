from datetime import datetime
from pathlib import Path

from autoresearch.db import connect_db, init_db
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


def test_upsert_incident_reopens_resolved_row_for_same_job_category_fingerprint(tmp_path: Path) -> None:
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

    with connect_db(db_path) as conn:
        conn.execute(
            """
            UPDATE incidents
            SET status = 'RESOLVED',
                resolved_at = '2026-04-16T00:01:00+00:00'
            WHERE incident_id = ?
            """,
            (created.incident_id,),
        )

    reopened = registry.upsert_incident(
        run_id="run_demo",
        job_id="job_demo",
        severity="CRITICAL",
        category="ENV_IMPORT_ERROR",
        fingerprint="no module named nonexistent_package",
        evidence={
            "scan_time": "2026-04-16T00:05:00+00:00",
            "snapshot_dir": "/tmp/scan-b",
            "classifier_rule": "import-error",
            "matched_lines": ["ModuleNotFoundError: No module named 'nonexistent_package'"],
        },
    )

    assert reopened.incident_id == created.incident_id
    assert reopened.status == "OPEN"
    assert reopened.resolved_at is None
    assert [row.incident_id for row in registry.list_open_incidents()] == [created.incident_id]


def test_upsert_incident_keeps_updated_at_monotonic_when_reopening_from_stale_snapshot(
    tmp_path: Path,
) -> None:
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
            "scan_time": "2026-04-16T00:10:00+00:00",
            "snapshot_dir": "/tmp/scan-a",
            "classifier_rule": "import-error",
            "matched_lines": ["ModuleNotFoundError: No module named 'nonexistent_package'"],
        },
    )

    with connect_db(db_path) as conn:
        conn.execute(
            """
            UPDATE incidents
            SET status = 'RESOLVED',
                updated_at = '2026-04-16T00:12:00+00:00',
                resolved_at = '2026-04-16T00:12:00+00:00'
            WHERE incident_id = ?
            """,
            (created.incident_id,),
        )

    reopened = registry.upsert_incident(
        run_id="run_demo",
        job_id="job_demo",
        severity="CRITICAL",
        category="ENV_IMPORT_ERROR",
        fingerprint="no module named nonexistent_package",
        evidence={
            "scan_time": "2026-04-16T00:05:00+00:00",
            "snapshot_dir": "/tmp/scan-b",
            "classifier_rule": "import-error",
            "matched_lines": ["ModuleNotFoundError: No module named 'nonexistent_package'"],
        },
    )

    assert reopened.status == "OPEN"
    assert reopened.resolved_at is None
    assert datetime.fromisoformat(reopened.updated_at) > datetime.fromisoformat(
        "2026-04-16T00:12:00+00:00"
    )


def test_upsert_incident_handles_mixed_naive_and_aware_scan_times(tmp_path: Path) -> None:
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
            "scan_time": "2026-04-16T00:00:00",
            "snapshot_dir": "/tmp/scan-a",
            "classifier_rule": "import-error",
            "matched_lines": ["ModuleNotFoundError: No module named 'nonexistent_package'"],
        },
    )
    updated = registry.upsert_incident(
        run_id="run_demo",
        job_id="job_demo",
        severity="CRITICAL",
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
    assert updated.severity == "CRITICAL"
    assert updated.updated_at == "2026-04-16T00:05:00+00:00"


def test_upsert_incident_reuses_existing_row_when_fingerprint_is_null(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"
    init_db(db_path)
    registry = IncidentRegistry(db_path)

    created = registry.upsert_incident(
        run_id="run_demo",
        job_id="job_demo",
        severity="HIGH",
        category="UNKNOWN",
        fingerprint=None,
        evidence={
            "scan_time": "2026-04-16T00:00:00+00:00",
            "snapshot_dir": "/tmp/scan-a",
            "classifier_rule": "fallback",
            "matched_lines": ["first"],
        },
    )
    updated = registry.upsert_incident(
        run_id="run_demo",
        job_id="job_demo",
        severity="CRITICAL",
        category="UNKNOWN",
        fingerprint=None,
        evidence={
            "scan_time": "2026-04-16T00:05:00+00:00",
            "snapshot_dir": "/tmp/scan-b",
            "classifier_rule": "fallback",
            "matched_lines": ["second"],
        },
    )

    assert created.incident_id == updated.incident_id
    assert created.created_at == updated.created_at
    assert updated.severity == "CRITICAL"
    assert updated.updated_at == "2026-04-16T00:05:00+00:00"


def test_upsert_incident_reuses_existing_row_when_job_id_is_null(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"
    init_db(db_path)
    registry = IncidentRegistry(db_path)

    created = registry.upsert_incident(
        run_id="run_demo",
        job_id=None,
        severity="HIGH",
        category="UNKNOWN",
        fingerprint="fallback",
        evidence={
            "scan_time": "2026-04-16T00:00:00+00:00",
            "snapshot_dir": "/tmp/scan-a",
            "classifier_rule": "fallback",
            "matched_lines": ["first"],
        },
    )
    updated = registry.upsert_incident(
        run_id="run_demo",
        job_id=None,
        severity="CRITICAL",
        category="UNKNOWN",
        fingerprint="fallback",
        evidence={
            "scan_time": "2026-04-16T00:05:00+00:00",
            "snapshot_dir": "/tmp/scan-b",
            "classifier_rule": "fallback",
            "matched_lines": ["second"],
        },
    )

    assert created.incident_id == updated.incident_id
    assert created.created_at == updated.created_at
    assert updated.severity == "CRITICAL"
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


def test_summarize_open_incidents_preserves_newest_first_within_same_severity_bucket_and_limit(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"
    init_db(db_path)
    registry = IncidentRegistry(db_path)

    registry.upsert_incident(
        run_id="run_1",
        job_id="job_oldest",
        severity="CRITICAL",
        category="RESOURCE_OOM",
        fingerprint="oom-1",
        evidence={"scan_time": "2026-04-16T00:00:00+00:00", "snapshot_dir": "/tmp/a", "classifier_rule": "oom-line", "matched_lines": ["oldest"]},
    )
    registry.upsert_incident(
        run_id="run_2",
        job_id="job_middle",
        severity="CRITICAL",
        category="RESOURCE_OOM",
        fingerprint="oom-2",
        evidence={"scan_time": "2026-04-16T00:05:00+00:00", "snapshot_dir": "/tmp/b", "classifier_rule": "oom-line", "matched_lines": ["middle"]},
    )
    registry.upsert_incident(
        run_id="run_3",
        job_id="job_newest",
        severity="CRITICAL",
        category="RESOURCE_OOM",
        fingerprint="oom-3",
        evidence={"scan_time": "2026-04-16T00:10:00+00:00", "snapshot_dir": "/tmp/c", "classifier_rule": "oom-line", "matched_lines": ["newest"]},
    )
    registry.upsert_incident(
        run_id="run_4",
        job_id="job_high",
        severity="HIGH",
        category="ENV_IMPORT_ERROR",
        fingerprint="import",
        evidence={"scan_time": "2026-04-16T00:15:00+00:00", "snapshot_dir": "/tmp/d", "classifier_rule": "import-error", "matched_lines": ["other"]},
    )

    summary = registry.summarize_open_incidents(limit=2)

    assert [record.job_id for record in summary.top_incidents] == ["job_newest", "job_middle"]
