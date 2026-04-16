from pathlib import Path

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


def test_execute_retry_marks_request_submitted_and_logs_decision(tmp_path: Path) -> None:
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

    executor = RetryExecutor(
        db_path=db_path,
        policy=RetryPolicy(
            RetryPolicySettings(
                safe_retry_categories=("FILESYSTEM_UNAVAILABLE",),
                allowed_actions=("RETRY_SAME_CONFIG",),
            )
        ),
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
    assert decisions[-1].decision == "execute-approved-retry"


def test_execute_retry_marks_request_failed_when_submitter_raises(tmp_path: Path) -> None:
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

    executor = RetryExecutor(
        db_path=db_path,
        policy=RetryPolicy(
            RetryPolicySettings(
                safe_retry_categories=("FILESYSTEM_UNAVAILABLE",),
                allowed_actions=("RETRY_SAME_CONFIG",),
            )
        ),
        submitter=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("bridge detached")),
        actor="operator",
    )

    updated = executor.execute(request.retry_request_id)

    assert updated.execution_status == "FAILED"
    assert updated.last_error == "bridge detached"
    assert updated.result_job_id is None
    assert updated.attempt_count == 0
