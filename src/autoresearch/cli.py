from pathlib import Path
import shlex
import tempfile
from typing import Annotated, Optional

import typer

from autoresearch.bridge.remote_exec import (
    RemoteBridgeError,
    copy_from_remote,
    copy_to_remote,
    execute_remote_command,
)
from autoresearch.bridge.remote_fs import bootstrap_remote_root
from autoresearch.bridge.ssh_master import SSHMasterClient
from autoresearch.db import connect_db, init_db
from autoresearch.incidents.classifier import classify_incident
from autoresearch.incidents.fetch import IncidentFetchError, collect_incident_evidence
from autoresearch.incidents.normalize import IncidentNormalizationError, normalize_incident_evidence
from autoresearch.incidents.registry import IncidentRegistry
from autoresearch.incidents.summaries import render_incident_row, render_incident_summary
from autoresearch.executor.pbs import (
    build_qstat_command,
    build_qsub_command,
    parse_qstat_json,
    parse_qsub_output,
    render_pbs_script,
)
from autoresearch.executor.polaris import build_polaris_job_request, build_probe_job_request
from autoresearch.runs.registry import RunRegistry
from autoresearch.schemas import CommandResult, RunCreateRequest
from autoresearch.settings import ProbeSettings, load_settings


app = typer.Typer(help="Auto Research control plane CLI.")
db_app = typer.Typer(help="Database commands.")
run_app = typer.Typer(help="Run registry commands.")
job_app = typer.Typer(help="Job helpers.")
bridge_app = typer.Typer(help="ALCF bridge commands.")
remote_app = typer.Typer(help="Remote environment commands.")
incident_app = typer.Typer(help="Incident triage commands.")

app.add_typer(db_app, name="db")
app.add_typer(run_app, name="run")
app.add_typer(job_app, name="job")
app.add_typer(bridge_app, name="bridge")
app.add_typer(remote_app, name="remote")
app.add_typer(incident_app, name="incident")


@db_app.command("init")
def init_database() -> None:
    settings = load_settings()
    init_db(settings.paths.db_path)
    typer.echo(f"Initialized database at {settings.paths.db_path}")


@run_app.command("create")
def create_run(
    kind: str = typer.Option(..., "--kind"),
    project: str = typer.Option(..., "--project"),
    notes: Optional[str] = typer.Option(None, "--notes"),
) -> None:
    settings = load_settings()
    registry = RunRegistry(settings.paths.db_path)
    record = registry.create_run(
        RunCreateRequest(run_kind=kind, project=project, notes=notes)
    )
    typer.echo(f"Created run {record.run_id}")


@run_app.command("list")
def list_runs() -> None:
    settings = load_settings()
    registry = RunRegistry(settings.paths.db_path)
    for record in registry.list_runs():
        typer.echo(
            f"{record.run_id}\t{record.run_kind}\t{record.project}\t"
            f"{record.status}\t{record.created_at}"
        )


@job_app.command("list")
def list_jobs() -> None:
    settings = load_settings()
    registry = RunRegistry(settings.paths.db_path)
    for record in registry.list_jobs():
        typer.echo(
            f"{record.job_id}\t{record.run_id}\t{record.backend}\t{record.state}\t"
            f"{record.pbs_job_id or '-'}\t{record.updated_at}"
        )


@job_app.command("render-pbs")
def render_job_pbs(
    run_id: str = typer.Option(..., "--run-id"),
    project: str = typer.Option(..., "--project"),
    queue: str = typer.Option(..., "--queue"),
    walltime: str = typer.Option(..., "--walltime"),
    entrypoint_path: str = typer.Option(..., "--entrypoint-path"),
) -> None:
    settings = load_settings()
    request = build_polaris_job_request(
        run_id=run_id,
        project=project,
        queue=queue,
        walltime=walltime,
        entrypoint_path=entrypoint_path,
        remote_root=settings.remote_root,
    )
    rendered = render_pbs_script(request)
    typer.echo(rendered.script_text, nl=False)


@job_app.command("submit-probe")
def submit_probe(
    project: Optional[str] = typer.Option(None, "--project"),
    queue: Optional[str] = typer.Option(None, "--queue"),
    walltime: Optional[str] = typer.Option(None, "--walltime"),
) -> None:
    try:
        run_id, job_id, pbs_job_id = submit_probe_job(
            project=project,
            queue=queue,
            walltime=walltime,
        )
    except RemoteBridgeError as error:
        _fail_remote_bridge_error(error)
    typer.echo(f"{run_id}\t{job_id}\t{pbs_job_id}")


@job_app.command("poll")
def poll_probe(
    job_id: str = typer.Option(..., "--job-id"),
) -> None:
    try:
        state, pbs_job_id = poll_probe_job(job_id)
    except RemoteBridgeError as error:
        _fail_remote_bridge_error(error)
    typer.echo(f"{job_id}\t{pbs_job_id}\t{state}")


