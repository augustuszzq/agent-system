from pathlib import Path

from autoresearch.incidents.classifier import classify_incident
from autoresearch.schemas import NormalizedIncidentInput


FIXTURES = Path(__file__).parent / "fixtures" / "incidents"


def _fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _normalized(
    *,
    job_state: str = "F",
    comment: str | None = None,
    stdout_tail: str = "",
    stderr_tail: str = "",
    current_log_tail_hash: str = "hash-a",
    previous_log_tail_hash: str | None = None,
) -> NormalizedIncidentInput:
    return NormalizedIncidentInput(
        job_id="job_demo",
        run_id="run_demo",
        pbs_job_id="12345.polaris",
        job_state=job_state,
        comment=comment,
        exec_host="x1001c1s2b0",
        stdout_tail=stdout_tail,
        stderr_tail=stderr_tail,
        snapshot_dir=Path("/tmp/snapshot"),
        scan_time="2026-04-16T01:02:03+00:00",
        current_log_tail_hash=current_log_tail_hash,
        previous_log_tail_hash=previous_log_tail_hash,
    )


def test_classify_filesystem_unavailable_from_qstat_comment() -> None:
    result = classify_incident(
        _normalized(comment="filesystem unavailable: eagle")
    )

    assert result is not None
    assert result.category == "FILESYSTEM_UNAVAILABLE"
    assert result.severity == "CRITICAL"


def test_classify_resource_oom_from_stdout_tail() -> None:
    result = classify_incident(
        _normalized(stdout_tail=_fixture_text("stdout_oom.log"))
    )

    assert result is not None
    assert result.category == "RESOURCE_OOM"
    assert result.severity == "CRITICAL"


def test_classify_import_error_from_stderr_tail() -> None:
    result = classify_incident(
        _normalized(stderr_tail=_fixture_text("stderr_import_error.log"))
    )

    assert result is not None
    assert result.category == "ENV_IMPORT_ERROR"
    assert result.fingerprint == "no module named nonexistent_package"


def test_classify_no_heartbeat_only_when_running_and_hashes_repeat() -> None:
    result = classify_incident(
        _normalized(
            job_state="R",
            stdout_tail="steady output",
            current_log_tail_hash="same",
            previous_log_tail_hash="same",
        )
    )

    assert result is not None
    assert result.category == "NO_HEARTBEAT"
    assert result.severity == "HIGH"


def test_classify_returns_none_for_empty_evidence() -> None:
    result = classify_incident(_normalized())

    assert result is None


def test_classify_unknown_when_nonempty_evidence_has_no_specific_match() -> None:
    result = classify_incident(
        _normalized(stderr_tail=_fixture_text("stderr_unknown.log"))
    )

    assert result is not None
    assert result.category == "UNKNOWN"
    assert result.severity == "MEDIUM"
