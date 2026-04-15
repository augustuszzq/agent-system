from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import sqlite3
import uuid

from autoresearch.db import connect_db
from autoresearch.schemas import RunCreateRequest


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    run_kind: str
    project: str
    created_at: str
    status: str
    notes: str | None


@dataclass(frozen=True)
class JobRecord:
    job_id: str
    run_id: str
    backend: str
    pbs_job_id: str | None
    queue: str
    walltime: str
    filesystems: str
    select_expr: str
    place_expr: str
    exec_host: str | None
    state: str
    submit_script_path: str | None
    stdout_path: str | None
    stderr_path: str | None
    created_at: str
    updated_at: str


class RunRegistry:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    def create_run(self, request: RunCreateRequest) -> RunRecord:
        record = RunRecord(
            run_id=f"run_{uuid.uuid4().hex[:12]}",
            run_kind=request.run_kind,
            project=request.project,
            created_at=datetime.now(UTC).isoformat(),
            status="CREATED",
            notes=request.notes,
        )
        with connect_db(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO runs (
                    run_id,
                    run_kind,
                    project,
                    created_at,
                    status,
                    notes
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    record.run_id,
                    record.run_kind,
                    record.project,
                    record.created_at,
                    record.status,
                    record.notes,
                ),
            )
        return record

    def list_runs(self) -> list[RunRecord]:
        with connect_db(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT run_id, run_kind, project, created_at, status, notes
                FROM runs
                ORDER BY created_at DESC
                """
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def create_job(
        self,
        run_id: str,
        backend: str,
        queue: str,
        walltime: str,
        filesystems: str,
        select_expr: str,
        place_expr: str,
        submit_script_path: str | None = None,
        stdout_path: str | None = None,
        stderr_path: str | None = None,
        pbs_job_id: str | None = None,
        exec_host: str | None = None,
    ) -> JobRecord:
        created_at = self._now_iso()
        record = JobRecord(
            job_id=f"job_{uuid.uuid4().hex[:12]}",
            run_id=run_id,
            backend=backend,
            pbs_job_id=pbs_job_id,
            queue=queue,
            walltime=walltime,
            filesystems=filesystems,
            select_expr=select_expr,
            place_expr=place_expr,
            exec_host=exec_host,
            state="DRAFT",
            submit_script_path=submit_script_path,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            created_at=created_at,
            updated_at=created_at,
        )
        with connect_db(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO jobs (
                    job_id,
                    run_id,
                    backend,
                    pbs_job_id,
                    queue,
                    walltime,
                    filesystems,
                    select_expr,
                    place_expr,
                    exec_host,
                    state,
                    submit_script_path,
                    stdout_path,
                    stderr_path,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.job_id,
                    record.run_id,
                    record.backend,
                    record.pbs_job_id,
                    record.queue,
                    record.walltime,
                    record.filesystems,
                    record.select_expr,
                    record.place_expr,
                    record.exec_host,
                    record.state,
                    record.submit_script_path,
                    record.stdout_path,
                    record.stderr_path,
                    record.created_at,
                    record.updated_at,
                ),
            )
        return record

    def get_job(self, job_id: str) -> JobRecord:
        with connect_db(self._db_path) as conn:
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
        return self._row_to_job_record(row)

    def update_job_state(
        self,
        job_id: str,
        state: str,
        pbs_job_id: str | None = None,
        exec_host: str | None = None,
    ) -> JobRecord:
        updated_at = self._now_iso()
        with connect_db(self._db_path) as conn:
            current_row = conn.execute(
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
            if current_row is None:
                raise KeyError(f"job not found: {job_id}")

            current_pbs_job_id = current_row["pbs_job_id"] if pbs_job_id is None else pbs_job_id
            current_exec_host = current_row["exec_host"] if exec_host is None else exec_host
            conn.execute(
                """
                UPDATE jobs
                SET state = ?,
                    pbs_job_id = ?,
                    exec_host = ?,
                    updated_at = ?
                WHERE job_id = ?
                """,
                (state, current_pbs_job_id, current_exec_host, updated_at, job_id),
            )
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
        return self._row_to_job_record(row)

    def mark_job_submitted(self, job_id: str, pbs_job_id: str) -> JobRecord:
        return self.update_job_state(
            job_id=job_id,
            state="SUBMITTED",
            pbs_job_id=pbs_job_id,
        )

    def list_jobs(self) -> list[JobRecord]:
        with connect_db(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT job_id, run_id, backend, pbs_job_id, queue, walltime,
                       filesystems, select_expr, place_expr, exec_host, state,
                       submit_script_path, stdout_path, stderr_path,
                       created_at, updated_at
                FROM jobs
                ORDER BY updated_at DESC, created_at DESC, job_id DESC
                """
            ).fetchall()
        return [self._row_to_job_record(row) for row in rows]

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> RunRecord:
        return RunRecord(
            run_id=row["run_id"],
            run_kind=row["run_kind"],
            project=row["project"],
            created_at=row["created_at"],
            status=row["status"],
            notes=row["notes"],
        )

    @staticmethod
    def _row_to_job_record(row: sqlite3.Row) -> JobRecord:
        return JobRecord(
            job_id=row["job_id"],
            run_id=row["run_id"],
            backend=row["backend"],
            pbs_job_id=row["pbs_job_id"],
            queue=row["queue"],
            walltime=row["walltime"],
            filesystems=row["filesystems"],
            select_expr=row["select_expr"],
            place_expr=row["place_expr"],
            exec_host=row["exec_host"],
            state=row["state"],
            submit_script_path=row["submit_script_path"],
            stdout_path=row["stdout_path"],
            stderr_path=row["stderr_path"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(UTC).isoformat(timespec="microseconds")
