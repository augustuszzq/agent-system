from __future__ import annotations

from autoresearch.incidents.registry import IncidentRecord, IncidentSummary


def render_incident_row(record: IncidentRecord) -> str:
    fingerprint = record.fingerprint or "-"
    return f"{record.severity} {record.category} {record.job_id or '-'} {record.updated_at} {fingerprint}"


def render_incident_summary(summary: IncidentSummary) -> str:
    lines = [
        "Open incident summary",
        f"Counts: {summary.counts}",
    ]
    if summary.top_incidents:
        lines.append("Top incidents:")
        lines.extend(f"- {render_incident_row(record)}" for record in summary.top_incidents)
    else:
        lines.append("Top incidents: none")
    return "\n".join(lines)
