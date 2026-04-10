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
