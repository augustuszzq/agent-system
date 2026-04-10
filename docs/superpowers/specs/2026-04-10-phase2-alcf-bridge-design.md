# Auto Research Phase 2 ALCF Bridge Design

## Objective

Build the Phase 2 bridge layer for `auto-research` so the control plane can manage a real OpenSSH control-master connection to Polaris through the configured relay alias. This phase adds a narrow CLI and bridge module set for:

- `attach`
- `check`
- `status`
- `detach`

The bridge must work with a real Polaris login relay, but it must not automate MFA, reimplement SSH, or expand into remote execution and PBS submission.

## Approved Direction

Three approaches were considered:

1. Real bridge plus narrow CLI
2. Real bridge plus minimal remote probe interface
3. Bridge plus file transfer and remote command helpers

The approved approach is option 1.

This means Phase 2 will:

- manage only the SSH control-master lifecycle
- expose only bridge-focused CLI commands
- detect bridge health and surface explicit states
- keep real network activity behind explicit user-invoked commands

This phase will not:

- run arbitrary remote commands through a public interface
- submit PBS jobs
- upload or download files
- perform any background auto-reconnect

## Scope

### In Scope

- Add a `bridge` command group to the CLI
- Load Polaris bridge configuration from `conf/polaris.yaml`
- Construct OpenSSH commands for control socket management
- Execute real `ssh` commands for `attach`, `check`, and `detach`
- Detect and report `DETACHED`, `ATTACHED`, and `STALE`
- Capture command stdout, stderr, exit code, and duration in explicit result objects
- Add unit and CLI tests using fake command runners
- Update docs and runbook for bridge usage

### Out of Scope

- Paramiko or any SSH library-based implementation
- PBS submission
- generic remote command execution
- file copy helpers
- automatic MFA handling
- automatic reconnect after bridge loss
- systemd automation around bridge lifecycle

## Constraints

- The control plane runs on the lab server, not on Polaris login nodes
- Polaris is a remote executor only
- MFA must stay manual
- The bridge may reuse an existing authenticated OpenSSH master connection, but may not attempt to recreate MFA state silently
- All bridge actions must shell out to system `ssh`
- All subprocess wrappers must preserve stdout, stderr, exit code, and duration
- The bridge must degrade cleanly if the control socket disappears or becomes stale

## Repository Changes For Phase 2

```text
auto-research/
├── conf/
│   └── polaris.yaml                 # expanded bridge config
├── docs/
│   ├── architecture.md              # bridge architecture section
│   └── runbook.md                   # bridge usage and failure handling
├── src/
│   └── autoresearch/
│       ├── cli.py                   # add bridge command group
│       ├── schemas.py               # command result + bridge result types
│       ├── settings.py              # load bridge config
│       └── bridge/
│           ├── __init__.py
│           ├── health.py
│           └── ssh_master.py
└── tests/
    ├── test_bridge.py
    └── test_cli.py                  # extend with bridge command coverage
```

No other packages are introduced in this phase.

## Configuration Model

`conf/polaris.yaml` will become the source of truth for bridge-specific settings. The minimum required fields are:

```yaml
bridge:
  alias: polaris-relay
  host: polaris-login-04.hsn.cm.polaris.alcf.anl.gov
  user: <ALCF_USERNAME>
  control_path: ~/.ssh/cm-%C
  server_alive_interval: 60
  server_alive_count_max: 3
  connect_timeout: 15
```

`settings.py` will parse this into a typed bridge settings object. The control plane will trust the configured SSH alias as the primary command target, while the other values exist for visibility and future validation. Phase 2 will not attempt to rewrite `~/.ssh/config`, but it will report the alias and control path it expects.

## Module Responsibilities

### `src/autoresearch/bridge/ssh_master.py`

This module owns bridge command construction and execution:

- build the attach command: `ssh -MNf <alias>`
- build the check command: `ssh -O check <alias>`
- build the detach command: `ssh -O exit <alias>`
- execute those commands through a thin subprocess wrapper

This module does not classify bridge state. It only reports command results and any observable filesystem facts needed by the health layer.

### `src/autoresearch/bridge/health.py`

This module maps raw command outcomes into bridge states:

