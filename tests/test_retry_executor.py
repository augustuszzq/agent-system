from pathlib import Path
import threading
import time

import pytest

from autoresearch.bridge.remote_exec import RemoteBridgeError
from autoresearch.db import init_db
from autoresearch.decisions import DecisionLog
from autoresearch.incidents.registry import IncidentRegistry
from autoresearch.retries.executor import RetryExecutor
from autoresearch.retries.policy import RetryPolicy
from autoresearch.retries.registry import RetryRequestRegistry
from autoresearch.runs.registry import RunRegistry
from autoresearch.schemas import RunCreateRequest
from autoresearch.settings import RetryPolicySettings


class FakeSubmitted:
    def __init__(self, run_id: str, job_id: str, pbs_job_id: str) -> None:
        self.run_id = run_id
        self.job_id = job_id
        self.pbs_job_id = pbs_job_id


def _create_retry_fixture(tmp_path: Path):
    db_path = tmp_path / "state" / "autoresearch.db"
    init_db(db_path)
    run_registry = RunRegistry(db_path)
    run = run_registry.create_run(RunCreateRequest(run_kind="probe", project="ALCF_PROJECT"))
    job = run_registry.create_job(
        run_id=run.run_id,
        backend="pbs",
        queue="debug",
        walltime="00:10:00",
        filesystems="eagle",
        select_expr="1:system=polaris",
        place_expr="scatter",
        submit_script_path="/eagle/demo/jobs/source/submit.pbs",
        stdout_path="/eagle/demo/runs/source/stdout.log",
        stderr_path="/eagle/demo/runs/source/stderr.log",
        pbs_job_id="123.polaris",
    )
    incident = IncidentRegistry(db_path).upsert_incident(
        run_id=run.run_id,
        job_id=job.job_id,
        severity="CRITICAL",
        category="FILESYSTEM_UNAVAILABLE",
        fingerprint="fs-down",
        evidence={"matched_lines": ["filesystem unavailable"]},
    )
    retry_registry = RetryRequestRegistry(db_path)
    request = retry_registry.create_request(
        incident_id=incident.incident_id,
        source_run_id=run.run_id,
        source_job_id=job.job_id,
        source_pbs_job_id=job.pbs_job_id,
        requested_action="RETRY_SAME_CONFIG",
    )
    retry_registry.approve(request.retry_request_id, actor="operator", reason="filesystem recovered")
    return db_path, run, job, incident, request


def _retry_policy() -> RetryPolicy:
    return RetryPolicy(
        RetryPolicySettings(
            safe_retry_categories=("FILESYSTEM_UNAVAILABLE",),
            allowed_actions=("RETRY_SAME_CONFIG",),
        )
    )


def _resolve_incident(db_path: Path, incident_id: str) -> None:
    from autoresearch.db import connect_db

    with connect_db(db_path) as conn:
        conn.execute(
            """
            UPDATE incidents
            SET status = 'RESOLVED',
                resolved_at = '2026-04-16T00:00:00+00:00'
            WHERE incident_id = ?
            """,
            (incident_id,),
        )


def test_execute_retry_marks_request_submitted_and_logs_decision(tmp_path: Path) -> None:
    db_path, _, _, _, request = _create_retry_fixture(tmp_path)

    executor = RetryExecutor(
        db_path=db_path,
        policy=_retry_policy(),
        submitter=lambda **kwargs: FakeSubmitted("run_retry", "job_retry", "456.polaris"),
        actor="operator",
    )

    updated = executor.execute(request.retry_request_id)

    assert updated.execution_status == "SUBMITTED"
    assert updated.result_run_id == "run_retry"
    assert updated.result_job_id == "job_retry"
    assert updated.result_pbs_job_id == "456.polaris"
    assert updated.attempt_count == 1
    decisions = DecisionLog(db_path).list_for_target("retry_request", request.retry_request_id)
    assert len(decisions) == 1
    assert decisions[-1].decision == "execute-approved-retry"
    assert decisions[-1].rationale == "submitted job_retry"