def build_bridge_service() -> SSHMasterClient:
    settings = load_settings()
    return SSHMasterClient(settings=settings.bridge)


def _echo_bridge_status(prefix: str, state: str, explanation: str) -> None:
    typer.echo(f"{prefix}: {state}")
    typer.echo(explanation)


def _echo_failed_command(result: CommandResult) -> None:
    typer.echo(
        f"Command failed ({result.returncode}): {' '.join(result.args)}",
        err=True,
    )
    if result.stderr:
        typer.echo(result.stderr, err=True)


def _fail_remote_bridge_error(error: RemoteBridgeError) -> None:
    typer.echo(str(error), err=True)
    raise typer.Exit(code=1)


def _resolve_probe_settings(
    settings,
    *,
    project: str | None,
    queue: str | None,
    walltime: str | None,
) -> ProbeSettings:
    return ProbeSettings(
        project=settings.probe.project if project is None else project,
        queue=settings.probe.queue if queue is None else queue,
        walltime=settings.probe.walltime if walltime is None else walltime,
    )


def _write_temporary_script(script_text: str, run_id: str) -> Path:
    temp_file = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=f"-{run_id}.pbs",
        delete=False,
    )
    try:
        temp_file.write(script_text)
        temp_file.flush()
        return Path(temp_file.name)
    finally:
        temp_file.close()


def _probe_state_from_pbs_state(pbs_state: str, exit_status: int | None = None) -> str:
    normalized = pbs_state.strip().upper()
    state_map = {
        "Q": "QUEUED",
        "R": "RUNNING",
    }
    if normalized == "F":
        if exit_status is None:
            return pbs_state
        if exit_status != 0:
            return "FAILED"
        return "SUCCEEDED"
    return state_map.get(normalized, pbs_state)


def submit_probe_job(
    project: str | None = None,
    queue: str | None = None,
    walltime: str | None = None,
) -> tuple[str, str, str]:
    settings = load_settings()
    service = build_bridge_service()
    bootstrap_remote_root(service, settings.remote_root, force=False)

    probe_settings = _resolve_probe_settings(
        settings,
        project=project,
        queue=queue,
        walltime=walltime,
    )
    registry = RunRegistry(settings.paths.db_path)
    run_record = registry.create_run(
        RunCreateRequest(run_kind="probe", project=probe_settings.project)
    )

    request = build_probe_job_request(
        run_id=run_record.run_id,
        entrypoint_path=f"{settings.remote_root}/jobs/probe/entrypoint.sh",
        remote_root=settings.remote_root,
        probe_settings=probe_settings,
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
    registry.get_job(job_record.job_id)

    temp_script = _write_temporary_script(rendered.script_text, run_record.run_id)
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

        copy_result = copy_to_remote(service, temp_script, request.submit_script_path, settings.remote_root)
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
        return run_record.run_id, job_record.job_id, qsub_parse.pbs_job_id
    finally:
        temp_script.unlink(missing_ok=True)


def poll_probe_job(job_id: str) -> tuple[str, str]:
    settings = load_settings()
    service = build_bridge_service()
    registry = RunRegistry(settings.paths.db_path)
    try:
        job_record = registry.get_job(job_id)
    except KeyError as exc:
        raise RemoteBridgeError(str(exc)) from exc

    if not job_record.pbs_job_id:
        raise RemoteBridgeError(f"job {job_id} has not been submitted yet")

    qstat_command = shlex.join(build_qstat_command(job_record.pbs_job_id))
    qstat_result = execute_remote_command(service, qstat_command)
    if qstat_result.returncode != 0:
        raise RemoteBridgeError(
            qstat_result.stderr.strip() or f"qstat failed with exit code {qstat_result.returncode}"
        )

    try:
        qstat_parse = parse_qstat_json(qstat_result.stdout)
    except ValueError as exc:
        raise RemoteBridgeError(str(exc)) from exc

    state = _probe_state_from_pbs_state(qstat_parse.state, qstat_parse.exit_status)
    registry.update_job_state(
        job_id=job_record.job_id,
        state=state,
        pbs_job_id=job_record.pbs_job_id,
        exec_host=qstat_parse.exec_host,
    )
    return state, job_record.pbs_job_id


def run_remote_bootstrap(force: bool) -> None:
    try:
        settings = load_settings()
        service = build_bridge_service()
        bootstrap_remote_root(service, settings.remote_root, force=force)
    except RemoteBridgeError as error:
        _fail_remote_bridge_error(error)


@incident_app.command("scan")
def scan_incident(
    job_id: str = typer.Option(..., "--job-id"),
) -> None:
    settings = load_settings()
    registry = RunRegistry(settings.paths.db_path)
    try:
        job_record = registry.get_job(job_id)
    except KeyError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1)
    bridge = build_bridge_service()

    try:
        fetched = collect_incident_evidence(settings.paths, job_record, bridge)
    except IncidentFetchError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1)

    try:
        normalized = normalize_incident_evidence(job_record=job_record, fetched=fetched)
    except IncidentNormalizationError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1)
    classified = classify_incident(normalized)
    if classified is None:
        typer.echo(f"No incident detected for job {job_id}.")
        return

    incident_registry = IncidentRegistry(settings.paths.db_path)
    with connect_db(settings.paths.db_path) as conn:
        existing_row = conn.execute(
            """
            SELECT 1
            FROM incidents
            WHERE job_id IS ? AND category = ? AND fingerprint IS ?
            LIMIT 1
            """,
            (normalized.job_id, classified.category, classified.fingerprint),
        ).fetchone()
    was_existing = existing_row is not None
    evidence = {
        "scan_time": normalized.scan_time,
        "snapshot_dir": str(normalized.snapshot_dir),
        "qstat_comment": normalized.comment,
        "job_state": normalized.job_state,
        "exec_host": normalized.exec_host,
        "matched_lines": list(classified.matched_lines),
        "classifier_rule": classified.rule_name,
    }
    record = incident_registry.upsert_incident(
        run_id=normalized.run_id,
        job_id=normalized.job_id,
        severity=classified.severity,
        category=classified.category,
        fingerprint=classified.fingerprint,
        evidence=evidence,
    )
    action = "updated" if was_existing else "created"
    typer.echo(f"{action.capitalize()} incident {record.incident_id} for job {job_id}")


