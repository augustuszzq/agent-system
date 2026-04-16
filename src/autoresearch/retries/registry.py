from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import sqlite3
import uuid

from autoresearch.db import connect_db
from autoresearch.decisions import DecisionLog
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
        with connect_db(self._db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = self._select_active_row(conn, incident_id, requested_action)
            if existing is not None:
                raise ValueError("retry request already exists")

            created_at = self._now_iso()
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
                created_at=created_at,
                updated_at=created_at,
                executed_at=None,
            )
            conn.execute(
                """
                INSERT INTO retry_requests (
                    retry_request_id, incident_id, source_run_id, source_job_id,
                    source_pbs_job_id, requested_action, approval_status,
                    execution_status, attempt_count, approved_by,
                    approval_reason, last_error, result_run_id, result_job_id,
                    result_pbs_job_id, created_at, updated_at, executed_at
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
                SELECT retry_request_id, incident_id, source_run_id,
                       source_job_id, source_pbs_job_id, requested_action,
                       approval_status, execution_status, attempt_count,
                       approved_by, approval_reason, last_error, result_run_id,
                       result_job_id, result_pbs_job_id, created_at, updated_at,
                       executed_at
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
                SELECT retry_request_id, incident_id, source_run_id,
                       source_job_id, source_pbs_job_id, requested_action,
                       approval_status, execution_status, attempt_count,
                       approved_by, approval_reason, last_error, result_run_id,
                       result_job_id, result_pbs_job_id, created_at, updated_at,
                       executed_at
                FROM retry_requests
                ORDER BY created_at DESC, updated_at DESC, retry_request_id DESC
                """
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def find_active_request(
        self, incident_id: str, requested_action: RetryAction
    ) -> RetryRequestRecord | None:
        with connect_db(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT retry_request_id, incident_id, source_run_id,
                       source_job_id, source_pbs_job_id, requested_action,
                       approval_status, execution_status, attempt_count,
                       approved_by, approval_reason, last_error, result_run_id,
                       result_job_id, result_pbs_job_id, created_at, updated_at,
                       executed_at
                FROM retry_requests
                WHERE incident_id = ?
                  AND requested_action = ?
                  AND (
                    approval_status = 'PENDING'
                    OR (
                      approval_status = 'APPROVED'
                      AND execution_status IN ('NOT_STARTED', 'CLAIMED')
                    )
                  )
                ORDER BY created_at ASC, retry_request_id ASC
                LIMIT 1
                """,
                (incident_id, requested_action),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def approve(self, retry_request_id: str, *, actor: str, reason: str) -> RetryRequestRecord:
        return self._update_decision(retry_request_id, actor=actor, reason=reason, approval_status="APPROVED")

    def reject(self, retry_request_id: str, *, actor: str, reason: str) -> RetryRequestRecord:
        return self._update_decision(retry_request_id, actor=actor, reason=reason, approval_status="REJECTED")

    def approve_with_decision(
        self,
        retry_request_id: str,
        *,
        actor: str,
        reason: str,
        decision_log: DecisionLog,
    ) -> RetryRequestRecord:
        return self._update_decision_with_log(
            retry_request_id,
            actor=actor,
            reason=reason,
            approval_status="APPROVED",
            decision_log=decision_log,
            decision="approve-retry",
        )

    def reject_with_decision(
        self,
        retry_request_id: str,
        *,
        actor: str,
        reason: str,
        decision_log: DecisionLog,
    ) -> RetryRequestRecord:
        return self._update_decision_with_log(
            retry_request_id,
            actor=actor,
            reason=reason,
            approval_status="REJECTED",
            decision_log=decision_log,
            decision="reject-retry",
        )

    def mark_failed(self, retry_request_id: str, *, error_text: str) -> RetryRequestRecord:
        with connect_db(self._db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            return self.mark_failed_in_connection(conn, retry_request_id, error_text=error_text)

    def mark_submitted(
        self,
        retry_request_id: str,
        *,
        result_run_id: str,
        result_job_id: str,
        result_pbs_job_id: str,
        executed_at: str,
    ) -> RetryRequestRecord:
        with connect_db(self._db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            return self.mark_submitted_in_connection(
                conn,
                retry_request_id,
                result_run_id=result_run_id,
                result_job_id=result_job_id,
                result_pbs_job_id=result_pbs_job_id,
                executed_at=executed_at,
            )

    def load_for_execution(
        self, conn: sqlite3.Connection, retry_request_id: str
    ) -> RetryRequestRecord:
        row = self._get_row_for_update(conn, retry_request_id)
        return self._row_to_record(row)

    def claim_execution(
        self, conn: sqlite3.Connection, retry_request_id: str
    ) -> RetryRequestRecord:
        record = self._get_row_for_update(conn, retry_request_id)
        self._require_pending_execution(record)
        updated_at = self._now_iso()
        conn.execute(
            """
            UPDATE retry_requests
            SET execution_status = ?,
                updated_at = ?
            WHERE retry_request_id = ?
            """,
            ("CLAIMED", updated_at, retry_request_id),
        )
        row = conn.execute(
            """
            SELECT retry_request_id, incident_id, source_run_id,
                   source_job_id, source_pbs_job_id, requested_action,
                   approval_status, execution_status, attempt_count,
                   approved_by, approval_reason, last_error, result_run_id,
                   result_job_id, result_pbs_job_id, created_at, updated_at,
                   executed_at
            FROM retry_requests
            WHERE retry_request_id = ?
            """,
            (retry_request_id,),
        ).fetchone()
        return self._row_to_record(row)

    def mark_failed_in_connection(
        self, conn: sqlite3.Connection, retry_request_id: str, *, error_text: str
    ) -> RetryRequestRecord:
        record = self._get_row_for_update(conn, retry_request_id)
        if record["approval_status"] != "APPROVED" or record["execution_status"] not in {
            "NOT_STARTED",
            "CLAIMED",
        }:
            raise ValueError("retry request must be approved and not started or claimed")
        updated_at = self._now_iso()
        conn.execute(
            """
            UPDATE retry_requests
            SET execution_status = ?,
                last_error = ?,
                updated_at = ?
            WHERE retry_request_id = ?
            """,
            ("FAILED", error_text, updated_at, retry_request_id),
        )
        row = conn.execute(
            """
            SELECT retry_request_id, incident_id, source_run_id,
                   source_job_id, source_pbs_job_id, requested_action,
                   approval_status, execution_status, attempt_count,
                   approved_by, approval_reason, last_error, result_run_id,
                   result_job_id, result_pbs_job_id, created_at, updated_at,
                   executed_at
            FROM retry_requests
            WHERE retry_request_id = ?
            """,
            (retry_request_id,),
        ).fetchone()
        return self._row_to_record(row)

    def mark_submitted_in_connection(
        self,
        conn: sqlite3.Connection,
        retry_request_id: str,
        *,
        result_run_id: str,
        result_job_id: str,
        result_pbs_job_id: str,
        executed_at: str,
    ) -> RetryRequestRecord:
        record = self._get_row_for_update(conn, retry_request_id)
        if record["approval_status"] != "APPROVED" or record["execution_status"] != "CLAIMED":
            raise ValueError("retry request must be claimed before submission is finalized")
        updated_at = self._now_iso()
        conn.execute(
            """
            UPDATE retry_requests
            SET execution_status = ?,
                attempt_count = ?,
                result_run_id = ?,
                result_job_id = ?,
                result_pbs_job_id = ?,
                executed_at = ?,
                updated_at = ?
            WHERE retry_request_id = ?
            """,
            (
                "SUBMITTED",
                record["attempt_count"] + 1,
                result_run_id,
                result_job_id,
                result_pbs_job_id,
                executed_at,
                updated_at,
                retry_request_id,
            ),
        )
        row = conn.execute(
            """
            SELECT retry_request_id, incident_id, source_run_id,
                   source_job_id, source_pbs_job_id, requested_action,
                   approval_status, execution_status, attempt_count,
                   approved_by, approval_reason, last_error, result_run_id,
                   result_job_id, result_pbs_job_id, created_at, updated_at,
                   executed_at
            FROM retry_requests
            WHERE retry_request_id = ?
            """,
            (retry_request_id,),
        ).fetchone()
        return self._row_to_record(row)

    def _update_decision(
        self,
        retry_request_id: str,
        *,
        actor: str,
        reason: str,
        approval_status: str,
    ) -> RetryRequestRecord:
        with connect_db(self._db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            record = self._get_row_for_update(conn, retry_request_id)
            self._require_pending(record)
            updated_at = self._now_iso()
            conn.execute(
                """
                UPDATE retry_requests
                SET approval_status = ?,
                    approved_by = ?,
                    approval_reason = ?,
                    updated_at = ?
                WHERE retry_request_id = ?
                """,
                (approval_status, actor, reason, updated_at, retry_request_id),
            )
            row = conn.execute(
                """
                SELECT retry_request_id, incident_id, source_run_id,
                       source_job_id, source_pbs_job_id, requested_action,
                       approval_status, execution_status, attempt_count,
                       approved_by, approval_reason, last_error, result_run_id,
                       result_job_id, result_pbs_job_id, created_at, updated_at,
                       executed_at
                FROM retry_requests
                WHERE retry_request_id = ?
                """,
                (retry_request_id,),
            ).fetchone()
        return self._row_to_record(row)

    def _update_decision_with_log(
        self,
        retry_request_id: str,
        *,
        actor: str,
        reason: str,
        approval_status: str,
        decision_log: DecisionLog,
        decision: str,
    ) -> RetryRequestRecord:
        with connect_db(self._db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            record = self._get_row_for_update(conn, retry_request_id)
            self._require_pending(record)
            updated_at = self._now_iso()
            conn.execute(
                """
                UPDATE retry_requests
                SET approval_status = ?,
                    approved_by = ?,
                    approval_reason = ?,
                    updated_at = ?
                WHERE retry_request_id = ?
                """,
                (approval_status, actor, reason, updated_at, retry_request_id),
            )
            decision_log.append(
                target_type="retry_request",
                target_id=retry_request_id,
                decision=decision,
                rationale=reason,
                actor=actor,
                conn=conn,
            )
            row = conn.execute(
                """
                SELECT retry_request_id, incident_id, source_run_id,
                       source_job_id, source_pbs_job_id, requested_action,
                       approval_status, execution_status, attempt_count,
                       approved_by, approval_reason, last_error, result_run_id,
                       result_job_id, result_pbs_job_id, created_at, updated_at,
                       executed_at
                FROM retry_requests
                WHERE retry_request_id = ?
                """,
                (retry_request_id,),
            ).fetchone()
        return self._row_to_record(row)

    @staticmethod
    def _require_pending(record: sqlite3.Row) -> None:
        if record["approval_status"] != "PENDING":
            raise ValueError("retry request must be pending")

    @staticmethod
    def _require_pending_execution(record: sqlite3.Row) -> None:
        if record["approval_status"] != "APPROVED" or record["execution_status"] != "NOT_STARTED":
            raise ValueError("retry request must be approved and not started")

    def _get_row_for_update(
        self, conn: sqlite3.Connection, retry_request_id: str
    ) -> sqlite3.Row:
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
        return row

    def _select_active_row(
        self, conn: sqlite3.Connection, incident_id: str, requested_action: RetryAction
    ) -> sqlite3.Row | None:
        return conn.execute(
            """
            SELECT retry_request_id, incident_id, source_run_id, source_job_id,
                   source_pbs_job_id, requested_action, approval_status,
                   execution_status, attempt_count, approved_by,
                   approval_reason, last_error, result_run_id, result_job_id,
                   result_pbs_job_id, created_at, updated_at, executed_at
            FROM retry_requests
            WHERE incident_id = ?
              AND requested_action = ?
              AND (
                approval_status = 'PENDING'
                OR (
                  approval_status = 'APPROVED'
                  AND execution_status IN ('NOT_STARTED', 'CLAIMED')
                )
              )
            ORDER BY created_at ASC, retry_request_id ASC
            LIMIT 1
            """,
            (incident_id, requested_action),
        ).fetchone()

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

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(UTC).isoformat(timespec="microseconds")
