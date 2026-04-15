# Phase 3B Remote Probe Design

## Objective

Phase 3B extends the local-only Phase 3A PBS executor into a real, operator-visible Polaris probe workflow.

This phase is not a general job-submission system. It is a narrow operational loop that:

1. adds minimal remote bridge primitives on top of the existing OpenSSH control-master bridge,
2. bootstraps the managed Eagle-side project root,
3. submits one fixed built-in probe job,
4. polls real scheduler state back into the local registry.

The goal is to prove that the control plane can safely cross the boundary from the lab server to Polaris without widening scope into arbitrary remote execution or full experiment orchestration.

## Scope

Phase 3B includes:

- bridge-mediated remote command execution through the existing `polaris-relay` attachment
- bridge-mediated file transfer for controlled paths
- remote bootstrap of the managed Eagle root
- one built-in probe job submission path
- real `qsub` invocation for the built-in probe
- real `qstat` polling and job state updates
- local run/job persistence for the probe workflow

Phase 3B excludes:

- arbitrary user-specified remote entrypoints
- general-purpose remote file management
- automatic retries or daemonized polling
- remote result cleanup
- generalized experiment submission
- incident classification
- background services on Polaris login nodes

## Hard Constraints

Phase 3B keeps the earlier project constraints unchanged:

- The control plane still runs on the lab server.
- Polaris login nodes remain an authentication bridge and command relay only.
- No MFA automation or credential bypass is allowed.
- Managed remote project paths remain under the configured `remote_root`, which defaults to `/eagle/lc-mpi/Zhiqing/auto-research`.
- The probe submission path must stay narrow and explicit.
- The first real submission path is the built-in probe only.

## Chosen Approach

Three approaches were considered:

1. Thin bridge primitives plus explicit orchestration
2. Service-first internal abstraction with broader indirection
3. One-command black-box probe runner

Phase 3B uses approach 1.

This keeps failures diagnosable. If the workflow breaks, the operator should be able to tell whether the problem is:

- bridge attachment,
- remote command execution,
- remote file transfer,
- Eagle bootstrap,
- PBS submission,
- scheduler polling,
- or local state updates.

That is more important than hiding complexity behind a single high-level command in the first real remote phase.

## Architecture

Phase 3B is split into three layers.

### 1. Bridge Primitive Layer

This extends the current `SSHMasterClient` with the minimum real remote actions:

- `exec(remote_cmd)`
- `copy_to(local_path, remote_path)`
- `copy_from(remote_path, local_path)`

This layer does not understand PBS semantics. It only knows how to reuse the existing OpenSSH master connection and return structured command results.

### 2. Remote Bootstrap Layer

This layer prepares the remote managed root under `remote_root`.

It is responsible for:

- creating required directory structure
- creating or uploading a small set of managed files
- enforcing “no overwrite unless `--force`” semantics

This layer is intentionally narrow. It is not a general remote filesystem client.

### 3. Probe Orchestration Layer

This layer owns the built-in probe workflow:

- create the local run and job records
- build the probe request from configured defaults plus CLI overrides
- render the PBS script
- upload the probe entrypoint and submit script
- call real `qsub`
- parse and persist the returned PBS job identifier
- poll real `qstat`
- update local job state

This layer is the only place in Phase 3B where the bridge, remote bootstrap, PBS renderer, PBS parser, and registry are composed together.

## Remote Root Layout

Phase 3B assumes the managed remote root is the configured `remote_root`, defaulting to:

```text
/eagle/lc-mpi/Zhiqing/auto-research
```

The bootstrap path manages at least:

```text
<remote_root>/
├── jobs/
├── runs/
├── manifests/
└── README.remote.md
```

For the built-in probe, the control plane may also manage a fixed probe script path under:

```text
<remote_root>/jobs/probe/
```

The exact filenames can be implementation details, but they must remain controlled by the system, not user-specified.

## Bootstrap Semantics

`remote bootstrap` is intentionally conservative.

Default behavior:

- create missing directories
- create or upload missing managed files
- do not overwrite existing managed files

`--force` behavior:

- permits overwriting the small set of explicitly managed files
- still does not turn the command into a general remote sync mechanism

The managed-file set for Phase 3B is intentionally small:

- `README.remote.md`
- built-in probe entrypoint
- built-in probe submit-related files if needed

Bootstrap must not delete remote content.

## Probe Job Semantics

The first real submission path is a built-in probe only.

The probe exists to validate:

- bridge health
- remote root writability
- PBS submission
- scheduler polling
- stdout/stderr placement
- local state updates

The probe is not configurable beyond a small override surface such as queue or walltime.

The probe does not accept an arbitrary remote entrypoint from the CLI.

### Default Parameters

Probe defaults come from `conf/polaris.yaml`, with CLI overrides allowed.

At minimum, Phase 3B should configure:

- `project`
- `queue`
- `walltime`

CLI overrides are allowed for operational flexibility, but configuration remains the default source of truth.

## CLI Surface

Phase 3B adds these commands.

### Bridge Commands

