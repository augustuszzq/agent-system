import hashlib
import json
import shlex
from pathlib import Path

import pytest

from autoresearch.paths import AppPaths, incident_snapshot_dir, incident_state_dir
from autoresearch.runs.registry import JobRecord
from autoresearch.schemas import BridgeStatusResult, CommandResult


FIXTURES = Path(__file__).parent / "fixtures" / "incidents"
REMOTE_ROOT = "/remote/repo"


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
        remote_root=REMOTE_ROOT,
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


def test_collect_incident_evidence_prefers_qstat_log_paths_for_live_tails(
    tmp_path: Path,
) -> None:
    from autoresearch.executor.pbs import build_qstat_command
    from autoresearch.incidents.fetch import collect_incident_evidence

    paths = _paths(tmp_path)
    job_record = _job_record()
    job_record = job_record.__class__(
        **{
            **job_record.__dict__,
            "stdout_path": None,
            "stderr_path": "/remote/repo/runs/run_demo/stale-stderr.log",
        }
    )
    qstat_payload = json.loads(_fixture_text("qstat_running.json"))
    qstat_payload["Jobs"][job_record.pbs_job_id]["Output_Path"] = "polaris:/remote/repo/qstat/stdout.log"
    qstat_payload["Jobs"][job_record.pbs_job_id]["Error_Path"] = "polaris:/remote/repo/qstat/stderr.log"
    qstat_text = json.dumps(qstat_payload)
    qstat_command = shlex.join(build_qstat_command(job_record.pbs_job_id or ""))
    stdout_command = "tail -n 200 /remote/repo/qstat/stdout.log"
    stderr_command = "tail -n 200 /remote/repo/qstat/stderr.log"
    bridge = FakeBridgeClient(
        state="ATTACHED",
        exec_results={
            qstat_command: _command_result(stdout=qstat_text),
            stdout_command: _command_result(stdout="stdout from qstat path\n"),
            stderr_command: _command_result(stdout="stderr from qstat path\n"),
        },
    )

    result = collect_incident_evidence(
        paths=paths,
        job_record=job_record,
        bridge_client=bridge,
        remote_root=REMOTE_ROOT,
    )

    assert result.source == "live"
    assert result.snapshot.stdout_tail_path.read_text(encoding="utf-8") == "stdout from qstat path\n"
    assert result.snapshot.stderr_tail_path.read_text(encoding="utf-8") == "stderr from qstat path\n"
    assert bridge.exec_calls == [qstat_command, stdout_command, stderr_command]


def test_collect_incident_evidence_falls_back_to_job_log_paths_when_qstat_paths_are_blank(
    tmp_path: Path,
) -> None:
    from autoresearch.executor.pbs import build_qstat_command
    from autoresearch.incidents.fetch import collect_incident_evidence

    paths = _paths(tmp_path)
    job_record = _job_record()
    qstat_payload = json.loads(_fixture_text("qstat_running.json"))
    qstat_payload["Jobs"][job_record.pbs_job_id]["Output_Path"] = "   "
    qstat_payload["Jobs"][job_record.pbs_job_id]["Error_Path"] = "\t"
    qstat_text = json.dumps(qstat_payload)
    qstat_command = shlex.join(build_qstat_command(job_record.pbs_job_id or ""))
    stdout_command = "tail -n 200 /remote/repo/runs/run_demo/stdout.log"
    stderr_command = "tail -n 200 /remote/repo/runs/run_demo/stderr.log"
    bridge = FakeBridgeClient(
        state="ATTACHED",
        exec_results={
            qstat_command: _command_result(stdout=qstat_text),
            stdout_command: _command_result(stdout="stdout from stored path\n"),
            stderr_command: _command_result(stdout="stderr from stored path\n"),
        },
    )

    result = collect_incident_evidence(
        paths=paths,
        job_record=job_record,
        bridge_client=bridge,
        remote_root=REMOTE_ROOT,
    )

    assert result.source == "live"
    assert result.snapshot.stdout_tail_path.read_text(encoding="utf-8") == "stdout from stored path\n"
    assert result.snapshot.stderr_tail_path.read_text(encoding="utf-8") == "stderr from stored path\n"
    assert bridge.exec_calls == [qstat_command, stdout_command, stderr_command]


