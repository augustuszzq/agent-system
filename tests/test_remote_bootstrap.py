from pathlib import Path

import pytest
import typer

from autoresearch import cli as cli_module

from autoresearch.bridge.remote_exec import (
    RemoteBridgeError,
    copy_from_remote,
    copy_to_remote,
    ensure_remote_path_within_root,
    execute_remote_command,
)
from autoresearch.bridge.remote_fs import build_bootstrap_files, build_bootstrap_mkdir_command
from autoresearch.schemas import BridgeStatusResult, CommandResult
from autoresearch.settings import BridgeSettings, ProbeSettings, Settings


REMOTE_ROOT = "/eagle/lc-mpi/Zhiqing/auto-research"


def _result(*args: str) -> CommandResult:
    return CommandResult(
        args=tuple(args),
        returncode=0,
        stdout="ok",
        stderr="",
        duration_seconds=0.01,
    )


class FakeRemoteClient:
    def __init__(self, state: str = "ATTACHED") -> None:
        self.state = state
        self.exec_calls: list[str] = []
        self.copy_to_calls: list[tuple[str, str]] = []
        self.copy_from_calls: list[tuple[str, str]] = []

    def status(self) -> BridgeStatusResult:
        return BridgeStatusResult(
            alias="polaris-relay",
            state=self.state,  # type: ignore[arg-type]
            explanation="status",
        )

    def exec(self, command: str) -> CommandResult:
        self.exec_calls.append(command)
        return _result("ssh", "polaris-relay", command)

    def copy_to(self, local_path: str, remote_path: str) -> CommandResult:
        self.copy_to_calls.append((local_path, remote_path))
        return _result("scp", local_path, remote_path)

    def copy_from(self, remote_path: str, local_path: str) -> CommandResult:
        self.copy_from_calls.append((remote_path, local_path))
        return _result("scp", remote_path, local_path)


def test_execute_remote_command_requires_attached_bridge() -> None:
    client = FakeRemoteClient(state="DETACHED")

    with pytest.raises(RemoteBridgeError, match="bridge must be ATTACHED"):
        execute_remote_command(client, "pwd")

    assert client.exec_calls == []


def test_execute_remote_command_forwards_to_attached_bridge() -> None:
    client = FakeRemoteClient(state="ATTACHED")

    result = execute_remote_command(client, "pwd")

    assert result.args == ("ssh", "polaris-relay", "pwd")
    assert client.exec_calls == ["pwd"]


@pytest.mark.parametrize(
    "remote_path",
    [
        "/eagle/lc-mpi/Zhiqing/auto-research/../other/file.txt",
        "/tmp/outside.txt",
        "jobs/probe/entrypoint.sh",
    ],
)
def test_ensure_remote_path_within_root_rejects_outside_paths(remote_path: str) -> None:
    with pytest.raises(RemoteBridgeError):
        ensure_remote_path_within_root(remote_path, REMOTE_ROOT)


def test_copy_to_remote_enforces_remote_root_and_forwards_copy_call() -> None:
    client = FakeRemoteClient(state="ATTACHED")
    remote_path = f"{REMOTE_ROOT}/jobs/probe/entrypoint.sh"

    result = copy_to_remote(client, Path("/tmp/local.txt"), remote_path, REMOTE_ROOT)

    assert result.args == ("scp", "/tmp/local.txt", remote_path)
    assert client.copy_to_calls == [("/tmp/local.txt", remote_path)]


def test_copy_to_remote_rejects_paths_outside_remote_root() -> None:
    client = FakeRemoteClient(state="ATTACHED")

    with pytest.raises(RemoteBridgeError, match="remote_path must stay within remote_root"):
        copy_to_remote(client, Path("/tmp/local.txt"), "/tmp/outside.txt", REMOTE_ROOT)

    assert client.copy_to_calls == []


def test_copy_from_remote_enforces_remote_root_and_forwards_copy_call() -> None:
    client = FakeRemoteClient(state="ATTACHED")
    remote_path = f"{REMOTE_ROOT}/runs/probe/stdout.log"

    result = copy_from_remote(client, remote_path, Path("/tmp/stdout.log"), REMOTE_ROOT)

    assert result.args == ("scp", remote_path, "/tmp/stdout.log")
    assert client.copy_from_calls == [(remote_path, "/tmp/stdout.log")]


def test_build_bootstrap_mkdir_command_returns_expected_directories() -> None:
    command = build_bootstrap_mkdir_command(REMOTE_ROOT)

    assert command == (
        "mkdir -p /eagle/lc-mpi/Zhiqing/auto-research "
        "/eagle/lc-mpi/Zhiqing/auto-research/jobs "
        "/eagle/lc-mpi/Zhiqing/auto-research/jobs/probe "
        "/eagle/lc-mpi/Zhiqing/auto-research/runs "
        "/eagle/lc-mpi/Zhiqing/auto-research/manifests"
    )


def test_build_bootstrap_files_returns_managed_paths_and_contents() -> None:
    files = build_bootstrap_files(REMOTE_ROOT)

    assert set(files) == {
        f"{REMOTE_ROOT}/README.remote.md",
        f"{REMOTE_ROOT}/jobs/probe/entrypoint.sh",
    }
    assert files[f"{REMOTE_ROOT}/README.remote.md"].startswith("# Auto Research Remote Root")
    assert files[f"{REMOTE_ROOT}/jobs/probe/entrypoint.sh"].startswith("#!/bin/bash")


