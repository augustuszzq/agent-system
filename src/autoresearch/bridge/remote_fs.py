from __future__ import annotations

import re
from textwrap import dedent

from autoresearch.bridge.remote_exec import RemoteBridgeError


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
        f"{normalized_root}/runs "
        f"{normalized_root}/manifests"
    )


def build_bootstrap_files(remote_root: str) -> dict[str, str]:
    normalized_root = _normalize_remote_root(remote_root)
    return {
        f"{normalized_root}/README.remote.md": _REMOTE_README,
        f"{normalized_root}/jobs/probe/entrypoint.sh": _PROBE_ENTRYPOINT,
    }
