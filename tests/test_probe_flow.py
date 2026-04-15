from autoresearch.executor.pbs import build_qstat_command, build_qsub_command
from autoresearch.executor.polaris import build_probe_job_request
from autoresearch.settings import ProbeSettings


REMOTE_ROOT = "/eagle/demo"


def test_build_probe_job_request_uses_probe_settings_defaults_and_derives_submit_script_path() -> None:
    probe_settings = ProbeSettings(
        project="ALCF_PROJECT",
        queue="debug",
        walltime="00:10:00",
    )

    request = build_probe_job_request(
        run_id="run_probe",
        entrypoint_path="/eagle/demo/jobs/probe/entrypoint.sh",
        remote_root=REMOTE_ROOT,
        probe_settings=probe_settings,
    )

    assert request.project == "ALCF_PROJECT"
    assert request.queue == "debug"
    assert request.walltime == "00:10:00"
    assert request.submit_script_path == f"{REMOTE_ROOT}/jobs/run_probe/submit.pbs"


def test_build_probe_job_request_allows_cli_overrides_for_queue_and_walltime() -> None:
    probe_settings = ProbeSettings(
        project="ALCF_PROJECT",
        queue="debug",
        walltime="00:10:00",
    )

    request = build_probe_job_request(
        run_id="run_probe",
        entrypoint_path="/eagle/demo/jobs/probe/entrypoint.sh",
        remote_root=REMOTE_ROOT,
        probe_settings=probe_settings,
        queue="prod",
        walltime="00:20:00",
    )

    assert request.project == "ALCF_PROJECT"
    assert request.queue == "prod"
    assert request.walltime == "00:20:00"
    assert request.submit_script_path == f"{REMOTE_ROOT}/jobs/run_probe/submit.pbs"


def test_build_qsub_command_returns_submit_script_path() -> None:
    assert build_qsub_command("/eagle/demo/jobs/run_probe/submit.pbs") == (
        "qsub",
        "/eagle/demo/jobs/run_probe/submit.pbs",
    )


def test_build_qstat_command_returns_json_query_for_job_id() -> None:
    assert build_qstat_command("123.polaris") == (
        "qstat",
        "-fF",
        "JSON",
        "123.polaris",
    )
