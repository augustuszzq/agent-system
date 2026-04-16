# Auto Research Phase 4A Incident Detection Design

## Objective

Build the first half of the incident system as a manual, operator-triggered detection and classification flow for `auto-research`.

Phase 4A adds:

- manual incident scanning for an existing job
- bridge-assisted live fetch of scheduler and log evidence when available
- local evidence snapshots for replay and audit
- deterministic incident classification into a small fixed taxonomy
- incident upsert behavior with stable deduplication
- operator-visible incident listing and summary commands

Phase 4A does not add safe retry, approval workflows, or a background watcher loop. Those remain Phase 4B work.

## Approved Direction

Three directions were considered:

1. `fetch -> normalize -> classify -> upsert -> summarize`
2. single-command all-in-one scan logic
3. immediate background watcher

The approved direction is option 1.

This means Phase 4A will keep evidence collection, normalization, classification, persistence, and summary rendering as separate steps with explicit boundaries.

That separation matters for two reasons:

- bridge availability is unstable by design, so evidence fetch must degrade cleanly to local-only analysis
- Phase 4B will need to reuse the same evidence and classification outputs when it adds safe retry and approval paths

## Scope

### In Scope

- Add an `incidents` package for fetch, normalization, classification, registry, and summary logic
- Add `incident scan --job-id <job_id>`
- Add `incident list`
- Add `incident summarize`
- When the bridge is attached, fetch:
  - `qstat -fF JSON <pbs_job_id>`
  - tail of the configured stdout log
  - tail of the configured stderr log
- Write fetched evidence to local snapshot files under the state directory
- Fall back to the latest local snapshot when the bridge is detached or stale
- Classify incidents into the approved fixed taxonomy
- Upsert incidents by `job_id + category + fingerprint`
- Add `updated_at` to incident persistence so repeated scans can update existing rows
- Add deterministic unit tests and fixture-driven classification tests

### Out of Scope

- automatic retry or resubmission
- approval or decision commands
- background polling or scheduled watcher loops
- heartbeat files or explicit heartbeat daemons
- checkpoint mtime inspection
- GPU utilization or hardware telemetry
- generative/LLM-based incident analysis
- automatic incident resolution when a later scan is clean
- arbitrary log collection outside the configured job stdout/stderr paths

## Constraints

- The control plane continues to run on the lab server
- Polaris remains a remote executor only
- No MFA automation is introduced
- Remote fetch is allowed only through the existing attached OpenSSH bridge
- If the bridge is not attached, incident scanning must still work against previously captured local evidence
- Incident classification must remain deterministic and reproducible
- The first taxonomy is intentionally narrow and conservative
- Incident writes must preserve evidence paths and scan timestamps so later actions are auditable

## Phase Split

Phase 4 is intentionally split:

- `Phase 4A`
  - incident evidence fetch
  - deterministic classification
  - incident upsert and summaries
- `Phase 4B`
  - safe retry
  - approval flow
  - retry policy enforcement
  - eventual watcher automation

This split keeps “what failed” separate from “what to do about it.” The first step is to get evidence capture and classification correct.

## Signal Sources

Phase 4A uses exactly three signal sources:

1. scheduler metadata from `qstat -fF JSON`
2. tail of the configured stdout log
3. tail of the configured stderr log

It may also use existing local registry fields such as:

- `job_id`
- `run_id`
- `pbs_job_id`
- `state`
- `stdout_path`
- `stderr_path`
- timestamps already stored in SQLite

Phase 4A explicitly does not use:

- heartbeat files
- checkpoint freshness
- GPU metrics
- cluster telemetry

## Runtime Behavior

### `incident scan`

`incident scan --job-id <job_id>` follows this sequence:

1. Load the job record from SQLite
2. Determine whether the bridge is attached
3. If attached:
   - fetch fresh `qstat -fF JSON`
   - fetch fresh stdout tail
   - fetch fresh stderr tail
   - write a local snapshot
4. If not attached:
   - load the newest local snapshot for that job if one exists
5. Normalize the evidence into a single analysis input
6. Run deterministic classification
7. Upsert matching incidents
8. Print one of:
   - incident created
   - incident updated
   - no incident detected

If neither live evidence nor a prior local snapshot exists, the command should fail clearly instead of inventing an “unknown” incident from empty input.

### Bridge Degradation

Bridge-assisted fetch is best-effort, not mandatory.

Behavior rules:

- `ATTACHED` bridge: fetch fresh evidence first
- `DETACHED` or `STALE` bridge: do not fail immediately if a local snapshot exists
- bridge fetch failure with a valid local snapshot: continue using the latest local snapshot
- bridge fetch failure without any local snapshot: fail with a clear operator-visible error

