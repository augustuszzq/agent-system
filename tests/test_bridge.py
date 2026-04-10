from autoresearch.bridge.health import classify_bridge_status
from autoresearch.schemas import CommandResult


def test_classify_bridge_status_reports_attached() -> None:
    result = CommandResult(
        args=("ssh", "-O", "check", "polaris-relay"),
        returncode=0,
        stdout="Master running",
        stderr="",
        duration_seconds=0.12,
    )

    status = classify_bridge_status(alias="polaris-relay", check_result=result, control_path_exists=None)

    assert status.state == "ATTACHED"


def test_classify_bridge_status_reports_detached_for_no_master() -> None:
    result = CommandResult(
        args=("ssh", "-O", "check", "polaris-relay"),
        returncode=255,
        stdout="",
        stderr="Control socket connect(/tmp/cm): No such file or directory",
        duration_seconds=0.07,
    )

    status = classify_bridge_status(alias="polaris-relay", check_result=result, control_path_exists=None)

    assert status.state == "DETACHED"


def test_classify_bridge_status_reports_stale_for_failed_check_with_socket() -> None:
    result = CommandResult(
        args=("ssh", "-O", "check", "polaris-relay"),
        returncode=255,
        stdout="",
        stderr="mux_client_request_session: read from master failed: Broken pipe",
        duration_seconds=0.09,
    )

    status = classify_bridge_status(alias="polaris-relay", check_result=result, control_path_exists=True)

    assert status.state == "STALE"
