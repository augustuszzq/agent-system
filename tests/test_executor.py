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
    assert request.remote_root == REMOTE_ROOT
    assert request.filesystems == "eagle"
    assert request.place_expr == "scatter"
    assert request.select_expr == "1:system=polaris"
    assert request.stdout_path == f"{REMOTE_ROOT}/runs/run_demo/stdout.log"
    assert request.stderr_path == f"{REMOTE_ROOT}/runs/run_demo/stderr.log"
    assert request.submit_script_path == f"{REMOTE_ROOT}/jobs/run_demo/submit.pbs"


def test_build_polaris_job_request_normalizes_whitespace_in_required_fields() -> None:
    request = build_polaris_job_request(
        run_id="  run_demo  ",
        project="  demo  ",
        queue="  debug  ",
        walltime="  00:10:00  ",
        entrypoint_path="  /tmp/entrypoint.sh  ",
    )

    assert request.run_id == "run_demo"
    assert request.project == "demo"
    assert request.queue == "debug"
    assert request.walltime == "00:10:00"
    assert request.entrypoint_path == "/tmp/entrypoint.sh"
    assert request.remote_root == REMOTE_ROOT
    assert request.job_name == "run_demo"
    assert request.stdout_path == f"{REMOTE_ROOT}/runs/run_demo/stdout.log"
    assert request.stderr_path == f"{REMOTE_ROOT}/runs/run_demo/stderr.log"
    assert request.submit_script_path == f"{REMOTE_ROOT}/jobs/run_demo/submit.pbs"


def test_build_polaris_job_request_falls_back_to_run_id_for_blank_job_name() -> None:
    request = build_polaris_job_request(
        run_id="  run_demo  ",
        project="demo",
        queue="debug",
        walltime="00:10:00",
        entrypoint_path="/tmp/entrypoint.sh",
        job_name="   ",
    )

    assert request.run_id == "run_demo"
    assert request.job_name == "run_demo"


def test_build_polaris_job_request_uses_configured_remote_root_for_derived_paths() -> None:
    request = build_polaris_job_request(
        run_id="run_demo",
        project="demo",
        queue="debug",
        walltime="00:10:00",
        entrypoint_path="/tmp/entrypoint.sh",
        remote_root="/custom/remote/root",
    )

    assert request.remote_root == "/custom/remote/root"
    assert request.stdout_path == "/custom/remote/root/runs/run_demo/stdout.log"
    assert request.stderr_path == "/custom/remote/root/runs/run_demo/stderr.log"
    assert request.submit_script_path == "/custom/remote/root/jobs/run_demo/submit.pbs"


def test_build_polaris_job_request_rejects_remote_root_with_internal_whitespace() -> None:
    with pytest.raises(ValueError, match="remote_root must not contain whitespace"):
        build_polaris_job_request(
            run_id="run_demo",
            project="demo",
            queue="debug",
            walltime="00:10:00",
            entrypoint_path="/tmp/entrypoint.sh",
            remote_root="/custom/remote root",
        )


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


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("queue", "", "queue must be non-empty"),
        ("queue", "   ", "queue must be non-empty"),
        ("entrypoint_path", "", "entrypoint_path must be non-empty"),
        ("entrypoint_path", "   ", "entrypoint_path must be non-empty"),
    ],
)
def test_build_polaris_job_request_rejects_blank_queue_and_entrypoint_path(
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


def test_build_polaris_job_request_rejects_run_id_with_internal_whitespace() -> None:
    with pytest.raises(ValueError, match="run_id must not contain whitespace"):
        build_polaris_job_request(
            run_id="run demo",
            project="demo",
            queue="debug",
            walltime="00:10:00",
            entrypoint_path="/tmp/entrypoint.sh",
        )


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
    assert "export AUTORESEARCH_REMOTE_ROOT=/eagle/lc-mpi/Zhiqing/auto-research" in rendered.script_text


def test_render_pbs_script_rejects_remote_root_with_internal_whitespace() -> None:
    request = build_polaris_job_request(
        run_id="run_demo",
        project="demo",
        queue="debug",
        walltime="00:10:00",
        entrypoint_path="/tmp/entrypoint.sh",
    )
    request = request.__class__(
        **{
            **request.__dict__,
            "remote_root": "/custom/remote root",
            "stdout_path": "/tmp/stdout.log",
            "stderr_path": "/tmp/stderr.log",
        }
    )

    with pytest.raises(ValueError, match="remote_root must not contain whitespace"):
        render_pbs_script(request)