This keeps incident analysis usable even when Polaris access is temporarily unavailable.

## Local Snapshot Layout

Each scan writes or reuses evidence under:

```text
<state_dir>/incidents/<job_id>/<scan_ts>/
├── qstat.json
├── stdout.tail.log
└── stderr.tail.log
```

`scan_ts` should be an ISO-like sortable timestamp safe for filenames.

Snapshots are append-only for Phase 4A. The system does not rewrite or delete prior incident evidence snapshots in this phase.

## Module Responsibilities

### `src/autoresearch/incidents/fetch.py`

Owns evidence collection and snapshot writes.

Responsibilities:

- read the job record needed for fetch
- run bridge-mediated fetch when the bridge is attached
- tail stdout/stderr logs through the bridge
- fetch `qstat -fF JSON`
- write local snapshot files
- load the latest prior snapshot when necessary

This module does not classify incidents and does not write to the `incidents` table.

### `src/autoresearch/incidents/normalize.py`

Owns the normalized analysis input.

At minimum the normalized object should contain:

- `job_id`
- `run_id`
- `pbs_job_id`
- `job_state`
- `comment`
- `exec_host`
- `stdout_tail`
- `stderr_tail`
- `snapshot_dir`
- `scan_time`

This module exists so the classifier does not need to know where evidence came from.

### `src/autoresearch/incidents/classifier.py`

Owns deterministic classification, severity assignment, and fingerprint generation.

This module must not call the bridge, read SQLite directly, or write files.

### `src/autoresearch/incidents/registry.py`

Owns `incidents` table reads and writes.

Responsibilities:

- upsert by `job_id + category + fingerprint`
- list incidents
- summarize open incidents

This module should stay separate from `runs/registry.py` so the run/job registry does not keep absorbing unrelated responsibilities.

### `src/autoresearch/incidents/summaries.py`

Owns human-readable summary text for:

- one incident
- grouped incident counts by category
- top severe open incidents

### `src/autoresearch/cli.py`

Adds the `incident` command group:

```bash
autoresearch incident scan --job-id <job_id>
autoresearch incident list
autoresearch incident summarize
```

CLI remains thin over fetch, classifier, registry, and summary helpers.

## Taxonomy

Phase 4A supports exactly these categories:

- `FILESYSTEM_UNAVAILABLE`
- `RESOURCE_OOM`
- `RESOURCE_WALLTIME`
- `ENV_IMPORT_ERROR`
- `ENV_PATH_ERROR`
- `NCCL_FAILURE`
- `MPI_BOOTSTRAP`
- `NO_HEARTBEAT`
- `UNKNOWN`

No other categories should be introduced in this phase.

## Classification Rules

Classification is deterministic and priority-ordered.

### 1. `FILESYSTEM_UNAVAILABLE`

Primary source:

- `qstat` comment

Example signals:

- `filesystem unavailable`
- explicit `eagle` unavailability
- obvious Lustre or filesystem access failure reported by scheduler comment

### 2. `RESOURCE_OOM`

Primary source:

- stdout/stderr tail

Example signals:

- `out of memory`
- `oom-kill`
- `Killed`

Only match `Killed` when nearby context indicates memory exhaustion rather than a generic signal.

### 3. `RESOURCE_WALLTIME`

Primary sources:

- scheduler comment
- stdout/stderr tail

Example signals:

- `walltime`
- `time limit`
- `job exceeded walltime`

### 4. `ENV_IMPORT_ERROR`

Primary source:

- stdout/stderr tail

Example signals:

- `ImportError`
- `ModuleNotFoundError`

### 5. `ENV_PATH_ERROR`

Primary source:

- stdout/stderr tail

Example signals:

- `No such file or directory`
- `can't open file`
- `cannot cd`
- missing script or missing configured path

### 6. `NCCL_FAILURE`

Primary source:

- stdout/stderr tail

Example signals:

- `NCCL`
- transport errors
- collective timeout/fatal messages

### 7. `MPI_BOOTSTRAP`

Primary source:

- stdout/stderr tail

Example signals:

- `MPI_Init`
- PMI/bootstrap/launcher failures

### 8. `NO_HEARTBEAT`

Phase 4A uses a conservative proxy rule instead of a real heartbeat daemon.

Match only when:

- the job is still in a running-like scheduler state
- no stronger classification above matched
- the latest log tail hash matches the previous scan’s log tail hash for the same job

This category means “appears stalled from repeated scans,” not “confirmed heartbeat timeout.”

### 9. `UNKNOWN`

If evidence is non-empty and no stronger rule matches, classify as `UNKNOWN`.

Empty evidence alone must not create an `UNKNOWN` incident.

