# Phase 3A PBS Executor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the local, testable half of the Polaris PBS executor: schema types, fixture-driven parsers, script rendering, job registry support, and a minimal `job` CLI without real Polaris submission.

**Architecture:** Keep the executor split by responsibility. `executor/polaris.py` owns Polaris defaults and path derivation, `executor/pbs.py` owns script rendering and scheduler output parsing, `runs/registry.py` owns SQLite persistence for `jobs`, and `cli.py` stays a thin shell over those services. Phase 3A must remain local-only so Phase 3B can later add bridge-mediated remote submission without rewriting parser or registry logic.

**Tech Stack:** Python 3.11+, Typer, SQLite, pytest, fixture-based parser tests

---

## File Map

- Create: `src/autoresearch/executor/__init__.py`
- Create: `src/autoresearch/executor/polaris.py`
- Create: `src/autoresearch/executor/pbs.py`
- Modify: `src/autoresearch/schemas.py`
- Modify: `src/autoresearch/runs/registry.py`
- Modify: `src/autoresearch/cli.py`
- Modify: `docs/architecture.md`
- Modify: `docs/runbook.md`
- Create: `tests/fixtures/qsub_success.txt`
- Create: `tests/fixtures/qstat_full.txt`
- Create: `tests/fixtures/qstat_full.json`
- Create: `tests/test_pbs_parser.py`
- Create: `tests/test_executor.py`
- Create: `tests/test_registry.py`
- Modify: `tests/test_cli.py`

## Task 1: Define PBS And Job Schemas

**Files:**
- Modify: `src/autoresearch/schemas.py`
- Test: `tests/test_pbs_parser.py`

- [ ] **Step 1: Write the failing schema-oriented tests**

```python
from autoresearch.schemas import (
    PolarisJobRequest,
    QsubParseResult,
    QstatParseResult,
    RenderedPBSScript,
)


def test_polaris_job_request_keeps_required_fields() -> None:
    request = PolarisJobRequest(
        run_id="run_demo",
        job_name="demo-job",
        project="demo",
        queue="debug",
        walltime="01:00:00",
        select_expr="1:system=polaris",
        entrypoint_path="/eagle/lc-mpi/Zhiqing/auto-research/jobs/run_demo/entrypoint.sh",
    )

    assert request.run_id == "run_demo"
    assert request.job_name == "demo-job"
    assert request.project == "demo"


def test_qsub_parse_result_exposes_job_identifier() -> None:
    result = QsubParseResult(raw_output="123456.polaris", pbs_job_id="123456.polaris")

    assert result.raw_output == "123456.polaris"
    assert result.pbs_job_id == "123456.polaris"


def test_qstat_parse_result_exposes_scheduler_fields() -> None:
    result = QstatParseResult(
        pbs_job_id="123456.polaris",
        state="R",
        queue="debug",
        comment="Job run at Fri Apr 10",
        exec_host="x1001/0",
        stdout_path="/eagle/.../stdout.log",
        stderr_path="/eagle/.../stderr.log",
    )

    assert result.state == "R"
    assert result.comment.startswith("Job run")


def test_rendered_pbs_script_wraps_text_payload() -> None:
    script = RenderedPBSScript(script_text="#!/bin/bash\n")

    assert script.script_text.startswith("#!/bin/bash")
```

- [ ] **Step 2: Run the new schema tests to confirm failure**

Run:

```bash
.venv/bin/pytest tests/test_pbs_parser.py -v
```

Expected: import or attribute errors because the new schema types do not exist yet.

- [ ] **Step 3: Add the schema dataclasses**

Add explicit dataclasses to `src/autoresearch/schemas.py` for:

```python
from dataclasses import dataclass
from typing import Literal


BridgeState = Literal["DETACHED", "ATTACHED", "STALE"]


@dataclass(frozen=True)
class RunCreateRequest:
    run_kind: str
    project: str
    notes: str | None = None


@dataclass(frozen=True)
class CommandResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float


@dataclass(frozen=True)
class BridgeStatusResult:
    alias: str
    state: BridgeState
    explanation: str
    command_result: CommandResult | None = None
    control_path_exists: bool | None = None


@dataclass(frozen=True)
class PolarisJobRequest:
    run_id: str
    job_name: str
    project: str
    queue: str
    walltime: str
    select_expr: str
    entrypoint_path: str
    place_expr: str = "scatter"
    filesystems: str = "eagle"
    stdout_path: str | None = None
    stderr_path: str | None = None
    submit_script_path: str | None = None


@dataclass(frozen=True)
class RenderedPBSScript:
    script_text: str


@dataclass(frozen=True)
class QsubParseResult:
    raw_output: str
    pbs_job_id: str


@dataclass(frozen=True)
class QstatParseResult:
    pbs_job_id: str
    state: str
    queue: str | None
    comment: str | None
    exec_host: str | None
    stdout_path: str | None
    stderr_path: str | None
```

- [ ] **Step 4: Run the schema tests again**

Run:

```bash
.venv/bin/pytest tests/test_pbs_parser.py -v
```

Expected: the schema-oriented tests pass, even though parser implementation tests still fail or remain incomplete.

- [ ] **Step 5: Commit the schema changes**

```bash
git add src/autoresearch/schemas.py tests/test_pbs_parser.py
git commit -m "feat: add pbs executor schemas"
```

## Task 2: Add PBS Fixtures And Parser Tests

**Files:**
- Create: `tests/fixtures/qsub_success.txt`
- Create: `tests/fixtures/qstat_full.txt`
- Create: `tests/fixtures/qstat_full.json`
- Create: `tests/test_pbs_parser.py`

- [ ] **Step 1: Create fixture files with realistic scheduler samples**

Create `tests/fixtures/qsub_success.txt`:

```text
123456.polaris-pbs-01.hsn.cm.polaris.alcf.anl.gov
```

Create `tests/fixtures/qstat_full.txt`:

```text
Job Id: 123456.polaris-pbs-01.hsn.cm.polaris.alcf.anl.gov
    Job_Name = demo-job
    job_state = R
    queue = debug
    exec_host = x1001/0
    comment = Job run at Fri Apr 10 at 12:34 on (x1001:ncpus=32)
    Output_Path = polaris-login-04:/eagle/lc-mpi/Zhiqing/auto-research/runs/run_demo/stdout.log
    Error_Path = polaris-login-04:/eagle/lc-mpi/Zhiqing/auto-research/runs/run_demo/stderr.log
```

Create `tests/fixtures/qstat_full.json`:

```json
{
  "Jobs": {
    "123456.polaris-pbs-01.hsn.cm.polaris.alcf.anl.gov": {
      "job_state": "Q",
      "queue": "debug",
      "comment": "Not Running: Insufficient amount of resource: vnode",
      "exec_host": "x1001/0",
      "Output_Path": "polaris-login-04:/eagle/lc-mpi/Zhiqing/auto-research/runs/run_demo/stdout.log",
      "Error_Path": "polaris-login-04:/eagle/lc-mpi/Zhiqing/auto-research/runs/run_demo/stderr.log"
    }
  }
}
```

- [ ] **Step 2: Write parser tests against the fixtures**

Create `tests/test_pbs_parser.py` with:

```python
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
```

- [ ] **Step 3: Run parser tests to confirm they fail before implementation**

Run:

```bash
.venv/bin/pytest tests/test_pbs_parser.py -v
```

Expected: import errors because `executor/pbs.py` does not exist yet, or failures because parser functions are not implemented.

- [ ] **Step 4: Commit the fixtures and failing tests**

```bash
git add tests/fixtures/qsub_success.txt tests/fixtures/qstat_full.txt tests/fixtures/qstat_full.json tests/test_pbs_parser.py
git commit -m "test: add pbs parser fixtures"
```

## Task 3: Implement PBS Parsers And Script Renderer

**Files:**
- Create: `src/autoresearch/executor/__init__.py`
- Create: `src/autoresearch/executor/pbs.py`
- Test: `tests/test_pbs_parser.py`

- [ ] **Step 1: Create the executor package export**

Create `src/autoresearch/executor/__init__.py`:

```python
"""Executor utilities for PBS and Polaris."""
```

