# Phase 4B Safe Retry And Approval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a narrow, operator-approved safe-retry flow that can create retry requests from whitelisted incidents, record approval and rejection decisions, and execute an approved same-config probe retry as a brand-new run and job.

**Architecture:** Keep Phase 4B split across explicit boundaries. `settings.py` loads the retry whitelist, `retries/policy.py` evaluates eligibility, `retries/registry.py` owns retry-request persistence and state transitions, `decisions.py` owns audit rows, `executor/probe_submit.py` centralizes the live probe submission path, and `retries/executor.py` orchestrates approved retry execution against that shared submit helper. `cli.py` stays thin and only wires request/list/approve/reject/execute commands together.

**Tech Stack:** Python 3.11+, Typer, SQLite, pytest, existing OpenSSH bridge helpers, existing Polaris probe submission flow

---

## File Map

- Modify: `conf/retry_policy.yaml`
- Modify: `src/autoresearch/models.py`
- Modify: `src/autoresearch/db.py`
- Modify: `src/autoresearch/settings.py`
- Modify: `src/autoresearch/schemas.py`
- Modify: `src/autoresearch/runs/registry.py`
- Modify: `src/autoresearch/cli.py`
- Create: `src/autoresearch/decisions.py`
- Create: `src/autoresearch/retries/__init__.py`
- Create: `src/autoresearch/retries/policy.py`
- Create: `src/autoresearch/retries/registry.py`
- Create: `src/autoresearch/retries/executor.py`
- Create: `src/autoresearch/executor/probe_submit.py`
- Modify: `docs/architecture.md`
- Modify: `docs/runbook.md`
- Modify: `tests/test_db.py`
- Modify: `tests/test_settings.py`
- Modify: `tests/test_run_registry.py`
- Modify: `tests/test_probe_flow.py`
- Modify: `tests/test_cli.py`
- Create: `tests/test_decisions.py`
- Create: `tests/test_retry_policy.py`
- Create: `tests/test_retry_registry.py`
- Create: `tests/test_retry_executor.py`

## Task 1: Add Retry Policy Config And Retry Request Persistence

**Files:**
- Modify: `conf/retry_policy.yaml`
- Modify: `src/autoresearch/models.py`
- Modify: `src/autoresearch/db.py`
- Modify: `src/autoresearch/settings.py`
- Modify: `src/autoresearch/schemas.py`
- Modify: `tests/test_db.py`
- Modify: `tests/test_settings.py`

- [ ] **Step 1: Write the failing settings and database tests**

Add these tests to `tests/test_settings.py`:

```python
from pathlib import Path

from autoresearch.settings import load_settings


def _write_retry_policy(conf_dir: Path) -> None:
    (conf_dir / "retry_policy.yaml").write_text(
        "safe_retry_categories:\n"
        "  - FILESYSTEM_UNAVAILABLE\n"
        "allowed_actions:\n"
        "  - RETRY_SAME_CONFIG\n",
        encoding="utf-8",
    )


def test_load_settings_reads_retry_policy(tmp_path: Path) -> None:
    repo_root = tmp_path
    conf_dir = repo_root / "conf"
    conf_dir.mkdir()
    (conf_dir / "app.yaml").write_text(
        "app_name: auto-research\n"
        "paths:\n"
        "  state_dir: state\n"
        "  cache_dir: cache\n"
        "  logs_dir: logs\n"
        "  db_path: state/autoresearch.db\n"
        "remote:\n"
        "  root: /eagle/lc-mpi/Zhiqing/auto-research\n",
        encoding="utf-8",
    )
    _write_bridge_config(conf_dir)
    _write_retry_policy(conf_dir)

    settings = load_settings(repo_root=repo_root)

    assert settings.retry_policy.safe_retry_categories == ("FILESYSTEM_UNAVAILABLE",)
    assert settings.retry_policy.allowed_actions == ("RETRY_SAME_CONFIG",)
```

Add these tests to `tests/test_db.py`:

```python
import sqlite3
from pathlib import Path

from autoresearch.db import init_db


def test_init_db_creates_retry_requests_table(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"

    init_db(db_path)

    conn = sqlite3.connect(db_path)
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    finally:
        conn.close()

    assert "retry_requests" in tables


def test_retry_requests_table_has_expected_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"

    init_db(db_path)

    conn = sqlite3.connect(db_path)
    try:
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(retry_requests)").fetchall()
        }
    finally:
        conn.close()

    assert columns == {
        "retry_request_id",
        "incident_id",
        "source_run_id",
        "source_job_id",
        "source_pbs_job_id",
        "requested_action",
        "approval_status",
        "execution_status",
        "attempt_count",
        "approved_by",
        "approval_reason",
        "last_error",
        "result_run_id",
        "result_job_id",
        "result_pbs_job_id",
        "created_at",
        "updated_at",
        "executed_at",
    }
```

- [ ] **Step 2: Run the tests to verify failure**

Run:

```bash
pytest tests/test_settings.py tests/test_db.py -q
```

Expected: failure because `Settings` has no `retry_policy` attribute and `retry_requests` does not exist.

- [ ] **Step 3: Add retry policy settings and the new SQLite table**

Update `conf/retry_policy.yaml` to the explicit Phase 4B shape:

```yaml
safe_retry_categories:
  - FILESYSTEM_UNAVAILABLE
allowed_actions:
  - RETRY_SAME_CONFIG
```

Extend `src/autoresearch/schemas.py` with retry literals that later tasks will reuse:

```python
RetryAction = Literal["RETRY_SAME_CONFIG"]
RetryApprovalStatus = Literal["PENDING", "APPROVED", "REJECTED"]
RetryExecutionStatus = Literal["NOT_STARTED", "SUBMITTED", "FAILED"]
```

