# Auto Research Phase 0-1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Phase 0 + Phase 1 local foundation for `auto-research`: project scaffold, typed settings, SQLite initialization, and `run create/list` CLI commands with tests.

**Architecture:** Keep the first slice local-only and explicitly typed. `settings.py` owns configuration loading, `db.py` owns SQLite/WAL/bootstrap, `runs/registry.py` owns run persistence, and `cli.py` only coordinates these services. Avoid any remote bridge or command execution behavior in this phase.

**Tech Stack:** Python 3.11+, Typer, PyYAML, SQLite, pytest

---

### Task 1: Package Skeleton And Baseline CLI

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `src/autoresearch/__init__.py`
- Create: `src/autoresearch/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing CLI help test**

```python
from typer.testing import CliRunner

from autoresearch.cli import app


runner = CliRunner()


def test_cli_help_shows_top_level_commands() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "db" in result.stdout
    assert "run" in result.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py::test_cli_help_shows_top_level_commands -v`
Expected: FAIL because `autoresearch.cli` does not exist yet

- [ ] **Step 3: Write the minimal package and CLI implementation**

`pyproject.toml`

```toml
[build-system]
requires = ["setuptools>=69", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "auto-research"
version = "0.1.0"
description = "Lab-server control plane for auto research workflows."
requires-python = ">=3.11"
dependencies = [
  "pyyaml>=6.0",
  "typer>=0.12,<1.0",
]

[project.optional-dependencies]
dev = [
  "pytest>=8.0",
]

[project.scripts]
autoresearch = "autoresearch.cli:main"

[tool.setuptools]
package-dir = {"" = "src"}

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

`src/autoresearch/__init__.py`

```python
__all__ = ["__version__"]

__version__ = "0.1.0"
```

`src/autoresearch/cli.py`

```python
import typer


app = typer.Typer(help="Auto Research control plane CLI.")
db_app = typer.Typer(help="Database commands.")
run_app = typer.Typer(help="Run registry commands.")

app.add_typer(db_app, name="db")
app.add_typer(run_app, name="run")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
```

`.gitignore`

```gitignore
.pytest_cache/
.venv/
__pycache__/
*.pyc
*.sqlite
*.sqlite-shm
*.sqlite-wal
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli.py::test_cli_help_shows_top_level_commands -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml .gitignore src/autoresearch/__init__.py src/autoresearch/cli.py tests/test_cli.py
git commit -m "feat: scaffold package and baseline cli"
```

### Task 2: Typed Settings And Config Files

**Files:**
- Create: `conf/app.yaml`
- Create: `conf/polaris.yaml`
- Create: `conf/projects.yaml`
- Create: `conf/retry_policy.yaml`
- Create: `conf/topics.yaml`
- Create: `src/autoresearch/paths.py`
- Create: `src/autoresearch/settings.py`
- Test: `tests/test_settings.py`

- [ ] **Step 1: Write the failing settings tests**

```python
from pathlib import Path

from autoresearch.settings import load_settings


def test_load_settings_reads_yaml_and_derives_paths(tmp_path: Path) -> None:
    repo_root = tmp_path
    conf_dir = repo_root / "conf"
    conf_dir.mkdir()
    (conf_dir / "app.yaml").write_text(
        "app_name: auto-research\n"
        "paths:\n"
        "  state_dir: state\n"
        "  cache_dir: cache\n"
        "  logs_dir: logs\n"
        "  db_path: state/autoresearch.db\n"
        "remote:\n"
        "  root: /eagle/lc-mpi/Zhiqing/auto-research\n",
        encoding="utf-8",
    )

    settings = load_settings(repo_root=repo_root)

    assert settings.app_name == "auto-research"
    assert settings.paths.state_dir == repo_root / "state"
    assert settings.paths.db_path == repo_root / "state" / "autoresearch.db"
    assert settings.remote_root == "/eagle/lc-mpi/Zhiqing/auto-research"


def test_env_override_replaces_db_path(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path
    conf_dir = repo_root / "conf"
    conf_dir.mkdir()
    (conf_dir / "app.yaml").write_text(
        "app_name: auto-research\n"
        "paths:\n"
        "  state_dir: state\n"
        "  cache_dir: cache\n"
        "  logs_dir: logs\n"
        "  db_path: state/autoresearch.db\n"
        "remote:\n"
        "  root: /eagle/lc-mpi/Zhiqing/auto-research\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AUTORESEARCH_DB", str(repo_root / "custom.db"))

    settings = load_settings(repo_root=repo_root)

    assert settings.paths.db_path == repo_root / "custom.db"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_settings.py -v`
Expected: FAIL because `load_settings` and typed settings classes do not exist yet

- [ ] **Step 3: Write the minimal settings implementation**

`conf/app.yaml`

```yaml
app_name: auto-research
paths:
  state_dir: state
  cache_dir: cache
  logs_dir: logs
  db_path: state/autoresearch.db
remote:
  root: /eagle/lc-mpi/Zhiqing/auto-research
```

`conf/polaris.yaml`

```yaml
bridge:
  alias: polaris-relay
  host: polaris-login-04.hsn.cm.polaris.alcf.anl.gov
```

`conf/projects.yaml`

```yaml
projects: []
```

`conf/retry_policy.yaml`

```yaml
safe_retry_categories: []
```

`conf/topics.yaml`

```yaml
profiles:
  ai_systems:
    include:
      - distributed training
      - inference serving
  llm_systems:
    include:
      - vllm
      - continuous batching
exclude:
  - biology
  - medicine
```

`src/autoresearch/paths.py`

```python
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppPaths:
    repo_root: Path
    state_dir: Path
    cache_dir: Path
    logs_dir: Path
    db_path: Path
```

`src/autoresearch/settings.py`

```python
from dataclasses import dataclass
from pathlib import Path
import os

import yaml

from autoresearch.paths import AppPaths


@dataclass(frozen=True)
class Settings:
    app_name: str
    paths: AppPaths
    remote_root: str


def _resolve_path(repo_root: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return repo_root / path


def load_settings(repo_root: Path | None = None) -> Settings:
    resolved_root = (repo_root or Path(__file__).resolve().parents[2]).resolve()
    config_path = resolved_root / "conf" / "app.yaml"
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    state_dir = _resolve_path(resolved_root, data["paths"]["state_dir"])
    cache_dir = _resolve_path(resolved_root, data["paths"]["cache_dir"])
    logs_dir = _resolve_path(resolved_root, data["paths"]["logs_dir"])
    db_path = _resolve_path(resolved_root, data["paths"]["db_path"])

    override_db = os.getenv("AUTORESEARCH_DB")
    if override_db:
        db_path = _resolve_path(resolved_root, override_db)

    return Settings(
        app_name=data["app_name"],
        paths=AppPaths(
            repo_root=resolved_root,
            state_dir=state_dir,
            cache_dir=cache_dir,
            logs_dir=logs_dir,
            db_path=db_path,
        ),
        remote_root=data["remote"]["root"],
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_settings.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add conf/app.yaml conf/polaris.yaml conf/projects.yaml conf/retry_policy.yaml conf/topics.yaml src/autoresearch/paths.py src/autoresearch/settings.py tests/test_settings.py
git commit -m "feat: add typed settings loader"
```

### Task 3: SQLite Bootstrap And Schema Creation

**Files:**
- Create: `src/autoresearch/models.py`
- Create: `src/autoresearch/db.py`
- Test: `tests/test_db.py`

- [ ] **Step 1: Write the failing database tests**

```python
import sqlite3
from pathlib import Path

from autoresearch.db import connect_db, init_db


def test_init_db_creates_core_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"

    init_db(db_path)

    conn = sqlite3.connect(db_path)
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    finally:
        conn.close()

    assert {"runs", "jobs", "incidents", "decisions"} <= tables


def test_connect_db_enables_wal(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"

    with connect_db(db_path) as conn:
        mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]

    assert mode.lower() == "wal"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_db.py -v`
Expected: FAIL because `connect_db` and `init_db` do not exist yet

- [ ] **Step 3: Write the minimal database layer**

`src/autoresearch/models.py`

```python
RUNS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS runs(
  run_id TEXT PRIMARY KEY,
  run_kind TEXT NOT NULL,
  project TEXT NOT NULL,
  created_at TEXT NOT NULL,
  started_at TEXT,
  ended_at TEXT,
  status TEXT NOT NULL,
  git_commit TEXT,
  git_dirty INTEGER NOT NULL DEFAULT 0,
  local_cmd TEXT,
  remote_cmd TEXT,
  working_dir TEXT,
  notes TEXT
)
"""

JOBS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS jobs(
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
"""

INCIDENTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS incidents(
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
  resolved_at TEXT
)
"""

DECISIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS decisions(
  decision_id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  target_type TEXT NOT NULL,
  target_id TEXT NOT NULL,
  decision TEXT NOT NULL,
  rationale TEXT,
  actor TEXT NOT NULL
)
"""
```

`src/autoresearch/db.py`

```python
from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Iterator

from autoresearch.models import (
    DECISIONS_TABLE_SQL,
    INCIDENTS_TABLE_SQL,
    JOBS_TABLE_SQL,
    RUNS_TABLE_SQL,
)


@contextmanager
def connect_db(db_path: Path) -> Iterator[sqlite3.Connection]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: Path) -> None:
    with connect_db(db_path) as conn:
        conn.execute(RUNS_TABLE_SQL)
        conn.execute(JOBS_TABLE_SQL)
        conn.execute(INCIDENTS_TABLE_SQL)
        conn.execute(DECISIONS_TABLE_SQL)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_db.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/autoresearch/models.py src/autoresearch/db.py tests/test_db.py
git commit -m "feat: add sqlite bootstrap and schema"
```

### Task 4: Run Registry Service

**Files:**
- Create: `src/autoresearch/schemas.py`
- Create: `src/autoresearch/runs/__init__.py`
- Create: `src/autoresearch/runs/registry.py`
- Test: `tests/test_run_registry.py`

- [ ] **Step 1: Write the failing registry tests**

```python
from pathlib import Path

from autoresearch.db import init_db
from autoresearch.runs.registry import RunCreateRequest, RunRegistry


def test_create_run_persists_initial_metadata(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"
    init_db(db_path)
    registry = RunRegistry(db_path)

    record = registry.create_run(
        RunCreateRequest(run_kind="local-debug", project="demo", notes="hello")
    )

    assert record.run_kind == "local-debug"
    assert record.project == "demo"
    assert record.status == "CREATED"
    assert record.notes == "hello"
    assert record.run_id.startswith("run_")


def test_list_runs_returns_newest_first(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"
    init_db(db_path)
    registry = RunRegistry(db_path)

    first = registry.create_run(RunCreateRequest(run_kind="a", project="demo"))
    second = registry.create_run(RunCreateRequest(run_kind="b", project="demo"))

    records = registry.list_runs()

    assert [record.run_id for record in records] == [second.run_id, first.run_id]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_run_registry.py -v`
Expected: FAIL because `RunRegistry` and request models do not exist yet

- [ ] **Step 3: Write the minimal registry implementation**

`src/autoresearch/schemas.py`

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class RunCreateRequest:
    run_kind: str
    project: str
    notes: str | None = None
```

`src/autoresearch/runs/__init__.py`

```python
from autoresearch.runs.registry import RunRecord, RunRegistry

__all__ = ["RunRecord", "RunRegistry"]
```

`src/autoresearch/runs/registry.py`

```python
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import sqlite3
import uuid

from autoresearch.db import connect_db
from autoresearch.schemas import RunCreateRequest


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    run_kind: str
    project: str
    created_at: str
    status: str
    notes: str | None


class RunRegistry:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    def create_run(self, request: RunCreateRequest) -> RunRecord:
        created_at = datetime.now(UTC).isoformat()
        run_id = f"run_{uuid.uuid4().hex[:12]}"
        record = RunRecord(
            run_id=run_id,
            run_kind=request.run_kind,
            project=request.project,
            created_at=created_at,
            status="CREATED",
            notes=request.notes,
        )
        with connect_db(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO runs (
                    run_id, run_kind, project, created_at, status, notes
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    record.run_id,
                    record.run_kind,
                    record.project,
                    record.created_at,
                    record.status,
                    record.notes,
                ),
            )
        return record

    def list_runs(self) -> list[RunRecord]:
        with connect_db(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT run_id, run_kind, project, created_at, status, notes
                FROM runs
                ORDER BY created_at DESC
                """
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> RunRecord:
        return RunRecord(
            run_id=row["run_id"],
            run_kind=row["run_kind"],
            project=row["project"],
            created_at=row["created_at"],
            status=row["status"],
            notes=row["notes"],
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_run_registry.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/autoresearch/schemas.py src/autoresearch/runs/__init__.py src/autoresearch/runs/registry.py tests/test_run_registry.py
git commit -m "feat: add run registry service"
```

### Task 5: CLI Wiring For Database And Run Commands

**Files:**
- Modify: `src/autoresearch/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write the failing command tests**

```python
from pathlib import Path

from typer.testing import CliRunner

from autoresearch.cli import app


runner = CliRunner()


def test_db_init_creates_database_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AUTORESEARCH_DB", str(tmp_path / "state" / "autoresearch.db"))
    monkeypatch.setenv("AUTORESEARCH_REPO_ROOT", str(tmp_path))
    (tmp_path / "conf").mkdir()
    (tmp_path / "conf" / "app.yaml").write_text(
        "app_name: auto-research\n"
        "paths:\n"
        "  state_dir: state\n"
        "  cache_dir: cache\n"
        "  logs_dir: logs\n"
        "  db_path: state/autoresearch.db\n"
        "remote:\n"
        "  root: /eagle/lc-mpi/Zhiqing/auto-research\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["db", "init"])

    assert result.exit_code == 0
    assert (tmp_path / "state" / "autoresearch.db").exists()


def test_run_create_and_list_round_trip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AUTORESEARCH_DB", str(tmp_path / "state" / "autoresearch.db"))
    monkeypatch.setenv("AUTORESEARCH_REPO_ROOT", str(tmp_path))
    (tmp_path / "conf").mkdir()
    (tmp_path / "conf" / "app.yaml").write_text(
        "app_name: auto-research\n"
        "paths:\n"
        "  state_dir: state\n"
        "  cache_dir: cache\n"
        "  logs_dir: logs\n"
        "  db_path: state/autoresearch.db\n"
        "remote:\n"
        "  root: /eagle/lc-mpi/Zhiqing/auto-research\n",
        encoding="utf-8",
    )

    init_result = runner.invoke(app, ["db", "init"])
    create_result = runner.invoke(
        app,
        ["run", "create", "--kind", "local-debug", "--project", "demo", "--notes", "hello"],
    )
    list_result = runner.invoke(app, ["run", "list"])

    assert init_result.exit_code == 0
    assert create_result.exit_code == 0
    assert "local-debug" in list_result.stdout
    assert "demo" in list_result.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py -v`
Expected: FAIL because `db init` and `run create/list` are not implemented yet

- [ ] **Step 3: Write the minimal CLI wiring**

`src/autoresearch/settings.py` update

```python
def resolve_repo_root(repo_root: Path | None = None) -> Path:
    explicit = os.getenv("AUTORESEARCH_REPO_ROOT")
    if explicit:
        return Path(explicit).resolve()
    return (repo_root or Path(__file__).resolve().parents[2]).resolve()


def load_settings(repo_root: Path | None = None) -> Settings:
    resolved_root = resolve_repo_root(repo_root=repo_root)
    ...
```

`src/autoresearch/cli.py`

```python
from typing import Optional

import typer

from autoresearch.db import init_db
from autoresearch.runs.registry import RunRegistry
from autoresearch.schemas import RunCreateRequest
from autoresearch.settings import load_settings


app = typer.Typer(help="Auto Research control plane CLI.")
db_app = typer.Typer(help="Database commands.")
run_app = typer.Typer(help="Run registry commands.")

app.add_typer(db_app, name="db")
app.add_typer(run_app, name="run")


@db_app.command("init")
def init_database() -> None:
    settings = load_settings()
    init_db(settings.paths.db_path)
    typer.echo(f"Initialized database at {settings.paths.db_path}")


@run_app.command("create")
def create_run(
    kind: str = typer.Option(..., "--kind"),
    project: str = typer.Option(..., "--project"),
    notes: Optional[str] = typer.Option(None, "--notes"),
) -> None:
    settings = load_settings()
    registry = RunRegistry(settings.paths.db_path)
    record = registry.create_run(
        RunCreateRequest(run_kind=kind, project=project, notes=notes)
    )
    typer.echo(f"Created run {record.run_id}")


@run_app.command("list")
def list_runs() -> None:
    settings = load_settings()
    registry = RunRegistry(settings.paths.db_path)
    for record in registry.list_runs():
        typer.echo(
            f"{record.run_id}\t{record.run_kind}\t{record.project}\t{record.status}\t{record.created_at}"
        )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/autoresearch/cli.py src/autoresearch/settings.py tests/test_cli.py
git commit -m "feat: wire db and run cli commands"
```

### Task 6: Project Docs, Deployment Stubs, And Resume Notes

**Files:**
- Create: `README.md`
- Create: `AGENTS.md`
- Create: `PLANS.md`
- Create: `SESSION_RESUME.md`
- Create: `.codex/config.toml`
- Create: `docs/architecture.md`
- Create: `docs/runbook.md`
- Create: `deploy/env/autoresearch.env.example`
- Create: `deploy/systemd/autoresearch.service`

- [ ] **Step 1: Write the failing repository smoke test**

```python
from pathlib import Path


def test_repository_docs_exist() -> None:
    repo_root = Path(__file__).resolve().parents[1]

    assert (repo_root / "README.md").exists()
    assert (repo_root / "AGENTS.md").exists()
    assert (repo_root / "PLANS.md").exists()
    assert (repo_root / "SESSION_RESUME.md").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py::test_repository_docs_exist -v`
Expected: FAIL because the docs files do not exist yet

- [ ] **Step 3: Write the minimal project docs**

`README.md`

```markdown
# auto-research

Local-first Phase 0 + Phase 1 scaffold for a lab-server research control plane.

## Quickstart

1. Create a virtual environment
2. Install editable dependencies with `pip install -e .[dev]`
3. Run `python -m autoresearch.cli db init`
4. Run `python -m autoresearch.cli run create --kind local-debug --project demo`
5. Run `python -m autoresearch.cli run list`
```

`AGENTS.md`

```markdown
# Auto Research Working Rules

## Project goal
Build a lab-server control plane for paper radar, run registry, incident triage, safe retry, and daily reports.

## Hard constraints
- Polaris is a remote executor only.
- Never automate or bypass ALCF MFA.
- Remote managed files must live under `/eagle/lc-mpi/Zhiqing/auto-research/`.
- Prefer Python 3.11+, Typer, SQLite, YAML, and JSONL.

## Engineering rules
- Keep modules small and typed.
- Prefer explicit schemas over loose dicts.
- Add tests for every new behavior.
```

`PLANS.md`

```markdown
# Execution Plan Template

## Objective

## Constraints

## Files to touch

## Risks

## Step-by-step plan
1.
2.
3.

## Verification

## Rollback
```

`SESSION_RESUME.md`

```markdown
# Session Resume

Use this repository as the working directory before resuming Codex sessions.

~~~bash
cd "/home/zhiqingzhong/agent system/auto-research"
codex resume --last
~~~

If multiple sessions exist for this repo, use `codex resume` and select the one scoped to this directory.
```

`.codex/config.toml`

```toml
model = "gpt-5.4"

[agents]
max_threads = 4
max_depth = 1
```

`docs/architecture.md`

```markdown
# Architecture

Phase 0 + 1 implement the local control-plane foundation only: config loading, SQLite bootstrap, and run registry CLI.
```

`docs/runbook.md`

```markdown
# Runbook

## Local bootstrap

1. Create a virtual environment.
2. Install with `pip install -e .[dev]`.
3. Initialize the database.
4. Create and list runs through the CLI.
```

`deploy/env/autoresearch.env.example`

```bash
AUTORESEARCH_DB=/srv/auto-research/state/autoresearch.db
AUTORESEARCH_REPO_ROOT=/srv/auto-research/repo
```

`deploy/systemd/autoresearch.service`

```ini
[Unit]
Description=Auto Research control plane
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/srv/auto-research/repo
ExecStart=/srv/auto-research/repo/.venv/bin/python -m autoresearch.cli
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli.py::test_repository_docs_exist -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add README.md AGENTS.md PLANS.md SESSION_RESUME.md .codex/config.toml docs/architecture.md docs/runbook.md deploy/env/autoresearch.env.example deploy/systemd/autoresearch.service tests/test_cli.py
git commit -m "docs: add project guidance and deployment stubs"
```

### Task 7: Final Verification Sweep

**Files:**
- Modify: `tests/test_cli.py` if needed for final assertions
- Modify: any files required by failing verification only

- [ ] **Step 1: Run the full test suite**

Run: `pytest -v`
Expected: all tests PASS

- [ ] **Step 2: Run CLI help manually**

Run: `PYTHONPATH=src python -m autoresearch.cli --help`
Expected: help text shows `db` and `run` command groups

- [ ] **Step 3: Run the local registry workflow manually**

Run: `PYTHONPATH=src python -m autoresearch.cli db init`
Expected: prints initialized database path

Run: `PYTHONPATH=src python -m autoresearch.cli run create --kind local-debug --project demo`
Expected: prints `Created run run_<id>`

Run: `PYTHONPATH=src python -m autoresearch.cli run list`
Expected: output contains `local-debug` and `demo`

- [ ] **Step 4: Commit**

```bash
git add .
git commit -m "test: verify phase0 and phase1 scaffold"
```

## Self-Review

- Spec coverage: this plan covers repository scaffold, settings, SQLite bootstrap, run registry, CLI, docs, and local resume guidance. It intentionally omits bridge, executor, incidents, papers, and reports because the approved spec made them out of scope.
- Placeholder scan: no `TODO`, `TBD`, or deferred implementation placeholders remain in task steps.
- Type consistency: the plan uses `Settings`, `AppPaths`, `RunCreateRequest`, `RunRecord`, and `RunRegistry` consistently across tests and implementation.
