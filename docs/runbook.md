# Runbook

## Local bootstrap

1. Create a virtual environment.
2. Install with `pip install -e .[dev]`.
3. Initialize the database.
4. Create and list runs through the CLI.

## Bridge configuration

Bridge settings live in `conf/polaris.yaml`. Phase 2 expects an SSH alias such as `polaris-relay` to already exist in `~/.ssh/config`.

Phase 2 does not rewrite SSH config and does not automate MFA.

## Bridge commands

Use the bridge CLI from the repo root:

```bash
python -m autoresearch.cli bridge attach
python -m autoresearch.cli bridge check
python -m autoresearch.cli bridge status
python -m autoresearch.cli bridge detach
```

Command behavior:

1. `bridge attach`
   Creates the OpenSSH control master with `ssh -MNf <alias>`. The first successful attach still requires manual MFA.
2. `bridge check`
   Returns success only when the bridge is `ATTACHED`. Any other state exits nonzero.
3. `bridge status`
   Prints the normalized bridge state plus a short explanation without changing anything.
4. `bridge detach`
   Closes the control master with `ssh -O exit <alias>`. If no master exists, the CLI reports `DETACHED` instead of pretending success.

## Failure handling

Expected operator-visible cases:

- `DETACHED`
  - there is no live control master attached
- `STALE`
  - the control socket or master state looks abnormal and should be reattached manually
- missing `ssh`
  - the command wrapper reports that the `ssh` executable is not available

If the bridge becomes detached or stale, the control plane should stop at the bridge boundary. Do not attempt to automate MFA recovery.
