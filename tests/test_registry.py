from pathlib import Path

from autoresearch.db import connect_db, init_db
from autoresearch.runs.registry import RunRegistry


def test_create_job_persists_draft_record(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"
    init_db(db_path)
    registry = RunRegistry(db_path)

    record = registry.create_job(
        run_id="run_demo",
        backend="pbs",
        queue="debug",
        walltime="00:10:00",
        filesystems="eagle",
        select_expr="1:ncpus=1",
        place_expr="scatter",
        submit_script_path="/tmp/submit.pbs",
        stdout_path="/tmp/stdout.log",
        stderr_path="/tmp/stderr.log",
    )

    assert record.job_id.startswith("job_")
    assert record.state == "DRAFT"
    assert record.pbs_job_id is None
    assert record.exec_host is None
    assert record.created_at == record.updated_at

    with connect_db(db_path) as conn:
        row = conn.execute(
            """
            SELECT job_id, run_id, backend, pbs_job_id, queue, walltime,
                   filesystems, select_expr, place_expr, exec_host, state,
                   submit_script_path, stdout_path, stderr_path,
                   created_at, updated_at
            FROM jobs
            WHERE job_id = ?
            """,
            (record.job_id,),
        ).fetchone()

    assert row is not None
    assert row["state"] == "DRAFT"
    assert row["pbs_job_id"] is None
    assert row["exec_host"] is None
    assert row["created_at"] == record.created_at
    assert row["updated_at"] == record.updated_at


def test_update_job_state_persists_state_and_pbs_metadata(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"
    init_db(db_path)
    registry = RunRegistry(db_path)

    created = registry.create_job(
        run_id="run_demo",
        backend="pbs",
        queue="debug",
        walltime="00:10:00",
        filesystems="eagle",
        select_expr="1:ncpus=1",
        place_expr="scatter",
        submit_script_path="/tmp/submit.pbs",
        stdout_path="/tmp/stdout.log",
        stderr_path="/tmp/stderr.log",
    )
    updated = registry.update_job_state(
        job_id=created.job_id,
        state="RUNNING",
        pbs_job_id="123456.polaris-pbs-01",
        exec_host="x1001/0",
    )

    assert updated.job_id == created.job_id
    assert updated.state == "RUNNING"
    assert updated.pbs_job_id == "123456.polaris-pbs-01"
    assert updated.exec_host == "x1001/0"
    assert updated.updated_at != created.updated_at

    with connect_db(db_path) as conn:
        row = conn.execute(
            """
            SELECT state, pbs_job_id, exec_host, updated_at
            FROM jobs
            WHERE job_id = ?
            """,
            (created.job_id,),
        ).fetchone()

    assert row is not None
    assert row["state"] == "RUNNING"
    assert row["pbs_job_id"] == "123456.polaris-pbs-01"
    assert row["exec_host"] == "x1001/0"
    assert row["updated_at"] == updated.updated_at


def test_list_jobs_returns_newest_first(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"
    init_db(db_path)
    registry = RunRegistry(db_path)

    first = registry.create_job(
        run_id="run_a",
        backend="pbs",
        queue="debug",
        walltime="00:10:00",
        filesystems="eagle",
        select_expr="1:ncpus=1",
        place_expr="scatter",
        submit_script_path="/tmp/submit-a.pbs",
        stdout_path="/tmp/stdout-a.log",
        stderr_path="/tmp/stderr-a.log",
    )
    second = registry.create_job(
        run_id="run_b",
        backend="pbs",
        queue="debug",
        walltime="00:10:00",
        filesystems="eagle",
        select_expr="1:ncpus=1",
        place_expr="scatter",
        submit_script_path="/tmp/submit-b.pbs",
        stdout_path="/tmp/stdout-b.log",
        stderr_path="/tmp/stderr-b.log",
    )

    records = registry.list_jobs()

    assert [record.job_id for record in records] == [second.job_id, first.job_id]
