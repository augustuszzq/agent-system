from collections.abc import Callable
from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import time

from autoresearch.bridge.health import classify_bridge_status
from autoresearch.schemas import BridgeStatusResult, CommandResult
from autoresearch.settings import BridgeSettings


CommandRunner = Callable[[tuple[str, ...]], CommandResult]


def run_command(args: tuple[str, ...]) -> CommandResult:
    started = time.perf_counter()
    try:
        completed = subprocess.run(args, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        return CommandResult(
            args=args,
            returncode=127,
            stdout="",
            stderr=f"{exc.filename or args[0]} executable not found",
            duration_seconds=time.perf_counter() - started,
        )
    return CommandResult(
        args=args,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        duration_seconds=time.perf_counter() - started,
    )


@dataclass
class SSHMasterClient:
    settings: BridgeSettings
    runner: CommandRunner = run_command

    def attach(self) -> CommandResult:
        return self.runner(("ssh", "-MNf", self.settings.alias))

    def check(self) -> CommandResult:
        return self.runner(("ssh", "-O", "check", self.settings.alias))

    def detach(self) -> CommandResult:
        return self.runner(("ssh", "-O", "exit", self.settings.alias))

    def status(self) -> BridgeStatusResult:
        check_result = self.check()
        return classify_bridge_status(
            alias=self.settings.alias,
            check_result=check_result,
            control_path_exists=self._control_path_exists(),
        )

    def _control_path_exists(self) -> bool | None:
        raw = self.settings.control_path
        if "%" in raw:
            return None
        return Path(os.path.expanduser(raw)).exists()
