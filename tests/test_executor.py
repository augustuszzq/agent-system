import pytest

from autoresearch.executor.pbs import render_pbs_script
from autoresearch.executor.polaris import build_polaris_job_request


REMOTE_ROOT = "/eagle/lc-mpi/Zhiqing/auto-research"


def test_build_polaris_job_request_derives_paths_and_defaults() -> None:
    request = build_polaris_job_request(
        run_id="run_demo",
        project="demo",
        queue="debug",
        walltime="00:10:00",
        entrypoint_path="/tmp/entrypoint.sh",
    )

    assert request.run_id == "run_demo"
    assert request.job_name == "run_demo"
    assert request.project == "demo"
    assert request.queue == "debug"
    assert request.walltime == "00:10:00"
    assert request.entrypoint_path == "/tmp/entrypoint.sh"
    assert request.filesystems == "eagle"
    assert request.place_expr == "scatter"
    assert request.select_expr == "1:system=polaris"
    assert request.stdout_path == f"{REMOTE_ROOT}/runs/run_demo/stdout.log"
    assert request.stderr_path == f"{REMOTE_ROOT}/runs/run_demo/stderr.log"
    assert request.submit_script_path == f"{REMOTE_ROOT}/jobs/run_demo/submit.pbs"


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("project", "", "project must be non-empty"),
        ("project", "   ", "project must be non-empty"),
        ("walltime", "", "walltime must be non-empty"),
        ("walltime", "   ", "walltime must be non-empty"),
    ],
)
def test_build_polaris_job_request_rejects_blank_required_fields(
    field: str,
    value: str,
    expected: str,
) -> None:
    kwargs = {
        "run_id": "run_demo",
        "project": "demo",
        "queue": "debug",
        "walltime": "00:10:00",
        "entrypoint_path": "/tmp/entrypoint.sh",
    }
    kwargs[field] = value

    with pytest.raises(ValueError, match=expected):
        build_polaris_job_request(**kwargs)


def test_render_pbs_script_uses_derived_output_paths() -> None:
    request = build_polaris_job_request(
        run_id="run_demo",
        project="demo",
        queue="debug",
        walltime="00:10:00",
        entrypoint_path="/tmp/entrypoint.sh",
    )

    rendered = render_pbs_script(request)

    assert f"#PBS -o {REMOTE_ROOT}/runs/run_demo/stdout.log" in rendered.script_text
    assert f"#PBS -e {REMOTE_ROOT}/runs/run_demo/stderr.log" in rendered.script_text
    assert "#PBS -l filesystems=eagle" in rendered.script_text
