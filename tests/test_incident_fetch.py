import hashlib
import json
import shlex
from pathlib import Path

import pytest

from autoresearch.paths import AppPaths, incident_snapshot_dir, incident_state_dir
from autoresearch.runs.registry import JobRecord
from autoresearch.schemas import BridgeStatusResult, CommandResult


FIXTURES = Path(__file__).parent / "fixtures" / "incidents"


def _paths(tmp_path: Path) -> AppPaths:
    return AppPaths(
        repo_root=tmp_path,
        state_dir=tmp_path / "state",
        cache_dir=tmp_path / "cache",
        logs_dir=tmp_path / "logs",
        db_path=tmp_path / "state" / "autoresearch.db",
    )


def _job_record() -> JobRecord:
    return JobRecord(
        job_id="job_demo",
        run_id="run_demo",
        backend="polaris",
        pbs_job_id="12345.polaris",
        queue="debug",
        walltime="00:10:00",
        filesystems="eagle",
        select_expr="1",
        place_expr="scatter",
        exec_host=None,
        state="SUBMITTED",
        submit_script_path="/remote/repo/runs/run_demo/submit.sh",
        stdout_path="/remote/repo/runs/run_demo/stdout.log",
        stderr_path="/remote/repo/runs/run_demo/stderr.log",
        created_at="2026-04-16T01:02:03+00:00",
        updated_at="2026-04-16T01:02:03+00:00",
    )


def _command_result(*, stdout: str = "", stderr: str = "", returncode: int = 0) -> CommandResult:
    return CommandResult(
        args=(),
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=0.1,
    )


class FakeBridgeClient:
    def __init__(
        self,
        *,
        state: str,
        exec_results: dict[str, CommandResult] | None = None,
    ) -> None:
        self._state = state
        self._exec_results = exec_results or {}
        self.exec_calls: list[str] = []

    def status(self) -> BridgeStatusResult:
        return BridgeStatusResult(
            alias="polaris",
            state=self._state,
            explanation=f"bridge is {self._state.lower()}",
        )

    def exec(self, command: str) -> CommandResult:
        self.exec_calls.append(command)
        try:
            return self._exec_results[command]
        except KeyError as exc:
            raise AssertionError(f"unexpected command: {command}") from exc


def _fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _combined_hash(stdout_tail: str, stderr_tail: str) -> str:
    return hashlib.sha256(f"{stdout_tail}\n\x1f\n{stderr_tail}".encode("utf-8")).hexdigest()


def test_collect_incident_evidence_fetches_live_snapshot_when_bridge_attached(
    tmp_path: Path,
) -> None:
    from autoresearch.executor.pbs import build_qstat_command
    from autoresearch.incidents.fetch import collect_incident_evidence

    paths = _paths(tmp_path)
    job_record = _job_record()
    qstat_command = shlex.join(build_qstat_command(job_record.pbs_job_id or ""))
    stdout_command = "tail -n 200 /remote/repo/runs/run_demo/stdout.log"
    stderr_command = "tail -n 200 /remote/repo/runs/run_demo/stderr.log"
    bridge = FakeBridgeClient(
        state="ATTACHED",
        exec_results={
            qstat_command: _command_result(stdout=_fixture_text("qstat_running.json")),
            stdout_command: _command_result(stdout="stdout line 1\nstdout line 2\n"),
            stderr_command: _command_result(stdout="stderr line 1\n"),
        },
    )

    result = collect_incident_evidence(
        paths=paths,
        job_record=job_record,
        bridge_client=bridge,
    )

    assert result.source == "live"
    assert result.snapshot.snapshot_dir == incident_snapshot_dir(
        paths, job_record.job_id, result.snapshot.scan_time
    )
    assert result.previous_snapshot is None
    assert result.snapshot.qstat_json_path.read_text(encoding="utf-8") == _fixture_text(
        "qstat_running.json"
    )
    assert result.snapshot.stdout_tail_path.read_text(encoding="utf-8") == "stdout line 1\nstdout line 2\n"
    assert result.snapshot.stderr_tail_path.read_text(encoding="utf-8") == "stderr line 1\n"
    assert bridge.exec_calls == [qstat_command, stdout_command, stderr_command]