def test_execute_retry_allows_submitter_that_writes_runs_and_jobs(tmp_path: Path) -> None:
    db_path, _, _, _, request = _create_retry_fixture(tmp_path)
    created: dict[str, str] = {}

    def db_writing_submitter(**kwargs):
        registry = RunRegistry(db_path)
        submitted_run = registry.create_run(
            RunCreateRequest(
                run_kind=kwargs["run_kind"],
                project=kwargs["project"],
                notes=kwargs["notes"],
            )
        )
        submitted_job = registry.create_job(
            run_id=submitted_run.run_id,
            backend="pbs",
            queue=kwargs["queue"],
            walltime=kwargs["walltime"],
            filesystems="eagle",
            select_expr="1:system=polaris",
            place_expr="scatter",
            submit_script_path="/eagle/demo/jobs/retry/submit.pbs",
            stdout_path="/eagle/demo/runs/retry/stdout.log",
            stderr_path="/eagle/demo/runs/retry/stderr.log",
            pbs_job_id="789.polaris",
        )
        created["run_id"] = submitted_run.run_id
        created["job_id"] = submitted_job.job_id
        return FakeSubmitted(submitted_run.run_id, submitted_job.job_id, "789.polaris")

    executor = RetryExecutor(
        db_path=db_path,
        policy=_retry_policy(),
        submitter=db_writing_submitter,
        actor="operator",
    )

    updated = executor.execute(request.retry_request_id)

    assert updated.execution_status == "SUBMITTED"
    assert updated.result_run_id == created["run_id"]
    assert updated.result_job_id == created["job_id"]
    assert updated.result_pbs_job_id == "789.polaris"
    decisions = DecisionLog(db_path).list_for_target("retry_request", request.retry_request_id)
    assert len(decisions) == 1


def test_execute_retry_claims_request_before_blocking_submitter_returns(tmp_path: Path) -> None:
    db_path, _, _, _, request = _create_retry_fixture(tmp_path)
    submitter_entered = threading.Event()
    release_submitter = threading.Event()
    results: dict[str, object] = {}

    def blocking_submitter(**kwargs):
        submitter_entered.set()
        if not release_submitter.wait(timeout=2):
            raise AssertionError("blocking submitter was not released")
        return FakeSubmitted("run_retry", "job_retry", "456.polaris")

    executor = RetryExecutor(
        db_path=db_path,
        policy=_retry_policy(),
        submitter=blocking_submitter,
        actor="operator",
    )

    def run_execute() -> None:
        try:
            results["updated"] = executor.execute(request.retry_request_id)
        except Exception as exc:  # noqa: BLE001 - capture unexpected thread failures for assertion
            results["exc"] = exc

    thread = threading.Thread(target=run_execute)
    thread.start()
    assert submitter_entered.wait(timeout=2)

    claimed = RetryRequestRegistry(db_path).get(request.retry_request_id)
    assert claimed.execution_status == "CLAIMED"
    assert claimed.approval_status == "APPROVED"
    assert claimed.attempt_count == 0
    assert claimed.result_run_id is None
    assert claimed.result_job_id is None
    assert claimed.result_pbs_job_id is None
    assert claimed.executed_at is None

    release_submitter.set()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert "exc" not in results
    assert results["updated"].execution_status == "SUBMITTED"


