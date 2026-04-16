from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path

import autoresearch.reports.daily as daily_module
from autoresearch.db import connect_db, init_db
from autoresearch.reports.daily import DailyReportBuilder


REPORT_DATE = "2026-04-16"
GENERATED_AT = datetime(2026, 4, 16, 12, 0, tzinfo=UTC)


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
                    "2026-04-15T22:30:00+00:00",
                    "2026-04-15T23:30:00+00:00",
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
                    "2026-04-15T22:00:00+00:00",
                    "2026-04-15T22:45:00+00:00",
                    "FAILED",
                    None,
                    0,
                    None,
                    None,
                    None,
                    None,
                ),
                (
                    "run_finished_outside_window",
                    "probe",
                    "demo",
                    "2026-04-15T23:00:00+00:00",
                    "2026-04-15T06:00:00+00:00",
                    "2026-04-15T06:30:00+00:00",
                    "SUCCEEDED",
                    None,
                    0,
                    None,
                    None,
                    None,
                    None,
                ),
                (
                    "run_failed_outside_window",
                    "probe",
                    "demo",
                    "2026-04-15T23:10:00+00:00",
                    "2026-04-15T07:00:00+00:00",
                    "2026-04-15T07:30:00+00:00",
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
                            "qstat_comment": "filesystem unavailable",
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
                            "matched_lines": ["out of memory", "memory limit reached"],
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
                (
                    "incident_fallback",
                    "run_failed_outside_window",
                    "job_fallback",
                    "CRITICAL",
                    "ENV_IMPORT_ERROR",
                    "fp-fallback",
                    json.dumps(
                        {
                            "scan_time": "2026-04-16T04:30:00+00:00",
                            "snapshot_dir": "/tmp/fallback",
                            "job_state": "F",
                            "classifier_rule": "import_error",
                            "matched_lines": [],
                        },
                        sort_keys=True,
                    ),
                    None,
                    "OPEN",
                    "2026-04-16T04:30:00+00:00",
                    "2026-04-16T04:30:00+00:00",
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
                "2026-04-15T23:00:00+00:00",
                "2026-04-15T23:05:00+00:00",
                "2026-04-15T23:05:00+00:00",
            )
        ]
        retry_rows.append(
            (
                "retry_submitted_outside_window",
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
                "run_retry2",
                "job_retry2",
                "pbs_retry2",
                "2026-04-15T06:00:00+00:00",
                "2026-04-15T06:05:00+00:00",
                "2026-04-15T06:05:00+00:00",
            )
        )
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

    result = builder.build(report_date=REPORT_DATE, generated_at=GENERATED_AT)

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

    result = builder.build(report_date=REPORT_DATE, generated_at=GENERATED_AT)

    assert "- Active runs: 1" in result.markdown
    assert "- Finished overnight: 1" in result.markdown
    assert "- Failed: 1" in result.markdown
    assert "- Auto-retried: 1" in result.markdown
    assert "- Auto-retried: 2" not in result.markdown
    assert "- Awaiting approval: 2" in result.markdown
    assert "- Open incidents: 3" in result.markdown
    assert "- ENV_IMPORT_ERROR: 1" in result.markdown
    assert "- FILESYSTEM_UNAVAILABLE: 1" in result.markdown
    assert "- RESOURCE_OOM: 1" in result.markdown
    assert "1. Approve retry retry_pending_1 for incident incident_oom" in result.markdown
    assert "2. Approve retry retry_pending_2 for incident incident_filesystem" in result.markdown
    assert "incident_fallback | ENV_IMPORT_ERROR | CRITICAL | run_failed_outside_window | job_fallback" in result.markdown
    assert "Evidence: filesystem unavailable" in result.markdown
    assert "incident_oom | RESOURCE_OOM | MEDIUM | run_active | job_active" in result.markdown
    assert "Evidence: out of memory" in result.markdown
    assert "incident_filesystem | FILESYSTEM_UNAVAILABLE | HIGH | run_failed | job_failed" in result.markdown
    assert "Evidence: evidence unavailable" in result.markdown


def test_build_daily_report_limits_pending_decisions_to_three_oldest_requests(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"
    init_db(db_path)
    _seed_daily_report_state(db_path, pending_requests=4)

    builder = DailyReportBuilder(db_path=db_path, state_dir=tmp_path / "state")

    result = builder.build(report_date=REPORT_DATE, generated_at=GENERATED_AT)

    assert "- Awaiting approval: 4" in result.markdown
    assert "1. Approve retry retry_pending_1 for incident incident_oom" in result.markdown
    assert "2. Approve retry retry_pending_2 for incident incident_filesystem" in result.markdown
    assert "3. Approve retry retry_pending_3 for incident incident_oom" in result.markdown
    assert "4. Approve retry retry_pending_4" not in result.markdown


def test_build_daily_report_uses_single_db_connection_snapshot(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"
    init_db(db_path)
    _seed_daily_report_state(db_path)

    builder = DailyReportBuilder(db_path=db_path, state_dir=tmp_path / "state")
    connect_calls = 0
    real_connect_db = daily_module.connect_db

    @contextmanager
    def tracking_connect_db(path: Path):
        nonlocal connect_calls
        connect_calls += 1
        with real_connect_db(path) as conn:
            yield conn

    monkeypatch.setattr(daily_module, "connect_db", tracking_connect_db)

    result = builder.build(report_date=REPORT_DATE, generated_at=GENERATED_AT)

    assert connect_calls == 1
    assert result.markdown


def test_build_daily_report_begins_read_transaction_before_selects(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"
    init_db(db_path)
    _seed_daily_report_state(db_path)

    builder = DailyReportBuilder(db_path=db_path, state_dir=tmp_path / "state")
    calls: list[str] = []
    real_connect_db = daily_module.connect_db

    class ConnectionProxy:
        def __init__(self, conn):
            self._conn = conn

        def execute(self, sql, params=()):
            calls.append(sql.strip().split()[0].upper())
            return self._conn.execute(sql, params)

        def __getattr__(self, name):
            return getattr(self._conn, name)

    @contextmanager
    def tracking_connect_db(path: Path):
        with real_connect_db(path) as conn:
            yield ConnectionProxy(conn)

    monkeypatch.setattr(daily_module, "connect_db", tracking_connect_db)

    result = builder.build(report_date=REPORT_DATE, generated_at=GENERATED_AT)

    assert calls[0] == "BEGIN"
    assert "SELECT" in calls[1:]
    assert result.markdown
