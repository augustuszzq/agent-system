# Auto Research Phase 4B Safe Retry And Approval Design

## Objective

Build the second half of the incident system as a narrow, auditable safe-retry and approval flow for `auto-research`.

Phase 4B adds:

- explicit retry requests tied to existing incidents
- manual operator approval and rejection commands
- execution of approved retry requests through the existing live Polaris submission path
- audit records that connect the source incident and job to the newly submitted retry run and job

Phase 4B does not add background watcher automation, fully automatic retries, experiment mutation, or generalized job resubmission. Those remain future work.

## Approved Direction

Three directions were considered:

1. decisions-only retry approval with immediate direct resubmit
2. explicit `retry_requests` table plus decision log, with approval separated from execution
3. generalized action ledger for every incident action

The approved direction is option 2.

This means Phase 4B will keep three concerns separate:

- `incidents` describe what failed
- `decisions` describe what the operator approved or rejected
- `retry_requests` describe the retry action itself, including whether it was executed and what new run/job it produced

That separation matters because the system needs to answer concrete audit questions later:

- which incident was approved for retry
- who approved it and why
- whether the approved retry was ever executed
- which new run and job resulted from that execution

## Scope

### In Scope

- add a `retry_requests` persistence model and registry
- add `retry request --incident-id <incident_id>`
- add `retry list`
- add `retry approve --retry-request-id <id> --reason <text>`
- add `retry reject --retry-request-id <id> --reason <text>`
- add `retry execute --retry-request-id <id>`
- allow retry requests only for incidents whose category is in the configured safe-retry whitelist
- implement `RETRY_SAME_CONFIG` only
- execute approved retries through the existing real Polaris probe submission path
- create a brand-new retry run and brand-new retry job for each execution
- record the resulting run/job/pbs identifiers back onto the retry request
- append decision log entries for approval, rejection, and execution
- add deterministic unit tests and CLI tests for request, approval, rejection, and execution state handling

### Out of Scope

- automatic retry execution without approval
- retrying arbitrary user-defined jobs
- modifying batch size, topology, queue, walltime, model config, or code during retry
- reusing the original run directory or original job record for retry execution
- repeated execution of the same retry request after it reaches `SUBMITTED` or `FAILED`
- auto-closing incidents after a retry succeeds
- scheduler-side healing logic beyond a same-config resubmit
- background polling of pending retry requests

## Constraints

- The control plane continues to run on the lab server
- Polaris remains a remote executor only
- No MFA automation is introduced
- Retry execution uses the existing attached OpenSSH bridge and the existing remote bootstrap and probe submission path
- Safe retry remains intentionally conservative
- Every retry action must preserve operator-visible audit trails and machine-readable linkage back to the source incident
- A retry request must not silently mutate the original experiment definition
- The first execution path supports probe jobs only

## Phase Split

Phase 4 is intentionally split:

- `Phase 4A`
  - incident evidence fetch
  - deterministic classification
  - incident upsert and summaries
- `Phase 4B`
  - retry request persistence
  - approval and rejection flow
  - safe retry execution
  - retry policy enforcement

The first step identified failures. The second step adds a narrow operator-controlled response path.

## Retry Model

### High-Level Semantics

Phase 4B defines one retry action only:

- `RETRY_SAME_CONFIG`

For Phase 4B, `same config` means:

- same project
- same queue
- same walltime
- same `filesystems`
- same `select_expr`
- same `place_expr`
- same built-in probe entrypoint
- no modifications to code, runtime flags, scheduler directives, or experiment definition

Because current PBS artifact paths are derived from `run_id`, a retry must not reuse the original run or original job record. Instead, execution creates:

- a new retry run with a fresh `run_id`
- a new retry job with a fresh `job_id`
- a new remote submit script path
- a new remote stdout/stderr path

This avoids remote path collisions and keeps retry attempts auditable.

### Retry Category Whitelist

The first whitelist is intentionally tiny.

Phase 4B only allows retry requests for:

- `FILESYSTEM_UNAVAILABLE`

Phase 4B explicitly does not allow retry requests for:

