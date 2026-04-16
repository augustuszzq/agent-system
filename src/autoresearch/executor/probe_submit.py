from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shlex
import tempfile

from autoresearch.bridge.remote_exec import RemoteBridgeError, copy_to_remote, execute_remote_command
from autoresearch.executor.pbs import build_qsub_command, parse_qsub_output, render_pbs_script
from autoresearch.executor.polaris import build_probe_job_request
from autoresearch.runs.registry import RunRegistry
from autoresearch.schemas import RunCreateRequest


@dataclass(frozen=True)
class SubmittedProbeRun:
    run_id: str
    job_id: str
    pbs_job_id: str


def submit_live_probe_run(
    *,
    settings,
    service,
    run_kind: str,
    notes: str | None,
    project: str,
    queue: str,
    walltime: str,
) -> SubmittedProbeRun:
    registry = RunRegistry(settings.paths.db_path)
    run_record = registry.create_run(
        RunCreateRequest(run_kind=run_kind, project=project, notes=notes)
    )
    request = build_probe_job_request(
        run_id=run_record.run_id,
        entrypoint_path=f"{settings.remote_root}/jobs/probe/entrypoint.sh",
        remote_root=settings.remote_root,
        probe_settings=settings.probe,
        queue=queue,
        walltime=walltime,
    )
    rendered = render_pbs_script(request)
    job_record = registry.create_job(
        run_id=run_record.run_id,
        backend="pbs",
        queue=request.queue,
        walltime=request.walltime,
        filesystems=request.filesystems,
        select_expr=request.select_expr,
        place_expr=request.place_expr,
        submit_script_path=request.submit_script_path,
        stdout_path=request.stdout_path,
        stderr_path=request.stderr_path,
    )

    temp_file = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=f"-{run_record.run_id}.pbs",
        delete=False,
    )
    try:
        temp_file.write(rendered.script_text)
        temp_file.flush()
        temp_path = Path(temp_file.name)
    finally:
        temp_file.close()

    try:
        submit_parent_dir = str(Path(request.submit_script_path).parent)
        mkdir_result = execute_remote_command(
            service,
            f"mkdir -p {shlex.quote(submit_parent_dir)}",
        )
        if mkdir_result.returncode != 0:
            raise RemoteBridgeError(
                mkdir_result.stderr.strip()
                or f"failed to create submit directory: {submit_parent_dir}"
            )

        copy_result = copy_to_remote(service, temp_path, request.submit_script_path, settings.remote_root)
        if copy_result.returncode != 0:
            raise RemoteBridgeError(
                copy_result.stderr.strip()
                or f"failed to upload submit script: {request.submit_script_path}"
            )

        run_log_parent_dir = str(Path(request.stdout_path).parent)
        mkdir_result = execute_remote_command(
            service,
            f"mkdir -p {shlex.quote(run_log_parent_dir)}",
        )
        if mkdir_result.returncode != 0:
            raise RemoteBridgeError(
                mkdir_result.stderr.strip()
                or f"failed to create run log directory: {run_log_parent_dir}"
            )

        qsub_command = shlex.join(build_qsub_command(request.submit_script_path))
        qsub_result = execute_remote_command(service, qsub_command)
        if qsub_result.returncode != 0:
            raise RemoteBridgeError(
                qsub_result.stderr.strip() or f"qsub failed with exit code {qsub_result.returncode}"
            )
        try:
            qsub_parse = parse_qsub_output(qsub_result.stdout)
        except ValueError as exc:
            raise RemoteBridgeError(str(exc)) from exc
        registry.mark_job_submitted(job_record.job_id, qsub_parse.pbs_job_id)
        return SubmittedProbeRun(
            run_id=run_record.run_id,
            job_id=job_record.job_id,
            pbs_job_id=qsub_parse.pbs_job_id,
        )
    finally:
        temp_path.unlink(missing_ok=True)

