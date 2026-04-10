from autoresearch.schemas import BridgeStatusResult, CommandResult


DETACHED_PATTERNS = (
    "no such file or directory",
    "no master running",
    "control socket connect",
)


def classify_bridge_status(
    alias: str,
    check_result: CommandResult,
    control_path_exists: bool | None,
) -> BridgeStatusResult:
    combined = f"{check_result.stdout}\n{check_result.stderr}".lower()
    if check_result.returncode == 0:
        return BridgeStatusResult(
            alias=alias,
            state="ATTACHED",
            explanation="OpenSSH control master is healthy.",
            command_result=check_result,
            control_path_exists=control_path_exists,
        )
    if control_path_exists:
        return BridgeStatusResult(
            alias=alias,
            state="STALE",
            explanation="Control socket exists but the master check failed.",
            command_result=check_result,
            control_path_exists=control_path_exists,
        )
    if any(pattern in combined for pattern in DETACHED_PATTERNS):
        return BridgeStatusResult(
            alias=alias,
            state="DETACHED",
            explanation="No active OpenSSH control master is attached.",
            command_result=check_result,
            control_path_exists=control_path_exists,
        )
    return BridgeStatusResult(
        alias=alias,
        state="STALE",
        explanation="Bridge state is abnormal and requires operator attention.",
        command_result=check_result,
        control_path_exists=control_path_exists,
    )