- [ ] **Step 2: Implement parser helpers and PBS renderer**

Create `src/autoresearch/executor/pbs.py`:

```python
import json

from autoresearch.schemas import PolarisJobRequest, QstatParseResult, QsubParseResult, RenderedPBSScript


def _strip_host_prefix(path_value: str | None) -> str | None:
    if not path_value:
        return path_value
    if ":" not in path_value:
        return path_value
    return path_value.split(":", 1)[1]


def parse_qsub_output(text: str) -> QsubParseResult:
    raw_output = text.strip()
    if not raw_output:
        raise ValueError("empty qsub output")
    return QsubParseResult(raw_output=raw_output, pbs_job_id=raw_output)


def parse_qstat_output(text: str) -> QstatParseResult:
    values: dict[str, str] = {}
    job_id: str | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("Job Id:"):
            job_id = line.split(":", 1)[1].strip()
            continue
        if " = " in line:
            key, value = line.split(" = ", 1)
            values[key.strip()] = value.strip()

    if not job_id:
        raise ValueError("missing job id in qstat output")

    return QstatParseResult(
        pbs_job_id=job_id,
        state=values["job_state"],
        queue=values.get("queue"),
        comment=values.get("comment"),
        exec_host=values.get("exec_host"),
        stdout_path=_strip_host_prefix(values.get("Output_Path")),
        stderr_path=_strip_host_prefix(values.get("Error_Path")),
    )


def parse_qstat_json(text: str) -> QstatParseResult:
    payload = json.loads(text)
    jobs = payload.get("Jobs", {})
    if not jobs:
        raise ValueError("no jobs in qstat json")

    job_id, job_data = next(iter(jobs.items()))
    return QstatParseResult(
        pbs_job_id=job_id,
        state=job_data["job_state"],
        queue=job_data.get("queue"),
        comment=job_data.get("comment"),
        exec_host=job_data.get("exec_host"),
        stdout_path=_strip_host_prefix(job_data.get("Output_Path")),
        stderr_path=_strip_host_prefix(job_data.get("Error_Path")),
    )


def render_pbs_script(request: PolarisJobRequest) -> RenderedPBSScript:
    script_text = f"""#!/bin/bash
#PBS -A {request.project}
#PBS -q {request.queue}
#PBS -l select={request.select_expr}
#PBS -l place={request.place_expr}
#PBS -l walltime={request.walltime}
#PBS -l filesystems={request.filesystems}
#PBS -N {request.job_name}
#PBS -k doe
#PBS -o {request.stdout_path}
#PBS -e {request.stderr_path}

set -euo pipefail

cd /eagle/lc-mpi/Zhiqing/auto-research/repo

export RUN_ID={request.run_id}
export AUTORESEARCH_REMOTE_ROOT=/eagle/lc-mpi/Zhiqing/auto-research
export RUN_DIR=/eagle/lc-mpi/Zhiqing/auto-research/runs/{request.run_id}
mkdir -p "$RUN_DIR"

bash {request.entrypoint_path}
"""
    return RenderedPBSScript(script_text=script_text)
```

- [ ] **Step 3: Run parser tests**

Run:

```bash
.venv/bin/pytest tests/test_pbs_parser.py -v
```

Expected: PASS for parser tests that do not yet depend on Polaris normalization.

- [ ] **Step 4: Commit the parser and renderer implementation**

```bash
git add src/autoresearch/executor/__init__.py src/autoresearch/executor/pbs.py tests/test_pbs_parser.py
git commit -m "feat: add pbs parser and renderer"
```

## Task 4: Implement Polaris Defaults And Rendering Tests

**Files:**
- Create: `src/autoresearch/executor/polaris.py`
- Create: `tests/test_executor.py`
- Test: `tests/test_pbs_parser.py`

- [ ] **Step 1: Write failing tests for Polaris normalization**

Create `tests/test_executor.py`:

