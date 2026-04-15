from pathlib import Path
import json

import pytest

from autoresearch import cli as cli_module
from autoresearch.bridge.remote_exec import RemoteBridgeError
from autoresearch.executor.pbs import build_qstat_command, build_qsub_command
from autoresearch.executor.polaris import build_probe_job_request
from autoresearch.db import init_db
from autoresearch.runs.registry import RunRegistry
from autoresearch.schemas import BridgeStatusResult, CommandResult, RunCreateRequest
from autoresearch.settings import ProbeSettings


REMOTE_ROOT = "/eagle/demo"


def _settings(tmp_path: Path) -> object:
    return type(
        "Settings",
        (),
        {
            "paths": type(
                "Paths",
                (),
                {
                    "db_path": tmp_path / "state" / "autoresearch.db",
                },
            )(),
            "remote_root": REMOTE_ROOT,
            "probe": ProbeSettings(project="ALCF_PROJECT", queue="debug", walltime="00:10:00"),
            "bridge": type(
                "Bridge",
                (),
                {
                    "alias": "polaris-relay",
                    "host": "example-host",
                    "user": "zzq",
                    "control_path": "~/.ssh/cm-%C",
                    "server_alive_interval": 60,
                    "server_alive_count_max": 3,
                    "connect_timeout": 15,
                },
            )(),
        },
    )()


class ProbeBridgeService:
    def __init__(
        self,
        *,
        qsub_output: str,
        qstat_state: str | None = None,
        mkdir_returncode: int = 0,
        copy_to_returncode: int = 0,
    ) -> None:
        self.qsub_output = qsub_output
        self.qstat_state = qstat_state
        self.mkdir_returncode = mkdir_returncode
        self.copy_to_returncode = copy_to_returncode
        self.exec_calls: list[str] = []
        self.copy_to_calls: list[tuple[str, str]] = []
        self.timeline: list[tuple[str, str]] = []

    def status(self) -> BridgeStatusResult:
        return BridgeStatusResult(
            alias="polaris-relay",
            state="ATTACHED",
            explanation="status",
        )

    def exec(self, command: str) -> CommandResult:
        self.timeline.append(("exec", command))
        self.exec_calls.append(command)
        if command.startswith("mkdir -p "):
            return CommandResult(
                args=("ssh", "polaris-relay", command),
                returncode=self.mkdir_returncode,
                stdout="",
                stderr="mkdir failed" if self.mkdir_returncode else "",
                duration_seconds=0.01,
            )
        if command.startswith("qsub "):
            return CommandResult(
                args=("ssh", "polaris-relay", command),
                returncode=0,
                stdout=self.qsub_output,
                stderr="",
                duration_seconds=0.01,
            )
        if command.startswith("qstat "):
            if self.qstat_state is None:
                raise AssertionError("qstat was not expected for this test")
            payload = {
                "Jobs": {
                    self.qsub_output.strip(): {
                        "job_state": self.qstat_state,
                        "queue": "debug",
                        "exec_host": "x1001/0",
                    }
                }
            }
            return CommandResult(
                args=("ssh", "polaris-relay", command),
                returncode=0,
                stdout=json.dumps(payload),
                stderr="",
                duration_seconds=0.01,
            )
        raise AssertionError(f"unexpected command: {command}")

    def copy_to(self, local_path: str, remote_path: str) -> CommandResult:
        self.timeline.append(("copy_to", remote_path))
        self.copy_to_calls.append((local_path, remote_path))
        return CommandResult(
            args=("scp", local_path, remote_path),
            returncode=self.copy_to_returncode,
            stdout="",
            stderr="scp failed" if self.copy_to_returncode else "",
            duration_seconds=0.01,
        )


def test_build_probe_job_request_uses_probe_settings_defaults_and_derives_submit_script_path() -> None:
    probe_settings = ProbeSettings(
        project="ALCF_PROJECT",
        queue="debug",
        walltime="00:10:00",
    )

    request = build_probe_job_request(
        run_id="run_probe",
        entrypoint_path="/eagle/demo/jobs/probe/entrypoint.sh",
        remote_root=REMOTE_ROOT,
        probe_settings=probe_settings,
    )

    assert request.project == "ALCF_PROJECT"
    assert request.queue == "debug"
    assert request.walltime == "00:10:00"
    assert request.submit_script_path == f"{REMOTE_ROOT}/jobs/run_probe/submit.pbs"