- `RESOURCE_OOM`
- `RESOURCE_WALLTIME`
- `ENV_IMPORT_ERROR`
- `ENV_PATH_ERROR`
- `NCCL_FAILURE`
- `MPI_BOOTSTRAP`
- `NO_HEARTBEAT`
- `UNKNOWN`

The reasoning is straightforward:

- these categories usually require configuration, environment, or code changes
- repeating the exact same config is unlikely to heal the failure safely
- the system must not pretend those are safe same-config retries

The category whitelist remains configuration-driven through `conf/retry_policy.yaml`.

## Data Model

### `retry_requests`

Add a dedicated `retry_requests` table with at least these columns:

```sql
retry_requests(
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
```

Notes:

- `requested_action` is fixed to `RETRY_SAME_CONFIG` for Phase 4B
- `attempt_count` starts at `0` and becomes `1` when execution successfully submits a new remote job
- `result_run_id`, `result_job_id`, and `result_pbs_job_id` remain null until execution succeeds
- `last_error` stores the latest submission-path failure when execution fails before a new PBS job is created

### Relationship To Existing Tables

- `incidents` remains the source of failure facts
- `decisions` remains the audit trail for operator actions
- `retry_requests` becomes the source of truth for retry action state

This gives clean answers to both operator questions and implementation questions.

## State Model

### Approval State

`approval_status` uses:

- `PENDING`
- `APPROVED`
- `REJECTED`

### Execution State

`execution_status` uses:

- `NOT_STARTED`
- `SUBMITTED`
- `FAILED`

### Allowed Flow

```text
PENDING + NOT_STARTED
  -> APPROVED + NOT_STARTED
  -> REJECTED + NOT_STARTED
  -> APPROVED + SUBMITTED
  -> APPROVED + FAILED
```

Rules:

- only `PENDING` requests may be approved or rejected
- only `APPROVED` requests may be executed
- once a request reaches `SUBMITTED`, it is terminal for Phase 4B
- once a request reaches `FAILED`, it is also terminal for Phase 4B
- if the operator wants to retry again after a failed execution attempt, they create a new retry request instead of reusing the old one

This keeps one request equal to one execution attempt.

## CLI Surface

Phase 4B adds a `retry` command group:

```bash
autoresearch retry request --incident-id <incident_id>
autoresearch retry list
autoresearch retry approve --retry-request-id <id> --reason "..."
autoresearch retry reject --retry-request-id <id> --reason "..."
autoresearch retry execute --retry-request-id <id>
```

### `retry request`

Behavior:

1. load the incident
2. validate that the incident exists and is open
3. validate that the incident category is in the configured safe-retry whitelist
4. validate that there is not already an active retry request for that incident and action
5. create a `PENDING + NOT_STARTED` retry request
6. print the request id and category

If the incident category is not whitelisted, the command should fail clearly and create no retry request.

### `retry list`

Behavior:

- list retry requests in descending `updated_at` order
- show at least:
  - `retry_request_id`
  - `incident_id`
  - `requested_action`
  - `approval_status`
  - `execution_status`
  - `result_job_id` or `-`
  - `updated_at`

The initial view should include all retry requests. Filtering can wait.

### `retry approve`

Behavior:

1. load the retry request
2. require it to be `PENDING`
3. update it to `APPROVED`
4. store `approved_by` and `approval_reason`
5. append a `decisions` row documenting the approval
6. print the updated request id and state

For Phase 4B, `approved_by` can be fixed to `operator`.

### `retry reject`

Behavior:

1. load the retry request
2. require it to be `PENDING`
3. update it to `REJECTED`
4. store the rejection reason in `approval_reason`
5. append a `decisions` row documenting the rejection
6. print the updated request id and state

### `retry execute`

Behavior:

1. load the retry request
2. require it to be `APPROVED + NOT_STARTED`
3. load the source incident
4. confirm the category is still whitelisted
5. load the source job and source run
6. confirm the source job is a supported probe job
7. derive a new retry run with a fresh `run_id`
8. derive a new retry job request using the original scheduler config
9. submit through the existing real remote probe submission path
10. on successful submission:
    - write `result_run_id`
    - write `result_job_id`
    - write `result_pbs_job_id`
    - set `execution_status=SUBMITTED`
    - set `attempt_count=1`
    - set `executed_at`
    - append a `decisions` row documenting execution
