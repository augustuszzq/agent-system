from typing import Literal, get_args, get_origin, get_type_hints

from autoresearch.bridge.health import classify_bridge_status
from autoresearch.schemas import BridgeStatusResult, CommandResult


def test_bridge_status_result_state_annotation_is_limited_to_known_states() -> None:
    state_hint = get_type_hints(BridgeStatusResult)["state"]

    assert get_origin(state_hint) is Literal
    assert get_args(state_hint) == ("DETACHED", "ATTACHED", "STALE")


def test_classify_bridge_status_reports_attached() -> None:
    result = CommandResult(
        args=("ssh", "-O", "check", "polaris-relay"),
        returncode=0,
        stdout="Master running",
        stderr="",
        duration_seconds=0.12,
    )

    status = classify_bridge_status(alias="polaris-relay", check_result=result, control_path_exists=None)

    assert status.alias == "polaris-relay"
    assert status.state == "ATTACHED"
    assert status.explanation == "OpenSSH control master is healthy."
    assert status.command_result is result
    assert status.control_path_exists is None


def test_classify_bridge_status_reports_detached_for_no_master() -> None:
    result = CommandResult(
        args=("ssh", "-O", "check", "polaris-relay"),
        returncode=255,
        stdout="",
        stderr="Control socket connect(/tmp/cm): No such file or directory",
        duration_seconds=0.07,
    )

    status = classify_bridge_status(alias="polaris-relay", check_result=result, control_path_exists=None)

    assert status.alias == "polaris-relay"
    assert status.state == "DETACHED"
    assert status.explanation == "No active OpenSSH control master is attached."
    assert status.command_result is result
    assert status.control_path_exists is None


def test_classify_bridge_status_reports_stale_for_failed_check_with_socket() -> None:
    result = CommandResult(
        args=("ssh", "-O", "check", "polaris-relay"),
        returncode=255,
        stdout="",
        stderr="mux_client_request_session: read from master failed: Broken pipe",
        duration_seconds=0.09,
    )

    status = classify_bridge_status(alias="polaris-relay", check_result=result, control_path_exists=True)

    assert status.alias == "polaris-relay"
    assert status.state == "STALE"
    assert status.explanation == "Control socket exists but the master check failed."
    assert status.command_result is result
    assert status.control_path_exists is True


def test_classify_bridge_status_reports_stale_for_abnormal_failure_without_socket() -> None:
    result = CommandResult(
        args=("ssh", "-O", "check", "polaris-relay"),
        returncode=255,
        stdout="",
        stderr="mux_client_request_session: unexpected reply from remote peer",
        duration_seconds=0.05,
    )

    status = classify_bridge_status(alias="polaris-relay", check_result=result, control_path_exists=False)

    assert status.alias == "polaris-relay"
    assert status.state == "STALE"
    assert status.explanation == "Bridge state is abnormal and requires operator attention."
    assert status.command_result is result
    assert status.control_path_exists is False