```python
from autoresearch.executor.polaris import build_polaris_job_request


def test_build_polaris_job_request_applies_default_paths() -> None:
    request = build_polaris_job_request(
        run_id="run_demo",
        project="demo",
        queue="debug",
        walltime="01:00:00",
        entrypoint_path="/eagle/lc-mpi/Zhiqing/auto-research/jobs/run_demo/entrypoint.sh",
    )

    assert request.filesystems == "eagle"
    assert request.place_expr == "scatter"
    assert request.stdout_path == "/eagle/lc-mpi/Zhiqing/auto-research/runs/run_demo/stdout.log"
    assert request.stderr_path == "/eagle/lc-mpi/Zhiqing/auto-research/runs/run_demo/stderr.log"
    assert request.submit_script_path == "/eagle/lc-mpi/Zhiqing/auto-research/jobs/run_demo/submit.pbs"


def test_build_polaris_job_request_requires_project_and_walltime() -> None:
    try:
        build_polaris_job_request(
            run_id="run_demo",
            project="",
            queue="debug",
            walltime="",
            entrypoint_path="/eagle/lc-mpi/Zhiqing/auto-research/jobs/run_demo/entrypoint.sh",
        )
    except ValueError as exc:
        assert "project" in str(exc) or "walltime" in str(exc)
    else:
        raise AssertionError("expected missing required values to fail")
```

- [ ] **Step 2: Run the executor tests to verify failure**

Run:

```bash
.venv/bin/pytest tests/test_executor.py -v
```

Expected: import failure because `build_polaris_job_request` does not exist yet.

- [ ] **Step 3: Implement Polaris normalization**

Create `src/autoresearch/executor/polaris.py`:

```python
from autoresearch.schemas import PolarisJobRequest


REMOTE_ROOT = "/eagle/lc-mpi/Zhiqing/auto-research"


def build_polaris_job_request(
    *,
    run_id: str,
    project: str,
    queue: str,
    walltime: str,
    entrypoint_path: str,
    job_name: str | None = None,
    select_expr: str = "1:system=polaris",
) -> PolarisJobRequest:
    if not project:
        raise ValueError("project is required")
    if not walltime:
        raise ValueError("walltime is required")

    return PolarisJobRequest(
        run_id=run_id,
        job_name=job_name or run_id,
        project=project,
        queue=queue,
        walltime=walltime,
        select_expr=select_expr,
        place_expr="scatter",
        filesystems="eagle",
        stdout_path=f"{REMOTE_ROOT}/runs/{run_id}/stdout.log",
        stderr_path=f"{REMOTE_ROOT}/runs/{run_id}/stderr.log",
        submit_script_path=f"{REMOTE_ROOT}/jobs/{run_id}/submit.pbs",
        entrypoint_path=entrypoint_path,
    )
```

- [ ] **Step 4: Run the executor and parser tests**

Run:

```bash
.venv/bin/pytest tests/test_executor.py tests/test_pbs_parser.py -v
```

Expected: PASS for normalization and rendering behavior.

- [ ] **Step 5: Commit the Polaris defaults**

```bash
git add src/autoresearch/executor/polaris.py tests/test_executor.py
git commit -m "feat: add polaris job normalization"
```

## Task 5: Extend Registry With Job Persistence

**Files:**
- Modify: `src/autoresearch/runs/registry.py`
- Create: `tests/test_registry.py`

- [ ] **Step 1: Write failing registry tests for jobs**

Create `tests/test_registry.py`:

