from typing import Optional

import typer

from autoresearch.db import init_db
from autoresearch.runs.registry import RunRegistry
from autoresearch.schemas import RunCreateRequest
from autoresearch.settings import load_settings


app = typer.Typer(help="Auto Research control plane CLI.")
db_app = typer.Typer(help="Database commands.")
run_app = typer.Typer(help="Run registry commands.")

app.add_typer(db_app, name="db")
app.add_typer(run_app, name="run")


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


def main() -> None:
    app()


if __name__ == "__main__":
    main()
