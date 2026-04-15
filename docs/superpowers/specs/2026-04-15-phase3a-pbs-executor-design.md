# Auto Research Phase 3A PBS Executor Design

## Objective

Build the first half of the Polaris executor as a local, testable PBS layer for `auto-research`.

Phase 3A adds:

- PBS script rendering
- `qsub` output parsing
- `qstat -f` and `qstat -fF JSON` parsing
- Polaris-specific defaulting and path rules
- `jobs` table read/write support
- a minimal `job` CLI for listing jobs and rendering PBS scripts

This phase does not perform real bridge-mediated submission to Polaris. It prepares the executor logic so Phase 3B can wire it to the real ALCF bridge and run an actual probe job.

## Approved Direction

Three implementation directions were considered:

1. Parser-first executor
2. Registry-first executor
3. CLI-first executor

The approved direction is option 1.

This means Phase 3A will:

- define explicit PBS and job schemas first
- build parser and renderer behavior around fixed fixtures
- add registry support after parser behavior is stable
- keep CLI thin and minimal

This phase will not:

- submit jobs through the real Polaris bridge
- upload scripts to Eagle
- poll a live queue
- bootstrap remote directories
- execute a real probe job

## Scope

### In Scope

- Add typed PBS/job schema objects
- Render Polaris PBS scripts from normalized job requests
- Parse `qsub` output into a structured result
- Parse `qstat -f` text into a structured result
- Parse `qstat -fF JSON` output into a structured result
- Add Polaris defaults such as `filesystems=eagle` and `place=scatter`
- Generate normalized stdout/stderr paths under `/eagle/lc-mpi/Zhiqing/auto-research/runs/<RUN_ID>/`
- Add `jobs` table registry methods for create, update, and list
- Add local fixture-driven tests for parsers and renderer
- Add a minimal `job` CLI for `list` and `render-pbs`

### Out of Scope

- Real `qsub`, `qstat`, or `qdel` execution against Polaris
- Bridge integration
- Remote script upload
- Remote filesystem bootstrap
- Live queue polling
- Incident generation from job output
- Heartbeat tracking
- Automatic resubmission
- General remote command execution

## Constraints

- Polaris remains a remote executor only
- ALCF-managed project data must continue to target `/eagle/lc-mpi/Zhiqing/auto-research/`
- PBS defaults must honor the documented Polaris requirements:
  - account required
  - walltime required
  - filesystems required
- `filesystems=eagle` is the default unless a later phase proves a different requirement
- `place=scatter` is the default placement rule
- Output paths must live under `/eagle/lc-mpi/Zhiqing/auto-research/runs/<RUN_ID>/`
- Parser behavior must be deterministic and fixture-driven
- CLI must stay thin over typed services and registries

## Phase Split

Phase 3 is intentionally split:

- `Phase 3A`
  - local, testable PBS executor logic
- `Phase 3B`
  - real Polaris bridge integration and probe job execution

This split keeps executor logic separable from live ALCF environment behavior. If Phase 3B fails later, the failure surface should be bridge or remote environment integration, not PBS parsing or script generation.

## Repository Changes For Phase 3A

```text
auto-research/
├── docs/
│   ├── architecture.md              # add executor architecture notes
│   └── runbook.md                   # add local executor usage notes
├── src/
│   └── autoresearch/
│       ├── cli.py                   # add minimal job command group
│       ├── schemas.py               # add PBS and job types
│       ├── executor/
│       │   ├── __init__.py
│       │   ├── pbs.py
│       │   └── polaris.py
│       └── runs/
│           └── registry.py          # extend jobs table support
└── tests/
    ├── fixtures/
    │   ├── qsub_success.txt
    │   ├── qstat_full.txt
    │   └── qstat_full.json
    ├── test_pbs_parser.py
    ├── test_executor.py
    ├── test_registry.py
    └── test_cli.py
```

No new runtime service or external library is required in this phase.

