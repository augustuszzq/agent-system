# Phase 4B Claimed-State Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an explicit `CLAIMED` retry execution state so Phase 4B can distinguish an in-progress claimed retry request from a fully finalized submitted retry.

**Architecture:** Keep the existing Phase 4B layering. `schemas.py` and `retries/registry.py` define the state model and transitions, `retries/executor.py` uses a three-step execution flow (`NOT_STARTED -> CLAIMED -> SUBMITTED|FAILED`), and the CLI plus docs only surface the new state without adding new commands. `CLAIMED` is an operator-visible intermediate state, not a recovery workflow.

**Tech Stack:** Python 3.11+, SQLite, Typer, pytest

---

## File Map

- Modify: `src/autoresearch/schemas.py`
- Modify: `src/autoresearch/retries/registry.py`
- Modify: `src/autoresearch/retries/executor.py`
- Modify: `src/autoresearch/cli.py`
- Modify: `docs/architecture.md`
- Modify: `docs/runbook.md`
- Modify: `tests/test_retry_registry.py`
- Modify: `tests/test_retry_executor.py`
- Modify: `tests/test_cli.py`

## Task 1: Add `CLAIMED` To The Retry State Model

**Files:**
- Modify: `src/autoresearch/schemas.py`
- Modify: `src/autoresearch/retries/registry.py`
- Modify: `tests/test_retry_registry.py`

- [ ] **Step 1: Write the failing registry tests**

Add these tests to `tests/test_retry_registry.py`:

```python
def test_claim_execution_sets_claimed_state(tmp_path: Path) -> None:
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

    with connect_db(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        claimed = registry.claim_execution(conn, record.retry_request_id)

    assert claimed.execution_status == "CLAIMED"
    persisted = registry.get(record.retry_request_id)
    assert persisted.execution_status == "CLAIMED"
```

```python
def test_mark_submitted_requires_claimed_state(tmp_path: Path) -> None:
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

    with pytest.raises(ValueError, match="claimed"):
        registry.mark_submitted(
            record.retry_request_id,
            result_run_id="run_retry",
            result_job_id="job_retry",
            result_pbs_job_id="456.polaris",
            executed_at="2026-04-16T00:00:00+00:00",
        )
```

```python
def test_find_active_request_includes_claimed_rows(tmp_path: Path) -> None:
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

    with connect_db(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        registry.claim_execution(conn, record.retry_request_id)

    active = registry.find_active_request("incident_demo", "RETRY_SAME_CONFIG")

    assert active is not None
    assert active.execution_status == "CLAIMED"
```

- [ ] **Step 2: Run the tests to verify failure**

Run:

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_retry_registry.py -q
```

Expected: failure because `CLAIMED` is not part of the execution-state model yet, `mark_submitted()` still accepts `NOT_STARTED`, and `find_active_request()` ignores claimed rows.

- [ ] **Step 3: Update the state model and registry transitions**

Update `src/autoresearch/schemas.py`:

```python
RetryExecutionStatus = Literal["NOT_STARTED", "CLAIMED", "SUBMITTED", "FAILED"]
```

In `src/autoresearch/retries/registry.py`, change the active-request query so a claimed row still blocks new requests:

```python
AND (
  approval_status = 'PENDING'
  OR (
    approval_status = 'APPROVED'
    AND execution_status IN ('NOT_STARTED', 'CLAIMED')
  )
)
```

Change `claim_execution()` so it writes `CLAIMED` instead of `SUBMITTED`:

```python
conn.execute(
    """
    UPDATE retry_requests
    SET execution_status = ?,
        updated_at = ?
    WHERE retry_request_id = ?
    """,
    ("CLAIMED", updated_at, retry_request_id),
)
```

Change `mark_failed_in_connection()` so the normal execution path accepts `CLAIMED` as the claimed state:

```python
if record["approval_status"] != "APPROVED" or record["execution_status"] not in {
    "NOT_STARTED",
    "CLAIMED",
}:
    raise ValueError("retry request must be approved and claimed or not started")
```

Change `mark_submitted_in_connection()` so it only finalizes from `CLAIMED`:

```python
if record["approval_status"] != "APPROVED" or record["execution_status"] != "CLAIMED":
    raise ValueError("retry request must be claimed before submission is finalized")
