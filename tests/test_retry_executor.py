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


def _seed_retry_request(db_path: Path) -> str:
    run_registry = RunRegistry(db_path)
    run = run_registry.create_run(RunCreateRequest(run_kind="probe", project="demo"))
    job = run_registry.create_job(
        run_id=run.run_id,
        backend="pbs",
        queue="debug",
        walltime="00:10:00",
        filesystems="eagle",
        select_expr="1:system=polaris",
        place_expr="scatter",
        submit_script_path="/tmp/submit.pbs",
        stdout_path="/tmp/stdout.log",
        stderr_path="/tmp/stderr.log",
        pbs_job_id="123.polaris",
    )
    incident = IncidentRegistry(db_path).upsert_incident(
        run_id=run.run_id,
        job_id=job.job_id,
        severity="HIGH",
        category="FILESYSTEM_UNAVAILABLE",
        fingerprint="fp",
        evidence={
            "scan_time": "2026-04-16T00:00:00+00:00",
            "snapshot_dir": "/tmp/snapshot",
            "qstat_comment": "filesystem unavailable",
            "job_state": "F",
            "exec_host": "node01",
            "matched_lines": ["filesystem unavailable"],
            "classifier_rule": "filesystem_unavailable",
        },
    )
    retry_registry = RetryRequestRegistry(db_path)
    request = retry_registry.create_request(
        incident_id=incident.incident_id,
        source_run_id=run.run_id,
        source_job_id=job.job_id,
        source_pbs_job_id=job.pbs_job_id,
        requested_action="RETRY_SAME_CONFIG",
    )
    retry_registry.approve(request.retry_request_id, actor="operator", reason="ok")
    DecisionLog(db_path).append(
        target_type="retry_request",
        target_id=request.retry_request_id,
        decision="approve-retry",
        rationale="ok",
        actor="operator",
    )
    return request.retry_request_id


def test_execute_retry_marks_request_submitted_and_logs_decision(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"
    init_db(db_path)
    retry_request_id = _seed_retry_request(db_path)
    calls: list[dict[str, object]] = []

    def submitter(**kwargs):
        calls.append(kwargs)
        return ("run_retry", "job_retry", "456.polaris")

    executor = RetryExecutor(
        db_path=db_path,
        policy=RetryPolicy(
            RetryPolicySettings(
                safe_retry_categories=("FILESYSTEM_UNAVAILABLE",),
                allowed_actions=("RETRY_SAME_CONFIG",),
            )
        ),
        actor="operator",
        submitter=submitter,
    )

    record = executor.execute(retry_request_id)

    assert calls[0]["run_kind"] == "probe-retry"
    assert record.execution_status == "SUBMITTED"
    assert record.result_run_id == "run_retry"
    assert record.attempt_count == 1
    assert [
        row.decision for row in DecisionLog(db_path).list_for_target("retry_request", retry_request_id)
    ] == ["approve-retry", "execute-retry"]


def test_execute_retry_marks_failure_when_submission_raises(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"
    init_db(db_path)
    retry_request_id = _seed_retry_request(db_path)

    def submitter(**kwargs):
        raise RuntimeError("qsub failed")

    executor = RetryExecutor(
        db_path=db_path,
        policy=RetryPolicy(
            RetryPolicySettings(
                safe_retry_categories=("FILESYSTEM_UNAVAILABLE",),
                allowed_actions=("RETRY_SAME_CONFIG",),
            )
        ),
        actor="operator",
        submitter=submitter,
    )

    try:
        executor.execute(retry_request_id)
    except RuntimeError:
        pass

    record = RetryRequestRegistry(db_path).get(retry_request_id)
    assert record.execution_status == "FAILED"
    assert record.last_error == "qsub failed"
