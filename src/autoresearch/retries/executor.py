from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from autoresearch.decisions import DecisionLog
from autoresearch.incidents.registry import IncidentRegistry
from autoresearch.retries.policy import RetryPolicy
from autoresearch.retries.registry import RetryRequestRegistry, RetryRequestRecord
from autoresearch.runs.registry import RunRegistry


class RetryExecutor:
    def __init__(self, *, db_path: Path, policy: RetryPolicy, submitter, actor: str = "operator") -> None:
        self._db_path = db_path
        self._policy = policy
        self._submitter = submitter
        self._actor = actor
        self._retry_registry = RetryRequestRegistry(db_path)
        self._incident_registry = IncidentRegistry(db_path)
        self._run_registry = RunRegistry(db_path)
        self._decision_log = DecisionLog(db_path)

    def execute(self, retry_request_id: str) -> RetryRequestRecord:
        request = self._retry_registry.get(retry_request_id)
        if request.approval_status != "APPROVED" or request.execution_status != "NOT_STARTED":
            raise ValueError("retry request must be approved and not started")

        incident = self._incident_registry.get_incident(request.incident_id)
        if incident.status != "OPEN":
            raise ValueError("retry request source incident must be open")

        if not self._policy.allows(category=incident.category, action=request.requested_action):
            raise ValueError("retry request category is not eligible")

        source_run = self._run_registry.get_run(request.source_run_id) if request.source_run_id else None
        if source_run is None:
            raise ValueError("retry request source run is required")
        if source_run.run_kind != "probe":
            raise ValueError("only probe runs are retryable in phase4b")

        source_job = self._run_registry.get_job(request.source_job_id) if request.source_job_id else None
        if source_job is None:
            raise ValueError("retry request source job is required")

        notes = (
            f"source_incident={incident.incident_id}\n"
            f"source_job={source_job.job_id}\n"
            f"retry_request={request.retry_request_id}"
        )

        try:
            submitted = self._submitter(
                run_kind="probe-retry",
                notes=notes,
                project=source_run.project,
                queue=source_job.queue,
                walltime=source_job.walltime,
            )
        except Exception as exc:
            return self._retry_registry.mark_failed(retry_request_id, error_text=str(exc))

        updated = self._retry_registry.mark_submitted(
            retry_request_id,
            result_run_id=submitted.run_id,
            result_job_id=submitted.job_id,
            result_pbs_job_id=submitted.pbs_job_id,
            executed_at=datetime.now(UTC).isoformat(),
        )
        self._decision_log.append(
            target_type="retry_request",
            target_id=retry_request_id,
            decision="execute-approved-retry",
            rationale=f"submitted {submitted.job_id}",
            actor=self._actor,
        )
        return updated
