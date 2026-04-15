"""Polaris PBS request helpers."""

from __future__ import annotations

from autoresearch.schemas import PolarisJobRequest


REMOTE_ROOT = "/eagle/lc-mpi/Zhiqing/auto-research"


def _require_non_empty(value: str, field_name: str) -> str:
    if not value or not value.strip():
        raise ValueError(f"{field_name} must be non-empty")
    return value.strip()


def build_polaris_job_request(
    *,
    run_id: str,
    project: str,
    queue: str,
    walltime: str,
    entrypoint_path: str,
    job_name: str | None = None,
    filesystems: str = "eagle",
    place_expr: str = "scatter",
    select_expr: str = "1:system=polaris",
) -> PolarisJobRequest:
    """Build a Polaris PBS request with derived paths and Polaris defaults."""

    run_id = _require_non_empty(run_id, "run_id")
    project = _require_non_empty(project, "project")
    queue = _require_non_empty(queue, "queue")
    walltime = _require_non_empty(walltime, "walltime")
    entrypoint_path = _require_non_empty(entrypoint_path, "entrypoint_path")

    resolved_job_name = run_id
    if job_name is not None and job_name.strip():
        resolved_job_name = job_name.strip()

    stdout_path = f"{REMOTE_ROOT}/runs/{run_id}/stdout.log"
    stderr_path = f"{REMOTE_ROOT}/runs/{run_id}/stderr.log"
    submit_script_path = f"{REMOTE_ROOT}/jobs/{run_id}/submit.pbs"

    return PolarisJobRequest(
        run_id=run_id,
        job_name=resolved_job_name,
        project=project,
        queue=queue,
        walltime=walltime,
        select_expr=select_expr,
        entrypoint_path=entrypoint_path,
        place_expr=place_expr,
        filesystems=filesystems,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        submit_script_path=submit_script_path,
    )
