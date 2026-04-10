# Architecture

`auto-research` currently has two implemented layers:

1. The local control-plane foundation from Phase 0 + Phase 1
2. The narrow ALCF bridge from Phase 2

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
