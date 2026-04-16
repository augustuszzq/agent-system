from dataclasses import dataclass
from pathlib import Path
from typing import Literal


BridgeState = Literal["DETACHED", "ATTACHED", "STALE"]
IncidentCategory = Literal[
    "FILESYSTEM_UNAVAILABLE",
    "RESOURCE_OOM",
    "RESOURCE_WALLTIME",
    "ENV_IMPORT_ERROR",
    "ENV_PATH_ERROR",
    "NCCL_FAILURE",
    "MPI_BOOTSTRAP",
    "NO_HEARTBEAT",
    "UNKNOWN",
]
IncidentSeverity = Literal["CRITICAL", "HIGH", "MEDIUM"]
IncidentStatus = Literal["OPEN", "RESOLVED"]
RetryAction = Literal["RETRY_SAME_CONFIG"]
RetryApprovalStatus = Literal["PENDING", "APPROVED", "REJECTED"]
RetryExecutionStatus = Literal["NOT_STARTED", "SUBMITTED", "FAILED"]


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
    exit_status: int | None = None
    queue: str | None = None
    comment: str | None = None
    exec_host: str | None = None
    stdout_path: str | None = None
    stderr_path: str | None = None


@dataclass(frozen=True)
class IncidentSnapshotRef:
    scan_time: str
    snapshot_dir: Path
    qstat_json_path: Path
    stdout_tail_path: Path
    stderr_tail_path: Path


@dataclass(frozen=True)
class IncidentFetchResult:
    source: Literal["live", "local-fallback"]
    snapshot: IncidentSnapshotRef
    previous_snapshot: IncidentSnapshotRef | None


@dataclass(frozen=True)
class NormalizedIncidentInput:
    job_id: str
    run_id: str
    pbs_job_id: str | None
    job_state: str
    comment: str | None
    exec_host: str | None
    stdout_tail: str
    stderr_tail: str
    snapshot_dir: Path
    scan_time: str
    current_log_tail_hash: str
    previous_log_tail_hash: str | None


@dataclass(frozen=True)
class ClassifiedIncident:
    category: IncidentCategory
    severity: IncidentSeverity
    fingerprint: str
    matched_lines: tuple[str, ...]
    rule_name: str
