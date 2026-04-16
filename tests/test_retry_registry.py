from pathlib import Path

import pytest

from autoresearch.db import init_db
from autoresearch.db import connect_db
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


def test_get_and_list_requests_return_created_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"
    init_db(db_path)
    registry = RetryRequestRegistry(db_path)

    created = registry.create_request(
        incident_id="incident_demo",
        source_run_id="run_demo",
        source_job_id="job_demo",
        source_pbs_job_id="123.polaris",
        requested_action="RETRY_SAME_CONFIG",
    )

    assert registry.get(created.retry_request_id) == created
    assert registry.list_requests() == [created]


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


def test_create_retry_request_rejects_duplicate_active_request(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"
    init_db(db_path)
    registry = RetryRequestRegistry(db_path)

    registry.create_request(
        incident_id="incident_demo",
        source_run_id="run_demo",
        source_job_id="job_demo",
        source_pbs_job_id="123.polaris",
        requested_action="RETRY_SAME_CONFIG",
    )

    with pytest.raises(ValueError, match="already exists"):
        registry.create_request(
            incident_id="incident_demo",
            source_run_id="run_demo_2",
            source_job_id="job_demo_2",
            source_pbs_job_id="456.polaris",
            requested_action="RETRY_SAME_CONFIG",
        )


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


def test_claim_execution_sets_claimed_state(tmp_path: Path) -> None:
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

    with connect_db(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        claimed = registry.claim_execution(conn, record.retry_request_id)

    assert claimed.execution_status == "CLAIMED"
    assert registry.get(record.retry_request_id).execution_status == "CLAIMED"


def test_mark_submitted_requires_claimed_state(tmp_path: Path) -> None:
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

    with pytest.raises(ValueError, match="claimed"):
        registry.mark_submitted(
            record.retry_request_id,
            result_run_id="run_retry",
            result_job_id="job_retry",
            result_pbs_job_id="456.polaris",
            executed_at="2026-04-16T00:00:00+00:00",
        )


def test_mark_failed_rejects_not_started_state(tmp_path: Path) -> None:
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

    with pytest.raises(ValueError, match="claimed"):
        registry.mark_failed(record.retry_request_id, error_text="boom")


def test_find_active_request_includes_claimed_rows(tmp_path: Path) -> None:
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

    with connect_db(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        registry.claim_execution(conn, record.retry_request_id)

    active = registry.find_active_request("incident_demo", "RETRY_SAME_CONFIG")

    assert active is not None
    assert active.execution_status == "CLAIMED"


def test_create_retry_request_rejects_duplicate_request_while_claimed(tmp_path: Path) -> None:
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

    with connect_db(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        registry.claim_execution(conn, first.retry_request_id)

    with pytest.raises(ValueError, match="already exists"):
        registry.create_request(
            incident_id="incident_demo",
            source_run_id="run_demo_2",
            source_job_id="job_demo_2",
            source_pbs_job_id="456.polaris",
            requested_action="RETRY_SAME_CONFIG",
        )


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

    with connect_db(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        registry.claim_execution(conn, first.retry_request_id)

    registry.mark_submitted(
        first.retry_request_id,
        result_run_id="run_retry",
        result_job_id="job_retry",
        result_pbs_job_id="456.polaris",
        executed_at="2026-04-16T00:00:00+00:00",
    )

    assert registry.find_active_request("incident_demo", "RETRY_SAME_CONFIG") is None