@incident_app.command("list")
def list_incidents() -> None:
    settings = load_settings()
    registry = IncidentRegistry(settings.paths.db_path)
    for record in registry.list_open_incidents():
        typer.echo(render_incident_row(record))


@incident_app.command("summarize")
def summarize_incidents() -> None:
    settings = load_settings()
    registry = IncidentRegistry(settings.paths.db_path)
    typer.echo(render_incident_summary(registry.summarize_open_incidents()))


@bridge_app.command("attach")
def attach_bridge() -> None:
    service = build_bridge_service()
    result = service.attach()
    if result.returncode != 0:
        _echo_failed_command(result)
        raise typer.Exit(code=result.returncode)
    _echo_bridge_status(
        f"Bridge {service.settings.alias}",
        "ATTACHED",
        "OpenSSH control master attach command completed.",
    )


@bridge_app.command("check")
def check_bridge() -> None:
    service = build_bridge_service()
    status = service.status()
    _echo_bridge_status(f"Bridge {status.alias}", status.state, status.explanation)
    if status.state != "ATTACHED":
        raise typer.Exit(code=1)


@bridge_app.command("status")
def status_bridge() -> None:
    service = build_bridge_service()
    status = service.status()
    _echo_bridge_status(f"Bridge {status.alias}", status.state, status.explanation)


@bridge_app.command("detach")
def detach_bridge() -> None:
    service = build_bridge_service()
    result = service.detach()
    if result.returncode == 0:
        _echo_bridge_status(
            f"Bridge {service.settings.alias}",
            "DETACHED",
            "OpenSSH control master exited cleanly.",
        )
        return

    status = service.status()
    if status.state == "DETACHED":
        _echo_bridge_status(f"Bridge {status.alias}", status.state, status.explanation)
        return

    _echo_failed_command(result)
    raise typer.Exit(code=result.returncode)


@bridge_app.command("exec")
def exec_bridge(
    remote_command: Annotated[list[str], typer.Argument(..., help="Remote command to run.")],
) -> None:
    try:
        service = build_bridge_service()
        result = execute_remote_command(service, shlex.join(remote_command))
    except RemoteBridgeError as error:
        _fail_remote_bridge_error(error)
    if result.returncode != 0:
        _echo_failed_command(result)
        raise typer.Exit(code=result.returncode)
    if result.stdout:
        typer.echo(result.stdout, nl=False)


@bridge_app.command("copy-to")
def bridge_copy_to(
    src: Path = typer.Option(..., "--src"),
    dst: str = typer.Option(..., "--dst"),
) -> None:
    try:
        settings = load_settings()
        service = build_bridge_service()
        result = copy_to_remote(service, src, dst, settings.remote_root)
    except RemoteBridgeError as error:
        _fail_remote_bridge_error(error)
    if result.returncode != 0:
        _echo_failed_command(result)
        raise typer.Exit(code=result.returncode)


@bridge_app.command("copy-from")
def bridge_copy_from(
    src: str = typer.Option(..., "--src"),
    dst: Path = typer.Option(..., "--dst"),
) -> None:
    try:
        settings = load_settings()
        service = build_bridge_service()
        result = copy_from_remote(service, src, dst, settings.remote_root)
    except RemoteBridgeError as error:
        _fail_remote_bridge_error(error)
    if result.returncode != 0:
        _echo_failed_command(result)
        raise typer.Exit(code=result.returncode)


@remote_app.command("bootstrap")
def remote_bootstrap(
    force: bool = typer.Option(False, "--force"),
) -> None:
    run_remote_bootstrap(force=force)
    typer.echo("Remote bootstrap completed.")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
