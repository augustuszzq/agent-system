"""PBS parsing and rendering helpers."""

from __future__ import annotations

import json
import re
import shlex

from autoresearch.schemas import (
    PolarisJobRequest,
    QstatParseResult,
    QsubParseResult,
    RenderedPBSScript,
)


def _strip_host_prefix(path_value: str | None) -> str | None:
    if path_value is None or path_value == "":
        return None
    if ":" not in path_value:
        return path_value

    host_part, path_part = path_value.split(":", 1)
    if not host_part or path_part.startswith("//") or not path_part.startswith("/"):
        return path_value
    return path_part


def _looks_like_pbs_job_id(job_id: str) -> bool:
    return bool(re.fullmatch(r"\d+(?:\.[A-Za-z0-9][A-Za-z0-9._-]*)+", job_id))


def parse_qsub_output(text: str) -> QsubParseResult:
    raw_output = text.strip()
    if not raw_output:
        raise ValueError("empty qsub output")
    if not _looks_like_pbs_job_id(raw_output):
        raise ValueError("malformed qsub output")
    return QsubParseResult(
        raw_output=raw_output,
        pbs_job_id=raw_output,
        is_success=True,
    )


def parse_qstat_output(text: str) -> QstatParseResult:
    values: dict[str, str] = {}
    job_id: str | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("Job Id:"):
            job_id = line.split(":", 1)[1].strip()
            continue

        if "=" not in line:
            continue

        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()

    if not job_id:
        raise ValueError("missing job id in qstat output")
    job_state = values.get("job_state")
    if job_state is None or not job_state:
        raise ValueError("missing job_state in qstat output")

    return QstatParseResult(
        pbs_job_id=job_id,
        state=job_state,
        queue=values.get("queue"),
        comment=values.get("comment"),
        exec_host=values.get("exec_host"),
        stdout_path=_strip_host_prefix(values.get("Output_Path")),
        stderr_path=_strip_host_prefix(values.get("Error_Path")),
    )


def parse_qstat_json(text: str) -> QstatParseResult:
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("malformed qstat json")

    jobs = payload.get("Jobs")
    if not isinstance(jobs, dict):
        raise ValueError("malformed qstat json")
    if not jobs:
        raise ValueError("no jobs in qstat json")
    if len(jobs) != 1:
        raise ValueError("expected exactly one job in qstat json")

    job_id, job_data = next(iter(jobs.items()))
    if not isinstance(job_data, dict):
        raise ValueError("malformed qstat json")

    job_state = job_data.get("job_state")
    if job_state is None:
        raise ValueError("missing job_state in qstat json")
    if not isinstance(job_state, str) or not job_state.strip():
        raise ValueError("malformed qstat json")

    queue = job_data.get("queue")
    comment = job_data.get("comment")
    exec_host = job_data.get("exec_host")
    output_path = job_data.get("Output_Path")
    error_path = job_data.get("Error_Path")

    if queue is not None and not isinstance(queue, str):
        raise ValueError("malformed qstat json")
    if comment is not None and not isinstance(comment, str):
        raise ValueError("malformed qstat json")
    if exec_host is not None and not isinstance(exec_host, str):
        raise ValueError("malformed qstat json")
    if output_path is not None and not isinstance(output_path, str):
        raise ValueError("malformed qstat json")
    if error_path is not None and not isinstance(error_path, str):
        raise ValueError("malformed qstat json")
    return QstatParseResult(
        pbs_job_id=job_id,
        state=job_state,
        queue=queue,
        comment=comment,
        exec_host=exec_host,
        stdout_path=_strip_host_prefix(output_path),
        stderr_path=_strip_host_prefix(error_path),
    )


def _require_non_empty(value: str, field_name: str) -> str:
    if not value or not value.strip():
        raise ValueError(f"{field_name} must be non-empty")
    return value.strip()


def _require_no_whitespace(value: str, field_name: str) -> str:
    if any(char.isspace() for char in value):
        raise ValueError(f"{field_name} must not contain whitespace")
    return value


def render_pbs_script(request: PolarisJobRequest) -> RenderedPBSScript:
    if (
        request.stdout_path is None
        or request.stderr_path is None
        or not request.stdout_path.strip()
        or not request.stderr_path.strip()
    ):
        raise ValueError("stdout_path and stderr_path must be set")

    run_id = _require_no_whitespace(_require_non_empty(request.run_id, "run_id"), "run_id")
    job_name = _require_no_whitespace(
        _require_non_empty(request.job_name, "job_name"),
        "job_name",
    )
    remote_root = _require_no_whitespace(
        _require_non_empty(request.remote_root, "remote_root"),
        "remote_root",
    )
    run_dir = f"{remote_root}/runs/{run_id}"
    repo_dir = f"{remote_root}/repo"

    script_text = f"""#!/bin/bash
#PBS -A {request.project}
#PBS -q {request.queue}
#PBS -l select={request.select_expr}
#PBS -l place={request.place_expr}
#PBS -l walltime={request.walltime}
#PBS -l filesystems={request.filesystems}
#PBS -N {job_name}
#PBS -k doe
#PBS -o {request.stdout_path}
#PBS -e {request.stderr_path}

set -euo pipefail

cd {shlex.quote(repo_dir)}

export RUN_ID={run_id}
export AUTORESEARCH_REMOTE_ROOT={shlex.quote(remote_root)}
export RUN_DIR={shlex.quote(run_dir)}
mkdir -p "$RUN_DIR"

bash {shlex.quote(request.entrypoint_path)}
"""
    return RenderedPBSScript(script_text=script_text)