11. print the new run/job/pbs ids

This command is responsible only for successful creation of the new remote job. It is not responsible for whether that new job later succeeds.

## Execution Path

### Reuse Of Existing Submission Logic

Phase 3B already includes a live probe submission path in the CLI layer.

Phase 4B should not implement a second probe submit flow. Instead, it should extract the existing probe submission steps into an internal helper that can be called both by:

- `job submit-probe`
- `retry execute`

That helper should accept explicit inputs for:

- new run kind
- project
- queue
- walltime
- notes/metadata for the new run

This keeps remote submission logic single-sourced.

### Retry Run Shape

The retry execution should create a new run with:

- `run_kind=probe-retry`
- `project` inherited from the source run
- `notes` containing at minimum:
  - source incident id
  - source job id
  - retry request id

The exact note format can be simple plain text in Phase 4B.

## Failure Handling

### Pre-Submission Failures

If execution fails before a new PBS job is created, for example because:

- the bridge is detached or stale
- remote bootstrap fails
- remote upload fails
- `qsub` returns an error

then the system should:

- set `execution_status=FAILED`
- leave `result_run_id`, `result_job_id`, and `result_pbs_job_id` null
- store a concise `last_error`
- keep `attempt_count=0`
- preserve the retry request for audit

### Post-Submission Failures

If a new PBS job is successfully created, `retry execute` is considered successful.

Anything that happens after that belongs to the new job lifecycle and should surface through the normal incident path from Phase 4A.

This boundary keeps the command’s responsibility narrow and testable.

## Configuration

### `conf/retry_policy.yaml`

Phase 4B should turn `conf/retry_policy.yaml` into this explicit structure:

```yaml
safe_retry_categories:
  - FILESYSTEM_UNAVAILABLE
allowed_actions:
  - RETRY_SAME_CONFIG
```

The whitelist must remain configuration-driven rather than hard-coded in the CLI.

## Module Responsibilities

### `src/autoresearch/retries/registry.py`

Owns `retry_requests` table reads, writes, and state transitions.

Responsibilities:

- create retry requests
- list retry requests
- approve retry requests
- reject retry requests
- mark execution failure
- mark execution submitted
- enforce state-transition guards

### `src/autoresearch/retries/policy.py`

Owns retry policy evaluation.

Responsibilities:

- load and interpret retry policy config
- determine whether an incident category is retry-eligible
- determine whether a requested action is allowed

### `src/autoresearch/retries/executor.py`

Owns the execution-side orchestration.

Responsibilities:

- validate a retry request is executable
- load the source incident/job/run
- invoke the shared internal probe submission helper
- map submission success or failure onto retry request state updates
- write execution-related decisions

This module should not redefine the remote bridge or PBS submission logic.

## Decision Log

Phase 4B should append `decisions` rows for at least these operator-visible actions:

- retry approved
- retry rejected
- approved retry executed

Decision targets are fixed for Phase 4B:

- `target_type=retry_request`
- `target_id=<retry_request_id>`

The related source incident remains reachable through the retry request row.

## Testing Strategy

Phase 4B should add:

- registry tests for retry request creation and state transitions
- policy tests for whitelist enforcement
- CLI tests for request/list/approve/reject/execute
- execution tests with mocked submission helpers and mocked bridge/remote failures
- database migration tests for the new `retry_requests` table

The execution path should not require a live Polaris connection in tests.

## Acceptance Criteria

Phase 4B is complete when:

- `retry request`, `retry list`, `retry approve`, `retry reject`, and `retry execute` all exist
- retry requests can only be created for whitelisted incident categories
- approval and rejection are explicitly recorded and state-guarded
- approved retries create a new retry run and new retry job instead of reusing the original ones
- execution results are written back to the retry request
- execution failures before submission are captured as `FAILED` with clear error text
- decision log entries exist for approval, rejection, and execution
- tests pass
- docs are updated

## Deferred Work

Explicitly deferred beyond Phase 4B:

- automatic execution after approval
- fully automatic retries without operator involvement
- retry support for generalized non-probe jobs
- repeated execution attempts on the same retry request
- auto-resolution of incidents after a later retry succeeds
- background scanning and pending-action loops
- richer operator identity and auth