```python
from pathlib import Path

from autoresearch.db import init_db
from autoresearch.runs.registry import RunRegistry
from autoresearch.schemas import RunCreateRequest


def test_create_job_persists_draft_record(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"
    init_db(db_path)
    registry = RunRegistry(db_path)
    run = registry.create_run(RunCreateRequest(run_kind="local-debug", project="demo"))

    job = registry.create_job(
        run_id=run.run_id,
        backend="polaris-pbs",
        queue="debug",
        walltime="01:00:00",
        filesystems="eagle",
        select_expr="1:system=polaris",
        place_expr="scatter",
        submit_script_path="/eagle/lc-mpi/Zhiqing/auto-research/jobs/run_demo/submit.pbs",
        stdout_path="/eagle/lc-mpi/Zhiqing/auto-research/runs/run_demo/stdout.log",
        stderr_path="/eagle/lc-mpi/Zhiqing/auto-research/runs/run_demo/stderr.log",
    )

    assert job.run_id == run.run_id
    assert job.state == "DRAFT"
    assert job.backend == "polaris-pbs"


def test_update_job_state_persists_scheduler_metadata(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"
    init_db(db_path)
    registry = RunRegistry(db_path)
    run = registry.create_run(RunCreateRequest(run_kind="local-debug", project="demo"))
    job = registry.create_job(
        run_id=run.run_id,
        backend="polaris-pbs",
        queue="debug",
        walltime="01:00:00",
        filesystems="eagle",
        select_expr="1:system=polaris",
        place_expr="scatter",
        submit_script_path="/eagle/lc-mpi/Zhiqing/auto-research/jobs/run_demo/submit.pbs",
        stdout_path="/eagle/lc-mpi/Zhiqing/auto-research/runs/run_demo/stdout.log",
        stderr_path="/eagle/lc-mpi/Zhiqing/auto-research/runs/run_demo/stderr.log",
    )

    updated = registry.update_job_state(
        job_id=job.job_id,
        state="QUEUED",
        pbs_job_id="123456.polaris-pbs-01.hsn.cm.polaris.alcf.anl.gov",
        exec_host="x1001/0",
    )

    assert updated.state == "QUEUED"
    assert updated.pbs_job_id == "123456.polaris-pbs-01.hsn.cm.polaris.alcf.anl.gov"
    assert updated.exec_host == "x1001/0"


def test_list_jobs_returns_newest_first(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"
    init_db(db_path)
    registry = RunRegistry(db_path)
    run = registry.create_run(RunCreateRequest(run_kind="local-debug", project="demo"))
    first = registry.create_job(
        run_id=run.run_id,
        backend="polaris-pbs",
        queue="debug",
        walltime="01:00:00",
        filesystems="eagle",
        select_expr="1:system=polaris",
        place_expr="scatter",
        submit_script_path="/eagle/lc-mpi/Zhiqing/auto-research/jobs/run_demo/submit-first.pbs",
        stdout_path="/eagle/lc-mpi/Zhiqing/auto-research/runs/run_demo/first.log",
        stderr_path="/eagle/lc-mpi/Zhiqing/auto-research/runs/run_demo/first.err",
    )
    second = registry.create_job(
        run_id=run.run_id,
        backend="polaris-pbs",
        queue="debug",
        walltime="01:00:00",
        filesystems="eagle",
        select_expr="1:system=polaris",
        place_expr="scatter",
        submit_script_path="/eagle/lc-mpi/Zhiqing/auto-research/jobs/run_demo/submit-second.pbs",
        stdout_path="/eagle/lc-mpi/Zhiqing/auto-research/runs/run_demo/second.log",
        stderr_path="/eagle/lc-mpi/Zhiqing/auto-research/runs/run_demo/second.err",
    )

    records = registry.list_jobs()

    assert [record.job_id for record in records] == [second.job_id, first.job_id]
```

- [ ] **Step 2: Run the new registry tests to confirm failure**

Run:

```bash
.venv/bin/pytest tests/test_registry.py -v
```

Expected: failures because `create_job`, `update_job_state`, and `list_jobs` do not exist yet.

- [ ] **Step 3: Extend `src/autoresearch/runs/registry.py` with job persistence**

Add a `JobRecord` dataclass and the following methods:

```python
@dataclass(frozen=True)
class JobRecord:
    job_id: str
    run_id: str
    backend: str
    pbs_job_id: str | None
    queue: str | None
    walltime: str | None
    filesystems: str | None
    select_expr: str | None
    place_expr: str | None
    exec_host: str | None
    state: str
    submit_script_path: str | None
    stdout_path: str | None
    stderr_path: str | None
    created_at: str
    updated_at: str
```

Implement:

```python
def create_job(
    self,
    *,
    run_id: str,
    backend: str,
    queue: str,
    walltime: str,
    filesystems: str,
    select_expr: str,
    place_expr: str,
    submit_script_path: str,
    stdout_path: str,
    stderr_path: str,
) -> JobRecord:
    ...


def update_job_state(
    self,
    *,
    job_id: str,
    state: str,
    pbs_job_id: str | None = None,
    exec_host: str | None = None,
) -> JobRecord:
    ...


def list_jobs(self) -> list[JobRecord]:
    ...
```

