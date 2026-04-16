from pathlib import Path

import pytest

from autoresearch.db import init_db
from autoresearch.retries.registry import RetryRequestRegistry


def test_create_retry_request_persists_pending_row(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"
    init_db(db_path)
    registry = RetryRequestRegistry(db_path)

    record = registry.create_request(
        incident_id="incident_demo",
        source_run_id="run_demo",
        source_job_id="job_demo",
        source_pbs_job_id="123.polaris",
        requested_action="RETRY_SAME_CONFIG",
    )

    assert record.approval_status == "PENDING"
    assert record.execution_status == "NOT_STARTED"
    assert record.attempt_count == 0


def test_reject_retry_request_requires_pending_and_stores_reason(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"
    init_db(db_path)
    registry = RetryRequestRegistry(db_path)

    record = registry.create_request(
        incident_id="incident_demo",
        source_run_id="run_demo",
        source_job_id="job_demo",
        source_pbs_job_id="123.polaris",
        requested_action="RETRY_SAME_CONFIG",
    )

    rejected = registry.reject(
        record.retry_request_id,
        actor="operator",
        reason="not convinced",
    )

    assert rejected.approval_status == "REJECTED"
    assert rejected.approval_reason == "not convinced"


def test_approve_retry_request_requires_pending(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"
    init_db(db_path)
    registry = RetryRequestRegistry(db_path)
    record = registry.create_request(
        incident_id="incident_demo",
        source_run_id="run_demo",
        source_job_id="job_demo",
        source_pbs_job_id="123.polaris",
        requested_action="RETRY_SAME_CONFIG",
    )
    registry.approve(record.retry_request_id, actor="operator", reason="ok")

    with pytest.raises(ValueError, match="pending"):
        registry.approve(record.retry_request_id, actor="operator", reason="again")


def test_find_active_request_by_incident_and_action_ignores_failed_and_submitted(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"
    init_db(db_path)
    registry = RetryRequestRegistry(db_path)
    first = registry.create_request(
        incident_id="incident_demo",
        source_run_id="run_demo",
        source_job_id="job_demo",
        source_pbs_job_id="123.polaris",
        requested_action="RETRY_SAME_CONFIG",
    )
    registry.approve(first.retry_request_id, actor="operator", reason="ok")
    registry.mark_submitted(
        first.retry_request_id,
        result_run_id="run_retry",
        result_job_id="job_retry",
        result_pbs_job_id="456.polaris",
        executed_at="2026-04-16T00:00:00+00:00",
    )

    assert registry.find_active_request("incident_demo", "RETRY_SAME_CONFIG") is None

