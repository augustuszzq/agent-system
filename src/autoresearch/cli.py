from datetime import UTC, datetime
from pathlib import Path
import shlex
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
from autoresearch.decisions import DecisionLog
from autoresearch.db import connect_db, init_db
from autoresearch.incidents.classifier import classify_incident
from autoresearch.incidents.fetch import IncidentFetchError, collect_incident_evidence
from autoresearch.incidents.normalize import IncidentNormalizationError, normalize_incident_evidence
from autoresearch.incidents.registry import IncidentRegistry
from autoresearch.incidents.summaries import render_incident_row, render_incident_summary
from autoresearch.executor.pbs import (
    build_qstat_command,
    parse_qstat_json,
    render_pbs_script,
)
from autoresearch.executor.polaris import build_polaris_job_request
from autoresearch.executor.probe_submit import submit_live_probe_run
from autoresearch.reports.daily import DailyReportBuilder
from autoresearch.retries.executor import RetryExecutor
from autoresearch.retries.policy import RetryPolicy
from autoresearch.retries.registry import RetryRequestRegistry
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
retry_app = typer.Typer(help="Retry commands.")
report_app = typer.Typer(help="Report commands.")

app.add_typer(db_app, name="db")
app.add_typer(run_app, name="run")
app.add_typer(job_app, name="job")
app.add_typer(bridge_app, name="bridge")
app.add_typer(remote_app, name="remote")
app.add_typer(incident_app, name="incident")
app.add_typer(retry_app, name="retry")
app.add_typer(report_app, name="report")


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


def _fail_cli_error(message: str) -> None:
    typer.echo(message, err=True)
    raise typer.Exit(code=1)


def _format_retry_request_row(record) -> str:
    result_job_id = record.result_job_id or "-"
    return (
        f"{record.retry_request_id}\t{record.incident_id}\t{record.requested_action}\t"
        f"{record.approval_status}\t{record.execution_status}\t{result_job_id}\t{record.updated_at}"
    )


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
    submitted = submit_live_probe_run(
        settings=settings,
        service=service,
        run_kind="probe",
        notes=None,
        project=probe_settings.project,
        queue=probe_settings.queue,
        walltime=probe_settings.walltime,
    )
    return submitted.run_id, submitted.job_id, submitted.pbs_job_id


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


@retry_app.command("request")
def request_retry(
    incident_id: str = typer.Option(..., "--incident-id"),
) -> None:
    settings = load_settings()
    incident_registry = IncidentRegistry(settings.paths.db_path)
    retry_registry = RetryRequestRegistry(settings.paths.db_path)
    policy = RetryPolicy(settings.retry_policy)

    try:
        incident = incident_registry.get_incident(incident_id)
    except KeyError as error:
        _fail_cli_error(str(error))

    if incident.status != "OPEN":
        _fail_cli_error(f"incident {incident_id} is not open")
    if not policy.allows(category=incident.category, action="RETRY_SAME_CONFIG"):
        _fail_cli_error(f"incident {incident_id} category {incident.category} is not retry-eligible")
    if retry_registry.find_active_request(incident_id, "RETRY_SAME_CONFIG") is not None:
        _fail_cli_error(f"active retry request already exists for incident {incident_id}")
    if incident.run_id is None or incident.job_id is None:
        _fail_cli_error(f"incident {incident_id} is missing run/job linkage")

    run_registry = RunRegistry(settings.paths.db_path)
    try:
        source_run = run_registry.get_run(incident.run_id)
        source_job = run_registry.get_job(incident.job_id)
    except KeyError as error:
        _fail_cli_error(str(error))
    if source_job.run_id != source_run.run_id:
        _fail_cli_error(
            f"incident {incident_id} has inconsistent run/job linkage: "
            f"run {source_run.run_id} does not match job {source_job.job_id} run {source_job.run_id}"
        )

    try:
        record = retry_registry.create_request(
            incident_id=incident.incident_id,
            source_run_id=source_run.run_id,
            source_job_id=source_job.job_id,
            source_pbs_job_id=source_job.pbs_job_id,
            requested_action="RETRY_SAME_CONFIG",
        )
    except ValueError as error:
        _fail_cli_error(str(error))
    typer.echo(f"{record.retry_request_id}\t{record.approval_status}\t{incident.category}")


@retry_app.command("list")
def list_retry_requests() -> None:
    settings = load_settings()
    retry_registry = RetryRequestRegistry(settings.paths.db_path)
    for record in retry_registry.list_requests():
        typer.echo(_format_retry_request_row(record))


@report_app.command("daily")
def report_daily() -> None:
    settings = load_settings()
    builder = DailyReportBuilder(
        db_path=settings.paths.db_path,
        state_dir=settings.paths.state_dir,
    )
    report_date = datetime.now(UTC).date().isoformat()
    result = builder.build(report_date=report_date)
    builder.write(result)
    typer.echo(result.markdown, nl=False)


@retry_app.command("approve")
def approve_retry(
    retry_request_id: str = typer.Option(..., "--retry-request-id"),
    reason: str = typer.Option(..., "--reason"),
) -> None:
    settings = load_settings()
    retry_registry = RetryRequestRegistry(settings.paths.db_path)
    decision_log = DecisionLog(settings.paths.db_path)
    try:
        record = retry_registry.approve_with_decision(
            retry_request_id,
            actor="operator",
            reason=reason,
            decision_log=decision_log,
        )
    except (KeyError, ValueError) as error:
        _fail_cli_error(str(error))
    typer.echo(f"{record.retry_request_id}\t{record.approval_status}\t{record.execution_status}")


@retry_app.command("reject")
def reject_retry(
    retry_request_id: str = typer.Option(..., "--retry-request-id"),
    reason: str = typer.Option(..., "--reason"),
) -> None:
    settings = load_settings()
    retry_registry = RetryRequestRegistry(settings.paths.db_path)
    decision_log = DecisionLog(settings.paths.db_path)
    try:
        record = retry_registry.reject_with_decision(
            retry_request_id,
            actor="operator",
            reason=reason,
            decision_log=decision_log,
        )
    except (KeyError, ValueError) as error:
        _fail_cli_error(str(error))
    typer.echo(f"{record.retry_request_id}\t{record.approval_status}\t{record.execution_status}")


@retry_app.command("execute")
def execute_retry(
    retry_request_id: str = typer.Option(..., "--retry-request-id"),
) -> None:
    settings = load_settings()
    executor = RetryExecutor(
        db_path=settings.paths.db_path,
        policy=RetryPolicy(settings.retry_policy),
        actor="operator",
        submitter=lambda **kwargs: submit_live_probe_run(
            settings=settings,
            service=build_bridge_service(),
            **kwargs,
        ),
    )
    try:
        record = executor.execute(retry_request_id)
    except (RemoteBridgeError, KeyError, ValueError) as error:
        _fail_cli_error(str(error))
    typer.echo(
        f"{record.retry_request_id}\t{record.result_run_id}\t{record.result_job_id}\t{record.result_pbs_job_id}"
    )


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
        fetched = collect_incident_evidence(settings.paths, job_record, bridge, settings.remote_root)
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
        typer.echo(f"No incident detected for job {job_id} (source={fetched.source}).")
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
        "evidence_source": fetched.source,
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
    typer.echo(
        f"{action.capitalize()} incident {record.incident_id} for job {job_id} "
        f"(source={fetched.source})"
    )


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
