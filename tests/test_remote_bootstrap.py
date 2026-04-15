from pathlib import Path

import pytest

from autoresearch.bridge.remote_exec import (
    RemoteBridgeError,
    copy_from_remote,
    copy_to_remote,
    ensure_remote_path_within_root,
    execute_remote_command,
)
from autoresearch.bridge.remote_fs import build_bootstrap_files, build_bootstrap_mkdir_command
from autoresearch.schemas import BridgeStatusResult, CommandResult


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