Extend `src/autoresearch/settings.py` with a retry policy dataclass and load logic:

```python
@dataclass(frozen=True)
class RetryPolicySettings:
    safe_retry_categories: tuple[IncidentCategory, ...]
    allowed_actions: tuple[RetryAction, ...]


@dataclass(frozen=True)
class Settings:
    app_name: str
    paths: AppPaths
    remote_root: str
    bridge: BridgeSettings
    probe: ProbeSettings
    retry_policy: RetryPolicySettings
```

Inside `load_settings()` load `conf/retry_policy.yaml` and attach it to `Settings`:

```python
retry_policy_config = yaml.safe_load(
    (resolved_root / "conf" / "retry_policy.yaml").read_text(encoding="utf-8")
)

...
retry_policy=RetryPolicySettings(
    safe_retry_categories=tuple(retry_policy_config["safe_retry_categories"]),
    allowed_actions=tuple(retry_policy_config["allowed_actions"]),
),
```

Add the new table SQL to `src/autoresearch/models.py`:

```python
RETRY_REQUESTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS retry_requests(
  retry_request_id TEXT PRIMARY KEY,
  incident_id TEXT NOT NULL,
  source_run_id TEXT,
  source_job_id TEXT,
  source_pbs_job_id TEXT,
  requested_action TEXT NOT NULL,
  approval_status TEXT NOT NULL,
  execution_status TEXT NOT NULL,
  attempt_count INTEGER NOT NULL,
  approved_by TEXT,
  approval_reason TEXT,
  last_error TEXT,
  result_run_id TEXT,
  result_job_id TEXT,
  result_pbs_job_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  executed_at TEXT
)
"""
```

Wire that SQL into `src/autoresearch/db.py`:

```python
from autoresearch.models import (
    DECISIONS_TABLE_SQL,
    INCIDENTS_TABLE_SQL,
    JOBS_TABLE_SQL,
    RETRY_REQUESTS_TABLE_SQL,
    RUNS_TABLE_SQL,
)

...
conn.execute(RETRY_REQUESTS_TABLE_SQL)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```bash
pytest tests/test_settings.py tests/test_db.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add conf/retry_policy.yaml src/autoresearch/models.py src/autoresearch/db.py src/autoresearch/settings.py src/autoresearch/schemas.py tests/test_db.py tests/test_settings.py
git commit -m "feat: add retry policy settings and persistence"
```

## Task 2: Add Decision Logging, Run Lookup, Retry Policy, And Retry Registry

**Files:**
- Modify: `src/autoresearch/runs/registry.py`
- Create: `src/autoresearch/decisions.py`
- Create: `src/autoresearch/retries/__init__.py`
- Create: `src/autoresearch/retries/policy.py`
- Create: `src/autoresearch/retries/registry.py`
- Modify: `tests/test_run_registry.py`
- Create: `tests/test_decisions.py`
- Create: `tests/test_retry_policy.py`
- Create: `tests/test_retry_registry.py`

- [ ] **Step 1: Write the failing registry and policy tests**

Add this test to `tests/test_run_registry.py`:

```python
from pathlib import Path

from autoresearch.db import init_db
from autoresearch.runs.registry import RunRegistry
from autoresearch.schemas import RunCreateRequest