```

and write `SUBMITTED` during finalization:

```python
conn.execute(
    """
    UPDATE retry_requests
    SET execution_status = ?,
        attempt_count = ?,
        result_run_id = ?,
        result_job_id = ?,
        result_pbs_job_id = ?,
        executed_at = ?,
        updated_at = ?
    WHERE retry_request_id = ?
    """,
    (
        "SUBMITTED",
        record["attempt_count"] + 1,
        result_run_id,
        result_job_id,
        result_pbs_job_id,
        executed_at,
        updated_at,
        retry_request_id,
    ),
)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_retry_registry.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/autoresearch/schemas.py src/autoresearch/retries/registry.py tests/test_retry_registry.py
git commit -m "feat: add claimed retry state"
```

## Task 2: Update Retry Execution To Use `CLAIMED`

**Files:**
- Modify: `src/autoresearch/retries/executor.py`
- Modify: `tests/test_retry_executor.py`

- [ ] **Step 1: Write the failing executor tests**

Add these tests to `tests/test_retry_executor.py`:

```python
def test_execute_retry_marks_request_claimed_while_submitter_is_in_progress(tmp_path: Path) -> None:
    db_path, _, _, _, request = _create_retry_fixture(tmp_path)
    entered = threading.Event()
    release = threading.Event()

    def blocking_submitter(**kwargs):
        entered.set()
        if not release.wait(timeout=2):
            raise AssertionError("submitter was not released")
        return FakeSubmitted("run_retry", "job_retry", "456.polaris")

    executor = RetryExecutor(
        db_path=db_path,
        policy=_retry_policy(),
        submitter=blocking_submitter,
        actor="operator",
    )

    result_holder: dict[str, object] = {}

    def run_execute() -> None:
        result_holder["record"] = executor.execute(request.retry_request_id)

    thread = threading.Thread(target=run_execute)
    thread.start()
    assert entered.wait(timeout=2)

    claimed = RetryRequestRegistry(db_path).get(request.retry_request_id)
    assert claimed.execution_status == "CLAIMED"
    assert claimed.attempt_count == 0
    assert claimed.result_job_id is None

    release.set()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert result_holder["record"].execution_status == "SUBMITTED"
```

```python
def test_execute_retry_marks_claimed_request_failed_for_remote_bridge_errors(tmp_path: Path) -> None:
    db_path, _, _, _, request = _create_retry_fixture(tmp_path)

    executor = RetryExecutor(
        db_path=db_path,
        policy=_retry_policy(),
        submitter=lambda **kwargs: (_ for _ in ()).throw(RemoteBridgeError("bridge detached")),
        actor="operator",
    )

    updated = executor.execute(request.retry_request_id)

    assert updated.execution_status == "FAILED"
    assert updated.last_error == "bridge detached"
    assert updated.attempt_count == 0
    assert updated.result_job_id is None
```

Update the concurrent execution expectation in `test_execute_retry_blocks_duplicate_execution_before_second_submitter_call()`:

```python
assert isinstance(results["second_exc"], ValueError)
assert "approved and not started" in str(results["second_exc"])
claimed = RetryRequestRegistry(db_path).get(request.retry_request_id)
assert claimed.execution_status in {"CLAIMED", "SUBMITTED"}
```

- [ ] **Step 2: Run the tests to verify failure**

Run:

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_retry_executor.py -q
```

Expected: failure because the executor still uses `SUBMITTED` as the claim state and there is no regression coverage for a visible intermediate `CLAIMED` state.

- [ ] **Step 3: Change the executor flow to `NOT_STARTED -> CLAIMED -> SUBMITTED|FAILED`**

Keep the overall structure of `RetryExecutor`, but make `_prepare_execution()` claim only:

```python
self._retry_registry.claim_execution(conn, retry_request_id)
```

with `claim_execution()` now persisting `CLAIMED` from Task 1.

Leave the submitter outside any SQLite write transaction:

```python
submitted = self._submitter(
    run_kind="probe-retry",
    notes=notes,
    project=source_run["project"],
    queue=source_job["queue"],
    walltime=source_job["walltime"],
)
```

Keep `_mark_failed()` as the second transaction, but it now finalizes `CLAIMED -> FAILED`.

