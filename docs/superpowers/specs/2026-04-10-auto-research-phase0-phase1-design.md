# Auto Research Phase 0-1 Design

## Objective

Build the first usable slice of `auto-research` as a lab-server control plane project under [`/home/zhiqingzhong/agent system/auto-research`](/home/zhiqingzhong/agent%20system/auto-research). This slice covers:

- repository scaffold
- Codex-facing project rules
- configuration loading
- SQLite initialization
- explicit schemas and models for the core registry entities
- a minimal CLI for `db init`, `run create`, and `run list`
- a project-local resume note so future Codex sessions can be resumed from the same working directory more predictably

This design deliberately stops before the ALCF bridge, Polaris PBS executor, incident watcher, paper radar, or reporting jobs.

## Scope

### In Scope

- Create the `auto-research` Python project skeleton
- Use Python 3.11+ with Typer CLI
- Persist state in SQLite with WAL enabled
- Define the initial tables for `runs`, `jobs`, `incidents`, and `decisions`
- Implement local configuration loading from YAML and environment variables
- Implement a `run` registry with create and list operations
- Add focused unit tests for the implemented slice
- Add a project-local session resume document

### Out of Scope

- FastAPI server runtime
- APScheduler jobs
- OpenSSH bridge logic
- PBS submission or parsing
- incident classification and retry actions
- paper ingestion
- daily brief generation
- systemd service wiring beyond static example files

## Constraints

- The control plane runs on a lab server, not on Polaris login nodes
- Polaris is a remote executor only
- ALCF-managed project data must live under `/eagle/lc-mpi/Zhiqing/auto-research/`
- MFA is never automated or bypassed
- SSH integration later must shell out to OpenSSH rather than using Paramiko
- Modules should stay small, typed, and explicit
- Every future state transition should be loggable and testable

## Recommended Approach

Three implementation approaches were considered:

1. Phase-first local foundation
   - Build Phase 0 and Phase 1 only, with a working CLI, DB, and tests
   - Best local verification, lowest risk, cleanest base for later Polaris work

2. Full-tree scaffold
   - Create the entire future repository structure at once with many placeholders
   - Faster visual completeness, but creates noise and weakens signal in reviews and tests

3. Vertical slice to probe Polaris immediately
   - Build bridge plus probe submission first
   - Better end-to-end realism, but poor local verification and too many moving parts for the first cut

The approved approach is option 1.

## Repository Layout For Phase 0-1

Only the directories and files needed by the first slice are created now.

```text
auto-research/
├── AGENTS.md
├── PLANS.md
├── README.md
├── SESSION_RESUME.md
├── pyproject.toml
├── .gitignore
├── .codex/
│   └── config.toml
├── conf/
│   ├── app.yaml
│   ├── polaris.yaml
│   ├── projects.yaml
│   ├── retry_policy.yaml
│   └── topics.yaml
├── docs/
│   ├── architecture.md
│   ├── runbook.md
│   └── superpowers/
│       └── specs/
│           └── 2026-04-10-auto-research-phase0-phase1-design.md
├── deploy/
│   ├── env/
│   │   └── autoresearch.env.example
│   └── systemd/
│       └── autoresearch.service
├── src/
│   └── autoresearch/
│       ├── __init__.py
│       ├── cli.py
│       ├── db.py
│       ├── logging.py
│       ├── models.py
│       ├── paths.py
│       ├── schemas.py
│       ├── settings.py
│       └── runs/
│           ├── __init__.py
│           └── registry.py
└── tests/
    ├── test_cli.py
    ├── test_db.py
    ├── test_settings.py
    └── test_run_registry.py
```

This layout keeps later extension points visible without forcing early placeholder modules for bridge, executor, incidents, papers, memory, or reports.

## Architecture

### Configuration Layer

`settings.py` reads `conf/app.yaml` and applies environment overrides. It produces a typed settings object containing:

- application directories
- database path
- local state and log locations
- default remote root for future Polaris integration