def test_execute_retry_blocks_duplicate_execution_before_second_submitter_call(tmp_path: Path) -> None:
    db_path, _, _, _, request = _create_retry_fixture(tmp_path)
    first_entered = threading.Event()
    release_first = threading.Event()
    second_submitter_called = threading.Event()
    results: dict[str, object] = {}

    def first_submitter(**kwargs):
        first_entered.set()
        if not release_first.wait(timeout=2):
            raise AssertionError("first submitter was not released")
        return FakeSubmitted("run_retry", "job_retry", "456.polaris")

    def second_submitter(**kwargs):
        second_submitter_called.set()
        return FakeSubmitted("run_retry_2", "job_retry_2", "789.polaris")

    first_executor = RetryExecutor(
        db_path=db_path,
        policy=_retry_policy(),
        submitter=first_submitter,
        actor="operator",
    )
    second_executor = RetryExecutor(
        db_path=db_path,
        policy=_retry_policy(),
        submitter=second_submitter,
        actor="operator",
    )

    def run_first() -> None:
        results["first"] = first_executor.execute(request.retry_request_id)

    def run_second() -> None:
        try:
            results["second"] = second_executor.execute(request.retry_request_id)
        except Exception as exc:  # noqa: BLE001 - capture the concurrent failure for assertion
            results["second_exc"] = exc

    first_thread = threading.Thread(target=run_first)
    second_thread = threading.Thread(target=run_second)
    first_thread.start()
    assert first_entered.wait(timeout=2)

    claimed = RetryRequestRegistry(db_path).get(request.retry_request_id)
    assert claimed.execution_status == "CLAIMED"
    assert claimed.attempt_count == 0
    assert claimed.result_run_id is None
    assert claimed.result_job_id is None
    assert claimed.result_pbs_job_id is None
    assert claimed.executed_at is None

    second_thread.start()
    time.sleep(0.2)
    assert not second_submitter_called.is_set()

    release_first.set()
    first_thread.join(timeout=2)
    second_thread.join(timeout=2)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert results["first"].execution_status == "SUBMITTED"
    assert results["first"].attempt_count == 1
    assert isinstance(results["second_exc"], ValueError)
    assert "approved and not started" in str(results["second_exc"])
    assert not second_submitter_called.is_set()


def test_execute_retry_marks_request_failed_for_remote_bridge_errors(tmp_path: Path) -> None:
    db_path, _, _, _, request = _create_retry_fixture(tmp_path)

    executor = RetryExecutor(
        db_path=db_path,
        policy=_retry_policy(),
        submitter=lambda **kwargs: (_ for _ in ()).throw(RemoteBridgeError("bridge detached")),
        actor="operator",
    )

    updated = executor.execute(request.retry_request_id)

    assert updated.execution_status == "FAILED"
    assert updated.last_error == "bridge detached"
    assert updated.result_job_id is None
    assert updated.attempt_count == 0


def test_execute_retry_rejects_closed_incidents_before_submit(tmp_path: Path) -> None:
    db_path, _, _, incident, request = _create_retry_fixture(tmp_path)
    _resolve_incident(db_path, incident.incident_id)

    submitter_called = threading.Event()

    executor = RetryExecutor(
        db_path=db_path,
        policy=_retry_policy(),
        submitter=lambda **kwargs: submitter_called.set(),
        actor="operator",
    )

    with pytest.raises(ValueError, match="source incident must be open"):
        executor.execute(request.retry_request_id)

    assert not submitter_called.is_set()