def test_get_run_returns_existing_run(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"
    init_db(db_path)
    registry = RunRegistry(db_path)

    created = registry.create_run(RunCreateRequest(run_kind="probe", project="demo", notes="hello"))

    record = registry.get_run(created.run_id)

    assert record == created
```

Create `tests/test_decisions.py`:

```python
from pathlib import Path

from autoresearch.db import init_db
from autoresearch.decisions import DecisionLog


def test_append_decision_persists_row(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"
    init_db(db_path)
    log = DecisionLog(db_path)

    record = log.append(
        target_type="retry_request",
        target_id="retry_123",
        decision="approve-retry",
        rationale="filesystem incident cleared",
        actor="operator",
    )

    rows = log.list_for_target("retry_request", "retry_123")

    assert rows == [record]
    assert record.actor == "operator"
```

Create `tests/test_retry_policy.py`:

```python
from autoresearch.retries.policy import RetryPolicy
from autoresearch.settings import RetryPolicySettings


def test_retry_policy_accepts_whitelisted_category_and_action() -> None:
    policy = RetryPolicy(
        RetryPolicySettings(
            safe_retry_categories=("FILESYSTEM_UNAVAILABLE",),
            allowed_actions=("RETRY_SAME_CONFIG",),
        )
    )

    assert policy.allows(category="FILESYSTEM_UNAVAILABLE", action="RETRY_SAME_CONFIG") is True


def test_retry_policy_rejects_non_whitelisted_category() -> None:
    policy = RetryPolicy(
        RetryPolicySettings(
            safe_retry_categories=("FILESYSTEM_UNAVAILABLE",),
            allowed_actions=("RETRY_SAME_CONFIG",),
        )
    )

    assert policy.allows(category="RESOURCE_OOM", action="RETRY_SAME_CONFIG") is False
```

Create `tests/test_retry_registry.py`:

```python
from pathlib import Path

import pytest

from autoresearch.db import init_db
from autoresearch.retries.registry import RetryRequestRegistry


def test_create_retry_request_persists_pending_row(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"
    init_db(db_path)
    registry = RetryRequestRegistry(db_path)

    record = registry.create_request(
        incident_id="incident_demo",
        source_run_id="run_demo",
        source_job_id="job_demo",
        source_pbs_job_id="123.polaris",
        requested_action="RETRY_SAME_CONFIG",
    )

    assert record.approval_status == "PENDING"
    assert record.execution_status == "NOT_STARTED"
    assert record.attempt_count == 0


def test_reject_retry_request_requires_pending_and_stores_reason(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"
    init_db(db_path)
    registry = RetryRequestRegistry(db_path)

    record = registry.create_request(
        incident_id="incident_demo",
        source_run_id="run_demo",
        source_job_id="job_demo",
        source_pbs_job_id="123.polaris",
        requested_action="RETRY_SAME_CONFIG",
    )

    rejected = registry.reject(
        record.retry_request_id,
        actor="operator",
        reason="not convinced",
    )

    assert rejected.approval_status == "REJECTED"
    assert rejected.approval_reason == "not convinced"


def test_approve_retry_request_requires_pending(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"
    init_db(db_path)
    registry = RetryRequestRegistry(db_path)
    record = registry.create_request(
        incident_id="incident_demo",
        source_run_id="run_demo",
        source_job_id="job_demo",
        source_pbs_job_id="123.polaris",
        requested_action="RETRY_SAME_CONFIG",
    )
    registry.approve(record.retry_request_id, actor="operator", reason="ok")

    with pytest.raises(ValueError, match="pending"):
        registry.approve(record.retry_request_id, actor="operator", reason="again")


def test_find_active_request_by_incident_and_action_ignores_failed_and_submitted(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"
    init_db(db_path)
    registry = RetryRequestRegistry(db_path)
    first = registry.create_request(
        incident_id="incident_demo",
        source_run_id="run_demo",
        source_job_id="job_demo",
        source_pbs_job_id="123.polaris",
        requested_action="RETRY_SAME_CONFIG",
    )
    registry.approve(first.retry_request_id, actor="operator", reason="ok")
    registry.mark_submitted(
        first.retry_request_id,
        result_run_id="run_retry",
        result_job_id="job_retry",
        result_pbs_job_id="456.polaris",
        executed_at="2026-04-16T00:00:00+00:00",
    )

    assert registry.find_active_request("incident_demo", "RETRY_SAME_CONFIG") is None
```

- [ ] **Step 2: Run the tests to verify failure**

Run:

```bash
pytest tests/test_run_registry.py tests/test_decisions.py tests/test_retry_policy.py tests/test_retry_registry.py -q
```

Expected: failure because `get_run`, `DecisionLog`, `RetryPolicy`, and `RetryRequestRegistry` do not exist yet.

- [ ] **Step 3: Implement run lookup, decision logging, retry policy, and retry registry**

Add `get_run()` to `src/autoresearch/runs/registry.py` next to `get_job()`:

```python
def get_run(self, run_id: str) -> RunRecord:
    with connect_db(self._db_path) as conn:
        row = conn.execute(
            """
            SELECT run_id, run_kind, project, created_at, status, notes
            FROM runs
            WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()
    if row is None:
        raise KeyError(f"run not found: {run_id}")
    return self._row_to_record(row)
```

Create `src/autoresearch/decisions.py`:

```python
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import uuid

from autoresearch.db import connect_db


@dataclass(frozen=True)
class DecisionRecord:
    decision_id: str
    created_at: str
    target_type: str
    target_id: str
    decision: str
    rationale: str | None
    actor: str


class DecisionLog:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    def append(self, *, target_type: str, target_id: str, decision: str, rationale: str | None, actor: str) -> DecisionRecord:
        record = DecisionRecord(
            decision_id=f"decision_{uuid.uuid4().hex[:12]}",
            created_at=datetime.now(UTC).isoformat(),
            target_type=target_type,
            target_id=target_id,
            decision=decision,
            rationale=rationale,
            actor=actor,
        )
        with connect_db(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO decisions (
                    decision_id, created_at, target_type, target_id,
                    decision, rationale, actor
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.decision_id,
                    record.created_at,
                    record.target_type,
                    record.target_id,
                    record.decision,
                    record.rationale,
                    record.actor,
                ),
            )
        return record

    def list_for_target(self, target_type: str, target_id: str) -> list[DecisionRecord]:
        with connect_db(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT decision_id, created_at, target_type, target_id,
                       decision, rationale, actor
                FROM decisions
                WHERE target_type = ? AND target_id = ?
                ORDER BY created_at ASC, decision_id ASC
                """,
                (target_type, target_id),
            ).fetchall()
        return [DecisionRecord(**dict(row)) for row in rows]
```

Create `src/autoresearch/retries/policy.py`:

```python
from autoresearch.settings import RetryPolicySettings
from autoresearch.schemas import IncidentCategory, RetryAction


class RetryPolicy:
    def __init__(self, settings: RetryPolicySettings) -> None:
        self._settings = settings

    def allows(self, *, category: IncidentCategory, action: RetryAction) -> bool:
        return (
            category in self._settings.safe_retry_categories
            and action in self._settings.allowed_actions
        )
```

Create `src/autoresearch/retries/registry.py` with a dataclass and guarded state transitions:

```python
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import uuid

from autoresearch.db import connect_db
from autoresearch.schemas import RetryAction


@dataclass(frozen=True)
class RetryRequestRecord:
    retry_request_id: str
    incident_id: str
    source_run_id: str | None
    source_job_id: str | None
    source_pbs_job_id: str | None
    requested_action: str
    approval_status: str
    execution_status: str
    attempt_count: int
    approved_by: str | None
    approval_reason: str | None
    last_error: str | None
    result_run_id: str | None
    result_job_id: str | None
    result_pbs_job_id: str | None
    created_at: str
    updated_at: str
    executed_at: str | None
```

Implement methods:

```python
class RetryRequestRegistry:
    def create_request(...): ...
    def get(self, retry_request_id: str) -> RetryRequestRecord: ...
    def list_requests(self) -> list[RetryRequestRecord]: ...
    def find_active_request(self, incident_id: str, requested_action: RetryAction) -> RetryRequestRecord | None: ...
    def approve(self, retry_request_id: str, *, actor: str, reason: str) -> RetryRequestRecord: ...
    def reject(self, retry_request_id: str, *, actor: str, reason: str) -> RetryRequestRecord: ...
    def mark_failed(self, retry_request_id: str, *, error_text: str) -> RetryRequestRecord: ...
    def mark_submitted(self, retry_request_id: str, *, result_run_id: str, result_job_id: str, result_pbs_job_id: str, executed_at: str) -> RetryRequestRecord: ...
```

State rules to enforce in code:

```python
if record.approval_status != "PENDING":
    raise ValueError("retry request must be pending")

if record.approval_status != "APPROVED" or record.execution_status != "NOT_STARTED":
    raise ValueError("retry request must be approved and not started")
```

Treat only `PENDING` and `APPROVED + NOT_STARTED` as active in `find_active_request()`.

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```bash
pytest tests/test_run_registry.py tests/test_decisions.py tests/test_retry_policy.py tests/test_retry_registry.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/autoresearch/runs/registry.py src/autoresearch/decisions.py src/autoresearch/retries/__init__.py src/autoresearch/retries/policy.py src/autoresearch/retries/registry.py tests/test_run_registry.py tests/test_decisions.py tests/test_retry_policy.py tests/test_retry_registry.py
git commit -m "feat: add retry registry and policy"
```

## Task 3: Extract A Shared Live Probe Submission Helper

**Files:**
- Create: `src/autoresearch/executor/probe_submit.py`
- Modify: `src/autoresearch/cli.py`
- Modify: `tests/test_probe_flow.py`

- [ ] **Step 1: Write the failing probe-submission tests**

Add these tests to `tests/test_probe_flow.py`:

```python
from pathlib import Path

import pytest

from autoresearch import cli as cli_module
from autoresearch.executor.probe_submit import submit_live_probe_run
from autoresearch.settings import ProbeSettings


def test_submit_live_probe_run_supports_custom_run_kind_and_notes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    init_db(settings.paths.db_path)
    service = ProbeBridgeService(
        qsub_output="123456.polaris-pbs-01.hsn.cm.polaris.alcf.anl.gov",
    )

    result = submit_live_probe_run(
        settings=settings,
        service=service,
        run_kind="probe-retry",
        notes="retry_request=retry_123",
        project="ALCF_PROJECT",
        queue="debug",
        walltime="00:10:00",
    )

    registry = RunRegistry(settings.paths.db_path)
    run_record = registry.get_run(result.run_id)
    job_record = registry.get_job(result.job_id)

    assert run_record.run_kind == "probe-retry"
    assert run_record.notes == "retry_request=retry_123"
    assert job_record.state == "SUBMITTED"


def test_job_submit_probe_reuses_shared_submit_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str | None]] = []

    def fake_submit(**kwargs):
        calls.append((kwargs["run_kind"], kwargs["notes"]))
        return type("Result", (), {"run_id": "run_demo", "job_id": "job_demo", "pbs_job_id": "123.polaris"})()

    monkeypatch.setattr(cli_module, "submit_live_probe_run", fake_submit)

    run_id, job_id, pbs_job_id = cli_module.submit_probe_job(
        project="ALCF_PROJECT",
        queue="debug",
        walltime="00:10:00",
    )

    assert (run_id, job_id, pbs_job_id) == ("run_demo", "job_demo", "123.polaris")
    assert calls == [("probe", None)]
```

- [ ] **Step 2: Run the tests to verify failure**

Run:

```bash
pytest tests/test_probe_flow.py -q
```

Expected: failure because `submit_live_probe_run` does not exist and `submit_probe_job` still owns submission inline.

- [ ] **Step 3: Extract the reusable helper and rewire `submit_probe_job`**

Create `src/autoresearch/executor/probe_submit.py`:

```python
from dataclasses import dataclass
from pathlib import Path
import shlex
import tempfile

from autoresearch.bridge.remote_exec import RemoteBridgeError, copy_to_remote, execute_remote_command
from autoresearch.executor.pbs import build_qsub_command, parse_qsub_output, render_pbs_script
from autoresearch.executor.polaris import build_probe_job_request
from autoresearch.runs.registry import RunRegistry
from autoresearch.schemas import RunCreateRequest


@dataclass(frozen=True)
class SubmittedProbeRun:
    run_id: str
    job_id: str
    pbs_job_id: str


def submit_live_probe_run(
    *,
    settings,
    service,
    run_kind: str,
    notes: str | None,
    project: str,
    queue: str,
    walltime: str,
) -> SubmittedProbeRun:
    registry = RunRegistry(settings.paths.db_path)
    run_record = registry.create_run(
        RunCreateRequest(run_kind=run_kind, project=project, notes=notes)
    )
    request = build_probe_job_request(
        run_id=run_record.run_id,
        entrypoint_path=f"{settings.remote_root}/jobs/probe/entrypoint.sh",
        remote_root=settings.remote_root,
        probe_settings=settings.probe,
        queue=queue,
        walltime=walltime,
    )
    rendered = render_pbs_script(request)
    job_record = registry.create_job(
        run_id=run_record.run_id,
        backend="pbs",
        queue=request.queue,
        walltime=request.walltime,
        filesystems=request.filesystems,
        select_expr=request.select_expr,
        place_expr=request.place_expr,
        submit_script_path=request.submit_script_path,
        stdout_path=request.stdout_path,
        stderr_path=request.stderr_path,
    )

    temp_file = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=f"-{run_record.run_id}.pbs", delete=False)
    try:
        temp_file.write(rendered.script_text)
        temp_file.flush()
        temp_path = Path(temp_file.name)
    finally:
        temp_file.close()

    try:
        mkdir_submit = execute_remote_command(
            service,
            f"mkdir -p {shlex.quote(str(Path(request.submit_script_path).parent))}",
        )
        if mkdir_submit.returncode != 0:
            raise RemoteBridgeError(
                mkdir_submit.stderr.strip() or "failed to create submit directory"
            )
        copy_result = copy_to_remote(service, temp_path, request.submit_script_path, settings.remote_root)
        if copy_result.returncode != 0:
            raise RemoteBridgeError(copy_result.stderr.strip() or "failed to upload submit script")
        mkdir_logs = execute_remote_command(
            service,
            f"mkdir -p {shlex.quote(str(Path(request.stdout_path).parent))}",
        )
        if mkdir_logs.returncode != 0:
            raise RemoteBridgeError(
                mkdir_logs.stderr.strip() or "failed to create run log directory"
            )
        qsub_result = execute_remote_command(service, shlex.join(build_qsub_command(request.submit_script_path)))
        if qsub_result.returncode != 0:
            raise RemoteBridgeError(qsub_result.stderr.strip() or "qsub failed")
        parsed = parse_qsub_output(qsub_result.stdout)
        registry.mark_job_submitted(job_record.job_id, parsed.pbs_job_id)
        return SubmittedProbeRun(
            run_id=run_record.run_id,
            job_id=job_record.job_id,
            pbs_job_id=parsed.pbs_job_id,
        )
    finally:
        temp_path.unlink(missing_ok=True)
```

Then simplify `src/autoresearch/cli.py`:

```python
from autoresearch.executor.probe_submit import submit_live_probe_run

...

def submit_probe_job(
    project: str | None = None,
    queue: str | None = None,
    walltime: str | None = None,
) -> tuple[str, str, str]:
    settings = load_settings()
    service = build_bridge_service()
    bootstrap_remote_root(service, settings.remote_root, force=False)
    probe_settings = _resolve_probe_settings(
        settings,
        project=project,
        queue=queue,
        walltime=walltime,
    )
    submitted = submit_live_probe_run(
        settings=settings,
        service=service,
        run_kind="probe",
        notes=None,
        project=probe_settings.project,
        queue=probe_settings.queue,
        walltime=probe_settings.walltime,
    )
    return submitted.run_id, submitted.job_id, submitted.pbs_job_id
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```bash
pytest tests/test_probe_flow.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/autoresearch/executor/probe_submit.py src/autoresearch/cli.py tests/test_probe_flow.py
git commit -m "refactor: share live probe submission path"
```

## Task 4: Implement Retry Execution Orchestration

**Files:**
- Create: `src/autoresearch/retries/executor.py`
- Modify: `tests/test_retry_executor.py`

- [ ] **Step 1: Write the failing retry-execution tests**

Create `tests/test_retry_executor.py`:

```python
from pathlib import Path

import pytest

from autoresearch.db import init_db
from autoresearch.decisions import DecisionLog
from autoresearch.incidents.registry import IncidentRegistry
from autoresearch.retries.executor import RetryExecutor
from autoresearch.retries.policy import RetryPolicy
from autoresearch.retries.registry import RetryRequestRegistry
from autoresearch.runs.registry import RunRegistry
from autoresearch.schemas import RunCreateRequest
from autoresearch.settings import RetryPolicySettings


class FakeSubmitted:
    def __init__(self, run_id: str, job_id: str, pbs_job_id: str) -> None:
        self.run_id = run_id
        self.job_id = job_id
        self.pbs_job_id = pbs_job_id


def test_execute_retry_marks_request_submitted_and_logs_decision(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"
    init_db(db_path)
    run_registry = RunRegistry(db_path)
    run = run_registry.create_run(RunCreateRequest(run_kind="probe", project="ALCF_PROJECT"))
    job = run_registry.create_job(
        run_id=run.run_id,
        backend="pbs",
        queue="debug",
        walltime="00:10:00",
        filesystems="eagle",
        select_expr="1:system=polaris",
        place_expr="scatter",
        submit_script_path="/eagle/demo/jobs/source/submit.pbs",
        stdout_path="/eagle/demo/runs/source/stdout.log",
        stderr_path="/eagle/demo/runs/source/stderr.log",
        pbs_job_id="123.polaris",
    )
    incident = IncidentRegistry(db_path).upsert_incident(
        run_id=run.run_id,
        job_id=job.job_id,
        severity="CRITICAL",
        category="FILESYSTEM_UNAVAILABLE",
        fingerprint="fs-down",
        evidence={"matched_lines": ["filesystem unavailable"]},
    )
    retry_registry = RetryRequestRegistry(db_path)
    request = retry_registry.create_request(
        incident_id=incident.incident_id,
        source_run_id=run.run_id,
        source_job_id=job.job_id,
        source_pbs_job_id=job.pbs_job_id,
        requested_action="RETRY_SAME_CONFIG",
    )
    retry_registry.approve(request.retry_request_id, actor="operator", reason="filesystem recovered")

    executor = RetryExecutor(
        db_path=db_path,
        policy=RetryPolicy(
            RetryPolicySettings(
                safe_retry_categories=("FILESYSTEM_UNAVAILABLE",),
                allowed_actions=("RETRY_SAME_CONFIG",),
            )
        ),
        submitter=lambda **kwargs: FakeSubmitted("run_retry", "job_retry", "456.polaris"),
        actor="operator",
    )

    updated = executor.execute(request.retry_request_id)

    assert updated.execution_status == "SUBMITTED"
    assert updated.result_run_id == "run_retry"
    assert updated.result_job_id == "job_retry"
    assert updated.result_pbs_job_id == "456.polaris"
    assert updated.attempt_count == 1
    decisions = DecisionLog(db_path).list_for_target("retry_request", request.retry_request_id)
    assert decisions[-1].decision == "execute-approved-retry"


def test_execute_retry_marks_request_failed_when_submitter_raises(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"
    init_db(db_path)
    run_registry = RunRegistry(db_path)
    run = run_registry.create_run(RunCreateRequest(run_kind="probe", project="ALCF_PROJECT"))
    job = run_registry.create_job(
        run_id=run.run_id,
        backend="pbs",
        queue="debug",
        walltime="00:10:00",
        filesystems="eagle",
        select_expr="1:system=polaris",
        place_expr="scatter",
        submit_script_path="/eagle/demo/jobs/source/submit.pbs",
        stdout_path="/eagle/demo/runs/source/stdout.log",
        stderr_path="/eagle/demo/runs/source/stderr.log",
        pbs_job_id="123.polaris",
    )
    incident = IncidentRegistry(db_path).upsert_incident(
        run_id=run.run_id,
        job_id=job.job_id,
        severity="CRITICAL",
        category="FILESYSTEM_UNAVAILABLE",
        fingerprint="fs-down",
        evidence={"matched_lines": ["filesystem unavailable"]},
    )
    retry_registry = RetryRequestRegistry(db_path)
    request = retry_registry.create_request(
        incident_id=incident.incident_id,
        source_run_id=run.run_id,
        source_job_id=job.job_id,
        source_pbs_job_id=job.pbs_job_id,
        requested_action="RETRY_SAME_CONFIG",
    )
    retry_registry.approve(request.retry_request_id, actor="operator", reason="filesystem recovered")

    executor = RetryExecutor(
        db_path=db_path,
        policy=RetryPolicy(
            RetryPolicySettings(
                safe_retry_categories=("FILESYSTEM_UNAVAILABLE",),
                allowed_actions=("RETRY_SAME_CONFIG",),
            )
        ),
        submitter=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("bridge detached")),
        actor="operator",
    )

    updated = executor.execute(request.retry_request_id)

    assert updated.execution_status == "FAILED"
    assert updated.last_error == "bridge detached"
    assert updated.result_job_id is None
    assert updated.attempt_count == 0
```

- [ ] **Step 2: Run the tests to verify failure**

Run:

```bash
pytest tests/test_retry_executor.py -q
```

Expected: failure because `RetryExecutor` does not exist.

- [ ] **Step 3: Implement the retry executor**

Create `src/autoresearch/retries/executor.py`:

```python
from datetime import UTC, datetime

from autoresearch.decisions import DecisionLog
from autoresearch.incidents.registry import IncidentRegistry
from autoresearch.retries.policy import RetryPolicy
from autoresearch.retries.registry import RetryRequestRegistry
from autoresearch.runs.registry import RunRegistry


class RetryExecutor:
    def __init__(self, *, db_path, policy: RetryPolicy, submitter, actor: str = "operator") -> None:
        self._db_path = db_path
        self._policy = policy
        self._submitter = submitter
        self._actor = actor
        self._retry_registry = RetryRequestRegistry(db_path)
        self._incident_registry = IncidentRegistry(db_path)
        self._run_registry = RunRegistry(db_path)
        self._decision_log = DecisionLog(db_path)

    def execute(self, retry_request_id: str):
        request = self._retry_registry.get(retry_request_id)
        if request.approval_status != "APPROVED" or request.execution_status != "NOT_STARTED":
            raise ValueError("retry request must be approved and not started")

        incident = self._incident_registry.get_incident(request.incident_id)
        if incident.status != "OPEN":
            raise ValueError("retry request source incident must be open")
        if not self._policy.allows(category=incident.category, action=request.requested_action):
            raise ValueError("retry request category is not eligible")

        source_job = self._run_registry.get_job(request.source_job_id)
        source_run = self._run_registry.get_run(request.source_run_id)
        if source_run.run_kind != "probe":
            raise ValueError("only probe runs are retryable in phase4b")

        notes = (
            f"source_incident={incident.incident_id}\n"
            f"source_job={source_job.job_id}\n"
            f"retry_request={request.retry_request_id}"
        )
        try:
            submitted = self._submitter(
                run_kind="probe-retry",
                notes=notes,
                project=source_run.project,
                queue=source_job.queue,
                walltime=source_job.walltime,
            )
        except Exception as exc:
            return self._retry_registry.mark_failed(request.retry_request_id, error_text=str(exc))

        updated = self._retry_registry.mark_submitted(
            request.retry_request_id,
            result_run_id=submitted.run_id,
            result_job_id=submitted.job_id,
            result_pbs_job_id=submitted.pbs_job_id,
            executed_at=datetime.now(UTC).isoformat(),
        )
        self._decision_log.append(
            target_type="retry_request",
            target_id=request.retry_request_id,
            decision="execute-approved-retry",
            rationale=f"submitted {submitted.job_id}",
            actor=self._actor,
        )
        return updated
```

Also add `get_incident()` to `src/autoresearch/incidents/registry.py` if it does not already exist, because the executor needs a single-incident lookup:

```python
def get_incident(self, incident_id: str) -> IncidentRecord:
    with connect_db(self._db_path) as conn:
        row = conn.execute(
            """
            SELECT incident_id, run_id, job_id, severity, category,
                   fingerprint, evidence_json, auto_action, status,
                   created_at, updated_at, resolved_at
            FROM incidents
            WHERE incident_id = ?
            """,
            (incident_id,),
        ).fetchone()
    if row is None:
        raise KeyError(f"incident not found: {incident_id}")
    return self._row_to_record(row)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```bash
pytest tests/test_retry_executor.py tests/test_retry_registry.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/autoresearch/retries/executor.py src/autoresearch/incidents/registry.py tests/test_retry_executor.py tests/test_retry_registry.py
git commit -m "feat: add retry execution orchestration"
```

## Task 5: Add Retry CLI Commands And Update Docs

**Files:**
- Modify: `src/autoresearch/cli.py`
- Modify: `tests/test_cli.py`
- Modify: `docs/architecture.md`
- Modify: `docs/runbook.md`

- [ ] **Step 1: Write the failing CLI tests**

Add these tests to `tests/test_cli.py`:

```python
from pathlib import Path

from typer.testing import CliRunner

from autoresearch import cli as cli_module
from autoresearch.db import init_db
from autoresearch.incidents.registry import IncidentRegistry
from autoresearch.runs.registry import RunRegistry
from autoresearch.schemas import RunCreateRequest


runner = CliRunner()


def test_retry_request_command_creates_pending_request(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"
    init_db(db_path)
    registry = RunRegistry(db_path)
    run = registry.create_run(RunCreateRequest(run_kind="probe", project="ALCF_PROJECT"))
    job = registry.create_job(
        run_id=run.run_id,
        backend="pbs",
        queue="debug",
        walltime="00:10:00",
        filesystems="eagle",
        select_expr="1:system=polaris",
        place_expr="scatter",
        submit_script_path="/eagle/demo/jobs/source/submit.pbs",
        stdout_path="/eagle/demo/runs/source/stdout.log",
        stderr_path="/eagle/demo/runs/source/stderr.log",
        pbs_job_id="123.polaris",
    )
    incident = IncidentRegistry(db_path).upsert_incident(
        run_id=run.run_id,
        job_id=job.job_id,
        severity="CRITICAL",
        category="FILESYSTEM_UNAVAILABLE",
        fingerprint="fs-down",
        evidence={"matched_lines": ["filesystem unavailable"]},
    )
    settings = _settings(tmp_path)
    monkeypatch.setattr(cli_module, "load_settings", lambda: settings)

    result = runner.invoke(cli_module.app, ["retry", "request", "--incident-id", incident.incident_id])

    assert result.exit_code == 0
    assert "PENDING" in result.stdout


def test_retry_approve_and_execute_commands_update_request(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"
    init_db(db_path)
    registry = RunRegistry(db_path)
    run = registry.create_run(RunCreateRequest(run_kind="probe", project="ALCF_PROJECT"))
    job = registry.create_job(
        run_id=run.run_id,
        backend="pbs",
        queue="debug",
        walltime="00:10:00",
        filesystems="eagle",
        select_expr="1:system=polaris",
        place_expr="scatter",
        submit_script_path="/eagle/demo/jobs/source/submit.pbs",
        stdout_path="/eagle/demo/runs/source/stdout.log",
        stderr_path="/eagle/demo/runs/source/stderr.log",
        pbs_job_id="123.polaris",
    )
    incident = IncidentRegistry(db_path).upsert_incident(
        run_id=run.run_id,
        job_id=job.job_id,
        severity="CRITICAL",
        category="FILESYSTEM_UNAVAILABLE",
        fingerprint="fs-down",
        evidence={"matched_lines": ["filesystem unavailable"]},
    )
    settings = _settings(tmp_path)
    monkeypatch.setattr(cli_module, "load_settings", lambda: settings)

    request_result = runner.invoke(cli_module.app, ["retry", "request", "--incident-id", incident.incident_id])
    retry_request_id = request_result.stdout.split()[0]
    approve_result = runner.invoke(
        cli_module.app,
        ["retry", "approve", "--retry-request-id", retry_request_id, "--reason", "filesystem recovered"],
    )
    monkeypatch.setattr(
        cli_module,
        "submit_live_probe_run",
        lambda **kwargs: type("Result", (), {"run_id": "run_retry", "job_id": "job_retry", "pbs_job_id": "456.polaris"})(),
    )
    execute_result = runner.invoke(
        cli_module.app,
        ["retry", "execute", "--retry-request-id", retry_request_id],
    )

    assert approve_result.exit_code == 0
    assert "APPROVED" in approve_result.stdout
    assert execute_result.exit_code == 0
    assert "job_retry" in execute_result.stdout


def test_retry_reject_and_list_commands_show_request_state(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"
    init_db(db_path)
    registry = RunRegistry(db_path)
    run = registry.create_run(RunCreateRequest(run_kind="probe", project="ALCF_PROJECT"))
    job = registry.create_job(
        run_id=run.run_id,
        backend="pbs",
        queue="debug",
        walltime="00:10:00",
        filesystems="eagle",
        select_expr="1:system=polaris",
        place_expr="scatter",
        submit_script_path="/eagle/demo/jobs/source/submit.pbs",
        stdout_path="/eagle/demo/runs/source/stdout.log",
        stderr_path="/eagle/demo/runs/source/stderr.log",
        pbs_job_id="123.polaris",
    )
    incident = IncidentRegistry(db_path).upsert_incident(
        run_id=run.run_id,
        job_id=job.job_id,
        severity="CRITICAL",
        category="FILESYSTEM_UNAVAILABLE",
        fingerprint="fs-down",
        evidence={"matched_lines": ["filesystem unavailable"]},
    )
    settings = _settings(tmp_path)
    monkeypatch.setattr(cli_module, "load_settings", lambda: settings)

    request_result = runner.invoke(cli_module.app, ["retry", "request", "--incident-id", incident.incident_id])
    retry_request_id = request_result.stdout.split()[0]
    reject_result = runner.invoke(
        cli_module.app,
        ["retry", "reject", "--retry-request-id", retry_request_id, "--reason", "not needed"],
    )
    list_result = runner.invoke(cli_module.app, ["retry", "list"])

    assert reject_result.exit_code == 0
    assert "REJECTED" in reject_result.stdout
    assert list_result.exit_code == 0
    assert retry_request_id in list_result.stdout
    assert "REJECTED" in list_result.stdout
```

- [ ] **Step 2: Run the tests to verify failure**

Run:

```bash
pytest tests/test_cli.py -q
```

Expected: failure because the `retry` Typer group and commands do not exist.

- [ ] **Step 3: Add the retry CLI and wire it to the registry, policy, decision log, and executor**

In `src/autoresearch/cli.py` add a new command group:

```python
from autoresearch.decisions import DecisionLog
from autoresearch.executor.probe_submit import submit_live_probe_run
from autoresearch.retries.executor import RetryExecutor
from autoresearch.retries.policy import RetryPolicy
from autoresearch.retries.registry import RetryRequestRegistry

...
retry_app = typer.Typer(help="Safe retry commands.")
app.add_typer(retry_app, name="retry")
```

Add `retry request`:

```python
@retry_app.command("request")
def retry_request(
    incident_id: str = typer.Option(..., "--incident-id"),
) -> None:
    settings = load_settings()
    incident_registry = IncidentRegistry(settings.paths.db_path)
    retry_registry = RetryRequestRegistry(settings.paths.db_path)
    policy = RetryPolicy(settings.retry_policy)
    incident = incident_registry.get_incident(incident_id)
    if incident.status != "OPEN":
        typer.echo(f"incident {incident_id} is not open", err=True)
        raise typer.Exit(code=1)
    if not policy.allows(category=incident.category, action="RETRY_SAME_CONFIG"):
        typer.echo(f"incident {incident_id} category {incident.category} is not retry-eligible", err=True)
        raise typer.Exit(code=1)
    if retry_registry.find_active_request(incident_id, "RETRY_SAME_CONFIG") is not None:
        typer.echo(f"active retry request already exists for incident {incident_id}", err=True)
        raise typer.Exit(code=1)
    record = retry_registry.create_request(
        incident_id=incident.incident_id,
        source_run_id=incident.run_id,
        source_job_id=incident.job_id,
        source_pbs_job_id=RunRegistry(settings.paths.db_path).get_job(incident.job_id).pbs_job_id,
        requested_action="RETRY_SAME_CONFIG",
    )
    typer.echo(f"{record.retry_request_id}\t{record.approval_status}\t{incident.category}")
```

Add `retry list`, `retry approve`, `retry reject`, and `retry execute` with the same thin-orchestrator pattern. For execution, use:

```python
executor = RetryExecutor(
    db_path=settings.paths.db_path,
    policy=RetryPolicy(settings.retry_policy),
    actor="operator",
    submitter=lambda **kwargs: submit_live_probe_run(
        settings=settings,
        service=build_bridge_service(),
        **kwargs,
    ),
)
record = executor.execute(retry_request_id)
```

Approval and rejection should also append decisions immediately:

```python
DecisionLog(settings.paths.db_path).append(
    target_type="retry_request",
    target_id=record.retry_request_id,
    decision="approve-retry",
    rationale=reason,
    actor="operator",
)
```

and

```python
DecisionLog(settings.paths.db_path).append(
    target_type="retry_request",
    target_id=record.retry_request_id,
    decision="reject-retry",
    rationale=reason,
    actor="operator",
)
```

Update the docs with the new Phase 4B operator path.

For `docs/architecture.md`, add a short section like:

```markdown
## Phase 4B safe retry

Phase 4B adds an operator-approved retry path:

1. `autoresearch retry request --incident-id <incident_id>`
2. `autoresearch retry approve --retry-request-id <id> --reason "..."`
3. `autoresearch retry execute --retry-request-id <id>`

The first implementation only allows `RETRY_SAME_CONFIG` for `FILESYSTEM_UNAVAILABLE`, and execution creates a brand-new retry run and job.
```

For `docs/runbook.md`, add an operator walkthrough like:

```markdown
## Phase 4B safe retry workflow

Create a retry request for an eligible incident:

```bash
python -m autoresearch.cli retry request --incident-id <incident_id>
```

Approve it:

```bash
python -m autoresearch.cli retry approve --retry-request-id <retry_request_id> --reason "filesystem recovered"
```

Execute it:

```bash
python -m autoresearch.cli retry execute --retry-request-id <retry_request_id>
```

List retry requests:

```bash
python -m autoresearch.cli retry list
```
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```bash
pytest tests/test_cli.py tests/test_probe_flow.py tests/test_retry_executor.py tests/test_retry_registry.py tests/test_decisions.py tests/test_settings.py tests/test_db.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/autoresearch/cli.py docs/architecture.md docs/runbook.md tests/test_cli.py tests/test_probe_flow.py tests/test_retry_executor.py tests/test_retry_registry.py tests/test_decisions.py tests/test_settings.py tests/test_db.py
git commit -m "feat: add safe retry approval flow"
```

## Final Verification

- [ ] **Step 1: Run the focused Phase 4B test suite**

Run:

```bash
pytest tests/test_db.py tests/test_settings.py tests/test_run_registry.py tests/test_decisions.py tests/test_retry_policy.py tests/test_retry_registry.py tests/test_retry_executor.py tests/test_probe_flow.py tests/test_cli.py -q
```

Expected: PASS.

- [ ] **Step 2: Run the full repository test suite**

Run:

```bash
PYTHONPATH=src pytest -q
```

Expected: PASS.

- [ ] **Step 3: Verify the new CLI surface**

Run:

```bash
PYTHONPATH=src python -m autoresearch.cli retry --help
```

Expected: help output shows `request`, `list`, `approve`, `reject`, and `execute`.

- [ ] **Step 4: Review git status**

Run:

```bash
git status --short
```

Expected: only the intended Phase 4B files are modified.