`paths.py` centralizes path derivation so the rest of the code does not hardcode filesystem conventions.

### Persistence Layer

`db.py` owns:

- opening SQLite connections
- enabling WAL mode
- creating tables if absent
- returning row-oriented query helpers suitable for small-scale v0 usage

The database schema includes all four core tables from the broader design, even though Phase 1 only actively mutates `runs`. That keeps future migrations smaller and makes the data model explicit from the start.

### Domain Models

`models.py` and `schemas.py` define explicit typed structures for:

- `RunRecord`
- `JobRecord`
- `IncidentRecord`
- `DecisionRecord`
- request/response shapes for `run create` and `run list`

The immediate goal is not ORM complexity. The goal is to avoid loose dicts, preserve field intent, and make CLI and tests deterministic.

### Registry Layer

`runs/registry.py` implements the first application service:

- create a run
- list runs in reverse chronological order

For this phase, `run create` only registers metadata. It does not execute commands, capture subprocess output, or transition lifecycle state beyond the initial registry write. This keeps the run registry decoupled from future command wrappers and remote executors.

### CLI Layer

`cli.py` exposes the minimum stable surface:

```bash
autoresearch db init
autoresearch run create --kind local-debug --project demo
autoresearch run list
```

This gives a verifiable local foundation without pretending the system can already manage remote jobs.

## Session Resume Handling

The Codex session backend remains in `~/.codex/sessions/`, so this project will not try to relocate internal session storage. Instead, the repository includes `SESSION_RESUME.md` with:

- the expected working directory
- the recommended `codex resume` usage pattern from inside the project directory
- any project-specific notes needed to continue work safely

This matches Codex's current behavior, where session resumption is filtered by current working directory, and avoids corrupting Codex-managed state.

## Data Flow

The first implemented data flow is intentionally short:

1. User runs `autoresearch db init`
2. CLI loads settings and initializes the SQLite database
3. User runs `autoresearch run create --kind ... --project ...`
4. CLI validates arguments and calls the registry service
5. Registry generates `run_id`, timestamps, and initial status
6. Registry writes a row to `runs`
7. User runs `autoresearch run list`
8. Registry returns persisted rows for display

This gives a complete local loop with real persistence and no remote dependencies.

## Testing Strategy

Phase 0-1 tests should cover only what is actually implemented:

- `test_settings.py`
  - loads `conf/app.yaml`
  - environment overrides win where expected
  - derived paths are stable
- `test_db.py`
  - initializes the database
  - verifies tables exist
  - verifies WAL is enabled
- `test_run_registry.py`
  - creates runs
  - lists runs in expected order
  - persists required fields
- `test_cli.py`
  - `db init` succeeds
  - `run create` succeeds
  - `run list` renders expected records

Tests use temporary directories and temporary database paths so they do not mutate user state.

## Error Handling

Phase 0-1 error handling stays narrow and explicit:

- missing or unreadable config file: fail with a clear CLI error
- database path parent missing: create it if it belongs to local app paths
- invalid required CLI arguments: let Typer reject them
- duplicate primary keys: impossible under generated `run_id` in normal flow, but still surfaced as explicit database errors

Later command wrappers, remote execution, and scheduler concerns are deliberately deferred.

## Verification

The implementation is complete for this slice when all of the following are true:

- `pytest` passes
- `python -m autoresearch.cli --help` works
- `python -m autoresearch.cli db init` creates the database
- `python -m autoresearch.cli run create --kind local-debug --project demo` writes a row
- `python -m autoresearch.cli run list` shows the stored run
- the repository contains `README.md`, `AGENTS.md`, `PLANS.md`, and `SESSION_RESUME.md`

## Risks And Non-Goals

- Creating all future package namespaces now would produce placeholder churn, so this design avoids it
- Implementing command execution now would couple registry logic to runtime concerns too early
- Moving or rewriting Codex's own session storage would add fragility with no real benefit

## Implementation Handoff

After this spec is approved, the next step is to write a concrete implementation plan for Phase 0-1 and then scaffold the project accordingly.