def test_build_probe_job_request_allows_cli_overrides_for_queue_and_walltime() -> None:
    probe_settings = ProbeSettings(
        project="ALCF_PROJECT",
        queue="debug",
        walltime="00:10:00",
    )

    request = build_probe_job_request(
        run_id="run_probe",
        entrypoint_path="/eagle/demo/jobs/probe/entrypoint.sh",
        remote_root=REMOTE_ROOT,
        probe_settings=probe_settings,
        queue="prod",
        walltime="00:20:00",
    )

    assert request.project == "ALCF_PROJECT"
    assert request.queue == "prod"
    assert request.walltime == "00:20:00"
    assert request.submit_script_path == f"{REMOTE_ROOT}/jobs/run_probe/submit.pbs"


def test_build_qsub_command_returns_submit_script_path() -> None:
    assert build_qsub_command("/eagle/demo/jobs/run_probe/submit.pbs") == (
        "qsub",
        "/eagle/demo/jobs/run_probe/submit.pbs",
    )


def test_build_qstat_command_returns_json_query_for_job_id() -> None:
    assert build_qstat_command("123.polaris") == (
        "qstat",
        "-fF",
        "JSON",
        "123.polaris",
    )


def test_submit_probe_job_persists_submission_and_updates_registry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    init_db(settings.paths.db_path)
    service = ProbeBridgeService(
        qsub_output="123456.polaris-pbs-01.hsn.cm.polaris.alcf.anl.gov",
    )
    bootstrap_calls: list[tuple[str, bool]] = []

    monkeypatch.setattr(cli_module, "load_settings", lambda: settings)
    monkeypatch.setattr(cli_module, "build_bridge_service", lambda: service)
    monkeypatch.setattr(
        cli_module,
        "bootstrap_remote_root",
        lambda client, remote_root, *, force: bootstrap_calls.append((remote_root, force)),
    )

    run_id, job_id, pbs_job_id = cli_module.submit_probe_job(
        project="CUSTOM_PROJECT",
        queue="prod",
        walltime="00:20:00",
    )

    assert bootstrap_calls == [(REMOTE_ROOT, False)]
    assert pbs_job_id == "123456.polaris-pbs-01.hsn.cm.polaris.alcf.anl.gov"
    assert service.exec_calls[0] == f"mkdir -p {REMOTE_ROOT}/jobs/{run_id}"
    assert service.exec_calls[1] == f"qsub {REMOTE_ROOT}/jobs/{run_id}/submit.pbs"
    assert len(service.exec_calls) == 2
    assert len(service.copy_to_calls) == 1
    assert service.copy_to_calls[0][1] == f"{REMOTE_ROOT}/jobs/{run_id}/submit.pbs"

    registry = RunRegistry(settings.paths.db_path)
    run_record = registry.list_runs()[0]
    job_record = registry.get_job(job_id)

    assert run_record.run_id == run_id
    assert run_record.run_kind == "probe"
    assert run_record.project == "CUSTOM_PROJECT"
    assert job_record.state == "SUBMITTED"
    assert job_record.pbs_job_id == pbs_job_id
    assert job_record.queue == "prod"
    assert job_record.walltime == "00:20:00"


def test_submit_probe_job_creates_submit_directory_before_upload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    init_db(settings.paths.db_path)
    service = ProbeBridgeService(
        qsub_output="123456.polaris-pbs-01.hsn.cm.polaris.alcf.anl.gov",
    )

    monkeypatch.setattr(cli_module, "load_settings", lambda: settings)
    monkeypatch.setattr(cli_module, "build_bridge_service", lambda: service)
    monkeypatch.setattr(
        cli_module,
        "bootstrap_remote_root",
        lambda client, remote_root, *, force: None,
    )

    run_id, _, _ = cli_module.submit_probe_job(
        project="CUSTOM_PROJECT",
        queue="prod",
        walltime="00:20:00",
    )

    assert service.timeline[0] == ("exec", f"mkdir -p {REMOTE_ROOT}/jobs/{run_id}")
    assert service.timeline[1] == ("copy_to", f"{REMOTE_ROOT}/jobs/{run_id}/submit.pbs")
    assert service.timeline[2][0] == "exec"
    assert service.timeline[2][1] == f"qsub {REMOTE_ROOT}/jobs/{run_id}/submit.pbs"


