from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
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

    def build(self, *, report_date: str) -> DailyReportResult:
        context = self._build_context(report_date=report_date)
        markdown = self._env.get_template("daily_brief.md.j2").render(**context).rstrip() + "\n"
        output_path = self._state_dir / "reports" / "daily" / f"{report_date}.md"
        return DailyReportResult(
            report_date=report_date,
            markdown=markdown,
            output_path=output_path,
        )

    def _build_context(self, *, report_date: str) -> dict[str, str]:
        return {
            "report_date": report_date,
            "paper_delta_block": self._build_paper_delta_block(),
            "run_status_block": self._build_run_status_block(report_date=report_date),
            "incident_summary_block": self._build_incident_summary_block(),
            "pending_decisions_block": self._build_pending_decisions_block(),
        }

    def _build_paper_delta_block(self) -> str:
        return "\n".join(_PAPER_FALLBACK_LINES)

    def _build_run_status_block(self, *, report_date: str) -> str:
        runs = self._fetch_rows(
            """
            SELECT run_id, status, ended_at
            FROM runs
            ORDER BY created_at ASC, run_id ASC
            """
        )
        retry_requests = self._fetch_rows(
            """
            SELECT approval_status, execution_status
            FROM retry_requests
            ORDER BY created_at ASC, retry_request_id ASC
            """
        )

        active_runs = sum(row["status"] in _ACTIVE_RUN_STATUSES for row in runs)
        finished_overnight = sum(
            self._iso_date(row["ended_at"]) == report_date and row["status"] != "FAILED"
            for row in runs
            if row["ended_at"]
        )
        failed_runs = sum(row["status"] == "FAILED" for row in runs)
        auto_retried = sum(
            row["approval_status"] == "APPROVED" and row["execution_status"] == "SUBMITTED"
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

    def _build_incident_summary_block(self) -> str:
        incidents = self._fetch_rows(
            """
            SELECT incident_id, severity, category, created_at, updated_at
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
                lines.append(
                    f"{index}. {row['incident_id']} ({row['severity']}) {row['category']}"
                )
        return "\n".join(lines)

    def _build_pending_decisions_block(self) -> str:
        requests = self._fetch_rows(
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

    def _fetch_rows(self, query: str, params: tuple[object, ...] = ()) -> list[sqlite3.Row]:
        with connect_db(self._db_path) as conn:
            rows = conn.execute(query, params).fetchall()
        return rows

    @staticmethod
    def _iso_date(value: str) -> str:
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).date().isoformat()
