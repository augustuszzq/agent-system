from __future__ import annotations

from pathlib import Path
import posixpath
from typing import Protocol

from autoresearch.schemas import BridgeStatusResult, CommandResult


class RemoteBridgeError(RuntimeError):
    """Raised when a remote bridge operation is not safe to execute."""


class _RemoteBridgeClient(Protocol):
    def status(self) -> BridgeStatusResult: ...

    def exec(self, command: str) -> CommandResult: ...

    def copy_to(self, local_path: str, remote_path: str) -> CommandResult: ...

    def copy_from(self, remote_path: str, local_path: str) -> CommandResult: ...


def _normalize_remote_path(path_value: str | Path, field_name: str) -> str:
    raw_value = str(path_value)
    if not raw_value.strip():
        raise RemoteBridgeError(f"{field_name} must be non-empty")

    normalized = posixpath.normpath(raw_value)
    if not normalized.startswith("/"):
        raise RemoteBridgeError(f"{field_name} must be absolute")
    return normalized


def ensure_bridge_attached(client: _RemoteBridgeClient) -> None:
    status = client.status()
    if status.state != "ATTACHED":
        raise RemoteBridgeError(
            f"bridge must be ATTACHED before remote operations (state={status.state})"
        )


def ensure_remote_path_within_root(remote_path: str | Path, remote_root: str | Path) -> str:
    normalized_root = _normalize_remote_path(remote_root, "remote_root")
    normalized_remote = _normalize_remote_path(remote_path, "remote_path")

    if posixpath.commonpath([normalized_root, normalized_remote]) != normalized_root:
        raise RemoteBridgeError("remote_path must stay within remote_root")
    return normalized_remote


def execute_remote_command(client: _RemoteBridgeClient, remote_command: str) -> CommandResult:
    ensure_bridge_attached(client)
    return client.exec(remote_command)


def copy_to_remote(
    client: _RemoteBridgeClient,
    local_path: Path,
    remote_path: str | Path,
    remote_root: str | Path,
) -> CommandResult:
    ensure_bridge_attached(client)
    safe_remote_path = ensure_remote_path_within_root(remote_path, remote_root)
    return client.copy_to(str(local_path), safe_remote_path)


def copy_from_remote(
    client: _RemoteBridgeClient,
    remote_path: str | Path,
    local_path: Path,
    remote_root: str | Path,
) -> CommandResult:
    ensure_bridge_attached(client)
    safe_remote_path = ensure_remote_path_within_root(remote_path, remote_root)
    return client.copy_from(safe_remote_path, str(local_path))
