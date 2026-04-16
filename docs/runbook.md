# Runbook

## Local bootstrap

1. Create a virtual environment.
2. Install with `pip install -e .[dev]`.
3. Initialize the database.
4. Create and list runs through the CLI.

## Bridge configuration

Bridge settings live in `conf/polaris.yaml`. Phase 2 expects an SSH alias such as `polaris-relay` to already exist in `~/.ssh/config`.

Phase 2 does not rewrite SSH config and does not automate MFA.

## Phase 3B remote probe workflow

Use the bridge and remote CLI from the repo root. These commands require an already attached bridge:

```bash
python -m autoresearch.cli bridge exec -- "pwd"
python -m autoresearch.cli bridge copy-to --src local.txt --dst <remote_root>/manifests/local.txt
python -m autoresearch.cli bridge copy-from --src <remote_root>/runs/<run_id>/stdout.log --dst /tmp/probe.log
python -m autoresearch.cli remote bootstrap
python -m autoresearch.cli job submit-probe
python -m autoresearch.cli job poll --job-id <job_id>
```

Command behavior:

1. `bridge exec -- "pwd"`
   Runs a single remote command through the attached bridge. This is the narrow remote exec path used by operators and by the probe workflow.
2. `bridge copy-to --src local.txt --dst <remote_root>/manifests/local.txt`
   Uploads a local file into the managed remote root.
3. `bridge copy-from --src <remote_root>/runs/<run_id>/stdout.log --dst /tmp/probe.log`
   Downloads a file from the managed remote root to the local machine.
4. `remote bootstrap`
   Creates the managed Eagle root layout and the built-in probe entrypoint if they are missing.
5. `job submit-probe`
   This is the first real remote submission path. It only submits the built-in probe job, and it uses real `qsub` against the managed remote root.
6. `job poll --job-id <job_id>`
   Queries the live PBS job with real `qstat -fF JSON` and updates the local job record with the current probe state.

## Phase 4A incident workflow

Use the incident commands from the repo root:

```bash
python -m autoresearch.cli incident scan --job-id <job_id>
python -m autoresearch.cli incident list
python -m autoresearch.cli incident summarize
```

Command behavior:

1. `incident scan --job-id <job_id>`
   Tries to collect fresh incident evidence for the job. When live capture succeeds, it writes a snapshot under `state/incidents/<job_id>/<scan_ts>/`, then normalizes, classifies, and upserts the result into the incident registry. Existing resolved incidents that are detected again are reopened and become visible in `OPEN` incident views again; only new matches are created as `OPEN`.
2. `incident list`
   Prints the current open incidents.
3. `incident summarize`
   Prints a compact summary of open incidents.

If the bridge is unavailable, detached, or stale, or if live capture or snapshot persistence fails, `incident scan` falls back to the newest local snapshot already stored for that job. Phase 4A does not auto-resolve incidents or retry scans.

## Local PBS executor commands

Use the local PBS helpers from the repo root:

```bash
python -m autoresearch.cli job list
python -m autoresearch.cli job render-pbs --run-id run_demo --project demo --queue debug --walltime 01:00:00 --entrypoint-path /path/from/your/configured/remote_root/jobs/run_demo/entrypoint.sh
```

`job list` prints the local registry view of draft rows and rows with scheduler metadata and state. `job render-pbs` prints the rendered PBS script only; it does not submit anything. Real submission remains a Phase 3B task.

`job submit-probe` is the first live submission path in the system, and it is intentionally narrow: it only submits the built-in probe. Generalized job submission remains out of scope.

## Failure handling

Expected operator-visible cases:

- `DETACHED`
  - there is no live control master attached
- `STALE`
  - the control socket or master state looks abnormal and should be reattached manually
- missing `ssh`
  - the command wrapper reports that the `ssh` executable is not available

If the bridge becomes detached or stale, the control plane should stop at the bridge boundary. Do not attempt to automate MFA recovery.

## Phase 4B safe retry workflow

Use the retry commands from the repo root:

```bash
python -m autoresearch.cli retry request --incident-id <incident_id>
python -m autoresearch.cli retry list
python -m autoresearch.cli retry approve --retry-request-id <retry_request_id> --reason "filesystem recovered"
python -m autoresearch.cli retry reject --retry-request-id <retry_request_id> --reason "not safe to retry"
python -m autoresearch.cli retry execute --retry-request-id <retry_request_id>
```

Command behavior:

1. `retry request --incident-id <incident_id>`
   Creates a retry request only when the incident exists, is `OPEN`, and its category is in `conf/retry_policy.yaml`. The first Phase 4B policy only allows `FILESYSTEM_UNAVAILABLE` and only supports `RETRY_SAME_CONFIG`.
2. `retry list`
   Prints the current retry-request registry view, including request state and any submitted result job id.
3. `retry approve --retry-request-id <retry_request_id> --reason "..."`
   Marks a pending request `APPROVED`, stores the operator reason, and appends an audit decision row.
4. `retry reject --retry-request-id <retry_request_id> --reason "..."`
   Marks a pending request `REJECTED`, stores the operator reason, and appends an audit decision row.
5. `retry execute --retry-request-id <retry_request_id>`
   Submits an approved request through the same live probe submission path used by `job submit-probe`, but as a new `probe-retry` run and job. Successful execution writes the new run/job/PBS ids back onto the retry request and appends an audit decision row.

If execution fails before a new PBS job is created, the retry request is marked `FAILED` and the last error is retained for audit. Phase 4B does not auto-retry the retry request itself.
