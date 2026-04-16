from __future__ import annotations

import json
from hashlib import sha256

from autoresearch.executor.pbs import parse_qstat_json
from autoresearch.runs.registry import JobRecord
from autoresearch.schemas import IncidentFetchResult, NormalizedIncidentInput


class IncidentNormalizationError(RuntimeError):
    pass


def normalize_incident_evidence(
    *,
    job_record: JobRecord,
    fetched: IncidentFetchResult,
) -> NormalizedIncidentInput:
    try:
        qstat = parse_qstat_json(fetched.snapshot.qstat_json_path.read_text(encoding="utf-8"))
        stdout_tail = fetched.snapshot.stdout_tail_path.read_text(encoding="utf-8")
        stderr_tail = fetched.snapshot.stderr_tail_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise IncidentNormalizationError(
            f"incident snapshot normalization failed: {fetched.snapshot.snapshot_dir}"
        ) from exc

    previous_log_tail_hash: str | None = None
    if fetched.previous_snapshot is not None:
        try:
            previous_stdout_tail = fetched.previous_snapshot.stdout_tail_path.read_text(encoding="utf-8")
            previous_stderr_tail = fetched.previous_snapshot.stderr_tail_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            previous_log_tail_hash = None
        else:
            previous_log_tail_hash = _combined_tail_hash(previous_stdout_tail, previous_stderr_tail)

    return NormalizedIncidentInput(
        job_id=job_record.job_id,
        run_id=job_record.run_id,
        pbs_job_id=qstat.pbs_job_id,
        job_state=qstat.state,
        comment=qstat.comment,
        exec_host=qstat.exec_host,
        stdout_tail=stdout_tail,
        stderr_tail=stderr_tail,
        snapshot_dir=fetched.snapshot.snapshot_dir,
        scan_time=fetched.snapshot.scan_time,
        current_log_tail_hash=_combined_tail_hash(stdout_tail, stderr_tail),
        previous_log_tail_hash=previous_log_tail_hash,
    )


def _combined_tail_hash(stdout_tail: str, stderr_tail: str) -> str:
    return sha256(f"{stdout_tail}\n\x1f\n{stderr_tail}".encode("utf-8")).hexdigest()
