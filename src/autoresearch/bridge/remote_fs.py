from __future__ import annotations

import re
import shlex
import tempfile
from pathlib import Path
from textwrap import dedent

from autoresearch.bridge.remote_exec import (
    RemoteBridgeError,
    copy_to_remote,
    execute_remote_command,
)


_REMOTE_README = dedent(
    """
    # Auto Research Remote Root

    This directory is managed by the Auto Research control plane.

    Managed paths:
    - README.remote.md
    - jobs/probe/entrypoint.sh

    Do not edit managed files by hand.
    Use `autoresearch remote bootstrap` to create missing directories and files.
    """
).strip() + "\n"


_PROBE_ENTRYPOINT = dedent(
    """
    #!/bin/bash
    set -euo pipefail

    echo "Auto Research built-in probe entrypoint"
    """
).strip() + "\n"


_SAFE_REMOTE_ROOT_RE = re.compile(r"^/(?:[A-Za-z0-9._-]+/)*[A-Za-z0-9._-]+$")


def _normalize_remote_root(remote_root: str) -> str:
    normalized = remote_root.strip()
    if not normalized:
        raise RemoteBridgeError("remote_root must be non-empty")
    if any(char.isspace() for char in normalized):
        raise RemoteBridgeError("remote_root must not contain whitespace")
    if not normalized.startswith("/"):
        raise RemoteBridgeError("remote_root must be absolute")
    normalized = normalized.rstrip("/") or "/"
    if not _SAFE_REMOTE_ROOT_RE.fullmatch(normalized):
        raise RemoteBridgeError("remote_root contains unsafe characters")
    return normalized


def build_bootstrap_mkdir_command(remote_root: str) -> str:
    normalized_root = _normalize_remote_root(remote_root)
    return (
        f"mkdir -p {normalized_root} "
        f"{normalized_root}/jobs "
        f"{normalized_root}/jobs/probe "
        f"{normalized_root}/repo "
        f"{normalized_root}/runs "
        f"{normalized_root}/manifests"
    )


def build_bootstrap_files(remote_root: str) -> dict[str, str]:
    normalized_root = _normalize_remote_root(remote_root)
    return {
        f"{normalized_root}/README.remote.md": _REMOTE_README,
        f"{normalized_root}/jobs/probe/entrypoint.sh": _PROBE_ENTRYPOINT,
    }


def _write_temporary_text_file(contents: str, *, suffix: str) -> Path:
    temp_file = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=suffix,
        delete=False,
    )
    try:
        temp_file.write(contents)
        temp_file.flush()
        return Path(temp_file.name)
    finally:
        temp_file.close()


def bootstrap_remote_root(client, remote_root: str, *, force: bool) -> None:
    normalized_root = _normalize_remote_root(remote_root)
    mkdir_result = execute_remote_command(
        client,
        build_bootstrap_mkdir_command(normalized_root),
    )
    if mkdir_result.returncode != 0:
        raise RemoteBridgeError(
            mkdir_result.stderr.strip()
            or f"failed to create bootstrap directories: {normalized_root}"
        )

    for remote_path, contents in build_bootstrap_files(normalized_root).items():
        should_upload = force
        if not force:
            check_result = execute_remote_command(
                client,
                f"test -f {shlex.quote(remote_path)}",
            )
            if check_result.returncode == 0:
                continue
            if check_result.returncode != 1:
                raise RemoteBridgeError(
                    f"failed to check remote file existence: {remote_path}"
                )
            should_upload = True

        if not should_upload:
            continue

        temp_path = _write_temporary_text_file(contents, suffix=Path(remote_path).name)
        try:
            copy_result = copy_to_remote(client, temp_path, remote_path, normalized_root)
            if copy_result.returncode != 0:
                raise RemoteBridgeError(
                    copy_result.stderr.strip() or f"failed to upload managed file: {remote_path}"
                )
        finally:
            temp_path.unlink(missing_ok=True)