def test_collect_incident_evidence_rejects_out_of_root_qstat_paths_and_uses_stored_paths(
    tmp_path: Path,
) -> None:
    from autoresearch.executor.pbs import build_qstat_command
    from autoresearch.incidents.fetch import collect_incident_evidence

    paths = _paths(tmp_path)
    job_record = _job_record()
    qstat_payload = json.loads(_fixture_text("qstat_running.json"))
    qstat_payload["Jobs"][job_record.pbs_job_id]["Output_Path"] = "polaris:/etc/passwd"
    qstat_payload["Jobs"][job_record.pbs_job_id]["Error_Path"] = "polaris:/var/log/messages"
    qstat_text = json.dumps(qstat_payload)
    qstat_command = shlex.join(build_qstat_command(job_record.pbs_job_id or ""))
    stdout_command = "tail -n 200 /remote/repo/runs/run_demo/stdout.log"
    stderr_command = "tail -n 200 /remote/repo/runs/run_demo/stderr.log"
    bridge = FakeBridgeClient(
        state="ATTACHED",
        exec_results={
            qstat_command: _command_result(stdout=qstat_text),
            stdout_command: _command_result(stdout="stdout from stored path\n"),
            stderr_command: _command_result(stdout="stderr from stored path\n"),
        },
    )

    result = collect_incident_evidence(
        paths=paths,
        job_record=job_record,
        bridge_client=bridge,
        remote_root=REMOTE_ROOT,
    )

    assert result.source == "live"
    assert result.snapshot.stdout_tail_path.read_text(encoding="utf-8") == "stdout from stored path\n"
    assert result.snapshot.stderr_tail_path.read_text(encoding="utf-8") == "stderr from stored path\n"
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
        remote_root=REMOTE_ROOT,
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
            remote_root=REMOTE_ROOT,
        )


def test_collect_incident_evidence_raises_when_live_log_paths_are_unusable(
    tmp_path: Path,
) -> None:
    from autoresearch.executor.pbs import build_qstat_command
    from autoresearch.incidents.fetch import IncidentFetchError, collect_incident_evidence

    paths = _paths(tmp_path)
    job_record = _job_record()
    job_record = job_record.__class__(
        **{
            **job_record.__dict__,
            "stdout_path": None,
            "stderr_path": "   ",
        }
    )
    qstat_payload = json.loads(_fixture_text("qstat_running.json"))
    qstat_payload["Jobs"][job_record.pbs_job_id]["Output_Path"] = " "
    qstat_payload["Jobs"][job_record.pbs_job_id]["Error_Path"] = "\t"
    qstat_command = shlex.join(build_qstat_command(job_record.pbs_job_id or ""))
    bridge = FakeBridgeClient(
        state="ATTACHED",
        exec_results={qstat_command: _command_result(stdout=json.dumps(qstat_payload))},
    )

    with pytest.raises(IncidentFetchError, match="has no usable stdout/stderr paths"):
        collect_incident_evidence(
            paths=paths,
            job_record=job_record,
            bridge_client=bridge,
            remote_root=REMOTE_ROOT,
        )


def test_collect_incident_evidence_keeps_live_snapshot_when_one_log_stream_is_missing(
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
            stdout_command: _command_result(stdout="fresh stdout\n"),
            stderr_command: _command_result(stderr="missing stderr log", returncode=1),
        },
    )

    result = collect_incident_evidence(
        paths=paths,
        job_record=job_record,
        bridge_client=bridge,
        remote_root=REMOTE_ROOT,
    )

    assert result.source == "live"
    assert result.snapshot.stdout_tail_path.read_text(encoding="utf-8") == "fresh stdout\n"
    assert result.snapshot.stderr_tail_path.read_text(encoding="utf-8") == ""