def test_execute_retry_rejects_non_probe_source_runs_before_submit(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"
    init_db(db_path)
    run_registry = RunRegistry(db_path)
    run = run_registry.create_run(RunCreateRequest(run_kind="analysis", project="ALCF_PROJECT"))
    job = run_registry.create_job(
        run_id=run.run_id,
        backend="pbs",
        queue="debug",
        walltime="00:10:00",
        filesystems="eagle",
        select_expr="1:system=polaris",
        place_expr="scatter",
        submit_script_path="/eagle/demo/jobs/source/submit.pbs",
        stdout_path="/eagle/demo/runs/source/stdout.log",
        stderr_path="/eagle/demo/runs/source/stderr.log",
        pbs_job_id="123.polaris",
    )
    incident = IncidentRegistry(db_path).upsert_incident(
        run_id=run.run_id,
        job_id=job.job_id,
        severity="CRITICAL",
        category="FILESYSTEM_UNAVAILABLE",
        fingerprint="fs-down",
        evidence={"matched_lines": ["filesystem unavailable"]},
    )
    retry_registry = RetryRequestRegistry(db_path)
    request = retry_registry.create_request(
        incident_id=incident.incident_id,
        source_run_id=run.run_id,
        source_job_id=job.job_id,
        source_pbs_job_id=job.pbs_job_id,
        requested_action="RETRY_SAME_CONFIG",
    )
    retry_registry.approve(request.retry_request_id, actor="operator", reason="filesystem recovered")

    submitter_called = threading.Event()

    executor = RetryExecutor(
        db_path=db_path,
        policy=_retry_policy(),
        submitter=lambda **kwargs: submitter_called.set(),
        actor="operator",
    )

    with pytest.raises(ValueError, match="only probe runs are retryable in phase4b"):
        executor.execute(request.retry_request_id)

    assert not submitter_called.is_set()


def test_execute_retry_propagates_unexpected_submitter_errors(tmp_path: Path) -> None:
    db_path, _, _, _, request = _create_retry_fixture(tmp_path)

    executor = RetryExecutor(
        db_path=db_path,
        policy=_retry_policy(),
        submitter=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("bug")),
        actor="operator",
    )

    with pytest.raises(RuntimeError, match="bug"):
        executor.execute(request.retry_request_id)

    updated = RetryRequestRegistry(db_path).get(request.retry_request_id)
    assert updated.execution_status == "FAILED"
    assert updated.last_error == "bug"


def test_execute_retry_rejects_mismatched_source_linkage_before_submit(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"
    init_db(db_path)
    run_registry = RunRegistry(db_path)
    source_run = run_registry.create_run(RunCreateRequest(run_kind="probe", project="ALCF_PROJECT"))
    source_job = run_registry.create_job(
        run_id=source_run.run_id,
        backend="pbs",
        queue="debug",
        walltime="00:10:00",
        filesystems="eagle",
        select_expr="1:system=polaris",
        place_expr="scatter",
        submit_script_path="/eagle/demo/jobs/source/submit.pbs",
        stdout_path="/eagle/demo/runs/source/stdout.log",
        stderr_path="/eagle/demo/runs/source/stderr.log",
        pbs_job_id="123.polaris",
    )
    other_run = run_registry.create_run(RunCreateRequest(run_kind="probe", project="ALCF_PROJECT"))
    other_job = run_registry.create_job(
        run_id=other_run.run_id,
        backend="pbs",
        queue="debug",
        walltime="00:10:00",
        filesystems="eagle",
        select_expr="1:system=polaris",
        place_expr="scatter",
        submit_script_path="/eagle/demo/jobs/other/submit.pbs",
        stdout_path="/eagle/demo/runs/other/stdout.log",
        stderr_path="/eagle/demo/runs/other/stderr.log",
        pbs_job_id="456.polaris",
    )
    incident = IncidentRegistry(db_path).upsert_incident(
        run_id=source_run.run_id,
        job_id=source_job.job_id,
        severity="CRITICAL",
        category="FILESYSTEM_UNAVAILABLE",
        fingerprint="fs-down",
        evidence={"matched_lines": ["filesystem unavailable"]},
    )
    retry_registry = RetryRequestRegistry(db_path)
    request = retry_registry.create_request(
        incident_id=incident.incident_id,
        source_run_id=other_run.run_id,
        source_job_id=source_job.job_id,
        source_pbs_job_id=source_job.pbs_job_id,
        requested_action="RETRY_SAME_CONFIG",
    )
    retry_registry.approve(request.retry_request_id, actor="operator", reason="filesystem recovered")

    submitter_called = threading.Event()

    executor = RetryExecutor(
        db_path=db_path,
        policy=_retry_policy(),
        submitter=lambda **kwargs: (submitter_called.set(), FakeSubmitted("run_retry", "job_retry", "456.polaris"))[1],
        actor="operator",
    )

    with pytest.raises(ValueError, match="source run/job linkage is inconsistent"):
        executor.execute(request.retry_request_id)

    assert not submitter_called.is_set()
