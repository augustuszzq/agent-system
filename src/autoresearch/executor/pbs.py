"""PBS parsing and rendering helpers."""

from __future__ import annotations

import json
import re

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

    return QstatParseResult(
        pbs_job_id=job_id,
        state=values["job_state"],
        queue=values.get("queue"),
        comment=values.get("comment"),
        exec_host=values.get("exec_host"),
        stdout_path=_strip_host_prefix(values.get("Output_Path")),
        stderr_path=_strip_host_prefix(values.get("Error_Path")),
    )


def parse_qstat_json(text: str) -> QstatParseResult:
    payload = json.loads(text)
    jobs = payload.get("Jobs", {})
    if not jobs:
        raise ValueError("no jobs in qstat json")

    job_id, job_data = next(iter(jobs.items()))
    return QstatParseResult(
        pbs_job_id=job_id,
        state=job_data["job_state"],
        queue=job_data.get("queue"),
        comment=job_data.get("comment"),
        exec_host=job_data.get("exec_host"),
        stdout_path=_strip_host_prefix(job_data.get("Output_Path")),
        stderr_path=_strip_host_prefix(job_data.get("Error_Path")),
    )


def render_pbs_script(request: PolarisJobRequest) -> RenderedPBSScript:
    if request.stdout_path is None or request.stderr_path is None:
        raise ValueError("stdout_path and stderr_path must be set")

    script_text = f"""#!/bin/bash
#PBS -A {request.project}
#PBS -q {request.queue}
#PBS -l select={request.select_expr}
#PBS -l place={request.place_expr}
#PBS -l walltime={request.walltime}
#PBS -l filesystems={request.filesystems}
#PBS -N {request.job_name}
#PBS -k doe
#PBS -o {request.stdout_path}
#PBS -e {request.stderr_path}

set -euo pipefail

cd /eagle/lc-mpi/Zhiqing/auto-research/repo

export RUN_ID={request.run_id}
export AUTORESEARCH_REMOTE_ROOT=/eagle/lc-mpi/Zhiqing/auto-research
export RUN_DIR=/eagle/lc-mpi/Zhiqing/auto-research/runs/{request.run_id}
mkdir -p "$RUN_DIR"

bash {request.entrypoint_path}
"""
    return RenderedPBSScript(script_text=script_text)
