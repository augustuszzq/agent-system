from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import sqlite3
import uuid

from autoresearch.db import connect_db
from autoresearch.schemas import RetryAction


@dataclass(frozen=True)
class RetryRequestRecord:
    retry_request_id: str
    incident_id: str
    source_run_id: str | None
    source_job_id: str | None
    source_pbs_job_id: str | None
    requested_action: str
    approval_status: str
    execution_status: str
    attempt_count: int
    approved_by: str | None
    approval_reason: str | None
    last_error: str | None
    result_run_id: str | None
    result_job_id: str | None
    result_pbs_job_id: str | None
    created_at: str
    updated_at: str
    executed_at: str | None


class RetryRequestRegistry:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    def create_request(
        self,
        *,
        incident_id: str,
        source_run_id: str | None,
        source_job_id: str | None,
        source_pbs_job_id: str | None,
        requested_action: RetryAction,
    ) -> RetryRequestRecord:
        now = self._now_iso()
        record = RetryRequestRecord(
            retry_request_id=f"retry_{uuid.uuid4().hex[:12]}",
            incident_id=incident_id,
            source_run_id=source_run_id,
            source_job_id=source_job_id,
            source_pbs_job_id=source_pbs_job_id,
            requested_action=requested_action,
            approval_status="PENDING",
            execution_status="NOT_STARTED",
            attempt_count=0,
            approved_by=None,
            approval_reason=None,
            last_error=None,
            result_run_id=None,
            result_job_id=None,
            result_pbs_job_id=None,
            created_at=now,
            updated_at=now,
            executed_at=None,
        )
        with connect_db(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO retry_requests (
                    retry_request_id,
                    incident_id,
                    source_run_id,
                    source_job_id,
                    source_pbs_job_id,
                    requested_action,
                    approval_status,
                    execution_status,
                    attempt_count,
                    approved_by,
                    approval_reason,
                    last_error,
                    result_run_id,
                    result_job_id,
                    result_pbs_job_id,
                    created_at,
                    updated_at,
                    executed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.retry_request_id,
                    record.incident_id,
                    record.source_run_id,
                    record.source_job_id,
                    record.source_pbs_job_id,
                    record.requested_action,
                    record.approval_status,
                    record.execution_status,
                    record.attempt_count,
                    record.approved_by,
                    record.approval_reason,
                    record.last_error,
                    record.result_run_id,
                    record.result_job_id,
                    record.result_pbs_job_id,
                    record.created_at,
                    record.updated_at,
                    record.executed_at,
                ),
            )
        return record

    def get(self, retry_request_id: str) -> RetryRequestRecord:
        with connect_db(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT retry_request_id, incident_id, source_run_id, source_job_id,
                       source_pbs_job_id, requested_action, approval_status,
                       execution_status, attempt_count, approved_by,
                       approval_reason, last_error, result_run_id, result_job_id,
                       result_pbs_job_id, created_at, updated_at, executed_at
                FROM retry_requests
                WHERE retry_request_id = ?
                """,
                (retry_request_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"retry request not found: {retry_request_id}")
        return self._row_to_record(row)

    def list_requests(self) -> list[RetryRequestRecord]:
        with connect_db(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT retry_request_id, incident_id, source_run_id, source_job_id,
                       source_pbs_job_id, requested_action, approval_status,
                       execution_status, attempt_count, approved_by,
                       approval_reason, last_error, result_run_id, result_job_id,
                       result_pbs_job_id, created_at, updated_at, executed_at
                FROM retry_requests
                ORDER BY updated_at DESC, created_at DESC, retry_request_id DESC
                """
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def find_active_request(
        self,
        incident_id: str,
        requested_action: RetryAction,
    ) -> RetryRequestRecord | None:
        with connect_db(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT retry_request_id, incident_id, source_run_id, source_job_id,
                       source_pbs_job_id, requested_action, approval_status,
                       execution_status, attempt_count, approved_by,
                       approval_reason, last_error, result_run_id, result_job_id,
                       result_pbs_job_id, created_at, updated_at, executed_at
                FROM retry_requests
                WHERE incident_id = ?
                  AND requested_action = ?
                  AND approval_status IN ('PENDING', 'APPROVED')
                  AND execution_status = 'NOT_STARTED'
                ORDER BY updated_at DESC, created_at DESC, retry_request_id DESC
                LIMIT 1
                """,
                (incident_id, requested_action),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def approve(self, retry_request_id: str, *, actor: str, reason: str) -> RetryRequestRecord:
        record = self.get(retry_request_id)
        if record.approval_status != "PENDING":
            raise ValueError("retry request must be pending")
        updated = self._update_request(
            retry_request_id,
            approval_status="APPROVED",
            approved_by=actor,
            approval_reason=reason,
        )
        return updated

    def reject(self, retry_request_id: str, *, actor: str, reason: str) -> RetryRequestRecord:
        record = self.get(retry_request_id)
        if record.approval_status != "PENDING":
            raise ValueError("retry request must be pending")
        updated = self._update_request(
            retry_request_id,
            approval_status="REJECTED",
            approved_by=actor,
            approval_reason=reason,
        )
        return updated

    def mark_failed(self, retry_request_id: str, *, error_text: str) -> RetryRequestRecord:
        record = self.get(retry_request_id)
        if record.approval_status != "APPROVED" or record.execution_status != "NOT_STARTED":
            raise ValueError("retry request must be approved and not started")
        return self._update_request(
            retry_request_id,
            execution_status="FAILED",
            last_error=error_text,
        )

    def mark_submitted(
        self,
        retry_request_id: str,
        *,
        result_run_id: str,
        result_job_id: str,
        result_pbs_job_id: str,
        executed_at: str,
    ) -> RetryRequestRecord:
        record = self.get(retry_request_id)
        if record.approval_status != "APPROVED" or record.execution_status != "NOT_STARTED":
            raise ValueError("retry request must be approved and not started")
        return self._update_request(
            retry_request_id,
            execution_status="SUBMITTED",
            attempt_count=1,
            result_run_id=result_run_id,
            result_job_id=result_job_id,
            result_pbs_job_id=result_pbs_job_id,
            executed_at=executed_at,
            last_error=None,
        )

    def _update_request(self, retry_request_id: str, **updates: object) -> RetryRequestRecord:
        now = self._now_iso()
        set_clauses = ["updated_at = ?"]
        params: list[object] = [now]
        for column, value in updates.items():
            set_clauses.append(f"{column} = ?")
            params.append(value)
        params.append(retry_request_id)
        with connect_db(self._db_path) as conn:
            conn.execute(
                f"""
                UPDATE retry_requests
                SET {', '.join(set_clauses)}
                WHERE retry_request_id = ?
                """,
                params,
            )
        return self.get(retry_request_id)

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> RetryRequestRecord:
        return RetryRequestRecord(
            retry_request_id=row["retry_request_id"],
            incident_id=row["incident_id"],
            source_run_id=row["source_run_id"],
            source_job_id=row["source_job_id"],
            source_pbs_job_id=row["source_pbs_job_id"],
            requested_action=row["requested_action"],
            approval_status=row["approval_status"],
            execution_status=row["execution_status"],
            attempt_count=row["attempt_count"],
            approved_by=row["approved_by"],
            approval_reason=row["approval_reason"],
            last_error=row["last_error"],
            result_run_id=row["result_run_id"],
            result_job_id=row["result_job_id"],
            result_pbs_job_id=row["result_pbs_job_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            executed_at=row["executed_at"],
        )

