from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from autoresearch.decisions import DecisionLog
from autoresearch.incidents.registry import IncidentRegistry
from autoresearch.retries.policy import RetryPolicy
from autoresearch.retries.registry import RetryRequestRecord, RetryRequestRegistry
from autoresearch.runs.registry import RunRegistry
from autoresearch.schemas import RetryAction


class RetryExecutor:
    def __init__(
        self,
        *,
        db_path: Path,
        policy: RetryPolicy,
        actor: str,
        submitter: Callable[..., object],
    ) -> None:
        self._db_path = db_path
        self._policy = policy
        self._actor = actor
        self._submitter = submitter
        self._retry_registry = RetryRequestRegistry(db_path)
        self._incident_registry = IncidentRegistry(db_path)
        self._run_registry = RunRegistry(db_path)
        self._decision_log = DecisionLog(db_path)

    def execute(self, retry_request_id: str) -> RetryRequestRecord:
        retry_request = self._retry_registry.get(retry_request_id)
        self._require_executable(retry_request)

        incident = self._incident_registry.get_incident(retry_request.incident_id)
        if not self._policy.allows(
            category=incident.category,
            action=retry_request.requested_action,  # type: ignore[arg-type]
        ):
            raise ValueError("retry request is no longer eligible")
        if incident.run_id is None or incident.job_id is None:
            raise ValueError("incident is missing source run/job linkage")

        source_run = self._run_registry.get_run(incident.run_id)
        source_job = self._run_registry.get_job(incident.job_id)
        if source_run.run_kind != "probe" or source_job.backend != "pbs":
            raise ValueError("retry execution only supports probe jobs")

        notes = (
            f"retry_request={retry_request.retry_request_id} "
            f"source_incident={incident.incident_id} "
            f"source_job={source_job.job_id}"
        )
        try:
            submitted = self._submitter(
                run_kind="probe-retry",
                project=source_run.project,
                queue=source_job.queue,
                walltime=source_job.walltime,
                notes=notes,
            )
        except Exception as exc:
            self._retry_registry.mark_failed(
                retry_request.retry_request_id,
                error_text=str(exc),
            )
            raise

        submitted_run_id, submitted_job_id, submitted_pbs_job_id = self._normalize_submission(
            submitted
        )
        record = self._retry_registry.mark_submitted(
            retry_request.retry_request_id,
            result_run_id=submitted_run_id,
            result_job_id=submitted_job_id,
            result_pbs_job_id=submitted_pbs_job_id,
            executed_at=datetime.now(UTC).isoformat(),
        )
        self._decision_log.append(
            target_type="retry_request",
            target_id=record.retry_request_id,
            decision="execute-retry",
            rationale=notes,
            actor=self._actor,
        )
        return record

    @staticmethod
    def _require_executable(retry_request: RetryRequestRecord) -> None:
        if retry_request.approval_status != "APPROVED" or retry_request.execution_status != "NOT_STARTED":
            raise ValueError("retry request must be approved and not started")

    @staticmethod
    def _normalize_submission(submitted: object) -> tuple[str, str, str]:
        if isinstance(submitted, tuple) and len(submitted) == 3:
            run_id, job_id, pbs_job_id = submitted
            return str(run_id), str(job_id), str(pbs_job_id)
        run_id = getattr(submitted, "run_id")
        job_id = getattr(submitted, "job_id")
        pbs_job_id = getattr(submitted, "pbs_job_id")
        return str(run_id), str(job_id), str(pbs_job_id)