Keep `_finalize_success()` as the second transaction, but it now finalizes `CLAIMED -> SUBMITTED`.

Do not add a recovery command in this round. The hardening goal is to make the intermediate state explicit, not to solve recovery yet.

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_retry_executor.py tests/test_retry_registry.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/autoresearch/retries/executor.py tests/test_retry_executor.py tests/test_retry_registry.py
git commit -m "feat: harden retry execution with claimed state"
```

## Task 3: Surface `CLAIMED` In The CLI And Docs

**Files:**
- Modify: `src/autoresearch/cli.py`
- Modify: `docs/architecture.md`
- Modify: `docs/runbook.md`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write the failing CLI and docs-oriented tests**

Add this test to `tests/test_cli.py`:

```python
def test_retry_list_shows_claimed_execution_state(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AUTORESEARCH_DB", str(tmp_path / "state" / "autoresearch.db"))
    monkeypatch.setenv("AUTORESEARCH_REPO_ROOT", str(tmp_path))
    _write_repo_config(tmp_path)

    incident_id, _, _ = _seed_retryable_incident(tmp_path)
    request_result = runner.invoke(app, ["retry", "request", "--incident-id", incident_id])
    retry_request_id = request_result.stdout.strip().split("\\t")[0]
    approve_result = runner.invoke(
        app,
        ["retry", "approve", "--retry-request-id", retry_request_id, "--reason", "filesystem recovered"],
    )
    assert approve_result.exit_code == 0

    with connect_db(tmp_path / "state" / "autoresearch.db") as conn:
        conn.execute(
            """
            UPDATE retry_requests
            SET execution_status = 'CLAIMED'
            WHERE retry_request_id = ?
            """,
            (retry_request_id,),
        )

    list_result = runner.invoke(app, ["retry", "list"])

    assert list_result.exit_code == 0
    assert "CLAIMED" in list_result.stdout
```

- [ ] **Step 2: Run the tests to verify failure**

Run:

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_cli.py -q
```

Expected: failure because the current execution-state type and CLI expectations do not include `CLAIMED`.

- [ ] **Step 3: Keep the CLI surface stable and update docs**

`src/autoresearch/cli.py` should not add any new commands. The only code-level change should be that existing list/output formatting continues to render the new `CLAIMED` state naturally through the existing row formatter:

```python
def _format_retry_request_row(record) -> str:
    result_job_id = record.result_job_id or "-"
    return (
        f"{record.retry_request_id}\t{record.incident_id}\t{record.requested_action}\t"
        f"{record.approval_status}\t{record.execution_status}\t{result_job_id}\t{record.updated_at}"
    )
```

Update `docs/architecture.md` to describe the new execution states:

```markdown
Phase 4B retry execution now uses an explicit intermediate state:

- `NOT_STARTED`
- `CLAIMED`
- `SUBMITTED`
- `FAILED`

`CLAIMED` means a retry request passed preflight and was reserved for execution, but local finalization has not yet completed. `SUBMITTED` is reserved for finalized remote submissions only.
```

Update `docs/runbook.md` with an operator note:

```markdown
If `retry list` shows `CLAIMED`, the request began execution but was not yet finalized locally. Phase 4B does not yet provide an automatic repair command for `CLAIMED`; treat it as an operator-visible intermediate state for follow-up inspection.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_cli.py tests/test_retry_executor.py tests/test_retry_registry.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/autoresearch/cli.py docs/architecture.md docs/runbook.md tests/test_cli.py tests/test_retry_executor.py tests/test_retry_registry.py
git commit -m "docs: surface claimed retry state"
```

## Final Verification

- [ ] **Step 1: Run the focused hardening test set**

Run:

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_retry_registry.py tests/test_retry_executor.py tests/test_cli.py -q
```

Expected: PASS.

- [ ] **Step 2: Run the full repository test suite**

Run:

```bash
PYTHONPATH=src .venv/bin/pytest -q
```

Expected: PASS.

- [ ] **Step 3: Verify the retry CLI surface**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m autoresearch.cli retry --help
```

Expected: help output still shows only `request`, `list`, `approve`, `reject`, and `execute`.

- [ ] **Step 4: Review git status**

Run:

```bash
git status --short
```

Expected: only the intended claimed-state hardening files are modified.
