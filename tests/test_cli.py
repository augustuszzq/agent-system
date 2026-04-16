from pathlib import Path

from typer.testing import CliRunner

from autoresearch import cli as cli_module
from autoresearch.bridge.remote_exec import RemoteBridgeError
from autoresearch.decisions import DecisionLog
from autoresearch.cli import app
from autoresearch.db import connect_db, init_db
from autoresearch.incidents.fetch import IncidentFetchError
from autoresearch.incidents.registry import IncidentRegistry
from autoresearch.incidents.summaries import render_incident_row, render_incident_summary
from autoresearch.retries.registry import RetryRequestRegistry
from autoresearch.runs.registry import RunRegistry
from autoresearch.schemas import (
    BridgeStatusResult,
    ClassifiedIncident,
    CommandResult,
    IncidentFetchResult,
    IncidentSnapshotRef,
    NormalizedIncidentInput,
    RunCreateRequest,
)


runner = CliRunner()


def _write_app_config(conf_dir: Path) -> None:
    (conf_dir / "app.yaml").write_text(
        "app_name: auto-research\n"
        "paths:\n"
        "  state_dir: state\n"
        "  cache_dir: cache\n"
        "  logs_dir: logs\n"
        "  db_path: state/autoresearch.db\n"
        "remote:\n"
        "  root: /eagle/lc-mpi/Zhiqing/auto-research\n",
        encoding="utf-8",
    )


def _write_bridge_config(conf_dir: Path) -> None:
    (conf_dir / "polaris.yaml").write_text(
        "bridge:\n"
        "  alias: polaris-relay\n"
        "  host: polaris-login-04.hsn.cm.polaris.alcf.anl.gov\n"
        "  user: zzq\n"
        "  control_path: ~/.ssh/cm-%C\n"
        "  server_alive_interval: 60\n"
        "  server_alive_count_max: 3\n"
        "  connect_timeout: 15\n"
        "probe:\n"
        "  project: demo\n"
        "  queue: debug\n"
        "  walltime: 00:10:00\n",
        encoding="utf-8",
    )


def _write_retry_policy(conf_dir: Path) -> None:
    (conf_dir / "retry_policy.yaml").write_text(
        "safe_retry_categories:\n"
        "  - FILESYSTEM_UNAVAILABLE\n"
        "allowed_actions:\n"
        "  - RETRY_SAME_CONFIG\n",
        encoding="utf-8",
    )


def _write_repo_config(tmp_path: Path) -> None:
    (tmp_path / "conf").mkdir()
    _write_app_config(tmp_path / "conf")
    _write_bridge_config(tmp_path / "conf")
    _write_retry_policy(tmp_path / "conf")


def _seed_retryable_incident(tmp_path: Path) -> tuple[str, str, str]:
    db_path = tmp_path / "state" / "autoresearch.db"
    init_db(db_path)
    run_registry = RunRegistry(db_path)
    run_record = run_registry.create_run(RunCreateRequest(run_kind="probe", project="demo"))
    job_record = run_registry.create_job(
        run_id=run_record.run_id,
        backend="pbs",
        queue="debug",
        walltime="00:10:00",
        filesystems="eagle",
        select_expr="1:system=polaris",
        place_expr="scatter",
        submit_script_path="/tmp/submit.pbs",
        stdout_path="/tmp/stdout.log",
        stderr_path="/tmp/stderr.log",
        pbs_job_id="123456.polaris-pbs-01",
    )
    incident = IncidentRegistry(db_path).upsert_incident(
        run_id=run_record.run_id,
        job_id=job_record.job_id,
        severity="HIGH",
        category="FILESYSTEM_UNAVAILABLE",
        fingerprint="fp-retry",
        evidence={
            "scan_time": "2026-04-16T12:00:00",
            "snapshot_dir": str(tmp_path / "state" / "incidents" / job_record.job_id / "scan"),
            "qstat_comment": "filesystem unavailable",
            "job_state": "F",
            "exec_host": "node01",
            "matched_lines": ["filesystem unavailable"],
            "classifier_rule": "filesystem_unavailable",
        },
    )
    return incident.incident_id, run_record.run_id, job_record.job_id


class FakeBridgeService:
    def __init__(
        self,
        *,
        alias: str = "polaris-relay",
        attach_result: CommandResult | None = None,
        check_result: CommandResult | None = None,
        detach_result: CommandResult | None = None,
        status_result: BridgeStatusResult | None = None,
        exec_result: CommandResult | None = None,
        copy_to_result: CommandResult | None = None,
        copy_from_result: CommandResult | None = None,
    ) -> None:
        self.settings = type("Settings", (), {"alias": alias})()
        self.attach_result = attach_result or CommandResult(
            args=("ssh", "-MNf", alias),
            returncode=0,
            stdout="",
            stderr="",
            duration_seconds=0.01,
        )
        self.check_result = check_result or CommandResult(
            args=("ssh", "-O", "check", alias),
            returncode=0,
            stdout="Master running",
            stderr="",
            duration_seconds=0.01,
        )
        self.detach_result = detach_result or CommandResult(
            args=("ssh", "-O", "exit", alias),
            returncode=0,
            stdout="",
            stderr="",
            duration_seconds=0.01,
        )
        self.status_result = status_result or BridgeStatusResult(
            alias=alias,
            state="ATTACHED",
            explanation="OpenSSH control master is healthy.",
            command_result=self.check_result,
            control_path_exists=None,
        )
        self.exec_result = exec_result or CommandResult(
            args=("ssh", alias, "pwd"),
            returncode=0,
            stdout="/remote/workdir\n",
            stderr="",
            duration_seconds=0.01,
        )
        self.copy_to_result = copy_to_result or CommandResult(
            args=("scp", "/tmp/local.txt", f"{alias}:/remote/file.txt"),
            returncode=0,
            stdout="",
            stderr="",
            duration_seconds=0.01,
        )
        self.copy_from_result = copy_from_result or CommandResult(
            args=("scp", f"{alias}:/remote/file.txt", "/tmp/local.txt"),
            returncode=0,
            stdout="",
            stderr="",
            duration_seconds=0.01,
        )
        self.calls: list[str] = []
        self.exec_calls: list[str] = []
        self.copy_to_calls: list[tuple[str, str]] = []
        self.copy_from_calls: list[tuple[str, str]] = []

    def attach(self) -> CommandResult:
        self.calls.append("attach")
        return self.attach_result

    def check(self) -> CommandResult:
        self.calls.append("check")
        return self.check_result

    def detach(self) -> CommandResult:
        self.calls.append("detach")
        return self.detach_result

    def status(self) -> BridgeStatusResult:
        self.calls.append("status")
        return self.status_result

    def exec(self, command: str) -> CommandResult:
        self.calls.append("exec")
        self.exec_calls.append(command)
        return self.exec_result

    def copy_to(self, local_path: str, remote_path: str) -> CommandResult:
        self.calls.append("copy_to")
        self.copy_to_calls.append((local_path, remote_path))
        return self.copy_to_result

    def copy_from(self, remote_path: str, local_path: str) -> CommandResult:
        self.calls.append("copy_from")
        self.copy_from_calls.append((remote_path, local_path))
        return self.copy_from_result


