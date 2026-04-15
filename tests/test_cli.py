from pathlib import Path

from typer.testing import CliRunner

from autoresearch import cli as cli_module
from autoresearch.cli import app
from autoresearch.db import init_db
from autoresearch.runs.registry import RunRegistry
from autoresearch.schemas import BridgeStatusResult, CommandResult


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


def _write_repo_config(tmp_path: Path) -> None:
    (tmp_path / "conf").mkdir()
    _write_app_config(tmp_path / "conf")
    _write_bridge_config(tmp_path / "conf")


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


def test_remote_bootstrap_force_fails_fast(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AUTORESEARCH_REPO_ROOT", str(tmp_path))
    _write_repo_config(tmp_path)

    result = runner.invoke(app, ["remote", "bootstrap", "--force"])

    assert result.exit_code == 1
    assert "--force is not implemented until Task 7" in result.stderr


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
