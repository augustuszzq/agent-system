# Phase 6A Daily Brief Design

## Goal

Add a local `autoresearch report daily` command that generates a daily Markdown brief from the current SQLite state, prints it to stdout, and writes it to `state/reports/daily/YYYY-MM-DD.md`.

This phase does not add schedulers, timers, email delivery, or report history management beyond writing the current file to disk.

## Scope

Phase 6A includes:

- a local daily brief builder
- a fixed four-section Markdown report
- stdout output plus default file output
- deterministic tests around report content and CLI behavior

Phase 6A excludes:

- APScheduler or systemd timer integration
- automatic report sending
- weekly reports
- paper ingestion or paper ranking work

## Report Shape

The first daily brief always renders the same four sections:

1. `Paper Delta`
2. `Run Status`
3. `Incident Summary`
4. `Pending Decisions`

The structure stays fixed even when data is missing.

## Data Sources

The report reads directly from the existing local SQLite database:

- `runs`
- `jobs`
- `incidents`
- `retry_requests`
- `decisions`

`papers` is not yet implemented in the current runtime schema. Phase 6A must therefore treat paper data as unavailable without failing report generation.

## Output Rules

### CLI

Phase 6A adds one command:

```bash
autoresearch report daily
```

The first version has no extra flags. Its behavior is fixed:

- use the current UTC date for the report title and output path
- print the rendered Markdown to stdout
- write the same Markdown to `state/reports/daily/YYYY-MM-DD.md`
- overwrite the file for that date if it already exists

### File Path

Reports are written under:

```text
<state_dir>/reports/daily/YYYY-MM-DD.md
```

For the current repository defaults this resolves to:

```text
state/reports/daily/YYYY-MM-DD.md
```

## Section Semantics

### Paper Delta

Phase 6A keeps this section in place even though the paper pipeline is not present yet.

It must render these lines when paper data is unavailable:

- `New papers scanned: not available yet`
- `Top relevant: not available yet`
- `Deep read today: not available yet`
- `Reproduce candidate: not available yet`

This is a deliberate placeholder with explicit wording, not an omitted section.

### Run Status

This section is computed from current run and retry state.

Fields:

- `Active runs`
  - count runs whose status is in a running set
  - Phase 6A uses the conservative set: `CREATED`, `SUBMITTED`, `QUEUED`, `RUNNING`
- `Finished overnight`
  - count runs whose status is `SUCCEEDED` and whose `ended_at` is within the last 24 hours
- `Failed`
  - count runs whose status is `FAILED` and whose `ended_at` is within the last 24 hours
- `Auto-retried`
  - count retry requests with `execution_status = SUBMITTED` and `executed_at` within the last 24 hours
- `Awaiting approval`
  - count retry requests with `approval_status = PENDING`

The report does not infer extra lifecycle states beyond what is already persisted.

### Incident Summary

This section is built from open incidents only.

It includes:

- category counts
- up to 3 top incidents

Top incidents are ordered by:

1. severity order: `CRITICAL`, `HIGH`, `MEDIUM`
2. most recent `updated_at`

Each top incident block includes:

- `incident_id`
- `category`
- `severity`
- `run_id`
- `job_id`
- a short evidence line

The evidence line should prefer existing normalized evidence fields when present:

- `qstat_comment`
- first element of `matched_lines`
- otherwise a compact fallback such as `no evidence summary available`

### Pending Decisions

Phase 6A keeps this section narrow and deterministic.

It is derived only from pending retry requests:

- include at most 3 items
- order by oldest `created_at` first
- render operator-facing action text such as:
  - `Approve retry <retry_request_id> for incident <incident_id>`

Phase 6A does not invent a broader decision backlog model.

## Architecture

Phase 6A uses a thin CLI over a dedicated report builder.

### New package

Add:

```text
src/autoresearch/reports/
├── __init__.py
├── daily.py
└── templates/
    └── daily_brief.md.j2
```

Responsibilities:

- `daily.py`
  - query database state
  - build a structured report context
  - render Markdown
  - write the daily file
- template
  - render the fixed Markdown structure only
  - avoid business logic beyond simple loops and conditionals

### CLI integration

`src/autoresearch/cli.py` adds a `report` command group and a `daily` subcommand.

The CLI remains thin:

- load settings
- call the daily report builder
- print the generated Markdown
- print the output path if needed by the current CLI pattern

## Dependencies

Phase 6A adds `Jinja2` as the template engine dependency because the project spec already selected it for reporting.

## Testing

Phase 6A adds two test layers.

### Builder tests

Create a dedicated report test module that seeds SQLite state and validates:

- fixed four-section output
- `Paper Delta` fallback wording when paper data is unavailable
- `Run Status` counts
- `Incident Summary` counts and top-incident ordering
- `Pending Decisions` limited to 3 pending retry requests
- file output path and file contents

### CLI tests

Add CLI coverage that verifies:

- `autoresearch report daily` exits successfully
- stdout contains the report title and key sections
- the daily report file is written under `state/reports/daily/`

## Done Criteria

Phase 6A is done when:

- `autoresearch report daily` exists
- it prints Markdown and writes `state/reports/daily/YYYY-MM-DD.md`
- the report always contains the four required sections
- `Paper Delta` explicitly reports `not available yet` when paper data is unavailable
- run, incident, and pending-retry summaries are deterministic
- tests pass
- architecture and runbook docs mention the new report command