- `DETACHED`
- `ATTACHED`
- `STALE`

State rules for this phase:

- `ATTACHED`
  - `ssh -O check <alias>` returns success
- `DETACHED`
  - control socket is absent and check fails in the expected detached pattern
  - or check fails with a clear "no master running" style result
- `STALE`
  - control socket path exists but `check` fails
  - or command output indicates an abnormal bridge condition that is not a clean detach

`ATTACHING` is intentionally not part of the runtime flow in Phase 2. It may be introduced later if asynchronous bridge management is added, but it is not needed for explicit CLI-driven operations.

### `src/autoresearch/schemas.py`

This file will gain explicit bridge-related types such as:

- `CommandResult`
- `BridgeStatusResult`

These types exist so CLI rendering and tests can depend on stable fields instead of free-form dicts.

### `src/autoresearch/settings.py`

This file will be extended to load typed bridge settings from `conf/polaris.yaml` and expose them alongside the existing app settings.

### `src/autoresearch/cli.py`

This file will gain a `bridge` command group with:

```bash
autoresearch bridge attach
autoresearch bridge check
autoresearch bridge status
autoresearch bridge detach
```

The CLI will remain a thin layer over typed services.

## CLI Semantics

### `autoresearch bridge attach`

- Executes `ssh -MNf <alias>`
- Reports success as `ATTACHED`
- If attach fails, prints the command failure details clearly
- Does not silently retry

### `autoresearch bridge check`

- Executes `ssh -O check <alias>`
- Prints a direct health result
- Intended as the lowest-level operator-facing probe

### `autoresearch bridge status`

- Produces a normalized summary based on the health layer
- Intended as the stable, human-readable command
- Should report the bridge state, alias, and a concise explanation

### `autoresearch bridge detach`

- Executes `ssh -O exit <alias>`
- If no master exists, reports that as a detached state rather than pretending success

## Data Flow

The runtime flow for bridge operations is intentionally small:

1. CLI command loads app and bridge settings
2. CLI invokes the bridge service
3. `ssh_master.py` builds and runs the OpenSSH command
4. The subprocess wrapper captures stdout, stderr, exit code, and duration
5. `health.py` maps the raw result into a bridge state when needed
6. CLI renders the state and key evidence for the operator

This keeps Phase 2 directly aligned with the bridge lifecycle and nothing more.

## Error Handling

Phase 2 must handle these failure modes explicitly:

- SSH binary missing
- alias not configured or invalid
- first attach requires MFA and the user has not completed it yet
- control socket missing
- control socket exists but no longer maps to a valid master connection
- `ssh -O exit` on an already detached bridge

The bridge layer must never hide these failures behind generic exceptions. The CLI should surface what command ran, whether it succeeded, and what state the bridge is believed to be in.

## Testing Strategy

Tests stay local and deterministic. They do not make live Polaris connections.

### `tests/test_bridge.py`

Cover:

- attach command construction
- check command construction
- detach command construction
- mapping of fake command results to `DETACHED`, `ATTACHED`, and `STALE`
- abnormal command outputs that should classify as `STALE`

### `tests/test_cli.py`

Extend CLI coverage to include:

- `bridge attach`
- `bridge check`
- `bridge status`
- `bridge detach`

These tests should use fake runners or injectable bridge services, not live SSH.

## Verification

Phase 2 is complete when all of the following are true:

- `autoresearch bridge attach|check|status|detach` exist
- command construction matches the approved OpenSSH control-master pattern
- state mapping for `DETACHED`, `ATTACHED`, and `STALE` is covered by tests
- `pytest` passes
- `python -m autoresearch.cli bridge --help` works
- docs explain manual bridge usage and failure handling

## Risks And Non-Goals

- Phase 2 intentionally stops short of remote command execution so it does not prematurely define interfaces the PBS executor will later constrain
- bridge state classification is necessarily heuristic around stale sockets, so tests should focus on explicit command-result patterns
- real attach still depends on manual MFA; that is not a bug and must remain true

## Implementation Handoff

After this spec is approved, the next step is to write a concrete implementation plan for the bridge package and CLI commands, then execute it with TDD in a fresh worktree.
