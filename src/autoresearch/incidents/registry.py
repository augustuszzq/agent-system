from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import json
import sqlite3
import uuid

from autoresearch.db import connect_db


_SEVERITY_ORDER = {
    "CRITICAL": 0,
    "HIGH": 1,
    "MEDIUM": 2,
}


@dataclass(frozen=True)
class IncidentRecord:
    incident_id: str
    run_id: str | None
    job_id: str | None
    severity: str
    category: str
    fingerprint: str | None
    evidence: dict[str, object]
    auto_action: str | None
    status: str
    created_at: str
    updated_at: str
    resolved_at: str | None


@dataclass(frozen=True)
class IncidentSummary:
    counts: dict[str, int]
    top_incidents: list[IncidentRecord]


class IncidentRegistry:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._ensure_schema()

    def upsert_incident(
        self,
        *,
        run_id: str | None,
        job_id: str | None,
        severity: str,
        category: str,
        fingerprint: str | None,
        evidence: dict[str, object],
    ) -> IncidentRecord:
        self._ensure_schema()
        created_at = self._evidence_scan_time(evidence)
        incident_id = f"incident_{uuid.uuid4().hex[:12]}"
        evidence_json = json.dumps(evidence, sort_keys=True)

        with connect_db(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO incidents (
                    incident_id,
                    run_id,
                    job_id,
                    severity,
                    category,
                    fingerprint,
                    evidence_json,
                    auto_action,
                    status,
                    created_at,
                    updated_at,
                    resolved_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id, category, fingerprint) DO UPDATE SET
                    severity = excluded.severity,
                    evidence_json = excluded.evidence_json,
                    updated_at = excluded.updated_at
                """,
                (
                    incident_id,
                    run_id,
                    job_id,
                    severity,
                    category,
                    fingerprint,
                    evidence_json,
                    None,
                    "OPEN",
                    created_at,
                    created_at,
                    None,
                ),
            )
            row = conn.execute(
                """
                SELECT incident_id, run_id, job_id, severity, category,
                       fingerprint, evidence_json, auto_action, status,
                       created_at, updated_at, resolved_at
                FROM incidents
                WHERE job_id IS ? AND category = ? AND fingerprint IS ?
                """,
                (job_id, category, fingerprint),
            ).fetchone()
        if row is None:
            raise RuntimeError("incident upsert did not return a row")
        return self._row_to_record(row)

    def list_open_incidents(self) -> list[IncidentRecord]:
        self._ensure_schema()
        with connect_db(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT incident_id, run_id, job_id, severity, category,
                       fingerprint, evidence_json, auto_action, status,
                       created_at, updated_at, resolved_at
                FROM incidents
                WHERE status = 'OPEN'
                ORDER BY updated_at DESC, created_at DESC, incident_id DESC
                """
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def summarize_open_incidents(self, limit: int = 3) -> IncidentSummary:
        self._ensure_schema()
        rows = self.list_open_incidents()
        counts: dict[str, int] = {}
        for record in rows:
            counts[record.category] = counts.get(record.category, 0) + 1

        top_incidents = sorted(
            rows,
            key=lambda record: (
                _SEVERITY_ORDER.get(record.severity, len(_SEVERITY_ORDER)),
                record.updated_at,
                record.created_at,
                record.incident_id,
            ),
        )[:limit]
        return IncidentSummary(counts=counts, top_incidents=top_incidents)

    def _ensure_schema(self) -> None:
        with connect_db(self._db_path) as conn:
            try:
                conn.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS incidents_job_category_fingerprint_idx
                    ON incidents(job_id, category, fingerprint)
                    """
                )
            except sqlite3.OperationalError:
                return

    @staticmethod
    def _evidence_scan_time(evidence: dict[str, object]) -> str:
        scan_time = evidence.get("scan_time")
        if isinstance(scan_time, str) and scan_time:
            return scan_time
        return datetime.now(UTC).isoformat(timespec="microseconds")

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> IncidentRecord:
        return IncidentRecord(
            incident_id=row["incident_id"],
            run_id=row["run_id"],
            job_id=row["job_id"],
            severity=row["severity"],
            category=row["category"],
            fingerprint=row["fingerprint"],
            evidence=json.loads(row["evidence_json"]),
            auto_action=row["auto_action"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            resolved_at=row["resolved_at"],
        )
