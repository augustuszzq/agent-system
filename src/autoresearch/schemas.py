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
