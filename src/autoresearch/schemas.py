from dataclasses import dataclass
from typing import Literal


BridgeState = Literal["DETACHED", "ATTACHED", "STALE"]


@dataclass(frozen=True)
class RunCreateRequest:
    run_kind: str
    project: str
    notes: str | None = None


@dataclass(frozen=True)
class CommandResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float


@dataclass(frozen=True)
class BridgeStatusResult:
    alias: str
    state: BridgeState
    explanation: str
    command_result: CommandResult | None = None
    control_path_exists: bool | None = None


@dataclass(frozen=True)
class PolarisJobRequest:
    run_id: str
    job_name: str
    project: str
    queue: str
    walltime: str
    select_expr: str
    entrypoint_path: str
    remote_root: str
    place_expr: str = "scatter"
    filesystems: str = "eagle"
    stdout_path: str | None = None
    stderr_path: str | None = None
    submit_script_path: str | None = None


@dataclass(frozen=True)
class RenderedPBSScript:
    script_text: str


@dataclass(frozen=True)
class QsubParseResult:
    raw_output: str
    pbs_job_id: str
    is_success: bool


@dataclass(frozen=True)
class QstatParseResult:
    pbs_job_id: str
    state: str
    queue: str | None = None
    comment: str | None = None
    exec_host: str | None = None
    stdout_path: str | None = None
    stderr_path: str | None = None
