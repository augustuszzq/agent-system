"""Polaris PBS request helpers."""

from __future__ import annotations

import re

from autoresearch.schemas import PolarisJobRequest


REMOTE_ROOT = "/eagle/lc-mpi/Zhiqing/auto-research"


def _require_non_empty(value: str, field_name: str) -> str:
    if not value or not value.strip():
        raise ValueError(f"{field_name} must be non-empty")
    return value.strip()


def _require_no_whitespace(value: str, field_name: str) -> str:
    if any(char.isspace() for char in value):
        raise ValueError(f"{field_name} must not contain whitespace")
    return value


_SAFE_DIRECTIVE_VALUE_RE = re.compile(r"^[A-Za-z0-9._:=,+-]+$")


def _require_safe_directive_value(value: str, field_name: str) -> str:
    if not _SAFE_DIRECTIVE_VALUE_RE.fullmatch(value):
        raise ValueError(f"{field_name} contains unsafe characters")
    return value


def build_polaris_job_request(
    *,
    run_id: str,
    project: str,
    queue: str,
    walltime: str,
    entrypoint_path: str,
    remote_root: str = REMOTE_ROOT,
    job_name: str | None = None,
    filesystems: str = "eagle",
    place_expr: str = "scatter",
    select_expr: str = "1:system=polaris",
) -> PolarisJobRequest:
    """Build a Polaris PBS request with derived paths and Polaris defaults."""

    run_id = _require_safe_directive_value(
        _require_no_whitespace(_require_non_empty(run_id, "run_id"), "run_id"),
        "run_id",
    )
    project = _require_safe_directive_value(
        _require_non_empty(project, "project"),
        "project",
    )
    queue = _require_safe_directive_value(
        _require_non_empty(queue, "queue"),
        "queue",
    )
    walltime = _require_safe_directive_value(
        _require_non_empty(walltime, "walltime"),
        "walltime",
    )
    entrypoint_path = _require_non_empty(entrypoint_path, "entrypoint_path")
    remote_root = _require_no_whitespace(
        _require_non_empty(remote_root, "remote_root"),
        "remote_root",
    )
    filesystems = _require_safe_directive_value(
        _require_non_empty(filesystems, "filesystems"),
        "filesystems",
    )
    place_expr = _require_safe_directive_value(
        _require_non_empty(place_expr, "place_expr"),
        "place_expr",
    )
    select_expr = _require_safe_directive_value(
        _require_non_empty(select_expr, "select_expr"),
        "select_expr",
    )

    resolved_job_name = run_id
    if job_name is not None and job_name.strip():
        resolved_job_name = _require_safe_directive_value(
            _require_no_whitespace(job_name.strip(), "job_name"),
            "job_name",
        )

    stdout_path = f"{remote_root}/runs/{run_id}/stdout.log"
    stderr_path = f"{remote_root}/runs/{run_id}/stderr.log"
    submit_script_path = f"{remote_root}/jobs/{run_id}/submit.pbs"

    return PolarisJobRequest(
        run_id=run_id,
        job_name=resolved_job_name,
        project=project,
        queue=queue,
        walltime=walltime,
        select_expr=select_expr,
        entrypoint_path=entrypoint_path,
        remote_root=remote_root,
        place_expr=place_expr,
        filesystems=filesystems,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        submit_script_path=submit_script_path,
    )