def test_submit_probe_job_raises_on_submit_directory_mkdir_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    init_db(settings.paths.db_path)
    service = ProbeBridgeService(
        qsub_output="123456.polaris-pbs-01.hsn.cm.polaris.alcf.anl.gov",
        mkdir_returncode=23,
    )

    monkeypatch.setattr(cli_module, "load_settings", lambda: settings)
    monkeypatch.setattr(cli_module, "build_bridge_service", lambda: service)
    monkeypatch.setattr(cli_module, "bootstrap_remote_root", lambda client, remote_root, *, force: None)

    with pytest.raises(RemoteBridgeError, match="mkdir failed"):
        cli_module.submit_probe_job(
            project="CUSTOM_PROJECT",
            queue="prod",
            walltime="00:20:00",
        )

    assert len(service.exec_calls) == 1
    assert service.exec_calls[0].startswith("mkdir -p /eagle/demo/jobs/run_")


def test_submit_probe_job_raises_on_submit_script_upload_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    init_db(settings.paths.db_path)
    service = ProbeBridgeService(
        qsub_output="123456.polaris-pbs-01.hsn.cm.polaris.alcf.anl.gov",
        copy_to_returncode=7,
    )

    monkeypatch.setattr(cli_module, "load_settings", lambda: settings)
    monkeypatch.setattr(cli_module, "build_bridge_service", lambda: service)
    monkeypatch.setattr(cli_module, "bootstrap_remote_root", lambda client, remote_root, *, force: None)

    with pytest.raises(RemoteBridgeError, match="scp failed"):
        cli_module.submit_probe_job(
            project="CUSTOM_PROJECT",
            queue="prod",
            walltime="00:20:00",
        )

    assert len(service.exec_calls) == 1
    assert service.exec_calls[0].startswith("mkdir -p /eagle/demo/jobs/run_")
    assert len(service.copy_to_calls) == 1
    assert service.copy_to_calls[0][1].startswith(f"{REMOTE_ROOT}/jobs/run_")


def test_submit_probe_job_raises_on_malformed_qsub_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    init_db(settings.paths.db_path)
    service = ProbeBridgeService(qsub_output="job submitted")

    monkeypatch.setattr(cli_module, "load_settings", lambda: settings)
    monkeypatch.setattr(cli_module, "build_bridge_service", lambda: service)
    monkeypatch.setattr(cli_module, "bootstrap_remote_root", lambda client, remote_root, *, force: None)

    with pytest.raises(RemoteBridgeError, match="malformed qsub output"):
        cli_module.submit_probe_job(
            project="CUSTOM_PROJECT",
            queue="prod",
            walltime="00:20:00",
        )


@pytest.mark.parametrize(
    ("pbs_state", "expected_state"),
    [
        ("Q", "QUEUED"),
        ("R", "RUNNING"),
        ("F", "SUCCEEDED"),
        ("X", "X"),
    ],
)
def test_poll_probe_job_returns_state_and_updates_registry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    pbs_state: str,
    expected_state: str,
) -> None:
    settings = _settings(tmp_path)
    init_db(settings.paths.db_path)
    registry = RunRegistry(settings.paths.db_path)
    run_record = registry.create_run(RunCreateRequest(run_kind="probe", project="ALCF_PROJECT"))
    created = registry.create_job(
        run_id=run_record.run_id,
        backend="pbs",
        queue="prod",
        walltime="00:20:00",
        filesystems="eagle",
        select_expr="1:system=polaris",
        place_expr="scatter",
        submit_script_path=f"{REMOTE_ROOT}/jobs/{run_record.run_id}/submit.pbs",
        stdout_path=f"{REMOTE_ROOT}/runs/{run_record.run_id}/stdout.log",
        stderr_path=f"{REMOTE_ROOT}/runs/{run_record.run_id}/stderr.log",
        pbs_job_id="123456.polaris-pbs-01.hsn.cm.polaris.alcf.anl.gov",
    )
    service = ProbeBridgeService(
        qsub_output=created.pbs_job_id or "",
        qstat_state=pbs_state,
    )

    monkeypatch.setattr(cli_module, "load_settings", lambda: settings)
    monkeypatch.setattr(cli_module, "build_bridge_service", lambda: service)

    state, pbs_job_id = cli_module.poll_probe_job(created.job_id)

    assert pbs_job_id == created.pbs_job_id
    assert state == expected_state
    assert service.exec_calls == [f"qstat -fF JSON {created.pbs_job_id}"]

    updated = registry.get_job(created.job_id)
    assert updated.state == expected_state
