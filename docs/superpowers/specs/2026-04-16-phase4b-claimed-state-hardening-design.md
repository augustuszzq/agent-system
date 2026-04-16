# Auto Research Phase 4B Claimed-State Hardening Design

## Objective

Harden the Phase 4B retry lifecycle so a retry request that has been claimed for execution is no longer indistinguishable from a request whose remote submission was fully finalized.

The immediate problem is a crash window in the current `retry execute` flow:

1. the request is claimed before the real submitter runs
2. the real submitter creates the new remote run and job
3. the process may die before the request is finalized locally

With the current state model, that leaves a request in a partially executed state that looks too similar to a successfully submitted request.

This hardening round adds one explicit intermediate execution state so the system can represent that gap directly instead of encoding it implicitly.

## Approved Direction

Three directions were considered:

1. keep the current states and treat `SUBMITTED + empty result ids` as an implicit in-progress marker
2. add an explicit `CLAIMED` execution state and use it as the only pre-submit claimed state
3. keep the current states and add only an ad hoc repair or cleanup command

The approved direction is option 2.

The reason is simple:

- option 1 is smaller but leaks hidden semantics into every caller
- option 3 adds recovery behavior without first making the state machine explicit
- option 2 gives the system a clean, inspectable lifecycle with minimal conceptual overhead

## Scope

### In Scope

- add `CLAIMED` as an explicit retry execution state
- update the retry execution state machine
- change `retry execute` so transaction 1 ends at `CLAIMED`
- keep the real submitter call outside any SQLite write transaction
- finalize success as `CLAIMED -> SUBMITTED`
- finalize pre-submit failure as `CLAIMED -> FAILED`
- update CLI output and tests so `CLAIMED` is visible
- document `CLAIMED` as an operator-visible intermediate state

### Out of Scope

- automatic recovery of stranded `CLAIMED` requests
- a new CLI command to resume or repair `CLAIMED` requests
- background reconciliation of claimed requests
- changes to retry category policy
- changes to the safe-retry command surface

This round only hardens the state model and the execution transition semantics.

## Data Model Change

### `retry_requests.execution_status`

The execution state enum expands from:

- `NOT_STARTED`
- `SUBMITTED`
- `FAILED`

to:

- `NOT_STARTED`
- `CLAIMED`
- `SUBMITTED`
- `FAILED`

No other columns are required for this round.

`result_run_id`, `result_job_id`, `result_pbs_job_id`, and `executed_at` remain the success finalization fields.

## State Model

### Approval State

`approval_status` remains:

- `PENDING`
- `APPROVED`
- `REJECTED`

### Execution State

`execution_status` becomes:

- `NOT_STARTED`
- `CLAIMED`
- `SUBMITTED`
- `FAILED`

### Allowed Flow

```text
PENDING + NOT_STARTED
  -> APPROVED + NOT_STARTED
  -> REJECTED + NOT_STARTED
  -> APPROVED + CLAIMED
  -> APPROVED + SUBMITTED
  -> APPROVED + FAILED
```

More precisely, execution now flows as:

```text
APPROVED + NOT_STARTED
  -> APPROVED + CLAIMED
  -> APPROVED + SUBMITTED | APPROVED + FAILED
```

Rules:

- only `APPROVED + NOT_STARTED` may enter execution
- only `CLAIMED` may finalize to `SUBMITTED`
- only `CLAIMED` may finalize to `FAILED` during the normal execution path
- `SUBMITTED` still means the new remote retry run and job were successfully recorded locally
- `CLAIMED` means execution began but local finalization has not yet completed

The key semantic difference is that `SUBMITTED` is no longer overloaded to mean both "claimed" and "finalized".

## Execution Semantics

### Transaction 1: Preflight And Claim

`retry execute` keeps the existing preflight checks:

- request is `APPROVED + NOT_STARTED`
- source incident exists and is `OPEN`
- category is still whitelisted
- source run and job exist and are consistent
- source run kind is `probe`

If preflight passes, transaction 1 updates:

- `execution_status = CLAIMED`
- `updated_at`

Then transaction 1 commits.

At this point, no second executor should be able to start the same retry request, because the request is no longer `NOT_STARTED`.

### Submitter Call

The real submitter still runs outside any SQLite write transaction.

This preserves the earlier fix for the live-path locking issue, because the shared submitter writes new runs and jobs through fresh SQLite connections.

### Transaction 2: Success Finalization

If the submitter returns successfully, transaction 2 updates:

- `execution_status = SUBMITTED`
- `attempt_count = 1`
- `result_run_id`
- `result_job_id`
- `result_pbs_job_id`
- `executed_at`
- `updated_at`

The execution decision row must still be appended in the same transaction as this success finalization.

### Transaction 2: Failure Finalization

If the submitter fails before successful finalization, transaction 2 updates:

- `execution_status = FAILED`
- `last_error`
- `updated_at`

For `RemoteBridgeError`, the command continues to return the failed retry request record.

For unexpected submitter exceptions, the request should still be marked `FAILED`, and the original exception may still be re-raised after the failed state is durably recorded.

## Operational Meaning Of `CLAIMED`

This round intentionally makes `CLAIMED` operator-visible.

`CLAIMED` means:

- the retry request passed preflight
- the system began execution
- the request is reserved against a second concurrent executor
- the submission path has not yet been finalized locally

It does **not** guarantee that a new remote PBS job exists.

That ambiguity is acceptable at this stage because it is explicit and inspectable. It is strictly better than encoding the same ambiguity inside `SUBMITTED`.

## CLI Behavior

The existing CLI surface remains unchanged:

```bash
autoresearch retry request --incident-id <incident_id>
autoresearch retry list
autoresearch retry approve --retry-request-id <id> --reason "..."
autoresearch retry reject --retry-request-id <id> --reason "..."
autoresearch retry execute --retry-request-id <id>
```

Behavior changes:

- `retry list` may now show `CLAIMED`
- `retry execute` only starts from `APPROVED + NOT_STARTED`
- a request already in `CLAIMED` must not be re-executed by the normal Phase 4B path

No new CLI command is added in this hardening round.

## Testing Requirements

This round must add or update tests for:

- state enum coverage including `CLAIMED`
- successful flow:
  - `NOT_STARTED -> CLAIMED -> SUBMITTED`
- failed flow:
  - `NOT_STARTED -> CLAIMED -> FAILED`
- concurrent execution:
  - second executor must fail once the first request is already `CLAIMED`
- crash-window semantics:
  - a `CLAIMED` request with no result ids is distinguishable from a finalized `SUBMITTED` request
- CLI output:
  - `retry list` renders `CLAIMED` correctly

The tests do not need to implement a full recovery command, because recovery remains out of scope.

## Follow-On Work

This hardening round intentionally stops at explicit state modeling.

If later work is needed, it should build on `CLAIMED`, not on hidden interpretation of `SUBMITTED`.

Natural follow-on options are:

- `retry inspect --retry-request-id <id>`
- operator resolution of stale `CLAIMED` requests
- background reconciliation of aged `CLAIMED` rows

Those are future work, not part of this round.
