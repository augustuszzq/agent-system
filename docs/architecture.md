# Architecture

`auto-research` currently has three implemented layers:

1. The local control-plane foundation from Phase 0 + Phase 1
2. The narrow ALCF bridge from Phase 2
3. The local PBS executor from Phase 3A

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

## Local foundation

The local foundation owns:

- typed settings loaded from `conf/app.yaml` and `conf/polaris.yaml`
- SQLite bootstrap with WAL enabled
- the initial run-registry CLI for `db` and `run`

This layer stays local to the lab server. It does not assume Polaris is reachable.

## ALCF bridge

The bridge is intentionally narrow. It does not submit jobs, copy files, or execute arbitrary remote commands yet.

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