@pytest.mark.parametrize(
    "remote_root",
    [
        "/safe;touch/tmp/pwned",
        "/safe$(cmd)",
    ],
)
def test_bootstrap_helpers_reject_shell_metacharacters_in_remote_root(remote_root: str) -> None:
    with pytest.raises(RemoteBridgeError, match="remote_root contains unsafe characters"):
        build_bootstrap_mkdir_command(remote_root)

    with pytest.raises(RemoteBridgeError, match="remote_root contains unsafe characters"):
        build_bootstrap_files(remote_root)


def test_run_remote_bootstrap_executes_mkdir_command(monkeypatch, tmp_path: Path) -> None:
    settings = Settings(
        app_name="auto-research",
        paths=type(
            "Paths",
            (),
            {
                "repo_root": tmp_path,
                "state_dir": tmp_path / "state",
                "cache_dir": tmp_path / "cache",
                "logs_dir": tmp_path / "logs",
                "db_path": tmp_path / "state" / "autoresearch.db",
            },
        )(),
        remote_root=REMOTE_ROOT,
        bridge=BridgeSettings(
            alias="polaris-relay",
            host="example-host",
            user="zzq",
            control_path="~/.ssh/cm-%C",
            server_alive_interval=60,
            server_alive_count_max=3,
            connect_timeout=15,
        ),
        probe=ProbeSettings(project="demo", queue="debug", walltime="00:10:00"),
    )
    service = object()
    result = CommandResult(
        args=("ssh", "polaris-relay", "mkdir"),
        returncode=0,
        stdout="",
        stderr="",
        duration_seconds=0.01,
    )
    calls: list[tuple[object, str]] = []

    monkeypatch.setattr(cli_module, "load_settings", lambda: settings)
    monkeypatch.setattr(cli_module, "build_bridge_service", lambda: service)

    def fake_execute_remote_command(service_arg: object, command: str) -> CommandResult:
        calls.append((service_arg, command))
        return result

    monkeypatch.setattr(cli_module, "execute_remote_command", fake_execute_remote_command)

    cli_module.run_remote_bootstrap(force=False)

    assert calls == [(service, build_bootstrap_mkdir_command(REMOTE_ROOT))]


def test_run_remote_bootstrap_raises_on_failed_remote_command(monkeypatch, tmp_path: Path) -> None:
    settings = Settings(
        app_name="auto-research",
        paths=type(
            "Paths",
            (),
            {
                "repo_root": tmp_path,
                "state_dir": tmp_path / "state",
                "cache_dir": tmp_path / "cache",
                "logs_dir": tmp_path / "logs",
                "db_path": tmp_path / "state" / "autoresearch.db",
            },
        )(),
        remote_root=REMOTE_ROOT,
        bridge=BridgeSettings(
            alias="polaris-relay",
            host="example-host",
            user="zzq",
            control_path="~/.ssh/cm-%C",
            server_alive_interval=60,
            server_alive_count_max=3,
            connect_timeout=15,
        ),
        probe=ProbeSettings(project="demo", queue="debug", walltime="00:10:00"),
    )
    result = CommandResult(
        args=("ssh", "polaris-relay", "mkdir"),
        returncode=23,
        stdout="",
        stderr="permission denied",
        duration_seconds=0.01,
    )
    failed_results: list[CommandResult] = []

    monkeypatch.setattr(cli_module, "load_settings", lambda: settings)
    monkeypatch.setattr(cli_module, "build_bridge_service", lambda: object())
    monkeypatch.setattr(cli_module, "execute_remote_command", lambda service, command: result)
    monkeypatch.setattr(cli_module, "_echo_failed_command", failed_results.append)

    with pytest.raises(typer.Exit) as exc_info:
        cli_module.run_remote_bootstrap(force=False)

    assert exc_info.value.exit_code == 23
    assert failed_results == [result]


def test_run_remote_bootstrap_force_fails_fast_without_remote_exec(
    monkeypatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = Settings(
        app_name="auto-research",
        paths=type(
            "Paths",
            (),
            {
                "repo_root": tmp_path,
                "state_dir": tmp_path / "state",
                "cache_dir": tmp_path / "cache",
                "logs_dir": tmp_path / "logs",
                "db_path": tmp_path / "state" / "autoresearch.db",
            },
        )(),
        remote_root=REMOTE_ROOT,
        bridge=BridgeSettings(
            alias="polaris-relay",
            host="example-host",
            user="zzq",
            control_path="~/.ssh/cm-%C",
            server_alive_interval=60,
            server_alive_count_max=3,
            connect_timeout=15,
        ),
        probe=ProbeSettings(project="demo", queue="debug", walltime="00:10:00"),
    )

    monkeypatch.setattr(cli_module, "load_settings", lambda: settings)
    monkeypatch.setattr(
        cli_module,
        "execute_remote_command",
        lambda service, command: pytest.fail("execute_remote_command should not run"),
    )

    with pytest.raises(typer.Exit) as exc_info:
        cli_module.run_remote_bootstrap(force=True)

    assert exc_info.value.exit_code == 1
    assert "--force is not implemented until Task 7" in capsys.readouterr().err
