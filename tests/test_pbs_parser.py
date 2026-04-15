import json
from pathlib import Path

import pytest

from autoresearch.executor.pbs import (
    parse_qstat_json,
    parse_qstat_output,
    parse_qsub_output,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures"


def test_parse_qsub_output_extracts_job_id() -> None:
    text = (FIXTURE_DIR / "qsub_success.txt").read_text(encoding="utf-8")

    result = parse_qsub_output(text)

    assert result.pbs_job_id == "123456.polaris-pbs-01.hsn.cm.polaris.alcf.anl.gov"
    assert result.raw_output.strip() == text.strip()


def test_parse_qsub_output_rejects_empty_text() -> None:
    with pytest.raises(ValueError, match="empty qsub output"):
        parse_qsub_output("")


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


def test_parse_qstat_json_extracts_key_fields() -> None:
    text = (FIXTURE_DIR / "qstat_full.json").read_text(encoding="utf-8")

    result = parse_qstat_json(text)

    assert result.pbs_job_id == "123456.polaris-pbs-01.hsn.cm.polaris.alcf.anl.gov"
    assert result.state == "Q"
    assert result.comment == "Not Running: Insufficient amount of resource: vnode"
    assert result.stdout_path == "/eagle/lc-mpi/Zhiqing/auto-research/runs/run_demo/stdout.log"


def test_parse_qstat_json_rejects_empty_jobs() -> None:
    with pytest.raises(ValueError, match="no jobs in qstat json"):
        parse_qstat_json(json.dumps({"Jobs": {}}))
