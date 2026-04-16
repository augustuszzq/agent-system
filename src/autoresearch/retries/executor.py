from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import sqlite3

from autoresearch.bridge.remote_exec import RemoteBridgeError
from autoresearch.db import connect_db
from autoresearch.decisions import DecisionLog
from autoresearch.retries.policy import RetryPolicy
from autoresearch.retries.registry import RetryRequestRecord, RetryRequestRegistry


class RetryExecutor:
    def __init__(self, *, db_path: Path, policy: RetryPolicy, submitter, actor: str = "operator") -> None:
        self._db_path = db_path
        self._policy = policy
        self._submitter = submitter
        self._actor = actor
        self._retry_registry = RetryRequestRegistry(db_path)
        self._decision_log = DecisionLog(db_path)

    def execute(self, retry_request_id: str) -> RetryRequestRecord:
        request, incident, source_run, source_job, notes = self._prepare_execution(retry_request_id)

        try:
            submitted = self._submitter(
                run_kind="probe-retry",
                notes=notes,
                project=source_run["project"],
                queue=source_job["queue"],
                walltime=source_job["walltime"],
            )
        except RemoteBridgeError as exc:
            return self._mark_failed(retry_request_id, str(exc))
        except Exception as exc:
            self._mark_failed(retry_request_id, str(exc))
            raise

        return self._finalize_success(retry_request_id, submitted)

    def _prepare_execution(
        self, retry_request_id: str
    ) -> tuple[RetryRequestRecord, sqlite3.Row, sqlite3.Row, sqlite3.Row, str]:
        with connect_db(self._db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            request = self._retry_registry.load_for_execution(conn, retry_request_id)
            if request.approval_status != "APPROVED" or request.execution_status != "NOT_STARTED":
                raise ValueError("retry request must be approved and not started")

            incident = self._fetch_incident(conn, request.incident_id)
            source_run = self._fetch_run(conn, request.source_run_id)
            source_job = self._fetch_job(conn, request.source_job_id)
            self._validate_retry_context(request, incident, source_run, source_job)

            if incident["status"] != "OPEN":
                raise ValueError("retry request source incident must be open")
            if source_run["run_kind"] != "probe":
                raise ValueError("only probe runs are retryable in phase4b")
            if not self._policy.allows(category=incident["category"], action=request.requested_action):
                raise ValueError("retry request category is not eligible")

            request = self._retry_registry.claim_execution(conn, retry_request_id)
            notes = (
                f"source_incident={incident['incident_id']}\n"
                f"source_job={source_job['job_id']}\n"
                f"retry_request={request.retry_request_id}"
            )
        return request, incident, source_run, source_job, notes

    def _finalize_success(
        self, retry_request_id: str, submitted
    ) -> RetryRequestRecord:
        with connect_db(self._db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            updated = self._retry_registry.mark_submitted_in_connection(
                conn,
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
                conn=conn,
            )
        return updated

    def _mark_failed(self, retry_request_id: str, error_text: str) -> RetryRequestRecord:
        with connect_db(self._db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            return self._retry_registry.mark_failed_in_connection(
                conn,
                retry_request_id,
                error_text=error_text,
            )

    @staticmethod
    def _validate_retry_context(
        request: RetryRequestRecord,
        incident: sqlite3.Row,
        source_run: sqlite3.Row,
        source_job: sqlite3.Row,
    ) -> None:
        if source_job["run_id"] != source_run["run_id"]:
            raise ValueError("retry request source run/job linkage is inconsistent")
        if request.source_run_id != source_run["run_id"]:
            raise ValueError("retry request source run does not match the source job")
        if request.source_job_id != source_job["job_id"]:
            raise ValueError("retry request source job does not match the source run")
        if incident["run_id"] is not None and incident["run_id"] != source_run["run_id"]:
            raise ValueError("retry request source incident does not match the source run")
        if incident["job_id"] is not None and incident["job_id"] != source_job["job_id"]:
            raise ValueError("retry request source incident does not match the source job")
        if request.source_pbs_job_id is not None and source_job["pbs_job_id"] is not None:
            if request.source_pbs_job_id != source_job["pbs_job_id"]:
                raise ValueError("retry request source PBS job id does not match the source job")

    @staticmethod
    def _fetch_run(conn: sqlite3.Connection, run_id: str | None) -> sqlite3.Row:
        if run_id is None:
            raise ValueError("retry request source run is required")
        row = conn.execute(
            """
            SELECT run_id, run_kind, project, created_at, status, notes
            FROM runs
            WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"run not found: {run_id}")
        return row

    @staticmethod
    def _fetch_job(conn: sqlite3.Connection, job_id: str | None) -> sqlite3.Row:
        if job_id is None:
            raise ValueError("retry request source job is required")
        row = conn.execute(
            """
            SELECT job_id, run_id, backend, pbs_job_id, queue, walltime,
                   filesystems, select_expr, place_expr, exec_host, state,
                   submit_script_path, stdout_path, stderr_path,
                   created_at, updated_at
            FROM jobs
            WHERE job_id = ?
            """,
            (job_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"job not found: {job_id}")
        return row

    @staticmethod
    def _fetch_incident(conn: sqlite3.Connection, incident_id: str) -> sqlite3.Row:
        row = conn.execute(
            """
            SELECT incident_id, run_id, job_id, severity, category,
                   fingerprint, evidence_json, auto_action, status,
                   created_at, updated_at, resolved_at
            FROM incidents
            WHERE incident_id = ?
            """,
            (incident_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"incident not found: {incident_id}")
        return row
