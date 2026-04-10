from typing import Optional

import typer

from autoresearch.bridge.ssh_master import SSHMasterClient
from autoresearch.db import init_db
from autoresearch.runs.registry import RunRegistry
from autoresearch.schemas import RunCreateRequest
from autoresearch.settings import load_settings


app = typer.Typer(help="Auto Research control plane CLI.")
db_app = typer.Typer(help="Database commands.")
run_app = typer.Typer(help="Run registry commands.")
bridge_app = typer.Typer(help="ALCF bridge commands.")

app.add_typer(db_app, name="db")
app.add_typer(run_app, name="run")
app.add_typer(bridge_app, name="bridge")


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


def build_bridge_service() -> SSHMasterClient:
    settings = load_settings()
    return SSHMasterClient(settings=settings.bridge)


def _echo_bridge_status(prefix: str, state: str, explanation: str) -> None:
    typer.echo(f"{prefix}: {state}")
    typer.echo(explanation)


def _echo_failed_command(result: object) -> None:
    stderr = getattr(result, "stderr", "")
    if stderr:
        typer.echo(stderr, err=True)


@bridge_app.command("attach")
def attach_bridge() -> None:
    service = build_bridge_service()
    result = service.attach()
    if result.returncode != 0:
        _echo_failed_command(result)
        raise typer.Exit(code=result.returncode)
    typer.echo(f"Attached bridge {service.settings.alias}")


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
    if result.returncode != 0:
        _echo_failed_command(result)
        raise typer.Exit(code=result.returncode)
    typer.echo(f"Detached bridge {service.settings.alias}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
