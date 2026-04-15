from dataclasses import fields

from autoresearch.schemas import (
    PolarisJobRequest,
    QstatParseResult,
    QsubParseResult,
    RenderedPBSScript,
)


def test_polaris_job_request_exposes_expected_fields_and_defaults() -> None:
    request = PolarisJobRequest(
        run_id="run_123",
        job_name="job-123",
        project="proj",
        queue="debug",
        walltime="00:30:00",
        select_expr="select=1:ncpus=1",
        entrypoint_path="/tmp/entrypoint.sh",
    )

    assert [field.name for field in fields(PolarisJobRequest)] == [
        "run_id",
        "job_name",
        "project",
        "queue",
        "walltime",
        "select_expr",
        "entrypoint_path",
        "place_expr",
        "filesystems",
        "stdout_path",
        "stderr_path",
        "submit_script_path",
    ]
    assert request.place_expr == "scatter"
    assert request.filesystems == "eagle"


def test_rendered_pbs_script_wraps_script_text() -> None:
    script = RenderedPBSScript(script_text="#!/bin/bash\n")

    assert script.script_text == "#!/bin/bash\n"


def test_qsub_parse_result_wraps_qsub_output() -> None:
    result = QsubParseResult(raw_output="123456.polaris", pbs_job_id="123456.polaris", is_success=True)

    assert result.raw_output == "123456.polaris"
    assert result.pbs_job_id == "123456.polaris"
    assert result.is_success is True


def test_qstat_parse_result_wraps_status_fields() -> None:
    result = QstatParseResult(
        pbs_job_id="123456.polaris",
        state="R",
        queue="debug",
        comment=None,
        exec_host="node001/0",
        stdout_path="/tmp/stdout",
        stderr_path="/tmp/stderr",
    )

    assert result.pbs_job_id == "123456.polaris"
    assert result.state == "R"
    assert result.queue == "debug"
    assert result.comment is None
    assert result.exec_host == "node001/0"
    assert result.stdout_path == "/tmp/stdout"
    assert result.stderr_path == "/tmp/stderr"


def test_qstat_parse_result_defaults_optional_fields_to_none() -> None:
    result = QstatParseResult(pbs_job_id="123456.polaris", state="Q")

    assert result.pbs_job_id == "123456.polaris"
    assert result.state == "Q"
    assert result.queue is None
    assert result.comment is None
    assert result.exec_host is None
    assert result.stdout_path is None
    assert result.stderr_path is None