def test_collect_incident_evidence_falls_back_to_local_snapshot_when_live_persistence_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoresearch.executor.pbs import build_qstat_command
    from autoresearch.incidents import fetch

    paths = _paths(tmp_path)
    job_record = _job_record()
    scan_time = "2026-04-16T02:03:04+00:00"
    existing_dir = incident_snapshot_dir(paths, job_record.job_id, scan_time)
    existing_dir.mkdir(parents=True, exist_ok=True)
    (existing_dir / "qstat.json").write_text(_fixture_text("qstat_running.json"), encoding="utf-8")
    (existing_dir / "stdout.tail.log").write_text("cached stdout\n", encoding="utf-8")
    (existing_dir / "stderr.tail.log").write_text("cached stderr\n", encoding="utf-8")

    monkeypatch.setattr(fetch, "_scan_time_now", lambda: scan_time)

    original_write_text = Path.write_text

    def fail_qstat_write(self: Path, data: str, *args, **kwargs) -> int:
        if self.name == "qstat.json" and self.parent.name.startswith(scan_time):
            raise OSError("disk full")
        return original_write_text(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_qstat_write)

    qstat_command = shlex.join(build_qstat_command(job_record.pbs_job_id or ""))
    stdout_command = "tail -n 200 /remote/repo/runs/run_demo/stdout.log"
    stderr_command = "tail -n 200 /remote/repo/runs/run_demo/stderr.log"
    bridge = FakeBridgeClient(
        state="ATTACHED",
        exec_results={
            qstat_command: _command_result(stdout=_fixture_text("qstat_running.json")),
            stdout_command: _command_result(stdout="live stdout\n"),
            stderr_command: _command_result(stdout="live stderr\n"),
        },
    )

    result = fetch.collect_incident_evidence(
        paths=paths,
        job_record=job_record,
        bridge_client=bridge,
        remote_root=REMOTE_ROOT,
    )

    assert result.source == "local-fallback"
    assert result.snapshot.snapshot_dir == existing_dir
    assert result.snapshot.stdout_tail_path.read_text(encoding="utf-8") == "cached stdout\n"
    assert result.snapshot.stderr_tail_path.read_text(encoding="utf-8") == "cached stderr\n"
    assert result.previous_snapshot is None