def test_collect_incident_evidence_falls_back_to_latest_snapshot_when_bridge_detached(
    tmp_path: Path,
) -> None:
    from autoresearch.incidents.fetch import collect_incident_evidence

    paths = _paths(tmp_path)
    job_record = _job_record()
    first_scan = "2026-04-16T02:03:04+00:00"
    latest_dir = incident_snapshot_dir(paths, job_record.job_id, first_scan)
    latest_dir.mkdir(parents=True, exist_ok=True)
    (latest_dir / "qstat.json").write_text(_fixture_text("qstat_running.json"), encoding="utf-8")
    (latest_dir / "stdout.tail.log").write_text("stdout cached\n", encoding="utf-8")
    (latest_dir / "stderr.tail.log").write_text("stderr cached\n", encoding="utf-8")

    bridge = FakeBridgeClient(state="DETACHED")

    result = collect_incident_evidence(
        paths=paths,
        job_record=job_record,
        bridge_client=bridge,
    )

    assert result.source == "local-fallback"
    assert result.snapshot.snapshot_dir == latest_dir
    assert result.previous_snapshot is None
    assert incident_state_dir(paths, job_record.job_id) == paths.state_dir / "incidents" / job_record.job_id
    assert bridge.exec_calls == []


def test_collect_incident_evidence_raises_when_no_live_or_local_evidence(
    tmp_path: Path,
) -> None:
    from autoresearch.incidents.fetch import IncidentFetchError, collect_incident_evidence

    paths = _paths(tmp_path)
    job_record = _job_record()
    bridge = FakeBridgeClient(state="DETACHED")

    with pytest.raises(IncidentFetchError, match="No incident evidence available"):
        collect_incident_evidence(
            paths=paths,
            job_record=job_record,
            bridge_client=bridge,
        )


def test_normalize_incident_evidence_parses_qstat_and_stabilizes_repeated_tail_hashes(
    tmp_path: Path,
) -> None:
    from autoresearch.incidents.fetch import collect_incident_evidence
    from autoresearch.incidents.normalize import normalize_incident_evidence

    paths = _paths(tmp_path)
    job_record = _job_record()

    first_dir = incident_snapshot_dir(paths, job_record.job_id, "2026-04-16T01:02:03+00:00")
    first_dir.mkdir(parents=True, exist_ok=True)
    qstat_json = json.loads(_fixture_text("qstat_running.json"))
    qstat_json["Jobs"][job_record.pbs_job_id]["comment"] = "filesystem unavailable: eagle"
    first_qstat_text = json.dumps(qstat_json)
    (first_dir / "qstat.json").write_text(first_qstat_text, encoding="utf-8")
    (first_dir / "stdout.tail.log").write_text("same stdout\n", encoding="utf-8")
    (first_dir / "stderr.tail.log").write_text("same stderr\n", encoding="utf-8")

    second_dir = incident_snapshot_dir(paths, job_record.job_id, "2026-04-16T02:03:04+00:00")
    second_dir.mkdir(parents=True, exist_ok=True)
    (second_dir / "qstat.json").write_text(first_qstat_text, encoding="utf-8")
    (second_dir / "stdout.tail.log").write_text("same stdout\n", encoding="utf-8")
    (second_dir / "stderr.tail.log").write_text("same stderr\n", encoding="utf-8")

    bridge = FakeBridgeClient(state="DETACHED")
    fetched = collect_incident_evidence(
        paths=paths,
        job_record=job_record,
        bridge_client=bridge,
    )

    normalized = normalize_incident_evidence(job_record=job_record, fetched=fetched)

    expected_hash = _combined_hash("same stdout\n", "same stderr\n")
    assert normalized.job_id == job_record.job_id
    assert normalized.run_id == job_record.run_id
    assert normalized.pbs_job_id == job_record.pbs_job_id
    assert normalized.job_state == "R"
    assert normalized.comment == "filesystem unavailable: eagle"
    assert normalized.exec_host == "x1001c1s2b0"
    assert normalized.stdout_tail == "same stdout\n"
    assert normalized.stderr_tail == "same stderr\n"
    assert normalized.snapshot_dir == second_dir
    assert normalized.scan_time == "2026-04-16T02:03:04+00:00"
    assert normalized.current_log_tail_hash == expected_hash
    assert normalized.previous_log_tail_hash == expected_hash
