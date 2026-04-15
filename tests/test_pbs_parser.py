import json
from pathlib import Path

import pytest

from autoresearch.executor.pbs import (
    _strip_host_prefix,
    parse_qstat_json,
    parse_qstat_output,
    parse_qsub_output,
    render_pbs_script,
)
from autoresearch.schemas import PolarisJobRequest


FIXTURE_DIR = Path(__file__).parent / "fixtures"


def test_parse_qsub_output_extracts_job_id() -> None:
    text = (FIXTURE_DIR / "qsub_success.txt").read_text(encoding="utf-8")

    result = parse_qsub_output(text)

    assert result.pbs_job_id == "123456.polaris-pbs-01.hsn.cm.polaris.alcf.anl.gov"
    assert result.raw_output == text.strip()
    assert result.is_success is True


def test_parse_qsub_output_rejects_empty_text() -> None:
    with pytest.raises(ValueError, match="empty qsub output"):
        parse_qsub_output("")


def test_parse_qsub_output_rejects_malformed_text() -> None:
    with pytest.raises(ValueError, match="malformed qsub output"):
        parse_qsub_output("job submitted")


def test_parse_qstat_output_extracts_key_fields() -> None:
    text = (FIXTURE_DIR / "qstat_full.txt").read_text(encoding="utf-8")

    result = parse_qstat_output(text)

    assert result.pbs_job_id == "123456.polaris-pbs-01.hsn.cm.polaris.alcf.anl.gov"
    assert result.state == "R"
    assert result.queue == "debug"
    assert result.comment == "Job run at Fri Apr 10 at 12:34 on (x1001:ncpus=32)"
    assert result.exec_host == "x1001/0"
    assert result.stdout_path == "/eagle/lc-mpi/Zhiqing/auto-research/runs/run_demo/stdout.log"
    assert result.stderr_path == "/eagle/lc-mpi/Zhiqing/auto-research/runs/run_demo/stderr.log"


def test_parse_qstat_output_rejects_missing_job_state() -> None:
    text = "\n".join(
        [
            "Job Id: 123456.polaris-pbs-01.hsn.cm.polaris.alcf.anl.gov",
            "    queue = debug",
            "    exec_host = x1001/0",
        ]
    )

    with pytest.raises(ValueError, match="missing job_state in qstat output"):
        parse_qstat_output(text)


def test_parse_qstat_output_rejects_blank_job_state() -> None:
    text = "\n".join(
        [
            "Job Id: 123456.polaris-pbs-01.hsn.cm.polaris.alcf.anl.gov",
            "    job_state =   ",
            "    queue = debug",
        ]
    )

    with pytest.raises(ValueError, match="missing job_state in qstat output"):
        parse_qstat_output(text)


def test_parse_qstat_json_extracts_key_fields() -> None:
    text = (FIXTURE_DIR / "qstat_full.json").read_text(encoding="utf-8")

    result = parse_qstat_json(text)

    assert result.pbs_job_id == "123456.polaris-pbs-01.hsn.cm.polaris.alcf.anl.gov"
    assert result.state == "Q"
    assert result.comment == "Not Running: Insufficient amount of resource: vnode"
    assert result.exec_host == "x1001/0"
    assert result.stdout_path == "/eagle/lc-mpi/Zhiqing/auto-research/runs/run_demo/stdout.log"
    assert result.stderr_path == "/eagle/lc-mpi/Zhiqing/auto-research/runs/run_demo/stderr.log"


def test_parse_qstat_json_rejects_empty_jobs() -> None:
    with pytest.raises(ValueError, match="no jobs in qstat json"):
        parse_qstat_json(json.dumps({"Jobs": {}}))


def test_parse_qstat_json_rejects_multiple_jobs() -> None:
    payload = {
        "Jobs": {
            "123456.polaris-pbs-01.hsn.cm.polaris.alcf.anl.gov": {"job_state": "Q"},
            "123457.polaris-pbs-01.hsn.cm.polaris.alcf.anl.gov": {"job_state": "R"},
        }
    }

    with pytest.raises(ValueError, match="expected exactly one job in qstat json"):
        parse_qstat_json(json.dumps(payload))


