from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import sqlite3

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from autoresearch.db import connect_db


_ACTIVE_RUN_STATUSES = {"CREATED", "SUBMITTED", "QUEUED", "RUNNING"}
_SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2}
_PAPER_FALLBACK_LINES = (
    "- New papers scanned: not available yet",
    "- Top relevant: not available yet",
    "- Deep read today: not available yet",
    "- Reproduce candidate: not available yet",
)


@dataclass(frozen=True)
class DailyReportResult:
    report_date: str
    markdown: str
    output_path: Path


class DailyReportBuilder:
    def __init__(self, *, db_path: Path, state_dir: Path) -> None:
        self._db_path = db_path
        self._state_dir = state_dir
        template_dir = Path(__file__).with_name("templates")
        self._env = Environment(
            loader=FileSystemLoader(str(template_dir)),
            undefined=StrictUndefined,
            autoescape=False,
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def build(
        self,
        *,
        report_date: str,
        generated_at: datetime | None = None,
    ) -> DailyReportResult:
        generated_at = self._normalize_generated_at(generated_at)
        with connect_db(self._db_path) as conn:
            conn.execute("BEGIN")
            context = self._build_context(conn, report_date=report_date, generated_at=generated_at)
            markdown = self._env.get_template("daily_brief.md.j2").render(**context).rstrip() + "\n"
        output_path = self._state_dir / "reports" / "daily" / f"{report_date}.md"
        return DailyReportResult(report_date=report_date, markdown=markdown, output_path=output_path)

    def _build_context(
        self,
        conn: sqlite3.Connection,
        *,
        report_date: str,
        generated_at: datetime,
    ) -> dict[str, str]:
        return {
            "report_date": report_date,
            "paper_delta_block": self._build_paper_delta_block(),
            "run_status_block": self._build_run_status_block(
                conn,
                generated_at=generated_at,
            ),
            "incident_summary_block": self._build_incident_summary_block(conn),
            "pending_decisions_block": self._build_pending_decisions_block(conn),
        }

    def _build_paper_delta_block(self) -> str:
        return "\n".join(_PAPER_FALLBACK_LINES)

    def _build_run_status_block(
        self,
        conn: sqlite3.Connection,
        *,
        generated_at: datetime,
    ) -> str:
        runs = self._fetch_rows(
            conn,
            """
            SELECT run_id, status, ended_at
            FROM runs
            ORDER BY created_at ASC, run_id ASC
            """
        )
        retry_requests = self._fetch_rows(
            conn,
            """
            SELECT approval_status, execution_status, executed_at
            FROM retry_requests
            ORDER BY created_at ASC, retry_request_id ASC
            """
        )

        active_runs = sum(row["status"] in _ACTIVE_RUN_STATUSES for row in runs)
        finished_overnight = sum(
            row["status"] == "SUCCEEDED"
            and row["ended_at"] is not None
            and self._within_last_24_hours(row["ended_at"], generated_at)
            for row in runs
        )
        failed_runs = sum(
            row["status"] == "FAILED"
            and row["ended_at"] is not None
            and self._within_last_24_hours(row["ended_at"], generated_at)
            for row in runs
        )
        auto_retried = sum(
            row["execution_status"] == "SUBMITTED"
            and row["executed_at"] is not None
            and self._within_last_24_hours(row["executed_at"], generated_at)
            for row in retry_requests
        )
        awaiting_approval = sum(row["approval_status"] == "PENDING" for row in retry_requests)

        return "\n".join(
            [
                f"- Active runs: {active_runs}",
                f"- Finished overnight: {finished_overnight}",
                f"- Failed: {failed_runs}",
                f"- Auto-retried: {auto_retried}",
                f"- Awaiting approval: {awaiting_approval}",
            ]
        )

    def _build_incident_summary_block(self, conn: sqlite3.Connection) -> str:
        incidents = self._fetch_rows(
            conn,
            """
            SELECT incident_id, run_id, job_id, severity, category, evidence_json,
                   created_at, updated_at
            FROM incidents
            WHERE status = 'OPEN'
            ORDER BY updated_at DESC, created_at DESC, incident_id DESC
            """
        )
        if not incidents:
            return "\n".join(["- Open incidents: 0", "- No open incidents"])

        category_counts: dict[str, int] = {}
        for row in incidents:
            category_counts[row["category"]] = category_counts.get(row["category"], 0) + 1

        lines = [f"- Open incidents: {len(incidents)}"]
        for category in sorted(category_counts):
            lines.append(f"- {category}: {category_counts[category]}")

        top_incidents = sorted(
            incidents,
            key=lambda row: _SEVERITY_ORDER.get(row["severity"], len(_SEVERITY_ORDER)),
        )[:3]
        if top_incidents:
            lines.append("- Top open incidents:")
            for index, row in enumerate(top_incidents, start=1):
                evidence = self._incident_evidence_line(row["evidence_json"])
                lines.append(
                    f"{index}. {row['incident_id']} | {row['category']} | {row['severity']} | "
                    f"{row['run_id'] or '-'} | {row['job_id'] or '-'}"
                )
                lines.append(f"   Evidence: {evidence}")
        return "\n".join(lines)

    def _build_pending_decisions_block(self, conn: sqlite3.Connection) -> str:
        requests = self._fetch_rows(
            conn,
            """
            SELECT retry_request_id, incident_id, created_at
            FROM retry_requests
            WHERE approval_status = 'PENDING'
            ORDER BY created_at ASC, retry_request_id ASC
            LIMIT 3
            """
        )
        if not requests:
            return "- No pending decisions"

        lines = [
            f"{index}. Approve retry {row['retry_request_id']} for incident {row['incident_id']}"
            for index, row in enumerate(requests, start=1)
        ]
        return "\n".join(lines)

    def _fetch_rows(
        self, conn: sqlite3.Connection, query: str, params: tuple[object, ...] = ()
    ) -> list[sqlite3.Row]:
        return conn.execute(query, params).fetchall()

    @staticmethod
    def _parse_iso_datetime(value: str) -> datetime:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    @classmethod
    @staticmethod
    def _within_last_24_hours(value: str, generated_at: datetime) -> bool:
        observed = DailyReportBuilder._parse_iso_datetime(value)
        return generated_at - timedelta(days=1) <= observed < generated_at

    @staticmethod
    def _normalize_generated_at(generated_at: datetime | None) -> datetime:
        if generated_at is None:
            return datetime.now(UTC)
        if generated_at.tzinfo is None:
            return generated_at.replace(tzinfo=UTC)
        return generated_at.astimezone(UTC)

    @staticmethod
    def _incident_evidence_line(evidence_json: str) -> str:
        evidence = json.loads(evidence_json)
        qstat_comment = evidence.get("qstat_comment")
        if isinstance(qstat_comment, str) and qstat_comment.strip():
            return qstat_comment.strip()

        matched_lines = evidence.get("matched_lines")
        if isinstance(matched_lines, list) and matched_lines:
            first_line = matched_lines[0]
            if isinstance(first_line, str) and first_line.strip():
                return first_line.strip()

        return "evidence unavailable"
