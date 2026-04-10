from pathlib import Path

from autoresearch.db import init_db
from autoresearch.runs.registry import RunRegistry
from autoresearch.schemas import RunCreateRequest


def test_create_run_persists_initial_metadata(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"
    init_db(db_path)
    registry = RunRegistry(db_path)

    record = registry.create_run(
        RunCreateRequest(run_kind="local-debug", project="demo", notes="hello")
    )

    assert record.run_kind == "local-debug"
    assert record.project == "demo"
    assert record.status == "CREATED"
    assert record.notes == "hello"
    assert record.run_id.startswith("run_")


def test_list_runs_returns_newest_first(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"
    init_db(db_path)
    registry = RunRegistry(db_path)

    first = registry.create_run(RunCreateRequest(run_kind="a", project="demo"))
    second = registry.create_run(RunCreateRequest(run_kind="b", project="demo"))

    records = registry.list_runs()

    assert [record.run_id for record in records] == [second.run_id, first.run_id]