def test_cli_help_shows_top_level_commands() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "db" in result.stdout
    assert "run" in result.stdout
    assert "bridge" in result.stdout
    assert "job" in result.stdout
    assert "incident" in result.stdout
    assert "retry" in result.stdout


def test_db_init_creates_database_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AUTORESEARCH_DB", str(tmp_path / "state" / "autoresearch.db"))
    monkeypatch.setenv("AUTORESEARCH_REPO_ROOT", str(tmp_path))
    _write_repo_config(tmp_path)

    result = runner.invoke(app, ["db", "init"])

    assert result.exit_code == 0
    assert (tmp_path / "state" / "autoresearch.db").exists()


def test_run_create_and_list_round_trip(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AUTORESEARCH_DB", str(tmp_path / "state" / "autoresearch.db"))
    monkeypatch.setenv("AUTORESEARCH_REPO_ROOT", str(tmp_path))
    _write_repo_config(tmp_path)

    init_result = runner.invoke(app, ["db", "init"])
    create_result = runner.invoke(
        app,
        ["run", "create", "--kind", "local-debug", "--project", "demo", "--notes", "hello"],
    )
    list_result = runner.invoke(app, ["run", "list"])

    assert init_result.exit_code == 0
    assert create_result.exit_code == 0
    assert "local-debug" in list_result.stdout
    assert "demo" in list_result.stdout


def test_job_render_pbs_prints_expected_script() -> None:
    result = runner.invoke(
        app,
        [
            "job",
            "render-pbs",
            "--run-id",
            "run_demo",
            "--project",
            "demo",
            "--queue",
            "debug",
            "--walltime",
            "00:10:00",
            "--entrypoint-path",
            "/tmp/entrypoint.sh",
        ],
    )

    assert result.exit_code == 0
    assert "#PBS -A demo" in result.stdout
    assert "#PBS -l filesystems=eagle" in result.stdout
    assert "/tmp/entrypoint.sh" in result.stdout


def test_job_render_pbs_uses_configured_remote_root(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AUTORESEARCH_REPO_ROOT", str(tmp_path))
    _write_repo_config(tmp_path)
    (tmp_path / "conf" / "app.yaml").write_text(
        "app_name: auto-research\n"
        "paths:\n"
        "  state_dir: state\n"
        "  cache_dir: cache\n"
        "  logs_dir: logs\n"
        "  db_path: state/autoresearch.db\n"
        "remote:\n"
        "  root: /custom/remote/root\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "job",
            "render-pbs",
            "--run-id",
            "run_demo",
            "--project",
            "demo",
            "--queue",
            "debug",
            "--walltime",
            "00:10:00",
            "--entrypoint-path",
            "/tmp/entrypoint.sh",
        ],
    )

    assert result.exit_code == 0
    assert "#PBS -o /custom/remote/root/runs/run_demo/stdout.log" in result.stdout
    assert "#PBS -e /custom/remote/root/runs/run_demo/stderr.log" in result.stdout
    assert "export AUTORESEARCH_REMOTE_ROOT=/custom/remote/root" in result.stdout
    assert "cd /custom/remote/root/repo" in result.stdout


def test_job_submit_probe_invokes_helper_and_prints_ids(monkeypatch) -> None:
    calls: list[tuple[str | None, str | None, str | None]] = []

    def fake_submit_probe_job(
        *, project: str | None = None, queue: str | None = None, walltime: str | None = None
    ) -> tuple[str, str, str]:
        calls.append((project, queue, walltime))
        return ("run_123", "job_456", "123456.polaris-pbs-01.hsn.cm.polaris.alcf.anl.gov")

    monkeypatch.setattr(cli_module, "submit_probe_job", fake_submit_probe_job)

    result = runner.invoke(
        app,
        [
            "job",
            "submit-probe",
            "--project",
            "CUSTOM_PROJECT",
            "--queue",
            "prod",
            "--walltime",
            "00:20:00",
        ],
    )

    assert result.exit_code == 0
    assert calls == [("CUSTOM_PROJECT", "prod", "00:20:00")]
    assert "run_123" in result.stdout
    assert "job_456" in result.stdout
    assert "123456.polaris-pbs-01.hsn.cm.polaris.alcf.anl.gov" in result.stdout


def test_job_poll_invokes_helper_and_prints_state(monkeypatch) -> None:
    calls: list[str] = []

    def fake_poll_probe_job(job_id: str) -> tuple[str, str]:
        calls.append(job_id)
        return ("RUNNING", "123456.polaris-pbs-01.hsn.cm.polaris.alcf.anl.gov")

    monkeypatch.setattr(cli_module, "poll_probe_job", fake_poll_probe_job)

    result = runner.invoke(app, ["job", "poll", "--job-id", "job_abc"])

    assert result.exit_code == 0
    assert calls == ["job_abc"]
    assert "RUNNING" in result.stdout
    assert "123456.polaris-pbs-01.hsn.cm.polaris.alcf.anl.gov" in result.stdout


def test_job_submit_probe_propagates_remote_bridge_error(monkeypatch) -> None:
    def fake_submit_probe_job(
        *, project: str | None = None, queue: str | None = None, walltime: str | None = None
    ) -> tuple[str, str, str]:
        raise RemoteBridgeError("probe bootstrap failed")

    monkeypatch.setattr(cli_module, "submit_probe_job", fake_submit_probe_job)

    result = runner.invoke(app, ["job", "submit-probe"])

    assert result.exit_code == 1
    assert "probe bootstrap failed" in result.stderr


def test_job_poll_propagates_remote_bridge_error(monkeypatch) -> None:
    def fake_poll_probe_job(job_id: str) -> tuple[str, str]:
        raise RemoteBridgeError("qstat failed")

    monkeypatch.setattr(cli_module, "poll_probe_job", fake_poll_probe_job)

    result = runner.invoke(app, ["job", "poll", "--job-id", "job_abc"])

    assert result.exit_code == 1
    assert "qstat failed" in result.stderr


def test_incident_list_prints_open_incident_row(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AUTORESEARCH_DB", str(tmp_path / "state" / "autoresearch.db"))
    monkeypatch.setenv("AUTORESEARCH_REPO_ROOT", str(tmp_path))
    _write_repo_config(tmp_path)

    init_db(tmp_path / "state" / "autoresearch.db")
    registry = IncidentRegistry(tmp_path / "state" / "autoresearch.db")
    record = registry.upsert_incident(
        run_id="run_demo",
        job_id="job_demo",
        severity="HIGH",
        category="ENV_PATH_ERROR",
        fingerprint="fp-123",
        evidence={
            "scan_time": "2026-04-16T12:00:00",
            "snapshot_dir": str(tmp_path / "state" / "incidents" / "job_demo" / "scan"),
            "qstat_comment": "missing file",
            "job_state": "F",
            "exec_host": "node01",
            "matched_lines": ["missing file"],
            "classifier_rule": "env_path_error",
        },
    )

    result = runner.invoke(app, ["incident", "list"])

    assert result.exit_code == 0
    assert result.stdout.strip() == render_incident_row(record)


def test_incident_summarize_prints_category_counts(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AUTORESEARCH_DB", str(tmp_path / "state" / "autoresearch.db"))
    monkeypatch.setenv("AUTORESEARCH_REPO_ROOT", str(tmp_path))
    _write_repo_config(tmp_path)

    init_db(tmp_path / "state" / "autoresearch.db")
    registry = IncidentRegistry(tmp_path / "state" / "autoresearch.db")
    registry.upsert_incident(
        run_id="run_demo",
        job_id="job_oom",
        severity="CRITICAL",
        category="RESOURCE_OOM",
        fingerprint="fp-oom",
        evidence={
            "scan_time": "2026-04-16T12:00:00",
            "snapshot_dir": str(tmp_path / "state" / "incidents" / "job_oom" / "scan"),
            "qstat_comment": "out of memory",
            "job_state": "F",
            "exec_host": "node01",
            "matched_lines": ["out of memory"],
            "classifier_rule": "resource_oom",
        },
    )
    registry.upsert_incident(
        run_id="run_demo",
        job_id="job_path",
        severity="HIGH",
        category="ENV_PATH_ERROR",
        fingerprint="fp-path",
        evidence={
            "scan_time": "2026-04-16T12:00:01",
            "snapshot_dir": str(tmp_path / "state" / "incidents" / "job_path" / "scan"),
            "qstat_comment": "cannot open",
            "job_state": "F",
            "exec_host": "node02",
            "matched_lines": ["cannot open"],
            "classifier_rule": "env_path_error",
        },
    )

    result = runner.invoke(app, ["incident", "summarize"])

    assert result.exit_code == 0
    summary = registry.summarize_open_incidents()
    assert result.stdout.strip() == render_incident_summary(summary)
    assert "Counts:" in result.stdout
    assert "'RESOURCE_OOM': 1" in result.stdout
    assert "'ENV_PATH_ERROR': 1" in result.stdout


def test_incident_scan_reports_created_incident_with_monkeypatched_fetch(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AUTORESEARCH_DB", str(tmp_path / "state" / "autoresearch.db"))
    monkeypatch.setenv("AUTORESEARCH_REPO_ROOT", str(tmp_path))
    _write_repo_config(tmp_path)

    init_db(tmp_path / "state" / "autoresearch.db")
    run_registry = RunRegistry(tmp_path / "state" / "autoresearch.db")
    run_record = run_registry.create_run(RunCreateRequest(run_kind="probe", project="demo"))
    job_record = run_registry.create_job(
        run_id=run_record.run_id,
        backend="pbs",
        queue="debug",
        walltime="00:10:00",
        filesystems="eagle",
        select_expr="1:system=polaris",
        place_expr="scatter",
        submit_script_path="/tmp/submit.pbs",
        stdout_path="/tmp/stdout.log",
        stderr_path="/tmp/stderr.log",
        pbs_job_id="123456.polaris-pbs-01",
    )

    fake_bridge = FakeBridgeService()
    collect_calls: list[tuple[object, object, object]] = []
    fake_snapshot_dir = tmp_path / "state" / "incidents" / job_record.job_id / "2026-04-16T12:00:00"
    fake_snapshot_dir.mkdir(parents=True)
    fake_snapshot = IncidentSnapshotRef(
        scan_time="2026-04-16T12:00:00",
        snapshot_dir=fake_snapshot_dir,
        qstat_json_path=fake_snapshot_dir / "qstat.json",
        stdout_tail_path=fake_snapshot_dir / "stdout.tail.log",
        stderr_tail_path=fake_snapshot_dir / "stderr.tail.log",
    )
    fake_fetched = IncidentFetchResult(source="live", snapshot=fake_snapshot, previous_snapshot=None)
    fake_normalized = NormalizedIncidentInput(
        job_id=job_record.job_id,
        run_id=job_record.run_id,
        pbs_job_id=job_record.pbs_job_id,
        job_state="F",
        comment="cannot open",
        exec_host="node01",
        stdout_tail="stdout tail",
        stderr_tail="stderr tail",
        snapshot_dir=fake_snapshot.snapshot_dir,
        scan_time=fake_snapshot.scan_time,
        current_log_tail_hash="abc123",
        previous_log_tail_hash=None,
    )
    fake_classified = ClassifiedIncident(
        category="ENV_PATH_ERROR",
        severity="HIGH",
        fingerprint="fp-123",
        matched_lines=("cannot open",),
        rule_name="env_path_error",
    )

    monkeypatch.setattr(cli_module, "build_bridge_service", lambda: fake_bridge)
    monkeypatch.setattr(
        cli_module,
        "collect_incident_evidence",
        lambda paths, job, bridge, remote_root: collect_calls.append(
            (paths, job, bridge, remote_root)
        ) or fake_fetched,
    )
    monkeypatch.setattr(cli_module, "normalize_incident_evidence", lambda **kwargs: fake_normalized)
    monkeypatch.setattr(cli_module, "classify_incident", lambda incident: fake_classified)

    result = runner.invoke(app, ["incident", "scan", "--job-id", job_record.job_id])

    assert result.exit_code == 0
    expected_paths = cli_module.load_settings().paths
    expected_remote_root = cli_module.load_settings().remote_root
    assert collect_calls == [(expected_paths, job_record, fake_bridge, expected_remote_root)]
    assert "created incident" in result.stdout.lower()
    assert "source=live" in result.stdout

    incident_registry = IncidentRegistry(tmp_path / "state" / "autoresearch.db")
    records = incident_registry.list_open_incidents()
    assert len(records) == 1
    assert records[0].category == "ENV_PATH_ERROR"
    assert records[0].evidence == {
        "evidence_source": "live",
        "scan_time": "2026-04-16T12:00:00",
        "snapshot_dir": str(fake_snapshot.snapshot_dir),
        "qstat_comment": "cannot open",
        "job_state": "F",
        "exec_host": "node01",
        "matched_lines": ["cannot open"],
        "classifier_rule": "env_path_error",
    }


def test_incident_scan_reports_no_incident_when_classifier_returns_none(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AUTORESEARCH_DB", str(tmp_path / "state" / "autoresearch.db"))
    monkeypatch.setenv("AUTORESEARCH_REPO_ROOT", str(tmp_path))
    _write_repo_config(tmp_path)

    init_db(tmp_path / "state" / "autoresearch.db")
    run_registry = RunRegistry(tmp_path / "state" / "autoresearch.db")
    run_record = run_registry.create_run(RunCreateRequest(run_kind="probe", project="demo"))
    job_record = run_registry.create_job(
        run_id=run_record.run_id,
        backend="pbs",
        queue="debug",
        walltime="00:10:00",
        filesystems="eagle",
        select_expr="1:system=polaris",
        place_expr="scatter",
        submit_script_path="/tmp/submit.pbs",
        stdout_path="/tmp/stdout.log",
        stderr_path="/tmp/stderr.log",
        pbs_job_id="123456.polaris-pbs-01",
    )

    fake_bridge = FakeBridgeService()
    fake_snapshot_dir = tmp_path / "state" / "incidents" / job_record.job_id / "2026-04-16T12:00:00"
    fake_snapshot_dir.mkdir(parents=True)
    fake_snapshot = IncidentSnapshotRef(
        scan_time="2026-04-16T12:00:00",
        snapshot_dir=fake_snapshot_dir,
        qstat_json_path=fake_snapshot_dir / "qstat.json",
        stdout_tail_path=fake_snapshot_dir / "stdout.tail.log",
        stderr_tail_path=fake_snapshot_dir / "stderr.tail.log",
    )
    fake_fetched = IncidentFetchResult(source="live", snapshot=fake_snapshot, previous_snapshot=None)
    fake_normalized = NormalizedIncidentInput(
        job_id=job_record.job_id,
        run_id=job_record.run_id,
        pbs_job_id=job_record.pbs_job_id,
        job_state="F",
        comment="all good",
        exec_host="node01",
        stdout_tail="stdout tail",
        stderr_tail="stderr tail",
        snapshot_dir=fake_snapshot.snapshot_dir,
        scan_time=fake_snapshot.scan_time,
        current_log_tail_hash="abc123",
        previous_log_tail_hash=None,
    )

    monkeypatch.setattr(cli_module, "build_bridge_service", lambda: fake_bridge)
    monkeypatch.setattr(
        cli_module,
        "collect_incident_evidence",
        lambda paths, job, bridge, remote_root: fake_fetched,
    )
    monkeypatch.setattr(cli_module, "normalize_incident_evidence", lambda **kwargs: fake_normalized)
    monkeypatch.setattr(cli_module, "classify_incident", lambda incident: None)

    result = runner.invoke(app, ["incident", "scan", "--job-id", job_record.job_id])

    assert result.exit_code == 0
    assert "No incident detected for job" in result.stdout
    assert "source=live" in result.stdout
    assert IncidentRegistry(tmp_path / "state" / "autoresearch.db").list_open_incidents() == []


def test_incident_scan_reports_fetch_errors_to_stderr(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AUTORESEARCH_DB", str(tmp_path / "state" / "autoresearch.db"))
    monkeypatch.setenv("AUTORESEARCH_REPO_ROOT", str(tmp_path))
    _write_repo_config(tmp_path)

    init_db(tmp_path / "state" / "autoresearch.db")
    run_registry = RunRegistry(tmp_path / "state" / "autoresearch.db")
    run_record = run_registry.create_run(RunCreateRequest(run_kind="probe", project="demo"))
    job_record = run_registry.create_job(
        run_id=run_record.run_id,
        backend="pbs",
        queue="debug",
        walltime="00:10:00",
        filesystems="eagle",
        select_expr="1:system=polaris",
        place_expr="scatter",
        submit_script_path="/tmp/submit.pbs",
        stdout_path="/tmp/stdout.log",
        stderr_path="/tmp/stderr.log",
        pbs_job_id="123456.polaris-pbs-01",
    )

    fake_bridge = FakeBridgeService()
    monkeypatch.setattr(cli_module, "build_bridge_service", lambda: fake_bridge)
    monkeypatch.setattr(
        cli_module,
        "collect_incident_evidence",
        lambda paths, job, bridge, remote_root: (_ for _ in ()).throw(
            IncidentFetchError("fetch failed")
        ),
    )

    result = runner.invoke(app, ["incident", "scan", "--job-id", job_record.job_id])

    assert result.exit_code == 1
    assert "fetch failed" in result.stderr


def test_incident_scan_reports_malformed_snapshot_as_cli_failure(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AUTORESEARCH_DB", str(tmp_path / "state" / "autoresearch.db"))
    monkeypatch.setenv("AUTORESEARCH_REPO_ROOT", str(tmp_path))
    _write_repo_config(tmp_path)

    init_db(tmp_path / "state" / "autoresearch.db")
    run_registry = RunRegistry(tmp_path / "state" / "autoresearch.db")
    run_record = run_registry.create_run(RunCreateRequest(run_kind="probe", project="demo"))
    job_record = run_registry.create_job(
        run_id=run_record.run_id,
        backend="pbs",
        queue="debug",
        walltime="00:10:00",
        filesystems="eagle",
        select_expr="1:system=polaris",
        place_expr="scatter",
        submit_script_path="/tmp/submit.pbs",
        stdout_path="/tmp/stdout.log",
        stderr_path="/tmp/stderr.log",
        pbs_job_id="123456.polaris-pbs-01",
    )

    fake_snapshot_dir = tmp_path / "state" / "incidents" / job_record.job_id / "2026-04-16T12:00:00"
    fake_snapshot_dir.mkdir(parents=True)
    fake_snapshot = IncidentSnapshotRef(
        scan_time="2026-04-16T12:00:00",
        snapshot_dir=fake_snapshot_dir,
        qstat_json_path=fake_snapshot_dir / "qstat.json",
        stdout_tail_path=fake_snapshot_dir / "stdout.tail.log",
        stderr_tail_path=fake_snapshot_dir / "stderr.tail.log",
    )
    fake_snapshot.qstat_json_path.write_text("{not-json", encoding="utf-8")
    fake_snapshot.stdout_tail_path.write_text("stdout tail", encoding="utf-8")
    fake_snapshot.stderr_tail_path.write_text("stderr tail", encoding="utf-8")
    fake_fetched = IncidentFetchResult(source="local-fallback", snapshot=fake_snapshot, previous_snapshot=None)

    monkeypatch.setattr(cli_module, "build_bridge_service", lambda: FakeBridgeService())
    monkeypatch.setattr(
        cli_module,
        "collect_incident_evidence",
        lambda paths, job, bridge, remote_root: fake_fetched,
    )

    result = runner.invoke(app, ["incident", "scan", "--job-id", job_record.job_id])

    assert result.exit_code == 1
    assert "incident snapshot normalization failed" in result.stderr
    assert "Traceback" not in result.stderr


def test_incident_scan_reports_unknown_job_as_cli_failure(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AUTORESEARCH_DB", str(tmp_path / "state" / "autoresearch.db"))
    monkeypatch.setenv("AUTORESEARCH_REPO_ROOT", str(tmp_path))
    _write_repo_config(tmp_path)

    init_db(tmp_path / "state" / "autoresearch.db")
    monkeypatch.setattr(cli_module.RunRegistry, "get_job", lambda self, job_id: (_ for _ in ()).throw(KeyError(f"job not found: {job_id}")))

    result = runner.invoke(app, ["incident", "scan", "--job-id", "job_missing"])

    assert result.exit_code == 1
    assert "job not found: job_missing" in result.stderr


def test_incident_scan_reports_updated_incident_when_matching_row_exists(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AUTORESEARCH_DB", str(tmp_path / "state" / "autoresearch.db"))
    monkeypatch.setenv("AUTORESEARCH_REPO_ROOT", str(tmp_path))
    _write_repo_config(tmp_path)

    init_db(tmp_path / "state" / "autoresearch.db")
    run_registry = RunRegistry(tmp_path / "state" / "autoresearch.db")
    run_record = run_registry.create_run(RunCreateRequest(run_kind="probe", project="demo"))
    job_record = run_registry.create_job(
        run_id=run_record.run_id,
        backend="pbs",
        queue="debug",
        walltime="00:10:00",
        filesystems="eagle",
        select_expr="1:system=polaris",
        place_expr="scatter",
        submit_script_path="/tmp/submit.pbs",
        stdout_path="/tmp/stdout.log",
        stderr_path="/tmp/stderr.log",
        pbs_job_id="123456.polaris-pbs-01",
    )

    fake_bridge = FakeBridgeService()
    fake_snapshot_dir = tmp_path / "state" / "incidents" / job_record.job_id / "2026-04-16T12:00:00"
    fake_snapshot_dir.mkdir(parents=True)
    fake_snapshot = IncidentSnapshotRef(
        scan_time="2026-04-16T12:00:00",
        snapshot_dir=fake_snapshot_dir,
        qstat_json_path=fake_snapshot_dir / "qstat.json",
        stdout_tail_path=fake_snapshot_dir / "stdout.tail.log",
        stderr_tail_path=fake_snapshot_dir / "stderr.tail.log",
    )
    fake_fetched = IncidentFetchResult(source="live", snapshot=fake_snapshot, previous_snapshot=None)
    fake_normalized = NormalizedIncidentInput(
        job_id=job_record.job_id,
        run_id=job_record.run_id,
        pbs_job_id=job_record.pbs_job_id,
        job_state="F",
        comment="cannot open",
        exec_host="node01",
        stdout_tail="stdout tail",
        stderr_tail="stderr tail",
        snapshot_dir=fake_snapshot.snapshot_dir,
        scan_time=fake_snapshot.scan_time,
        current_log_tail_hash="abc123",
        previous_log_tail_hash=None,
    )
    fake_classified = ClassifiedIncident(
        category="ENV_PATH_ERROR",
        severity="HIGH",
        fingerprint="fp-123",
        matched_lines=("cannot open",),
        rule_name="env_path_error",
    )

    monkeypatch.setattr(cli_module, "build_bridge_service", lambda: fake_bridge)
    monkeypatch.setattr(
        cli_module,
        "collect_incident_evidence",
        lambda paths, job, bridge, remote_root: fake_fetched,
    )
    monkeypatch.setattr(cli_module, "normalize_incident_evidence", lambda **kwargs: fake_normalized)
    monkeypatch.setattr(cli_module, "classify_incident", lambda incident: fake_classified)

    with connect_db(tmp_path / "state" / "autoresearch.db") as conn:
        conn.execute(
            """
            INSERT INTO incidents (
                incident_id, run_id, job_id, severity, category, fingerprint,
                evidence_json, auto_action, status, created_at, updated_at,
                resolved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "incident_seed",
                run_record.run_id,
                job_record.job_id,
                "HIGH",
                "ENV_PATH_ERROR",
                "fp-123",
                '{"scan_time":"2026-04-16T11:59:00"}',
                None,
                "RESOLVED",
                "2026-04-16T11:59:00",
                "2026-04-16T11:59:00",
                "2026-04-16T11:59:30",
            ),
        )

    result = runner.invoke(app, ["incident", "scan", "--job-id", job_record.job_id])

    assert result.exit_code == 0
    assert "updated incident" in result.stdout.lower()
    assert "source=live" in result.stdout


def test_retry_request_approve_execute_round_trip(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AUTORESEARCH_DB", str(tmp_path / "state" / "autoresearch.db"))
    monkeypatch.setenv("AUTORESEARCH_REPO_ROOT", str(tmp_path))
    _write_repo_config(tmp_path)

    incident_id, _, source_job_id = _seed_retryable_incident(tmp_path)

    request_result = runner.invoke(app, ["retry", "request", "--incident-id", incident_id])
    assert request_result.exit_code == 0
    retry_request_id = request_result.stdout.strip().split("\t")[0]
    assert retry_request_id.startswith("retry_")
    assert "\tPENDING\t" in request_result.stdout

    duplicate_result = runner.invoke(app, ["retry", "request", "--incident-id", incident_id])
    assert duplicate_result.exit_code == 1
    assert "active retry request already exists for incident" in duplicate_result.stderr

    approve_result = runner.invoke(
        app,
        [
            "retry",
            "approve",
            "--retry-request-id",
            retry_request_id,
            "--reason",
            "filesystem recovered",
        ],
    )
    assert approve_result.exit_code == 0
    assert "\tAPPROVED\tNOT_STARTED" in approve_result.stdout

    calls: list[dict[str, object]] = []
    fake_service = FakeBridgeService()

    def fake_submit_live_probe_run(**kwargs):
        calls.append(kwargs)
        return type(
            "SubmittedProbeRun",
            (),
            {
                "run_id": "run_retry",
                "job_id": "job_retry",
                "pbs_job_id": "456.polaris",
            },
        )()

    monkeypatch.setattr(cli_module, "build_bridge_service", lambda: fake_service)
    monkeypatch.setattr(cli_module, "submit_live_probe_run", fake_submit_live_probe_run)

    execute_result = runner.invoke(
        app,
        ["retry", "execute", "--retry-request-id", retry_request_id],
    )

    assert execute_result.exit_code == 0
    assert execute_result.stdout.strip() == f"{retry_request_id}\trun_retry\tjob_retry\t456.polaris"
    assert calls and calls[0]["run_kind"] == "probe-retry"
    assert calls[0]["project"] == "demo"
    assert calls[0]["queue"] == "debug"
    assert calls[0]["walltime"] == "00:10:00"
    assert calls[0]["service"] is fake_service

    retry_registry = RetryRequestRegistry(tmp_path / "state" / "autoresearch.db")
    record = retry_registry.get(retry_request_id)
    assert record.approval_status == "APPROVED"
    assert record.execution_status == "SUBMITTED"
    assert record.attempt_count == 1
    assert record.result_run_id == "run_retry"
    assert record.result_job_id == "job_retry"
    assert record.result_pbs_job_id == "456.polaris"

    decisions = DecisionLog(tmp_path / "state" / "autoresearch.db").list_for_target(
        "retry_request", retry_request_id
    )
    assert [row.decision for row in decisions] == ["approve-retry", "execute-approved-retry"]


def test_retry_reject_and_list_show_rejected_request(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AUTORESEARCH_DB", str(tmp_path / "state" / "autoresearch.db"))
    monkeypatch.setenv("AUTORESEARCH_REPO_ROOT", str(tmp_path))
    _write_repo_config(tmp_path)

    incident_id, _, _ = _seed_retryable_incident(tmp_path)
    request_result = runner.invoke(app, ["retry", "request", "--incident-id", incident_id])
    retry_request_id = request_result.stdout.strip().split("\t")[0]

    reject_result = runner.invoke(
        app,
        [
            "retry",
            "reject",
            "--retry-request-id",
            retry_request_id,
            "--reason",
            "not safe to retry",
        ],
    )

    assert reject_result.exit_code == 0
    assert "\tREJECTED\tNOT_STARTED" in reject_result.stdout

    list_result = runner.invoke(app, ["retry", "list"])
    row = list_result.stdout.strip().splitlines()[0].split("\t")
    assert row[0] == retry_request_id
    assert row[1] == incident_id
    assert row[3] == "REJECTED"
    assert row[4] == "NOT_STARTED"

def test_job_render_pbs_rejects_configured_remote_root_with_whitespace(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AUTORESEARCH_REPO_ROOT", str(tmp_path))
    _write_repo_config(tmp_path)
    (tmp_path / "conf" / "app.yaml").write_text(
        "app_name: auto-research\n"
        "paths:\n"
        "  state_dir: state\n"
        "  cache_dir: cache\n"
        "  logs_dir: logs\n"
        "  db_path: state/autoresearch.db\n"
        "remote:\n"
        "  root: /custom/remote root\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "job",
            "render-pbs",
            "--run-id",
            "run_demo",
            "--project",
            "demo",
            "--queue",
            "debug",
            "--walltime",
            "00:10:00",
            "--entrypoint-path",
            "/tmp/entrypoint.sh",
        ],
    )

    assert result.exit_code != 0
    assert isinstance(result.exception, ValueError)
    assert str(result.exception) == "remote_root must not contain whitespace"


def test_job_list_prints_persisted_job_record(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AUTORESEARCH_DB", str(tmp_path / "state" / "autoresearch.db"))
    monkeypatch.setenv("AUTORESEARCH_REPO_ROOT", str(tmp_path))
    _write_repo_config(tmp_path)

    init_db(tmp_path / "state" / "autoresearch.db")
    registry = RunRegistry(tmp_path / "state" / "autoresearch.db")
    record = registry.create_job(
        run_id="run_demo",
        backend="pbs",
        queue="debug",
        walltime="00:10:00",
        filesystems="eagle",
        select_expr="1:system=polaris",
        place_expr="scatter",
        submit_script_path="/tmp/submit.pbs",
        stdout_path="/tmp/stdout.log",
        stderr_path="/tmp/stderr.log",
    )

    result = runner.invoke(app, ["job", "list"])

    assert result.exit_code == 0
    row = result.stdout.strip().splitlines()[0].split("\t")
    assert row[0] == record.job_id
    assert row[1] == "run_demo"
    assert row[2] == "pbs"
    assert row[3] == record.state
    assert row[4] == "-"
    assert row[5] == record.updated_at


def test_bridge_attach_uses_bridge_service(monkeypatch) -> None:
    fake_service = FakeBridgeService()
    monkeypatch.setattr(cli_module, "build_bridge_service", lambda: fake_service)

    result = runner.invoke(app, ["bridge", "attach"])

    assert result.exit_code == 0
    assert fake_service.calls == ["attach"]
    assert "Bridge polaris-relay: ATTACHED" in result.stdout
    assert "OpenSSH control master attach command completed." in result.stdout


def test_bridge_attach_reports_failures(monkeypatch) -> None:
    fake_service = FakeBridgeService(
        attach_result=CommandResult(
            args=("ssh", "-MNf", "polaris-relay"),
            returncode=255,
            stdout="",
            stderr="Permission denied",
            duration_seconds=0.01,
        )
    )
    monkeypatch.setattr(cli_module, "build_bridge_service", lambda: fake_service)

    result = runner.invoke(app, ["bridge", "attach"])

    assert result.exit_code == 255
    assert fake_service.calls == ["attach"]
    assert "Command failed (255): ssh -MNf polaris-relay" in result.stderr
    assert "Permission denied" in result.stderr


def test_bridge_check_exits_nonzero_when_not_attached(monkeypatch) -> None:
    fake_service = FakeBridgeService(
        status_result=BridgeStatusResult(
            alias="polaris-relay",
            state="DETACHED",
            explanation="No active OpenSSH control master is attached.",
            command_result=CommandResult(
                args=("ssh", "-O", "check", "polaris-relay"),
                returncode=255,
                stdout="",
                stderr="Control socket connect(/tmp/cm): No such file or directory",
                duration_seconds=0.01,
            ),
            control_path_exists=None,
        )
    )
    monkeypatch.setattr(cli_module, "build_bridge_service", lambda: fake_service)

    result = runner.invoke(app, ["bridge", "check"])

    assert result.exit_code == 1
    assert fake_service.calls == ["status"]
    assert "Bridge polaris-relay: DETACHED" in result.stdout
    assert "No active OpenSSH control master is attached." in result.stdout


def test_bridge_status_reports_attached(monkeypatch) -> None:
    fake_service = FakeBridgeService()
    monkeypatch.setattr(cli_module, "build_bridge_service", lambda: fake_service)

    result = runner.invoke(app, ["bridge", "status"])

    assert result.exit_code == 0
    assert fake_service.calls == ["status"]
    assert "Bridge polaris-relay: ATTACHED" in result.stdout


def test_bridge_exec_uses_bridge_service_and_prints_stdout(monkeypatch) -> None:
    fake_service = FakeBridgeService(
        exec_result=CommandResult(
            args=("ssh", "polaris-relay", "pwd"),
            returncode=0,
            stdout="/remote/workdir\n",
            stderr="",
            duration_seconds=0.01,
        )
    )
    monkeypatch.setattr(cli_module, "build_bridge_service", lambda: fake_service)

    result = runner.invoke(app, ["bridge", "exec", "--", "pwd"])

    assert result.exit_code == 0
    assert fake_service.calls == ["status", "exec"]
    assert fake_service.exec_calls == ["pwd"]
    assert result.stdout == "/remote/workdir\n"


def test_bridge_exec_preserves_argument_boundaries(monkeypatch) -> None:
    fake_service = FakeBridgeService()
    monkeypatch.setattr(cli_module, "build_bridge_service", lambda: fake_service)

    result = runner.invoke(app, ["bridge", "exec", "--", "printf", "%s", "value with spaces"])

    assert result.exit_code == 0
    assert fake_service.exec_calls == ["printf %s 'value with spaces'"]


def test_bridge_exec_reports_detached_bridge_errors(monkeypatch) -> None:
    fake_service = FakeBridgeService(
        status_result=BridgeStatusResult(
            alias="polaris-relay",
            state="DETACHED",
            explanation="No active OpenSSH control master is attached.",
            command_result=CommandResult(
                args=("ssh", "-O", "check", "polaris-relay"),
                returncode=255,
                stdout="",
                stderr="Control socket connect(/tmp/cm): No such file or directory",
                duration_seconds=0.01,
            ),
            control_path_exists=None,
        )
    )
    monkeypatch.setattr(cli_module, "build_bridge_service", lambda: fake_service)

    result = runner.invoke(app, ["bridge", "exec", "--", "pwd"])

    assert result.exit_code == 1
    assert "bridge must be ATTACHED before remote operations (state=DETACHED)" in result.stderr


def test_bridge_copy_to_reports_path_validation_errors(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AUTORESEARCH_REPO_ROOT", str(tmp_path))
    _write_repo_config(tmp_path)
    fake_service = FakeBridgeService()
    monkeypatch.setattr(cli_module, "build_bridge_service", lambda: fake_service)

    result = runner.invoke(
        app,
        [
            "bridge",
            "copy-to",
            "--src",
            str(tmp_path / "local.txt"),
            "--dst",
            "/tmp/outside.txt",
        ],
    )

    assert result.exit_code == 1
    assert "remote_path must stay within remote_root" in result.stderr


def test_bridge_copy_to_uses_bridge_service(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AUTORESEARCH_REPO_ROOT", str(tmp_path))
    _write_repo_config(tmp_path)
    fake_service = FakeBridgeService()
    monkeypatch.setattr(cli_module, "build_bridge_service", lambda: fake_service)

    result = runner.invoke(
        app,
        [
            "bridge",
            "copy-to",
            "--src",
            str(tmp_path / "local.txt"),
            "--dst",
            "/eagle/lc-mpi/Zhiqing/auto-research/jobs/probe/entrypoint.sh",
        ],
    )

    assert result.exit_code == 0
    assert fake_service.calls == ["status", "copy_to"]
    assert fake_service.copy_to_calls == [
        (str(tmp_path / "local.txt"), "/eagle/lc-mpi/Zhiqing/auto-research/jobs/probe/entrypoint.sh")
    ]


def test_bridge_copy_from_uses_bridge_service(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AUTORESEARCH_REPO_ROOT", str(tmp_path))
    _write_repo_config(tmp_path)
    fake_service = FakeBridgeService()
    monkeypatch.setattr(cli_module, "build_bridge_service", lambda: fake_service)

    result = runner.invoke(
        app,
        [
            "bridge",
            "copy-from",
            "--src",
            "/eagle/lc-mpi/Zhiqing/auto-research/runs/probe/stdout.log",
            "--dst",
            str(tmp_path / "stdout.log"),
        ],
    )

    assert result.exit_code == 0
    assert fake_service.calls == ["status", "copy_from"]
    assert fake_service.copy_from_calls == [
        ("/eagle/lc-mpi/Zhiqing/auto-research/runs/probe/stdout.log", str(tmp_path / "stdout.log"))
    ]


def test_bridge_copy_from_reports_failed_copy_command(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AUTORESEARCH_REPO_ROOT", str(tmp_path))
    _write_repo_config(tmp_path)
    fake_service = FakeBridgeService(
        copy_from_result=CommandResult(
            args=("scp", "polaris-relay:/remote/file.txt", str(tmp_path / "stdout.log")),
            returncode=7,
            stdout="",
            stderr="scp failed",
            duration_seconds=0.01,
        )
    )
    monkeypatch.setattr(cli_module, "build_bridge_service", lambda: fake_service)

    result = runner.invoke(
        app,
        [
            "bridge",
            "copy-from",
            "--src",
            "/eagle/lc-mpi/Zhiqing/auto-research/runs/probe/stdout.log",
            "--dst",
            str(tmp_path / "stdout.log"),
        ],
    )

    assert result.exit_code == 7
    assert fake_service.calls == ["status", "copy_from"]
    assert "Command failed (7): scp polaris-relay:/remote/file.txt" in result.stderr
    assert "scp failed" in result.stderr


def test_remote_bootstrap_force_invokes_helper(monkeypatch) -> None:
    calls: list[bool] = []

    def fake_run_remote_bootstrap(*, force: bool) -> None:
        calls.append(force)

    monkeypatch.setattr(cli_module, "run_remote_bootstrap", fake_run_remote_bootstrap)

    result = runner.invoke(app, ["remote", "bootstrap", "--force"])

    assert result.exit_code == 0
    assert calls == [True]
    assert "Remote bootstrap completed." in result.stdout


def test_remote_bootstrap_force_reports_completion(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AUTORESEARCH_REPO_ROOT", str(tmp_path))
    _write_repo_config(tmp_path)
    calls: list[bool] = []

    def fake_run_remote_bootstrap(*, force: bool) -> None:
        calls.append(force)

    monkeypatch.setattr(cli_module, "run_remote_bootstrap", fake_run_remote_bootstrap)

    result = runner.invoke(app, ["remote", "bootstrap", "--force"])

    assert result.exit_code == 0
    assert calls == [True]
    assert "Remote bootstrap completed." in result.stdout


def test_bridge_detach_uses_bridge_service(monkeypatch) -> None:
    fake_service = FakeBridgeService()
    monkeypatch.setattr(cli_module, "build_bridge_service", lambda: fake_service)

    result = runner.invoke(app, ["bridge", "detach"])

    assert result.exit_code == 0
    assert fake_service.calls == ["detach"]
    assert "Bridge polaris-relay: DETACHED" in result.stdout
    assert "OpenSSH control master exited cleanly." in result.stdout


def test_bridge_detach_reports_detached_state_when_no_master(monkeypatch) -> None:
    fake_service = FakeBridgeService(
        detach_result=CommandResult(
            args=("ssh", "-O", "exit", "polaris-relay"),
            returncode=255,
            stdout="",
            stderr="Control socket connect(/tmp/cm): No such file or directory",
            duration_seconds=0.01,
        ),
        status_result=BridgeStatusResult(
            alias="polaris-relay",
            state="DETACHED",
            explanation="No active OpenSSH control master is attached.",
            command_result=CommandResult(
                args=("ssh", "-O", "check", "polaris-relay"),
                returncode=255,
                stdout="",
                stderr="Control socket connect(/tmp/cm): No such file or directory",
                duration_seconds=0.01,
            ),
            control_path_exists=None,
        ),
    )
    monkeypatch.setattr(cli_module, "build_bridge_service", lambda: fake_service)

    result = runner.invoke(app, ["bridge", "detach"])

    assert result.exit_code == 0
    assert fake_service.calls == ["detach", "status"]
    assert "Bridge polaris-relay: DETACHED" in result.stdout
    assert "No active OpenSSH control master is attached." in result.stdout


def test_repository_docs_exist() -> None:
    repo_root = Path(__file__).resolve().parents[1]

    assert (repo_root / "README.md").exists()
    assert (repo_root / "AGENTS.md").exists()
    assert (repo_root / "PLANS.md").exists()
    assert (repo_root / "SESSION_RESUME.md").exists()
