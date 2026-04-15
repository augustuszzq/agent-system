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
        "  connect_timeout: 15\n",
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
        self.calls: list[str] = []

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
    assert record.job_id in result.stdout
    assert "run_demo" in result.stdout
    assert "pbs" in result.stdout
    assert "-" in result.stdout
    assert record.state in result.stdout
    assert record.updated_at in result.stdout


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