```bash
autoresearch bridge exec -- "pwd"
autoresearch bridge copy-to --src local.txt --dst /eagle/.../tmp/local.txt
autoresearch bridge copy-from --src /eagle/.../runs/.../probe.log --dst /tmp/probe.log
```

Behavior rules:

- All commands require the bridge to already be attached.
- Exit codes from remote commands are preserved.
- Command wrappers must continue to capture stdout, stderr, exit code, and duration.
- Copy destinations must be constrained to the configured `remote_root` for Phase 3B-managed operations.

### Remote Bootstrap Commands

```bash
autoresearch remote bootstrap
autoresearch remote bootstrap --force
```

Behavior rules:

- Without `--force`, existing managed files are preserved.
- With `--force`, only the explicitly managed-file set may be overwritten.

### Probe Commands

```bash
autoresearch job submit-probe
autoresearch job submit-probe --queue debug --walltime 00:20:00
autoresearch job poll --job-id <job_id>
```

Behavior rules:

- `submit-probe` only submits the fixed built-in probe.
- `submit-probe` creates the local run and job records before or during submission.
- `poll` must use real scheduler output and update local job state.

Phase 3B does not add:

- `job submit`
- arbitrary entrypoint submission
- auto-poll daemons
- generalized cancellation workflows

## State Model

### Bridge

Bridge state remains:

- `ATTACHED`
- `DETACHED`
- `STALE`

Phase 3B does not add automatic bridge recovery.

### Probe Job

The job state flow for the built-in probe is:

```text
DRAFT
  -> SUBMITTED
  -> QUEUED
  -> RUNNING
  -> SUCCEEDED | FAILED
```

State transitions should be updated from actual submission and polling outcomes, not inferred from wishful control flow.

If bridge or remote execution fails before `qsub` succeeds, the job must not be advanced as if remote submission occurred.

## Error Handling

Phase 3B should fail clearly in these cases:

- bridge not attached
- remote command execution failure
- remote copy failure
- bootstrap write failure
- `qsub` malformed or failed output
- `qstat` malformed or failed output
- `qstat` returning unexpected multi-job payload where a single-job response is expected

The system should preserve evidence in command results and local job records instead of hiding it behind generic exceptions.

Phase 3B must not silently continue on remote errors.

## File Map

Expected code areas for Phase 3B:

- modify `src/autoresearch/bridge/ssh_master.py`
- create `src/autoresearch/bridge/remote_exec.py`
- create `src/autoresearch/bridge/remote_fs.py`
- modify `src/autoresearch/executor/polaris.py`
- modify `src/autoresearch/executor/pbs.py`
- modify `src/autoresearch/runs/registry.py`
- modify `src/autoresearch/cli.py`
- modify `src/autoresearch/settings.py`
- modify `conf/polaris.yaml`
- add or extend tests for bridge command construction, remote bootstrap, probe flow, and CLI behavior

The exact decomposition should keep each file narrow:

- `ssh_master.py`: OpenSSH command construction and execution
- `remote_exec.py`: safe remote execution wrapper
- `remote_fs.py`: bootstrap-specific filesystem operations
- `polaris.py`: probe defaults and request construction
- `pbs.py`: script rendering and scheduler parsing/execution wrappers
- `registry.py`: persistence and state updates
- `cli.py`: thin command surface only

## Testing Strategy

Automated tests remain local and fake-runner based.

Phase 3B tests should cover:

- `bridge exec` command construction
- `copy_to/copy_from` command construction
- remote bootstrap behavior with and without `--force`
- constrained path handling under `remote_root`
- probe submission flow using fake `qsub` output
- probe polling flow using fake `qstat` output
- CLI coverage for:
  - `bridge exec`
  - `bridge copy-to`
  - `bridge copy-from`
  - `remote bootstrap`
  - `job submit-probe`
  - `job poll`

Automated tests should not require live Polaris access.

### Manual Acceptance

Manual acceptance is required for this phase.

At minimum:

1. attach bridge manually
2. run `remote bootstrap`
3. submit built-in probe
4. poll until terminal state
5. verify stdout/stderr path placement under `remote_root`
6. verify local `jobs` table state matches scheduler outcome

The probe must complete successfully at least once for Phase 3B to count as operationally validated.

## Out of Scope

Phase 3B intentionally does not solve:

- arbitrary experiment submission
- artifact synchronization beyond the narrow probe path
- job cancellation policy
- incident classification
- auto-retry
- report generation from live probe runs
- remote environment generalization

Those belong to later phases once the remote probe path is stable.

## Done Criteria

Phase 3B is done when:

- `bridge exec`, `bridge copy-to`, and `bridge copy-from` exist and are tested
- `remote bootstrap` exists and respects `--force`
- `job submit-probe` submits the built-in probe through real `qsub`
- `job poll` parses real scheduler output and updates local state
- automated tests pass locally
- docs and config are updated
- a real probe job succeeds at least once on Polaris

## Risks

Main risks for Phase 3B:

- bridge behavior and remote command quoting drift from OpenSSH expectations
- accidental widening of scope into generic remote administration
- conflating bootstrap failures with scheduler failures
- letting built-in probe logic morph into a generic submission path too early

The design stays narrow specifically to keep those risks contained.