Use UTC ISO timestamps and `uuid.uuid4().hex[:12]` in the same style as run ids, for example `job_<12hex>`.

- [ ] **Step 4: Run registry tests**

Run:

```bash
.venv/bin/pytest tests/test_registry.py tests/test_run_registry.py -v
```

Expected: PASS for both job and run registry behavior.

- [ ] **Step 5: Commit the registry changes**

```bash
git add src/autoresearch/runs/registry.py tests/test_registry.py
git commit -m "feat: add job registry support"
```

## Task 6: Add Minimal Job CLI

**Files:**
- Modify: `src/autoresearch/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Add failing CLI tests for `job` commands**

Extend `tests/test_cli.py` with:

```python
def test_cli_help_shows_job_command_group() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "job" in result.stdout


def test_job_render_pbs_prints_script(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AUTORESEARCH_DB", str(tmp_path / "state" / "autoresearch.db"))
    monkeypatch.setenv("AUTORESEARCH_REPO_ROOT", str(tmp_path))
    _write_repo_config(tmp_path)

    result = runner.invoke(
        app,
        [
            "job",
            "render-pbs",
            "--run-id",
            "run_demo",
            "--project",
            "demo",
            "--queue",
            "debug",
            "--walltime",
            "01:00:00",
            "--entrypoint-path",
            "/eagle/lc-mpi/Zhiqing/auto-research/jobs/run_demo/entrypoint.sh",
        ],
    )

    assert result.exit_code == 0
    assert "#PBS -A demo" in result.stdout
    assert "#PBS -l filesystems=eagle" in result.stdout
    assert "bash /eagle/lc-mpi/Zhiqing/auto-research/jobs/run_demo/entrypoint.sh" in result.stdout


def test_job_list_prints_persisted_jobs(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AUTORESEARCH_DB", str(tmp_path / "state" / "autoresearch.db"))
    monkeypatch.setenv("AUTORESEARCH_REPO_ROOT", str(tmp_path))
    _write_repo_config(tmp_path)

    init_result = runner.invoke(app, ["db", "init"])
    registry = cli_module.RunRegistry(tmp_path / "state" / "autoresearch.db")
    run = registry.create_run(cli_module.RunCreateRequest(run_kind="local-debug", project="demo"))
    registry.create_job(
        run_id=run.run_id,
        backend="polaris-pbs",
        queue="debug",
        walltime="01:00:00",
        filesystems="eagle",
        select_expr="1:system=polaris",
        place_expr="scatter",
        submit_script_path="/eagle/lc-mpi/Zhiqing/auto-research/jobs/run_demo/submit.pbs",
        stdout_path="/eagle/lc-mpi/Zhiqing/auto-research/runs/run_demo/stdout.log",
        stderr_path="/eagle/lc-mpi/Zhiqing/auto-research/runs/run_demo/stderr.log",
    )

    result = runner.invoke(app, ["job", "list"])

    assert init_result.exit_code == 0
    assert result.exit_code == 0
    assert "polaris-pbs" in result.stdout
    assert "DRAFT" in result.stdout
```

- [ ] **Step 2: Run the CLI tests to confirm failure**

Run:

```bash
.venv/bin/pytest tests/test_cli.py -v
```

Expected: failures because the `job` command group is not defined yet.

- [ ] **Step 3: Implement the `job` command group in `src/autoresearch/cli.py`**

Add imports for:

```python
from autoresearch.executor.pbs import render_pbs_script
from autoresearch.executor.polaris import build_polaris_job_request
```

Add the Typer group:

```python
job_app = typer.Typer(help="PBS job commands.")
app.add_typer(job_app, name="job")
```

Add `job list`:

```python
@job_app.command("list")
def list_jobs() -> None:
    settings = load_settings()
    registry = RunRegistry(settings.paths.db_path)
    for record in registry.list_jobs():
        typer.echo(
            f"{record.job_id}\t{record.run_id}\t{record.backend}\t"
            f"{record.state}\t{record.pbs_job_id or '-'}\t{record.updated_at}"
        )
```

Add `job render-pbs`:

```python
@job_app.command("render-pbs")
def render_job_pbs(
    run_id: str = typer.Option(..., "--run-id"),
    project: str = typer.Option(..., "--project"),
    queue: str = typer.Option(..., "--queue"),
    walltime: str = typer.Option(..., "--walltime"),
    entrypoint_path: str = typer.Option(..., "--entrypoint-path"),
) -> None:
    request = build_polaris_job_request(
        run_id=run_id,
        project=project,
        queue=queue,
        walltime=walltime,
        entrypoint_path=entrypoint_path,
    )
    rendered = render_pbs_script(request)
    typer.echo(rendered.script_text.rstrip())
```

- [ ] **Step 4: Run the CLI and focused suite**

Run:

```bash
.venv/bin/pytest tests/test_cli.py tests/test_registry.py tests/test_executor.py tests/test_pbs_parser.py -v
```

Expected: PASS for CLI plus executor/registry integration.

- [ ] **Step 5: Commit the CLI changes**

```bash
git add src/autoresearch/cli.py tests/test_cli.py
git commit -m "feat: add pbs job cli"
```

## Task 7: Update Docs And Run Full Verification

**Files:**
- Modify: `docs/architecture.md`
- Modify: `docs/runbook.md`

- [ ] **Step 1: Document the new executor layer in `docs/architecture.md`**

Add a new section describing:

```markdown
## Local PBS executor

Phase 3A adds a local executor layer for Polaris PBS behavior without live submission.

Current executor modules:

- `src/autoresearch/executor/polaris.py`
  - normalizes Polaris defaults such as `filesystems=eagle` and `place=scatter`
  - derives stdout, stderr, and submit-script paths under `/eagle/lc-mpi/Zhiqing/auto-research/`
- `src/autoresearch/executor/pbs.py`
  - renders PBS scripts
  - parses `qsub`, `qstat -f`, and `qstat -fF JSON`
- `src/autoresearch/runs/registry.py`
  - persists draft and scheduler-derived job records

Phase 3A is local-only. It does not yet call the ALCF bridge for real submission.
```

- [ ] **Step 2: Document operator usage in `docs/runbook.md`**

Add a new section:

```markdown
## Local PBS executor commands

Phase 3A adds local-only PBS tooling:

```bash
python -m autoresearch.cli job list
python -m autoresearch.cli job render-pbs --run-id run_demo --project demo --queue debug --walltime 01:00:00 --entrypoint-path /eagle/lc-mpi/Zhiqing/auto-research/jobs/run_demo/entrypoint.sh
```

`job render-pbs` prints a rendered script but does not submit anything.
Real submission remains a Phase 3B task.
```

- [ ] **Step 3: Run the full test suite**

Run:

```bash
.venv/bin/pytest -q
```

Expected: all tests pass.

- [ ] **Step 4: Run CLI help verification**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m autoresearch.cli --help
PYTHONPATH=src .venv/bin/python -m autoresearch.cli job --help
```

Expected: both commands succeed, and `job` plus `render-pbs` appear in help output.

- [ ] **Step 5: Commit docs and verification-backed finish**

```bash
git add docs/architecture.md docs/runbook.md
git commit -m "docs: add phase3a executor docs"
```

## Self-Review

- Spec coverage check:
  - schema types: Task 1
  - parser fixtures and parser behavior: Tasks 2 and 3
  - Polaris defaults and path derivation: Task 4
  - jobs registry support: Task 5
  - minimal `job` CLI: Task 6
  - architecture/runbook updates and final verification: Task 7
- Placeholder scan:
  - no `TODO`, `TBD`, or “implement later” placeholders remain in the task steps
- Type consistency:
  - `PolarisJobRequest`, `RenderedPBSScript`, `QsubParseResult`, and `QstatParseResult` are used consistently across parser, executor, and CLI tasks
  - `RunRegistry` remains the persistence entrypoint for both runs and jobs
