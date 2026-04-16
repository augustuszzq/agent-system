from pathlib import Path

from autoresearch.db import init_db
from autoresearch.retries.registry import RetryRequestRegistry


def test_create_retry_request_persists_pending_row(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"
    init_db(db_path)
    registry = RetryRequestRegistry(db_path)

    record = registry.create_request(
        incident_id="incident_123",
        source_run_id="run_123",
        source_job_id="job_123",
        source_pbs_job_id="123.polaris",
        requested_action="RETRY_SAME_CONFIG",
    )

    assert record.approval_status == "PENDING"
    assert record.execution_status == "NOT_STARTED"
    assert registry.get(record.retry_request_id) == record
    assert registry.find_active_request("incident_123", "RETRY_SAME_CONFIG") == record


def test_retry_request_state_transitions_and_execution_updates(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"
    init_db(db_path)
    registry = RetryRequestRegistry(db_path)

    record = registry.create_request(
        incident_id="incident_123",
        source_run_id="run_123",
        source_job_id="job_123",
        source_pbs_job_id="123.polaris",
        requested_action="RETRY_SAME_CONFIG",
    )
    approved = registry.approve(record.retry_request_id, actor="operator", reason="ok")
    assert approved.approval_status == "APPROVED"
    assert approved.approved_by == "operator"
    submitted = registry.mark_submitted(
        record.retry_request_id,
        result_run_id="run_retry",
        result_job_id="job_retry",
        result_pbs_job_id="456.polaris",
        executed_at="2026-04-16T00:00:00+00:00",
    )
    assert submitted.execution_status == "SUBMITTED"
    assert submitted.attempt_count == 1
    assert submitted.result_job_id == "job_retry"

