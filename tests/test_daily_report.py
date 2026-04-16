from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path

from autoresearch.db import connect_db, init_db
from autoresearch.reports.daily import DailyReportBuilder


REPORT_DATE = "2026-04-16"


def _seed_daily_report_state(db_path: Path, *, pending_requests: int = 2) -> None:
    with connect_db(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO runs (
                run_id, run_kind, project, created_at, started_at, ended_at,
                status, git_commit, git_dirty, local_cmd, remote_cmd,
                working_dir, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "run_active",
                    "probe",
                    "demo",
                    "2026-04-15T22:00:00+00:00",
                    "2026-04-16T00:30:00+00:00",
                    None,
                    "RUNNING",
                    None,
                    0,
                    None,
                    None,
                    None,
                    None,
                ),
                (
                    "run_finished",
                    "probe",
                    "demo",
                    "2026-04-15T20:00:00+00:00",
                    "2026-04-16T01:00:00+00:00",
                    "2026-04-16T03:00:00+00:00",
                    "SUCCEEDED",
                    None,
                    0,
                    None,
                    None,
                    None,
                    None,
                ),
                (
                    "run_failed",
                    "probe",
                    "demo",
                    "2026-04-15T21:00:00+00:00",
                    "2026-04-16T02:00:00+00:00",
                    "2026-04-16T04:00:00+00:00",
                    "FAILED",
                    None,
                    0,
                    None,
                    None,
                    None,
                    None,
                ),
            ],
        )

        conn.executemany(
            """
            INSERT INTO incidents (
                incident_id, run_id, job_id, severity, category, fingerprint,
                evidence_json, auto_action, status, created_at, updated_at,
                resolved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "incident_filesystem",
                    "run_failed",
                    "job_failed",
                    "HIGH",
                    "FILESYSTEM_UNAVAILABLE",
                    "fp-filesystem",
                    json.dumps(
                        {
                            "scan_time": "2026-04-16T02:30:00+00:00",
                            "snapshot_dir": "/tmp/filesystem",
                            "job_state": "F",
                            "classifier_rule": "filesystem_unavailable",
                        },
                        sort_keys=True,
                    ),
                    None,
                    "OPEN",
                    "2026-04-16T02:30:00+00:00",
                    "2026-04-16T02:30:00+00:00",
                    None,
                ),
                (
                    "incident_oom",
                    "run_active",
                    "job_active",
                    "MEDIUM",
                    "RESOURCE_OOM",
                    "fp-oom",
                    json.dumps(
                        {
                            "scan_time": "2026-04-16T03:30:00+00:00",
                            "snapshot_dir": "/tmp/oom",
                            "job_state": "F",
                            "classifier_rule": "resource_oom",
                        },
                        sort_keys=True,
                    ),
                    None,
                    "OPEN",
                    "2026-04-16T03:30:00+00:00",
                    "2026-04-16T03:30:00+00:00",
                    None,
                ),
            ],
        )

        retry_rows = [
            (
                "retry_submitted",
                "incident_filesystem",
                "run_failed",
                "job_failed",
                "pbs_failed",
                "RETRY_SAME_CONFIG",
                "APPROVED",
                "SUBMITTED",
                1,
                "auto",
                "approved",
                None,
                "run_retry",
                "job_retry",
                "pbs_retry",
                "2026-04-16T05:00:00+00:00",
                "2026-04-16T05:05:00+00:00",
                "2026-04-16T05:05:00+00:00",
            )
        ]
        base_time = datetime(2026, 4, 16, 6, 0, tzinfo=UTC)
        for index in range(pending_requests):
            created_at = (base_time + timedelta(minutes=index)).isoformat()
            retry_rows.append(
                (
                    f"retry_pending_{index + 1}",
                    "incident_oom" if index % 2 == 0 else "incident_filesystem",
                    "run_active",
                    "job_active",
                    "pbs_active",
                    "RETRY_SAME_CONFIG",
                    "PENDING",
                    "NOT_STARTED",
                    0,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    created_at,
                    created_at,
                    None,
                )
            )

        conn.executemany(
            """
            INSERT INTO retry_requests (
                retry_request_id, incident_id, source_run_id, source_job_id,
                source_pbs_job_id, requested_action, approval_status,
                execution_status, attempt_count, approved_by,
                approval_reason, last_error, result_run_id, result_job_id,
                result_pbs_job_id, created_at, updated_at, executed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            retry_rows,
        )


def test_build_daily_report_uses_paper_fallback_when_papers_are_unavailable(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"
    init_db(db_path)

    builder = DailyReportBuilder(db_path=db_path, state_dir=tmp_path / "state")

    result = builder.build(report_date=REPORT_DATE)

    assert result.report_date == REPORT_DATE
    assert result.output_path == tmp_path / "state" / "reports" / "daily" / f"{REPORT_DATE}.md"
    assert "# Daily Brief 2026-04-16" in result.markdown
    assert "## Paper Delta" in result.markdown
    assert "New papers scanned: not available yet" in result.markdown
    assert "Top relevant: not available yet" in result.markdown
    assert "Deep read today: not available yet" in result.markdown
    assert "Reproduce candidate: not available yet" in result.markdown


def test_build_daily_report_summarizes_runs_incidents_and_pending_retries(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"
    init_db(db_path)
    _seed_daily_report_state(db_path)

    builder = DailyReportBuilder(db_path=db_path, state_dir=tmp_path / "state")

    result = builder.build(report_date=REPORT_DATE)

    assert "- Active runs: 1" in result.markdown
    assert "- Finished overnight: 1" in result.markdown
    assert "- Failed: 1" in result.markdown
    assert "- Auto-retried: 1" in result.markdown
    assert "- Awaiting approval: 2" in result.markdown
    assert "- FILESYSTEM_UNAVAILABLE: 1" in result.markdown
    assert "- RESOURCE_OOM: 1" in result.markdown
    assert "1. Approve retry retry_pending_1 for incident incident_oom" in result.markdown
    assert "2. Approve retry retry_pending_2 for incident incident_filesystem" in result.markdown


def test_build_daily_report_limits_pending_decisions_to_three_oldest_requests(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"
    init_db(db_path)
    _seed_daily_report_state(db_path, pending_requests=4)

    builder = DailyReportBuilder(db_path=db_path, state_dir=tmp_path / "state")

    result = builder.build(report_date=REPORT_DATE)

    assert "- Awaiting approval: 4" in result.markdown
    assert "1. Approve retry retry_pending_1 for incident incident_oom" in result.markdown
    assert "2. Approve retry retry_pending_2 for incident incident_filesystem" in result.markdown
    assert "3. Approve retry retry_pending_3 for incident incident_oom" in result.markdown
    assert "4. Approve retry retry_pending_4" not in result.markdown