@pytest.mark.parametrize(
    "payload",
    [
        {"Jobs": []},
        {"Jobs": {"123456.polaris-pbs-01.hsn.cm.polaris.alcf.anl.gov": "not-a-mapping"}},
    ],
)
def test_parse_qstat_json_rejects_malformed_jobs_shape(payload: object) -> None:
    with pytest.raises(ValueError, match="malformed qstat json"):
        parse_qstat_json(json.dumps(payload))


def test_parse_qstat_json_rejects_missing_job_state() -> None:
    payload = {
        "Jobs": {
            "123456.polaris-pbs-01.hsn.cm.polaris.alcf.anl.gov": {
                "queue": "debug",
                "comment": "Not Running",
                "exec_host": "x1001/0",
            }
        }
    }

    with pytest.raises(ValueError, match="missing job_state in qstat json"):
        parse_qstat_json(json.dumps(payload))


def test_parse_qstat_json_rejects_blank_job_state() -> None:
    payload = {
        "Jobs": {
            "123456.polaris-pbs-01.hsn.cm.polaris.alcf.anl.gov": {
                "job_state": "   ",
                "queue": "debug",
                "comment": "Not Running",
                "exec_host": "x1001/0",
            }
        }
    }

    with pytest.raises(ValueError, match="malformed qstat json"):
        parse_qstat_json(json.dumps(payload))


@pytest.mark.parametrize(
    ("path_value", "expected"),
    [
        (None, None),
        ("", None),
        ("/eagle/lc-mpi/Zhiqing/auto-research/runs/run_demo/stdout.log", "/eagle/lc-mpi/Zhiqing/auto-research/runs/run_demo/stdout.log"),
        ("polaris-login-04:/eagle/lc-mpi/Zhiqing/auto-research/runs/run_demo/stdout.log", "/eagle/lc-mpi/Zhiqing/auto-research/runs/run_demo/stdout.log"),
        ("s3://bucket/path", "s3://bucket/path"),
        ("foo:bar/baz", "foo:bar/baz"),
    ],
)
def test_strip_host_prefix_handles_common_inputs(path_value: str | None, expected: str | None) -> None:
    assert _strip_host_prefix(path_value) == expected


def test_render_pbs_script_rejects_missing_output_paths() -> None:
    request = PolarisJobRequest(
        run_id="run_demo",
        job_name="demo-job",
        project="demo",
        queue="debug",
        walltime="00:10:00",
        select_expr="1:ncpus=1",
        entrypoint_path="/tmp/entrypoint.sh",
        remote_root="/eagle/lc-mpi/Zhiqing/auto-research",
    )

    with pytest.raises(ValueError, match="stdout_path and stderr_path must be set"):
        render_pbs_script(request)


@pytest.mark.parametrize("path_value", ["", "   "])
def test_render_pbs_script_rejects_blank_output_paths(path_value: str) -> None:
    request = PolarisJobRequest(
        run_id="run_demo",
        job_name="demo-job",
        project="demo",
        queue="debug",
        walltime="00:10:00",
        select_expr="1:ncpus=1",
        entrypoint_path="/tmp/entrypoint.sh",
        remote_root="/eagle/lc-mpi/Zhiqing/auto-research",
        stdout_path=path_value,
        stderr_path="/tmp/stderr.log",
    )

    with pytest.raises(ValueError, match="stdout_path and stderr_path must be set"):
        render_pbs_script(request)


def test_render_pbs_script_shell_quotes_entrypoint_path() -> None:
    request = PolarisJobRequest(
        run_id="run_demo",
        job_name="demo-job",
        project="demo",
        queue="debug",
        walltime="00:10:00",
        select_expr="1:ncpus=1",
        entrypoint_path="/tmp/entrypoint with spaces.sh",
        remote_root="/remote/root",
        stdout_path="/tmp/stdout.log",
        stderr_path="/tmp/stderr.log",
    )

    rendered = render_pbs_script(request)

    assert "bash '/tmp/entrypoint with spaces.sh'" in rendered.script_text