def test_collect_incident_evidence_persists_fresh_live_snapshot_when_same_second_repeats(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoresearch.executor.pbs import build_qstat_command
    from autoresearch.incidents import fetch

    paths = _paths(tmp_path)
    job_record = _job_record()
    scan_time = "2026-04-16T02:03:04+00:00"
    existing_dir = incident_snapshot_dir(paths, job_record.job_id, scan_time)
    existing_dir.mkdir(parents=True, exist_ok=True)
    (existing_dir / "qstat.json").write_text(_fixture_text("qstat_running.json"), encoding="utf-8")
    (existing_dir / "stdout.tail.log").write_text("cached stdout\n", encoding="utf-8")
    (existing_dir / "stderr.tail.log").write_text("cached stderr\n", encoding="utf-8")

    monkeypatch.setattr(fetch, "_scan_time_now", lambda: scan_time)

    qstat_command = shlex.join(build_qstat_command(job_record.pbs_job_id or ""))
    stdout_command = "tail -n 200 /remote/repo/runs/run_demo/stdout.log"
    stderr_command = "tail -n 200 /remote/repo/runs/run_demo/stderr.log"
    bridge = FakeBridgeClient(
        state="ATTACHED",
        exec_results={
            qstat_command: _command_result(stdout=_fixture_text("qstat_running.json")),
            stdout_command: _command_result(stdout="fresh stdout\n"),
            stderr_command: _command_result(stdout="fresh stderr\n"),
        },
    )

    result = fetch.collect_incident_evidence(
        paths=paths,
        job_record=job_record,
        bridge_client=bridge,
        remote_root=REMOTE_ROOT,
    )

    assert result.source == "live"
    assert result.snapshot.scan_time != scan_time
    assert result.snapshot.scan_time.startswith(f"{scan_time}--")
    assert result.snapshot.snapshot_dir != existing_dir
    assert result.snapshot.stdout_tail_path.read_text(encoding="utf-8") == "fresh stdout\n"
    assert result.snapshot.stderr_tail_path.read_text(encoding="utf-8") == "fresh stderr\n"
    assert result.previous_snapshot is not None
    assert result.previous_snapshot.snapshot_dir == existing_dir


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
        remote_root=REMOTE_ROOT,
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


def test_normalize_incident_evidence_ignores_corrupt_previous_snapshot(
    tmp_path: Path,
) -> None:
    from autoresearch.incidents.fetch import collect_incident_evidence
    from autoresearch.incidents.normalize import normalize_incident_evidence

    paths = _paths(tmp_path)
    job_record = _job_record()

    first_dir = incident_snapshot_dir(paths, job_record.job_id, "2026-04-16T01:02:03+00:00")
    first_dir.mkdir(parents=True, exist_ok=True)
    (first_dir / "qstat.json").write_text(_fixture_text("qstat_running.json"), encoding="utf-8")
    (first_dir / "stdout.tail.log").write_bytes(b"\xff\xfe\x00")
    (first_dir / "stderr.tail.log").write_text("older stderr\n", encoding="utf-8")

    second_dir = incident_snapshot_dir(paths, job_record.job_id, "2026-04-16T02:03:04+00:00")
    second_dir.mkdir(parents=True, exist_ok=True)
    (second_dir / "qstat.json").write_text(_fixture_text("qstat_running.json"), encoding="utf-8")
    (second_dir / "stdout.tail.log").write_text("current stdout\n", encoding="utf-8")
    (second_dir / "stderr.tail.log").write_text("current stderr\n", encoding="utf-8")

    fetched = collect_incident_evidence(
        paths=paths,
        job_record=job_record,
        bridge_client=FakeBridgeClient(state="DETACHED"),
        remote_root=REMOTE_ROOT,
    )

    normalized = normalize_incident_evidence(job_record=job_record, fetched=fetched)

    assert normalized.snapshot_dir == second_dir
    assert normalized.stdout_tail == "current stdout\n"
    assert normalized.stderr_tail == "current stderr\n"
    assert normalized.current_log_tail_hash == _combined_hash("current stdout\n", "current stderr\n")
    assert normalized.previous_log_tail_hash is None


def test_normalize_incident_evidence_raises_controlled_error_for_malformed_snapshot(
    tmp_path: Path,
) -> None:
    from autoresearch.incidents.fetch import collect_incident_evidence
    from autoresearch.incidents.normalize import IncidentNormalizationError, normalize_incident_evidence

    paths = _paths(tmp_path)
    job_record = _job_record()
    scan_dir = incident_snapshot_dir(paths, job_record.job_id, "2026-04-16T02:03:04+00:00")
    scan_dir.mkdir(parents=True, exist_ok=True)
    (scan_dir / "qstat.json").write_text("{not-json", encoding="utf-8")
    (scan_dir / "stdout.tail.log").write_text("stdout\n", encoding="utf-8")
    (scan_dir / "stderr.tail.log").write_text("stderr\n", encoding="utf-8")

    fetched = collect_incident_evidence(
        paths=paths,
        job_record=job_record,
        bridge_client=FakeBridgeClient(state="DETACHED"),
        remote_root=REMOTE_ROOT,
    )

    with pytest.raises(IncidentNormalizationError, match="incident snapshot normalization failed"):
        normalize_incident_evidence(job_record=job_record, fetched=fetched)