## Data Model Additions

Phase 0 and Phase 1 already created the `jobs` table. Phase 3A starts using it.

The `jobs` table remains:

```sql
jobs(
  job_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  backend TEXT NOT NULL,
  pbs_job_id TEXT,
  queue TEXT,
  walltime TEXT,
  filesystems TEXT,
  select_expr TEXT,
  place_expr TEXT,
  exec_host TEXT,
  state TEXT NOT NULL,
  submit_script_path TEXT,
  stdout_path TEXT,
  stderr_path TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
)
```

Phase 3A will populate this table for local executor-managed job records even when no live submission occurs yet.

Expected initial states:

- `DRAFT`
- `SUBMITTED`
- `QUEUED`
- `RUNNING`
- `SUCCEEDED`
- `FAILED`
- `CANCELLED`

Phase 3A only needs enough state support to create records and update them from parsed scheduler results. It does not need a full orchestration state machine yet.

## Module Responsibilities

### `src/autoresearch/executor/polaris.py`

This module owns Polaris-specific normalization rules:

- default queue or queue passthrough behavior
- required account/walltime validation
- default `filesystems=eagle`
- default `place=scatter`
- normalized stdout/stderr path construction
- default submit script path shape

This module does not parse scheduler output and does not write to SQLite.

### `src/autoresearch/executor/pbs.py`

This module owns PBS-facing text and parser behavior:

- render a complete PBS script from a normalized request
- parse raw `qsub` output into a structured submission result
- parse raw `qstat -f` text into structured fields
- parse raw `qstat -fF JSON` output into structured fields

This module does not call the bridge and does not write to SQLite.

### `src/autoresearch/schemas.py`

This file gains explicit types for:

- PBS render requests
- rendered PBS script payloads
- parsed `qsub` results
- parsed `qstat` results
- job creation and update payloads

These types keep parser, registry, and CLI boundaries explicit.

### `src/autoresearch/runs/registry.py`

This file remains the single registry module for Phase 3A.

It will gain:

- `JobRecord`
- job creation support
- job state update support
- job listing support

The file is intentionally not split further yet. The current codebase is still small enough to keep run and job registry logic together.

### `src/autoresearch/cli.py`

This file will gain a `job` command group.

Phase 3A CLI remains intentionally small:

```bash
autoresearch job list
autoresearch job render-pbs --run-id <RUN_ID> --queue <QUEUE> --walltime <HH:MM:SS>
```

The parser utilities are not exposed as user-facing CLI commands in this phase. They exist for service code and fixture-driven tests.

## Normalized PBS Request Shape

The normalized PBS request must include enough information to render a valid Polaris script:

- `run_id`
- `job_name`
- `project`
- `queue`
- `walltime`
- `select_expr`
- `place_expr`
- `filesystems`
- `stdout_path`
- `stderr_path`
- `submit_script_path`
- `entrypoint_path`
- optional environment variables

Defaults and derivations:

- `filesystems` defaults to `eagle`
- `place_expr` defaults to `scatter`
- `stdout_path` defaults to `/eagle/lc-mpi/Zhiqing/auto-research/runs/<RUN_ID>/stdout.log`
- `stderr_path` defaults to `/eagle/lc-mpi/Zhiqing/auto-research/runs/<RUN_ID>/stderr.log`
- `submit_script_path` defaults to `/eagle/lc-mpi/Zhiqing/auto-research/jobs/<RUN_ID>/submit.pbs`

## PBS Script Rendering

`render_pbs_script(...)` must produce a script equivalent in shape to:

```bash
#!/bin/bash
#PBS -A <PROJECT>
#PBS -q <QUEUE>
#PBS -l select=1:system=polaris
#PBS -l place=scatter
#PBS -l walltime=01:00:00
#PBS -l filesystems=eagle
#PBS -N <JOB_NAME>
#PBS -k doe
#PBS -o /eagle/lc-mpi/Zhiqing/auto-research/runs/<RUN_ID>/stdout.log
#PBS -e /eagle/lc-mpi/Zhiqing/auto-research/runs/<RUN_ID>/stderr.log

set -euo pipefail

cd /eagle/lc-mpi/Zhiqing/auto-research/repo

export RUN_ID=<RUN_ID>
export AUTORESEARCH_REMOTE_ROOT=/eagle/lc-mpi/Zhiqing/auto-research
export RUN_DIR=/eagle/lc-mpi/Zhiqing/auto-research/runs/<RUN_ID>
mkdir -p "$RUN_DIR"

bash /eagle/lc-mpi/Zhiqing/auto-research/jobs/<RUN_ID>/entrypoint.sh
```

Phase 3A treats this as a rendered artifact only. It does not upload or execute the script.

## Parser Behavior

### `qsub` Parsing

The parser should accept the common success case where `qsub` returns a scheduler job identifier such as:

```text
123456.polaris-pbs-01.hsn.cm.polaris.alcf.anl.gov
```

The parser should extract:

- raw output
- normalized PBS job id
- success/failure classification

If the output is empty or malformed, parsing should fail explicitly rather than guessing.

### `qstat -f` Parsing

The text parser should extract the minimum useful set of fields:

- PBS job id
- job state
- queue
- comment
- exec_host
- output path when present
- error path when present

The `comment` field is especially important because Phase 4 will rely on it for incident classification.

### `qstat -fF JSON` Parsing

The JSON parser should extract the same scheduler-facing fields as the text parser, but from the structured JSON representation when available. Phase 3A should prefer explicit JSON field extraction over string matching.

## Registry Behavior

Phase 3A registry support should enable the following local lifecycle:

1. Create a run through existing `run create`
2. Render a PBS script for that run
3. Create a local job record in `DRAFT`
4. Update the job record from parsed submission or status data
5. List job records through the CLI

This creates a persistent executor data model before live scheduler integration.

## CLI Semantics

### `autoresearch job list`

- lists persisted job records
- shows at least:
  - `job_id`
  - `run_id`
  - `backend`
  - `state`
  - `pbs_job_id`
  - `updated_at`

### `autoresearch job render-pbs`

- validates the minimum required inputs
- normalizes them through Polaris defaults
- renders the script
- prints the rendered script to stdout
- does not persist a job record

Job persistence remains a registry concern in Phase 3A. The CLI renderer is intentionally a pure render path so it cannot be mistaken for real submission.

## Testing Strategy

Tests must stay local and deterministic.

### `tests/fixtures/`

Fixtures will include:

- `qsub_success.txt`
- `qstat_full.txt`
- `qstat_full.json`

These are treated as stable parser samples. They should reflect realistic Polaris-shaped output, especially around the `comment` field and scheduler job id format.

### `tests/test_pbs_parser.py`

Cover:

- successful `qsub` parsing
- malformed `qsub` output failure
- `qstat -f` field extraction
- `qstat -fF JSON` field extraction

### `tests/test_executor.py`

Cover:

- Polaris defaulting rules
- stdout/stderr path derivation
- submit script path derivation
- PBS script rendering content

### `tests/test_registry.py`

Cover:

- job record creation
- job state update
- job listing order and field mapping

### `tests/test_cli.py`

Extend CLI coverage for:

- `job list`
- `job render-pbs`

CLI tests should assert that the output makes a clear distinction between rendered draft artifacts and real submission.

## Verification

Phase 3A is complete when all of the following are true:

- `executor/pbs.py` renders valid Polaris-shaped PBS scripts
- `qsub`, `qstat -f`, and `qstat -fF JSON` parsing are covered by fixtures
- Polaris defaulting rules are encoded and tested
- `jobs` registry methods exist and are tested
- `autoresearch job list` works
- `autoresearch job render-pbs` works
- `pytest` passes
- docs explain that Phase 3A is local-only and Phase 3B will add real submission
