# Architecture

`auto-research` currently has three implemented layers:

1. The local control-plane foundation from Phase 0 + Phase 1
2. The narrow ALCF bridge from Phase 2
3. The local PBS executor from Phase 3A

Phase 3B adds a narrow remote probe loop on top of those layers. It is intentionally operational, not generalized.

## Local PBS executor

Phase 3A adds a local PBS executor layer that models Polaris PBS behavior without live submission. It is used to build, render, and inspect job records locally so the control plane can validate PBS wiring before any real bridge-driven submission exists.

Current executor and registry modules:

- `src/autoresearch/executor/polaris.py`
  - normalizes `filesystems=eagle` and `place=scatter`
  - derives stdout, stderr, and submit-script paths under the configured `remote_root` (default `/eagle/lc-mpi/Zhiqing/auto-research/`)
- `src/autoresearch/executor/pbs.py`
  - renders PBS scripts
  - parses `qsub`
  - parses `qstat -f`
  - parses `qstat -fF JSON`
- `src/autoresearch/runs/registry.py`
  - persists draft job records
  - stores scheduler metadata and state on existing rows

Phase 3A stays local-only. It does not call the ALCF bridge for real submission.

## Phase 3B remote probe workflow

Phase 3B connects the bridge to a single managed remote root on Eagle and exposes only the commands needed to bootstrap and exercise the built-in probe job.

The workflow is:

1. Use bridge-level `exec`, `copy-to`, and `copy-from` to operate on the managed remote root.
2. Bootstrap the remote Eagle root with `autoresearch remote bootstrap`.
3. Submit the built-in probe with `autoresearch job submit-probe`.
4. Poll the real PBS job with `autoresearch job poll --job-id <job_id>`.

Implementation boundaries:

- `src/autoresearch/bridge/remote_exec.py`
  - wraps remote command execution and file transfer behind the attached bridge
  - applies lexical path checks so remote paths stay within the configured remote root; it does not resolve remote symlinks or other runtime filesystem indirections
- `src/autoresearch/bridge/remote_fs.py`
  - creates the managed Eagle root layout
  - writes the managed `README.remote.md` and built-in probe entrypoint
- `src/autoresearch/cli.py`
  - exposes `bridge exec`, `bridge copy-to`, `bridge copy-from`, `remote bootstrap`, `job submit-probe`, and `job poll`
  - builds the probe submission request
  - submits the probe with real `qsub`
  - polls the submitted probe with real `qstat -fF JSON`
- `src/autoresearch/executor/polaris.py`
  - builds the built-in probe request from configured probe settings
  - keeps the probe submission aligned with the Polaris job layout

The remote root is still managed, not open-ended. Phase 3B only bootstraps the Eagle directory tree needed for the probe and only submits the built-in probe job.

Out of scope for Phase 3B:

- arbitrary remote entrypoints
- generalized remote job submission
- arbitrary file operations outside the managed remote root
- non-probe PBS workflows

## Phase 4A manual incident detection

Phase 4A adds a manual, operator-triggered incident scan path:

1. `autoresearch incident scan --job-id <job_id>`
2. If the bridge is attached, attempt to fetch fresh `qstat -fF JSON` output plus stdout/stderr tails.
3. When live capture succeeds, persist the evidence under `state/incidents/<job_id>/<scan_ts>/`.
4. Normalize and classify the evidence deterministically.
5. Upsert the matching incident by `job_id + category + fingerprint`; new matches are `OPEN`, and repeat detection of a matching resolved incident reopens it so it becomes visible in `OPEN` incident views again.

If the bridge is detached or stale, or if live capture or snapshot persistence fails, the scan falls back to the newest local snapshot already stored for that job. Phase 4A does not auto-resolve incidents and does not retry scans.

## Local foundation

The local foundation owns:

- typed settings loaded from `conf/app.yaml` and `conf/polaris.yaml`
- SQLite bootstrap with WAL enabled
- the initial run-registry CLI for `db` and `run`

This layer stays local to the lab server. It does not assume Polaris is reachable.

## ALCF bridge

The bridge is intentionally narrow. Phase 2 exposed only master-management commands; Phase 3B adds a small operational surface on top of that same attachment model.

Current bridge modules:

- `src/autoresearch/settings.py`
  - loads typed bridge settings from `conf/polaris.yaml`
- `src/autoresearch/schemas.py`
  - defines `CommandResult` and `BridgeStatusResult`
- `src/autoresearch/bridge/ssh_master.py`
  - runs `ssh -MNf <alias>`, `ssh -O check <alias>`, and `ssh -O exit <alias>`
  - captures stdout, stderr, exit code, and duration
- `src/autoresearch/bridge/health.py`
  - maps raw command outcomes into `ATTACHED`, `DETACHED`, or `STALE`
- `src/autoresearch/cli.py`
  - exposes `autoresearch bridge attach|check|status|detach`

## Bridge state model

Phase 2 uses a small explicit state model:

- `ATTACHED`
  - `ssh -O check <alias>` succeeded
- `DETACHED`
  - no active master is attached, or a detach command confirms there is no active master
- `STALE`
  - the control socket or check result is abnormal and needs operator attention

`ATTACHING` is intentionally not part of the runtime flow yet. Bridge operations are still explicit CLI actions.