## Severity Mapping

Severity is fixed in Phase 4A.

- `CRITICAL`
  - `FILESYSTEM_UNAVAILABLE`
  - `RESOURCE_OOM`
  - `NCCL_FAILURE`
  - `MPI_BOOTSTRAP`
- `HIGH`
  - `RESOURCE_WALLTIME`
  - `ENV_IMPORT_ERROR`
  - `ENV_PATH_ERROR`
  - `NO_HEARTBEAT`
- `MEDIUM`
  - `UNKNOWN`

No dynamic severity scoring is needed in this phase.

## Fingerprint Rules

The deduplication key is:

```text
job_id + category + fingerprint
```

Fingerprint generation should be stable and category-specific:

- `FILESYSTEM_UNAVAILABLE`
  - normalized scheduler comment
- `RESOURCE_OOM`
  - first normalized OOM evidence line
- `RESOURCE_WALLTIME`
  - first normalized walltime evidence line
- `ENV_IMPORT_ERROR`
  - missing module name or canonical import-error line
- `ENV_PATH_ERROR`
  - normalized missing path or failing command line
- `NCCL_FAILURE`
  - first normalized NCCL failure line
- `MPI_BOOTSTRAP`
  - first normalized MPI/bootstrap failure line
- `NO_HEARTBEAT`
  - fixed fingerprint `no-heartbeat`
- `UNKNOWN`
  - stable hash of the normalized scheduler comment plus first few stderr lines

The exact normalization helpers are implementation details, but they must remove trivial timestamp or spacing noise so repeated scans of the same failure reuse the same incident row.

## Incident Persistence

The current `incidents` table is missing `updated_at`, which is required for upsert semantics.

Phase 4A should extend it to include:

```sql
updated_at TEXT NOT NULL
```

The effective incident shape becomes:

```sql
incidents(
  incident_id TEXT PRIMARY KEY,
  run_id TEXT,
  job_id TEXT,
  severity TEXT NOT NULL,
  category TEXT NOT NULL,
  fingerprint TEXT,
  evidence_json TEXT NOT NULL,
  auto_action TEXT,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  resolved_at TEXT
)
```

Phase 4A only needs:

- `OPEN`
- `RESOLVED`

But it does not auto-resolve incidents yet.

Upsert behavior:

- first match: create new `OPEN` incident
- repeated match on same key: update `evidence_json` and `updated_at`
- clean later scan: do not auto-close the incident

## Evidence Payload

`evidence_json` should include enough material for operator review and later retry decisions.

At minimum:

- `scan_time`
- `snapshot_dir`
- `qstat_comment`
- `job_state`
- `exec_host`
- matched evidence lines
- classifier rule name

It should remain structured JSON, not free-form markdown.

## CLI Surface

### `incident scan`

```bash
autoresearch incident scan --job-id <job_id>
```

Output should clearly indicate:

- whether live fetch succeeded or local snapshot fallback was used
- whether an incident was created, updated, or not found
- category and severity when an incident exists

### `incident list`

```bash
autoresearch incident list
```

Default behavior:

- list open incidents only
- newest first

Each row should include enough context to scan quickly:

- `incident_id`
- `job_id`
- `category`
- `severity`
- `status`
- `updated_at`

### `incident summarize`

```bash
autoresearch incident summarize
```

This command should print:

- grouped counts by category
- highest-severity open incidents first
- a short one-paragraph or bullet summary per top incident

Phase 4A does not require markdown file generation. It only needs operator-visible CLI output.

## Testing Strategy

Phase 4A tests should cover:

- bridge-attached fetch path
- fallback to local snapshot when the bridge is unavailable
- deterministic classification for each supported category
- fingerprint stability for repeated scans
- incident upsert behavior
- `NO_HEARTBEAT` proxy rule over repeated scans
- `incident list`
- `incident summarize`

Recommended fixtures:

- `qstat` JSON with filesystem-unavailable comment
- stdout/stderr tails for:
  - OOM
  - walltime
  - import error
  - path error
  - NCCL failure
  - MPI bootstrap failure

## Acceptance Criteria

Phase 4A is done when:

- `incident scan --job-id <job_id>` can fetch live evidence through an attached bridge
- the same command can fall back to local snapshots when the bridge is unavailable
- repeated scans of the same failure update an existing incident instead of duplicating it
- deterministic classification covers the approved fixed taxonomy
- `incident list` and `incident summarize` produce operator-usable output
- tests pass locally

## Not For This Phase

To keep the phase narrow, do not add:

- background watcher services
- safe retry execution
- approval commands
- automatic incident resolution
- additional telemetry inputs
- LLM-based classification
