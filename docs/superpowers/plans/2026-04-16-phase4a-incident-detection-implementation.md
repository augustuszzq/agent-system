# Phase 4A Incident Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a manual, operator-triggered incident detection flow that fetches live evidence when the Polaris bridge is attached, falls back to local evidence snapshots when it is not, classifies incidents deterministically, and persists incident rows with stable upsert semantics.

**Architecture:** Keep the Phase 4A path narrow and composable. `incidents/fetch.py` owns evidence collection plus snapshot layout, `incidents/normalize.py` turns raw evidence into one deterministic analysis input, `incidents/classifier.py` maps that input into a fixed taxonomy with stable fingerprints, and `incidents/registry.py` owns the `incidents` table. `cli.py` stays thin and only orchestrates `scan`, `list`, and `summarize`.

**Tech Stack:** Python 3.11+, Typer, SQLite, pytest, OpenSSH bridge helpers, fixture-driven parser/classifier tests

---

## File Map

- Modify: `src/autoresearch/models.py`
- Modify: `src/autoresearch/db.py`
- Modify: `src/autoresearch/schemas.py`
- Modify: `src/autoresearch/paths.py`
- Modify: `src/autoresearch/cli.py`
- Create: `src/autoresearch/incidents/__init__.py`
- Create: `src/autoresearch/incidents/fetch.py`
- Create: `src/autoresearch/incidents/normalize.py`
- Create: `src/autoresearch/incidents/classifier.py`
- Create: `src/autoresearch/incidents/registry.py`
- Create: `src/autoresearch/incidents/summaries.py`
- Modify: `docs/architecture.md`
- Modify: `docs/runbook.md`
- Modify: `tests/test_db.py`
- Modify: `tests/test_cli.py`
- Create: `tests/test_incident_classifier.py`
- Create: `tests/test_incident_fetch.py`
- Create: `tests/test_incident_registry.py`
- Create: `tests/fixtures/incidents/qstat_filesystem_unavailable.json`
- Create: `tests/fixtures/incidents/qstat_running.json`
- Create: `tests/fixtures/incidents/stdout_oom.log`
- Create: `tests/fixtures/incidents/stderr_import_error.log`
- Create: `tests/fixtures/incidents/stderr_path_error.log`
- Create: `tests/fixtures/incidents/stderr_nccl_failure.log`
- Create: `tests/fixtures/incidents/stderr_mpi_bootstrap.log`
- Create: `tests/fixtures/incidents/stderr_walltime.log`
- Create: `tests/fixtures/incidents/stderr_unknown.log`

## Task 1: Extend Incident Schema And SQLite Persistence

**Files:**
- Modify: `src/autoresearch/models.py`
- Modify: `src/autoresearch/db.py`
- Modify: `src/autoresearch/schemas.py`
- Modify: `tests/test_db.py`

- [ ] **Step 1: Write failing database and schema tests**

Add these tests to `tests/test_db.py`:

```python
import sqlite3
from pathlib import Path

from autoresearch.db import init_db


def test_init_db_adds_updated_at_to_incidents_table(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"

    init_db(db_path)

    conn = sqlite3.connect(db_path)
    try:
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(incidents)").fetchall()
        }
    finally:
        conn.close()

    assert "updated_at" in columns


def test_init_db_migrates_existing_incidents_table_without_updated_at(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE incidents(
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
        )
        conn.execute(
            """
            INSERT INTO incidents (
                incident_id, run_id, job_id, severity, category, fingerprint,
                evidence_json, auto_action, status, created_at, resolved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "inc_demo",
                "run_demo",
                "job_demo",
                "HIGH",
                "UNKNOWN",
                "fp",
                "{}",
                None,
                "OPEN",
                "2026-04-16T00:00:00+00:00",
                None,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    init_db(db_path)

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT created_at, updated_at FROM incidents WHERE incident_id = ?",
            ("inc_demo",),
        ).fetchone()
    finally:
        conn.close()

    assert row == ("2026-04-16T00:00:00+00:00", "2026-04-16T00:00:00+00:00")
```

- [ ] **Step 2: Run the database tests to verify failure**

Run:

```bash
pytest tests/test_db.py -q
```

Expected: failure because `incidents.updated_at` is not created or migrated yet.

- [ ] **Step 3: Add incident schema types and migration support**

Update `src/autoresearch/models.py` so the `incidents` table definition becomes:

```python
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
  updated_at TEXT NOT NULL,
  resolved_at TEXT
)
"""
```

Update `src/autoresearch/db.py` to migrate old databases in place:

```python
def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row[1] for row in rows}


def _ensure_incidents_updated_at(conn: sqlite3.Connection) -> None:
    columns = _table_columns(conn, "incidents")
    if "updated_at" in columns:
        return

    conn.execute("ALTER TABLE incidents ADD COLUMN updated_at TEXT")
    conn.execute(
        """
        UPDATE incidents
        SET updated_at = created_at
        WHERE updated_at IS NULL
        """
    )


def init_db(db_path: Path) -> None:
    with connect_db(db_path) as conn:
        conn.execute(RUNS_TABLE_SQL)
        conn.execute(JOBS_TABLE_SQL)
        conn.execute(INCIDENTS_TABLE_SQL)
        conn.execute(DECISIONS_TABLE_SQL)
        _ensure_incidents_updated_at(conn)
```

Extend `src/autoresearch/schemas.py` with the types that later tasks will reuse:

```python
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


IncidentCategory = Literal[
    "FILESYSTEM_UNAVAILABLE",
    "RESOURCE_OOM",
    "RESOURCE_WALLTIME",
    "ENV_IMPORT_ERROR",
    "ENV_PATH_ERROR",
    "NCCL_FAILURE",
    "MPI_BOOTSTRAP",
    "NO_HEARTBEAT",
    "UNKNOWN",
]
IncidentSeverity = Literal["CRITICAL", "HIGH", "MEDIUM"]
IncidentStatus = Literal["OPEN", "RESOLVED"]


@dataclass(frozen=True)
class IncidentSnapshotRef:
    scan_time: str
    snapshot_dir: Path
    qstat_json_path: Path
    stdout_tail_path: Path
    stderr_tail_path: Path


@dataclass(frozen=True)
class IncidentFetchResult:
    source: Literal["live", "local-fallback"]
    snapshot: IncidentSnapshotRef
    previous_snapshot: IncidentSnapshotRef | None


@dataclass(frozen=True)
class NormalizedIncidentInput:
    job_id: str
    run_id: str
    pbs_job_id: str | None
    job_state: str
    comment: str | None
    exec_host: str | None
    stdout_tail: str
    stderr_tail: str
    snapshot_dir: Path
    scan_time: str
    current_log_tail_hash: str
    previous_log_tail_hash: str | None


@dataclass(frozen=True)
class ClassifiedIncident:
    category: IncidentCategory
    severity: IncidentSeverity
    fingerprint: str
    matched_lines: tuple[str, ...]
    rule_name: str
```

- [ ] **Step 4: Re-run the database tests**

Run:

```bash
pytest tests/test_db.py -q
```

Expected: both tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/autoresearch/models.py src/autoresearch/db.py src/autoresearch/schemas.py tests/test_db.py
git commit -m "feat: add incident schema and db migration"
```

## Task 2: Add Deterministic Incident Classification With Fixtures

**Files:**
- Create: `tests/fixtures/incidents/qstat_filesystem_unavailable.json`
- Create: `tests/fixtures/incidents/qstat_running.json`
- Create: `tests/fixtures/incidents/stdout_oom.log`
- Create: `tests/fixtures/incidents/stderr_import_error.log`
- Create: `tests/fixtures/incidents/stderr_path_error.log`
- Create: `tests/fixtures/incidents/stderr_nccl_failure.log`
- Create: `tests/fixtures/incidents/stderr_mpi_bootstrap.log`
- Create: `tests/fixtures/incidents/stderr_walltime.log`
- Create: `tests/fixtures/incidents/stderr_unknown.log`
- Create: `tests/test_incident_classifier.py`
- Create: `src/autoresearch/incidents/__init__.py`
- Create: `src/autoresearch/incidents/classifier.py`

- [ ] **Step 1: Write fixture files and failing classifier tests**

Create these fixture files:

`tests/fixtures/incidents/qstat_filesystem_unavailable.json`

```json
{
  "Jobs": {
    "12345.polaris": {
      "job_state": "Q",
      "comment": "filesystem unavailable: eagle",
      "exec_host": ""
    }
  }
}
```

`tests/fixtures/incidents/qstat_running.json`

```json
{
  "Jobs": {
    "12345.polaris": {
      "job_state": "R",
      "comment": "",
      "exec_host": "x1001c1s2b0"
    }
  }
}
```

`tests/fixtures/incidents/stdout_oom.log`

```text
rank 0 training step 200
RuntimeError: CUDA out of memory while trying to allocate 1.00 GiB
Killed
```

`tests/fixtures/incidents/stderr_import_error.log`

```text
Traceback (most recent call last):
  File "train.py", line 1, in <module>
    import nonexistent_package
ModuleNotFoundError: No module named 'nonexistent_package'
```

`tests/fixtures/incidents/stderr_path_error.log`

```text
bash: /eagle/lc-mpi/Zhiqing/auto-research/jobs/demo/entrypoint.sh: No such file or directory
```

`tests/fixtures/incidents/stderr_nccl_failure.log`

```text
ncclUnhandledCudaError: NCCL error in: /workspace/collectives.cpp:133, unhandled cuda error
NCCL WARN connection closed by remote peer
```

`tests/fixtures/incidents/stderr_mpi_bootstrap.log`

```text
MPI_Init failed during bootstrap
PMI server not found
```

`tests/fixtures/incidents/stderr_walltime.log`

```text
PBS: job killed: walltime 00:10:00 exceeded limit 00:10:00
```

`tests/fixtures/incidents/stderr_unknown.log`

```text
fatal: unclassified scheduler-side failure
```

Create `tests/test_incident_classifier.py`:

```python
from pathlib import Path

from autoresearch.incidents.classifier import classify_incident
from autoresearch.schemas import NormalizedIncidentInput


FIXTURES = Path(__file__).parent / "fixtures" / "incidents"


def _fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _normalized(
    *,
    job_state: str = "F",
    comment: str | None = None,
    stdout_tail: str = "",
    stderr_tail: str = "",
    current_log_tail_hash: str = "hash-a",
    previous_log_tail_hash: str | None = None,
) -> NormalizedIncidentInput:
    return NormalizedIncidentInput(
        job_id="job_demo",
        run_id="run_demo",
        pbs_job_id="12345.polaris",
        job_state=job_state,
        comment=comment,
        exec_host="x1001c1s2b0",
        stdout_tail=stdout_tail,
        stderr_tail=stderr_tail,
        snapshot_dir=Path("/tmp/snapshot"),
        scan_time="2026-04-16T01:02:03+00:00",
        current_log_tail_hash=current_log_tail_hash,
        previous_log_tail_hash=previous_log_tail_hash,
    )


def test_classify_filesystem_unavailable_from_qstat_comment() -> None:
    result = classify_incident(
        _normalized(comment="filesystem unavailable: eagle")
    )

    assert result is not None
    assert result.category == "FILESYSTEM_UNAVAILABLE"
    assert result.severity == "CRITICAL"


def test_classify_resource_oom_from_stdout_tail() -> None:
    result = classify_incident(
        _normalized(stdout_tail=_fixture_text("stdout_oom.log"))
    )

    assert result is not None
    assert result.category == "RESOURCE_OOM"
    assert result.severity == "CRITICAL"


def test_classify_import_error_from_stderr_tail() -> None:
    result = classify_incident(
        _normalized(stderr_tail=_fixture_text("stderr_import_error.log"))
    )

    assert result is not None
    assert result.category == "ENV_IMPORT_ERROR"
    assert result.fingerprint == "no module named nonexistent_package"


def test_classify_no_heartbeat_only_when_running_and_hashes_repeat() -> None:
    result = classify_incident(
        _normalized(
            job_state="R",
            stdout_tail="steady output",
            current_log_tail_hash="same",
            previous_log_tail_hash="same",
        )
    )

    assert result is not None
    assert result.category == "NO_HEARTBEAT"
    assert result.severity == "HIGH"


def test_classify_returns_none_for_empty_evidence() -> None:
    result = classify_incident(_normalized())

    assert result is None


def test_classify_unknown_when_nonempty_evidence_has_no_specific_match() -> None:
    result = classify_incident(
        _normalized(stderr_tail=_fixture_text("stderr_unknown.log"))
    )

    assert result is not None
    assert result.category == "UNKNOWN"
    assert result.severity == "MEDIUM"
```

- [ ] **Step 2: Run the classifier tests to verify failure**

Run:

```bash
pytest tests/test_incident_classifier.py -q
```

Expected: import errors because `autoresearch.incidents.classifier` does not exist yet.

- [ ] **Step 3: Implement priority-ordered deterministic classification**

Create `src/autoresearch/incidents/__init__.py`:

```python
"""Incident detection and classification helpers."""
```

Create `src/autoresearch/incidents/classifier.py`:

```python
from __future__ import annotations

import hashlib
import re

from autoresearch.schemas import ClassifiedIncident, NormalizedIncidentInput


def classify_incident(data: NormalizedIncidentInput) -> ClassifiedIncident | None:
    comment = (data.comment or "").strip()
    stdout = data.stdout_tail.strip()
    stderr = data.stderr_tail.strip()
    combined = "\n".join(part for part in (stdout, stderr) if part).strip()

    if _is_filesystem_unavailable(comment):
        line = _first_nonempty_line(comment)
        return ClassifiedIncident("FILESYSTEM_UNAVAILABLE", "CRITICAL", _normalize_line(line), (line,), "qstat-comment")
    if _find_oom_line(combined):
        line = _find_oom_line(combined)
        return ClassifiedIncident("RESOURCE_OOM", "CRITICAL", _normalize_line(line), (line,), "oom-line")
    if _find_walltime_line("\n".join((comment, combined))):
        line = _find_walltime_line("\n".join((comment, combined)))
        return ClassifiedIncident("RESOURCE_WALLTIME", "HIGH", _normalize_line(line), (line,), "walltime-line")
    if _find_import_error_line(combined):
        line = _find_import_error_line(combined)
        return ClassifiedIncident("ENV_IMPORT_ERROR", "HIGH", _normalize_import_fingerprint(line), (line,), "import-error")
    if _find_path_error_line(combined):
        line = _find_path_error_line(combined)
        return ClassifiedIncident("ENV_PATH_ERROR", "HIGH", _normalize_line(line), (line,), "path-error")
    if _find_nccl_line(combined):
        line = _find_nccl_line(combined)
        return ClassifiedIncident("NCCL_FAILURE", "CRITICAL", _normalize_line(line), (line,), "nccl-line")
    if _find_mpi_bootstrap_line(combined):
        line = _find_mpi_bootstrap_line(combined)
        return ClassifiedIncident("MPI_BOOTSTRAP", "CRITICAL", _normalize_line(line), (line,), "mpi-bootstrap")
    if _is_no_heartbeat(data):
        return ClassifiedIncident("NO_HEARTBEAT", "HIGH", "no-heartbeat", ("log tail hash repeated",), "repeated-log-hash")
    if combined or comment:
        digest = hashlib.sha256(f"{_normalize_line(comment)}|{_normalize_line(_first_lines(stderr, 3))}".encode("utf-8")).hexdigest()[:16]
        return ClassifiedIncident("UNKNOWN", "MEDIUM", digest, tuple(filter(None, (_first_nonempty_line(comment), _first_nonempty_line(stderr)))), "fallback-unknown")
    return None


def _is_filesystem_unavailable(comment: str) -> bool:
    text = comment.lower()
    return "filesystem unavailable" in text or ("eagle" in text and "unavailable" in text)


def _find_oom_line(text: str) -> str | None:
    for line in text.splitlines():
        lower = line.lower()
        if "out of memory" in lower or "oom-kill" in lower:
            return line
        if lower.strip() == "killed" and "memory" in text.lower():
            return line
    return None


def _find_walltime_line(text: str) -> str | None:
    for line in text.splitlines():
        lower = line.lower()
        if "walltime" in lower or "time limit" in lower:
            return line
    return None


def _find_import_error_line(text: str) -> str | None:
    for line in text.splitlines():
        if "ImportError" in line or "ModuleNotFoundError" in line:
            return line
    return None


def _find_path_error_line(text: str) -> str | None:
    for line in text.splitlines():
        lower = line.lower()
        if "no such file or directory" in lower or "can't open file" in lower or "cannot cd" in lower:
            return line
    return None


def _find_nccl_line(text: str) -> str | None:
    for line in text.splitlines():
        if "nccl" in line.lower():
            return line
    return None


def _find_mpi_bootstrap_line(text: str) -> str | None:
    for line in text.splitlines():
        lower = line.lower()
        if "mpi_init" in lower or "pmi" in lower or "bootstrap" in lower:
            return line
    return None


def _is_no_heartbeat(data: NormalizedIncidentInput) -> bool:
    return (
        data.job_state.strip().upper() in {"R", "RUNNING"}
        and data.previous_log_tail_hash is not None
        and data.current_log_tail_hash == data.previous_log_tail_hash
        and bool(data.stdout_tail.strip() or data.stderr_tail.strip())
    )


def _normalize_import_fingerprint(line: str) -> str:
    lowered = line.lower()
    match = re.search(r"no module named ['\"]([^'\"]+)['\"]", lowered)
    if match:
        return f"no module named {match.group(1)}"
    return _normalize_line(line)


def _normalize_line(text: str) -> str:
    return " ".join(text.strip().lower().split())


def _first_nonempty_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line
    return ""


def _first_lines(text: str, limit: int) -> str:
    return "\n".join(text.splitlines()[:limit])
```

- [ ] **Step 4: Re-run the classifier tests**

Run:

```bash
pytest tests/test_incident_classifier.py -q
```

Expected: classifier tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/incidents tests/test_incident_classifier.py src/autoresearch/incidents/__init__.py src/autoresearch/incidents/classifier.py
git commit -m "feat: add deterministic incident classifier"
```

## Task 3: Add Incident Registry Upsert And Summary Rendering

**Files:**
- Create: `tests/test_incident_registry.py`
- Create: `src/autoresearch/incidents/registry.py`
- Create: `src/autoresearch/incidents/summaries.py`

- [ ] **Step 1: Write failing registry and summary tests**

Create `tests/test_incident_registry.py`:

```python
import json
from pathlib import Path

from autoresearch.db import init_db
from autoresearch.incidents.registry import IncidentRegistry


def test_upsert_incident_reuses_existing_row_for_same_job_category_fingerprint(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"
    init_db(db_path)
    registry = IncidentRegistry(db_path)

    created = registry.upsert_incident(
        run_id="run_demo",
        job_id="job_demo",
        severity="HIGH",
        category="ENV_IMPORT_ERROR",
        fingerprint="no module named nonexistent_package",
        evidence={
            "scan_time": "2026-04-16T00:00:00+00:00",
            "snapshot_dir": "/tmp/scan-a",
            "classifier_rule": "import-error",
            "matched_lines": ["ModuleNotFoundError: No module named 'nonexistent_package'"],
        },
    )
    updated = registry.upsert_incident(
        run_id="run_demo",
        job_id="job_demo",
        severity="HIGH",
        category="ENV_IMPORT_ERROR",
        fingerprint="no module named nonexistent_package",
        evidence={
            "scan_time": "2026-04-16T00:05:00+00:00",
            "snapshot_dir": "/tmp/scan-b",
            "classifier_rule": "import-error",
            "matched_lines": ["ModuleNotFoundError: No module named 'nonexistent_package'"],
        },
    )

    assert created.incident_id == updated.incident_id
    assert created.created_at == updated.created_at
    assert updated.updated_at == "2026-04-16T00:05:00+00:00"


def test_list_open_incidents_returns_newest_first(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"
    init_db(db_path)
    registry = IncidentRegistry(db_path)

    registry.upsert_incident(
        run_id="run_1",
        job_id="job_1",
        severity="MEDIUM",
        category="UNKNOWN",
        fingerprint="a",
        evidence={"scan_time": "2026-04-16T00:00:00+00:00", "snapshot_dir": "/tmp/a", "classifier_rule": "fallback", "matched_lines": ["a"]},
    )
    registry.upsert_incident(
        run_id="run_2",
        job_id="job_2",
        severity="CRITICAL",
        category="RESOURCE_OOM",
        fingerprint="oom",
        evidence={"scan_time": "2026-04-16T00:10:00+00:00", "snapshot_dir": "/tmp/b", "classifier_rule": "oom-line", "matched_lines": ["out of memory"]},
    )

    rows = registry.list_open_incidents()

    assert [row.job_id for row in rows] == ["job_2", "job_1"]


def test_summarize_open_incidents_groups_by_category_and_severity(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"
    init_db(db_path)
    registry = IncidentRegistry(db_path)

    registry.upsert_incident(
        run_id="run_1",
        job_id="job_1",
        severity="CRITICAL",
        category="RESOURCE_OOM",
        fingerprint="oom",
        evidence={"scan_time": "2026-04-16T00:10:00+00:00", "snapshot_dir": "/tmp/a", "classifier_rule": "oom-line", "matched_lines": ["out of memory"]},
    )
    registry.upsert_incident(
        run_id="run_2",
        job_id="job_2",
        severity="HIGH",
        category="ENV_IMPORT_ERROR",
        fingerprint="import",
        evidence={"scan_time": "2026-04-16T00:11:00+00:00", "snapshot_dir": "/tmp/b", "classifier_rule": "import-error", "matched_lines": ["ModuleNotFoundError"]},
    )

    summary = registry.summarize_open_incidents(limit=3)

    assert summary.counts["RESOURCE_OOM"] == 1
    assert summary.counts["ENV_IMPORT_ERROR"] == 1
    assert summary.top_incidents[0].job_id == "job_1"
```

- [ ] **Step 2: Run the registry tests to verify failure**

Run:

```bash
pytest tests/test_incident_registry.py -q
```

Expected: import errors because `IncidentRegistry` does not exist yet.

- [ ] **Step 3: Implement incident upsert and summary primitives**

Create `src/autoresearch/incidents/registry.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
import uuid

from autoresearch.db import connect_db
from autoresearch.schemas import IncidentCategory, IncidentSeverity, IncidentStatus


@dataclass(frozen=True)
class IncidentRecord:
    incident_id: str
    run_id: str | None
    job_id: str | None
    severity: IncidentSeverity
    category: IncidentCategory
    fingerprint: str
    evidence_json: str
    status: IncidentStatus
    created_at: str
    updated_at: str
    resolved_at: str | None


@dataclass(frozen=True)
class IncidentSummary:
    counts: dict[str, int]
    top_incidents: list[IncidentRecord]


class IncidentRegistry:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    def upsert_incident(
        self,
        *,
        run_id: str | None,
        job_id: str | None,
        severity: IncidentSeverity,
        category: IncidentCategory,
        fingerprint: str,
        evidence: dict[str, object],
    ) -> IncidentRecord:
        updated_at = str(evidence["scan_time"])
        evidence_json = json.dumps(evidence, sort_keys=True)
        with connect_db(self._db_path) as conn:
            existing = conn.execute(
                """
                SELECT incident_id, run_id, job_id, severity, category, fingerprint,
                       evidence_json, status, created_at, updated_at, resolved_at
                FROM incidents
                WHERE job_id = ? AND category = ? AND fingerprint = ?
                """,
                (job_id, category, fingerprint),
            ).fetchone()
            if existing is None:
                record = IncidentRecord(
                    incident_id=f"inc_{uuid.uuid4().hex[:12]}",
                    run_id=run_id,
                    job_id=job_id,
                    severity=severity,
                    category=category,
                    fingerprint=fingerprint,
                    evidence_json=evidence_json,
                    status="OPEN",
                    created_at=updated_at,
                    updated_at=updated_at,
                    resolved_at=None,
                )
                conn.execute(
                    """
                    INSERT INTO incidents (
                        incident_id, run_id, job_id, severity, category, fingerprint,
                        evidence_json, auto_action, status, created_at, updated_at, resolved_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.incident_id,
                        record.run_id,
                        record.job_id,
                        record.severity,
                        record.category,
                        record.fingerprint,
                        record.evidence_json,
                        None,
                        record.status,
                        record.created_at,
                        record.updated_at,
                        record.resolved_at,
                    ),
                )
                return record

            conn.execute(
                """
                UPDATE incidents
                SET severity = ?, evidence_json = ?, updated_at = ?
                WHERE incident_id = ?
                """,
                (severity, evidence_json, updated_at, existing["incident_id"]),
            )
            row = conn.execute(
                """
                SELECT incident_id, run_id, job_id, severity, category, fingerprint,
                       evidence_json, status, created_at, updated_at, resolved_at
                FROM incidents
                WHERE incident_id = ?
                """,
                (existing["incident_id"],),
            ).fetchone()
        return _row_to_record(row)

    def list_open_incidents(self) -> list[IncidentRecord]:
        with connect_db(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT incident_id, run_id, job_id, severity, category, fingerprint,
                       evidence_json, status, created_at, updated_at, resolved_at
                FROM incidents
                WHERE status = 'OPEN'
                ORDER BY updated_at DESC, created_at DESC, incident_id DESC
                """
            ).fetchall()
        return [_row_to_record(row) for row in rows]

    def summarize_open_incidents(self, limit: int = 3) -> IncidentSummary:
        incidents = self.list_open_incidents()
        counts: dict[str, int] = {}
        for incident in incidents:
            counts[incident.category] = counts.get(incident.category, 0) + 1
        order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2}
        top = sorted(incidents, key=lambda item: (order[item.severity], item.updated_at))[:limit]
        return IncidentSummary(counts=counts, top_incidents=top)


def _row_to_record(row: sqlite3.Row) -> IncidentRecord:
    return IncidentRecord(
        incident_id=row["incident_id"],
        run_id=row["run_id"],
        job_id=row["job_id"],
        severity=row["severity"],
        category=row["category"],
        fingerprint=row["fingerprint"],
        evidence_json=row["evidence_json"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        resolved_at=row["resolved_at"],
    )
```

Create `src/autoresearch/incidents/summaries.py`:

```python
from autoresearch.incidents.registry import IncidentRecord, IncidentSummary


def render_incident_row(record: IncidentRecord) -> str:
    return "\t".join(
        [
            record.incident_id,
            record.job_id or "-",
            record.category,
            record.severity,
            record.status,
            record.updated_at,
        ]
    )


def render_incident_summary(summary: IncidentSummary) -> str:
    lines = ["Counts:"]
    for category in sorted(summary.counts):
        lines.append(f"- {category}: {summary.counts[category]}")
    if summary.top_incidents:
        lines.append("")
        lines.append("Top incidents:")
        for incident in summary.top_incidents:
            lines.append(f"- {incident.severity} {incident.category} {incident.job_id} {incident.updated_at}")
    return "\n".join(lines)
```

- [ ] **Step 4: Re-run the registry tests**

Run:

```bash
pytest tests/test_incident_registry.py -q
```

Expected: registry and summary tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests/test_incident_registry.py src/autoresearch/incidents/registry.py src/autoresearch/incidents/summaries.py
git commit -m "feat: add incident registry and summaries"
```

## Task 4: Add Evidence Fetch, Snapshot Storage, And Normalization

**Files:**
- Modify: `src/autoresearch/paths.py`
- Create: `src/autoresearch/incidents/fetch.py`
- Create: `src/autoresearch/incidents/normalize.py`
- Create: `tests/test_incident_fetch.py`

- [ ] **Step 1: Write failing fetch and normalization tests**

Create `tests/test_incident_fetch.py`:

```python
from pathlib import Path

import pytest

from autoresearch.db import init_db
from autoresearch.incidents.fetch import IncidentFetchError, collect_incident_evidence
from autoresearch.incidents.normalize import normalize_incident_evidence
from autoresearch.runs.registry import RunRegistry
from autoresearch.schemas import BridgeStatusResult, CommandResult, RunCreateRequest
from autoresearch.settings import load_settings


class FakeBridge:
    def __init__(self, *, state: str, qstat_text: str = "", stdout_text: str = "", stderr_text: str = "") -> None:
        self._status = BridgeStatusResult(
            alias="polaris-relay",
            state=state,
            explanation=state,
            command_result=None,
            control_path_exists=None,
        )
        self.qstat_text = qstat_text
        self.stdout_text = stdout_text
        self.stderr_text = stderr_text
        self.commands: list[str] = []

    def status(self) -> BridgeStatusResult:
        return self._status

    def exec(self, command: str) -> CommandResult:
        self.commands.append(command)
        if command.startswith("qstat "):
            return CommandResult(("ssh", "polaris-relay", command), 0, self.qstat_text, "", 0.01)
        if "stdout.log" in command:
            return CommandResult(("ssh", "polaris-relay", command), 0, self.stdout_text, "", 0.01)
        if "stderr.log" in command:
            return CommandResult(("ssh", "polaris-relay", command), 0, self.stderr_text, "", 0.01)
        return CommandResult(("ssh", "polaris-relay", command), 1, "", "unexpected command", 0.01)


def _write_repo_config(repo_root: Path) -> None:
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
    (conf_dir / "polaris.yaml").write_text(
        "bridge:\n"
        "  alias: polaris-relay\n"
        "  host: polaris-login-04.hsn.cm.polaris.alcf.anl.gov\n"
        "  user: zzq\n"
        "  control_path: ~/.ssh/cm-%C\n"
        "  server_alive_interval: 60\n"
        "  server_alive_count_max: 3\n"
        "  connect_timeout: 15\n"
        "probe:\n"
        "  project: demo\n"
        "  queue: debug\n"
        "  walltime: 00:10:00\n",
        encoding="utf-8",
    )


def _create_job(repo_root: Path) -> tuple[object, object]:
    settings = load_settings(repo_root=repo_root)
    init_db(settings.paths.db_path)
    registry = RunRegistry(settings.paths.db_path)
    run_record = registry.create_run(RunCreateRequest(run_kind="probe", project="demo"))
    job_record = registry.create_job(
        run_id=run_record.run_id,
        backend="pbs",
        queue="debug",
        walltime="00:10:00",
        filesystems="eagle",
        select_expr="1:system=polaris",
        place_expr="scatter",
        stdout_path="/eagle/lc-mpi/Zhiqing/auto-research/runs/demo/stdout.log",
        stderr_path="/eagle/lc-mpi/Zhiqing/auto-research/runs/demo/stderr.log",
        pbs_job_id="12345.polaris",
    )
    return settings, job_record


def test_collect_incident_evidence_fetches_live_snapshot_when_bridge_attached(tmp_path: Path) -> None:
    _write_repo_config(tmp_path)
    settings, job_record = _create_job(tmp_path)
    bridge = FakeBridge(
        state="ATTACHED",
        qstat_text='{"Jobs":{"12345.polaris":{"job_state":"R","comment":"","exec_host":"x1001c1s2b0"}}}',
        stdout_text="heartbeat line\n",
        stderr_text="",
    )

    result = collect_incident_evidence(settings.paths, job_record, bridge)

    assert result.source == "live"
    assert result.snapshot.qstat_json_path.exists()
    assert result.snapshot.stdout_tail_path.read_text(encoding="utf-8") == "heartbeat line\n"
    assert result.previous_snapshot is None


def test_collect_incident_evidence_falls_back_to_latest_local_snapshot(tmp_path: Path) -> None:
    _write_repo_config(tmp_path)
    settings, job_record = _create_job(tmp_path)
    bridge = FakeBridge(
        state="ATTACHED",
        qstat_text='{"Jobs":{"12345.polaris":{"job_state":"R","comment":"","exec_host":"x1001c1s2b0"}}}',
        stdout_text="first tail\n",
        stderr_text="",
    )
    collect_incident_evidence(settings.paths, job_record, bridge)

    fallback = collect_incident_evidence(settings.paths, job_record, FakeBridge(state="DETACHED"))

    assert fallback.source == "local-fallback"
    assert fallback.snapshot.stdout_tail_path.read_text(encoding="utf-8") == "first tail\n"


def test_collect_incident_evidence_raises_when_no_live_or_local_evidence_exists(tmp_path: Path) -> None:
    _write_repo_config(tmp_path)
    settings, job_record = _create_job(tmp_path)

    with pytest.raises(IncidentFetchError):
        collect_incident_evidence(settings.paths, job_record, FakeBridge(state="DETACHED"))


def test_normalize_incident_evidence_parses_qstat_and_computes_tail_hashes(tmp_path: Path) -> None:
    _write_repo_config(tmp_path)
    settings, job_record = _create_job(tmp_path)
    first = collect_incident_evidence(
        settings.paths,
        job_record,
        FakeBridge(
            state="ATTACHED",
            qstat_text='{"Jobs":{"12345.polaris":{"job_state":"R","comment":"","exec_host":"x1001c1s2b0"}}}',
            stdout_text="steady line\n",
            stderr_text="",
        ),
    )
    second = collect_incident_evidence(
        settings.paths,
        job_record,
        FakeBridge(state="DETACHED"),
    )

    normalized = normalize_incident_evidence(job_record=job_record, fetched=second)

    assert normalized.job_state == "R"
    assert normalized.exec_host == "x1001c1s2b0"
    assert normalized.current_log_tail_hash == normalized.previous_log_tail_hash
```

- [ ] **Step 2: Run the fetch tests to verify failure**

Run:

```bash
pytest tests/test_incident_fetch.py -q
```

Expected: import errors because `fetch.py` and `normalize.py` do not exist yet.

- [ ] **Step 3: Implement snapshot helpers, live fetch, and normalization**

Update `src/autoresearch/paths.py` with incident snapshot helpers:

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


def incident_state_dir(paths: AppPaths, job_id: str) -> Path:
    return paths.state_dir / "incidents" / job_id


def incident_snapshot_dir(paths: AppPaths, job_id: str, scan_time: str) -> Path:
    safe_scan_time = scan_time.replace(":", "_")
    return incident_state_dir(paths, job_id) / safe_scan_time
```

Create `src/autoresearch/incidents/fetch.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import shlex

from autoresearch.bridge.remote_exec import RemoteBridgeError, execute_remote_command
from autoresearch.executor.pbs import build_qstat_command
from autoresearch.paths import AppPaths, incident_state_dir, incident_snapshot_dir
from autoresearch.runs.registry import JobRecord
from autoresearch.schemas import IncidentFetchResult, IncidentSnapshotRef


class IncidentFetchError(RuntimeError):
    """Raised when no usable evidence is available for a scan."""


def collect_incident_evidence(paths: AppPaths, job_record: JobRecord, bridge_client) -> IncidentFetchResult:
    latest = load_latest_snapshot(paths, job_record.job_id)
    bridge_state = bridge_client.status().state
    if bridge_state == "ATTACHED":
        try:
            live = _fetch_live_snapshot(paths, job_record, bridge_client)
            return IncidentFetchResult(source="live", snapshot=live, previous_snapshot=latest)
        except RemoteBridgeError:
            if latest is not None:
                return IncidentFetchResult(source="local-fallback", snapshot=latest, previous_snapshot=_load_previous_snapshot(paths, job_record.job_id, latest))
            raise IncidentFetchError(f"unable to fetch live evidence for {job_record.job_id}")
    if latest is None:
        raise IncidentFetchError(f"no live bridge and no local incident snapshot for {job_record.job_id}")
    return IncidentFetchResult(
        source="local-fallback",
        snapshot=latest,
        previous_snapshot=_load_previous_snapshot(paths, job_record.job_id, latest),
    )


def load_latest_snapshot(paths: AppPaths, job_id: str) -> IncidentSnapshotRef | None:
    job_dir = incident_state_dir(paths, job_id)
    if not job_dir.exists():
        return None
    entries = sorted((entry for entry in job_dir.iterdir() if entry.is_dir()), reverse=True)
    if not entries:
        return None
    return _snapshot_ref(entries[0])


def _load_previous_snapshot(paths: AppPaths, job_id: str, latest: IncidentSnapshotRef) -> IncidentSnapshotRef | None:
    job_dir = incident_state_dir(paths, job_id)
    entries = sorted((entry for entry in job_dir.iterdir() if entry.is_dir()), reverse=True)
    remaining = [entry for entry in entries if entry.name != latest.snapshot_dir.name]
    if not remaining:
        return None
    return _snapshot_ref(remaining[0])


def _fetch_live_snapshot(paths: AppPaths, job_record: JobRecord, bridge_client) -> IncidentSnapshotRef:
    if not job_record.pbs_job_id:
        raise IncidentFetchError(f"job {job_record.job_id} is missing pbs_job_id")
    if not job_record.stdout_path or not job_record.stderr_path:
        raise IncidentFetchError(f"job {job_record.job_id} is missing stdout/stderr paths")

    qstat_command = shlex.join(build_qstat_command(job_record.pbs_job_id))
    qstat_result = execute_remote_command(bridge_client, qstat_command)
    stdout_result = execute_remote_command(
        bridge_client,
        f"tail -n 200 {shlex.quote(job_record.stdout_path)}",
    )
    stderr_result = execute_remote_command(
        bridge_client,
        f"tail -n 200 {shlex.quote(job_record.stderr_path)}",
    )
    for label, result in (
        ("qstat", qstat_result),
        ("stdout tail", stdout_result),
        ("stderr tail", stderr_result),
    ):
        if result.returncode != 0:
            raise RemoteBridgeError(
                result.stderr.strip() or f"{label} fetch failed for {job_record.job_id}"
            )

    scan_time = datetime.now(UTC).isoformat(timespec="seconds")
    snapshot_dir = incident_snapshot_dir(paths, job_record.job_id, scan_time)
    snapshot_dir.mkdir(parents=True, exist_ok=False)
    qstat_path = snapshot_dir / "qstat.json"
    stdout_path = snapshot_dir / "stdout.tail.log"
    stderr_path = snapshot_dir / "stderr.tail.log"
    qstat_path.write_text(qstat_result.stdout, encoding="utf-8")
    stdout_path.write_text(stdout_result.stdout, encoding="utf-8")
    stderr_path.write_text(stderr_result.stdout, encoding="utf-8")
    return IncidentSnapshotRef(scan_time, snapshot_dir, qstat_path, stdout_path, stderr_path)


def _snapshot_ref(snapshot_dir: Path) -> IncidentSnapshotRef:
    return IncidentSnapshotRef(
        scan_time=snapshot_dir.name.replace("_", ":"),
        snapshot_dir=snapshot_dir,
        qstat_json_path=snapshot_dir / "qstat.json",
        stdout_tail_path=snapshot_dir / "stdout.tail.log",
        stderr_tail_path=snapshot_dir / "stderr.tail.log",
    )
```

Create `src/autoresearch/incidents/normalize.py`:

```python
from __future__ import annotations

import hashlib

from autoresearch.executor.pbs import parse_qstat_json
from autoresearch.runs.registry import JobRecord
from autoresearch.schemas import IncidentFetchResult, NormalizedIncidentInput


def normalize_incident_evidence(
    *,
    job_record: JobRecord,
    fetched: IncidentFetchResult,
) -> NormalizedIncidentInput:
    qstat_text = fetched.snapshot.qstat_json_path.read_text(encoding="utf-8")
    stdout_tail = fetched.snapshot.stdout_tail_path.read_text(encoding="utf-8")
    stderr_tail = fetched.snapshot.stderr_tail_path.read_text(encoding="utf-8")
    qstat = parse_qstat_json(qstat_text)
    current_hash = _tail_hash(stdout_tail, stderr_tail)
    previous_hash = None
    if fetched.previous_snapshot is not None:
        previous_stdout = fetched.previous_snapshot.stdout_tail_path.read_text(encoding="utf-8")
        previous_stderr = fetched.previous_snapshot.stderr_tail_path.read_text(encoding="utf-8")
        previous_hash = _tail_hash(previous_stdout, previous_stderr)
    return NormalizedIncidentInput(
        job_id=job_record.job_id,
        run_id=job_record.run_id,
        pbs_job_id=job_record.pbs_job_id,
        job_state=qstat.state,
        comment=qstat.comment,
        exec_host=qstat.exec_host,
        stdout_tail=stdout_tail,
        stderr_tail=stderr_tail,
        snapshot_dir=fetched.snapshot.snapshot_dir,
        scan_time=fetched.snapshot.scan_time,
        current_log_tail_hash=current_hash,
        previous_log_tail_hash=previous_hash,
    )


def _tail_hash(stdout_tail: str, stderr_tail: str) -> str:
    payload = f"{stdout_tail}\n---\n{stderr_tail}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
```

- [ ] **Step 4: Re-run the fetch tests**

Run:

```bash
pytest tests/test_incident_fetch.py -q
```

Expected: fetch and normalization tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/autoresearch/paths.py src/autoresearch/incidents/fetch.py src/autoresearch/incidents/normalize.py tests/test_incident_fetch.py
git commit -m "feat: add incident evidence fetch and normalization"
```

## Task 5: Wire Incident Scan, List, And Summarize Into The CLI

**Files:**
- Modify: `src/autoresearch/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing CLI tests**

Add these tests to `tests/test_cli.py`:

```python
def test_incident_list_prints_open_incidents(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AUTORESEARCH_DB", str(tmp_path / "state" / "autoresearch.db"))
    monkeypatch.setenv("AUTORESEARCH_REPO_ROOT", str(tmp_path))
    _write_repo_config(tmp_path)
    init_result = runner.invoke(app, ["db", "init"])
    assert init_result.exit_code == 0

    from autoresearch.incidents.registry import IncidentRegistry

    registry = IncidentRegistry(tmp_path / "state" / "autoresearch.db")
    registry.upsert_incident(
        run_id="run_demo",
        job_id="job_demo",
        severity="HIGH",
        category="ENV_IMPORT_ERROR",
        fingerprint="no module named nonexistent_package",
        evidence={
            "scan_time": "2026-04-16T00:00:00+00:00",
            "snapshot_dir": "/tmp/scan",
            "classifier_rule": "import-error",
            "matched_lines": ["ModuleNotFoundError"],
        },
    )

    result = runner.invoke(app, ["incident", "list"])

    assert result.exit_code == 0
    assert "job_demo" in result.stdout
    assert "ENV_IMPORT_ERROR" in result.stdout


def test_incident_summarize_prints_counts(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AUTORESEARCH_DB", str(tmp_path / "state" / "autoresearch.db"))
    monkeypatch.setenv("AUTORESEARCH_REPO_ROOT", str(tmp_path))
    _write_repo_config(tmp_path)
    runner.invoke(app, ["db", "init"])

    from autoresearch.incidents.registry import IncidentRegistry

    registry = IncidentRegistry(tmp_path / "state" / "autoresearch.db")
    registry.upsert_incident(
        run_id="run_demo",
        job_id="job_demo",
        severity="CRITICAL",
        category="RESOURCE_OOM",
        fingerprint="oom",
        evidence={
            "scan_time": "2026-04-16T00:10:00+00:00",
            "snapshot_dir": "/tmp/scan",
            "classifier_rule": "oom-line",
            "matched_lines": ["out of memory"],
        },
    )

    result = runner.invoke(app, ["incident", "summarize"])

    assert result.exit_code == 0
    assert "RESOURCE_OOM: 1" in result.stdout


def test_incident_scan_reports_created_incident(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AUTORESEARCH_DB", str(tmp_path / "state" / "autoresearch.db"))
    monkeypatch.setenv("AUTORESEARCH_REPO_ROOT", str(tmp_path))
    _write_repo_config(tmp_path)
    runner.invoke(app, ["db", "init"])

    registry = RunRegistry(tmp_path / "state" / "autoresearch.db")
    run_record = registry.create_run(RunCreateRequest(run_kind="probe", project="demo"))
    job_record = registry.create_job(
        run_id=run_record.run_id,
        backend="pbs",
        queue="debug",
        walltime="00:10:00",
        filesystems="eagle",
        select_expr="1:system=polaris",
        place_expr="scatter",
        stdout_path="/eagle/demo/stdout.log",
        stderr_path="/eagle/demo/stderr.log",
        pbs_job_id="12345.polaris",
    )

    class FakeBridge:
        def status(self):
            return BridgeStatusResult(
                alias="polaris-relay",
                state="DETACHED",
                explanation="detached",
                command_result=None,
                control_path_exists=None,
            )

    def fake_collect(paths, job_record, bridge_client):
        snapshot_dir = tmp_path / "state" / "incidents" / job_record.job_id / "2026-04-16T00_00_00+00_00"
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        (snapshot_dir / "qstat.json").write_text('{"Jobs":{"12345.polaris":{"job_state":"F","comment":"","exec_host":"x1001"}}}', encoding="utf-8")
        (snapshot_dir / "stdout.tail.log").write_text("", encoding="utf-8")
        (snapshot_dir / "stderr.tail.log").write_text("ModuleNotFoundError: No module named \\'pkg\\'\\n", encoding="utf-8")
        from autoresearch.schemas import IncidentFetchResult, IncidentSnapshotRef
        snapshot = IncidentSnapshotRef(
            scan_time="2026-04-16T00:00:00+00:00",
            snapshot_dir=snapshot_dir,
            qstat_json_path=snapshot_dir / "qstat.json",
            stdout_tail_path=snapshot_dir / "stdout.tail.log",
            stderr_tail_path=snapshot_dir / "stderr.tail.log",
        )
        return IncidentFetchResult(source="local-fallback", snapshot=snapshot, previous_snapshot=None)

    monkeypatch.setattr(cli_module, "build_bridge_service", lambda: FakeBridge())
    monkeypatch.setattr(cli_module, "collect_incident_evidence", fake_collect)

    result = runner.invoke(app, ["incident", "scan", "--job-id", job_record.job_id])

    assert result.exit_code == 0
    assert "incident created" in result.stdout
    assert "ENV_IMPORT_ERROR" in result.stdout
```

- [ ] **Step 2: Run the CLI tests to verify failure**

Run:

```bash
pytest tests/test_cli.py -q
```

Expected: failures because the `incident` Typer group and commands do not exist yet.

- [ ] **Step 3: Implement a thin incident CLI**

Update `src/autoresearch/cli.py`:

```python
from autoresearch.incidents.classifier import classify_incident
from autoresearch.incidents.fetch import IncidentFetchError, collect_incident_evidence
from autoresearch.incidents.normalize import normalize_incident_evidence
from autoresearch.incidents.registry import IncidentRegistry
from autoresearch.incidents.summaries import render_incident_row, render_incident_summary


incident_app = typer.Typer(help="Incident detection commands.")
app.add_typer(incident_app, name="incident")


@incident_app.command("scan")
def scan_incident(
    job_id: str = typer.Option(..., "--job-id"),
) -> None:
    settings = load_settings()
    run_registry = RunRegistry(settings.paths.db_path)
    incident_registry = IncidentRegistry(settings.paths.db_path)
    job_record = run_registry.get_job(job_id)
    bridge = build_bridge_service()
    try:
        fetched = collect_incident_evidence(settings.paths, job_record, bridge)
    except IncidentFetchError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1)

    normalized = normalize_incident_evidence(job_record=job_record, fetched=fetched)
    classified = classify_incident(normalized)
    if classified is None:
        typer.echo(f"{job_id}\t{fetched.source}\tno incident detected")
        return

    existing_ids = {
        row.incident_id
        for row in incident_registry.list_open_incidents()
        if row.job_id == job_id and row.category == classified.category and row.fingerprint == classified.fingerprint
    }
    record = incident_registry.upsert_incident(
        run_id=job_record.run_id,
        job_id=job_record.job_id,
        severity=classified.severity,
        category=classified.category,
        fingerprint=classified.fingerprint,
        evidence={
            "scan_time": normalized.scan_time,
            "snapshot_dir": str(normalized.snapshot_dir),
            "qstat_comment": normalized.comment,
            "job_state": normalized.job_state,
            "exec_host": normalized.exec_host,
            "matched_lines": list(classified.matched_lines),
            "classifier_rule": classified.rule_name,
        },
    )
    action = "incident updated" if record.incident_id in existing_ids else "incident created"
    typer.echo(f"{job_id}\t{fetched.source}\t{action}\t{classified.category}\t{classified.severity}")


@incident_app.command("list")
def list_incidents() -> None:
    settings = load_settings()
    registry = IncidentRegistry(settings.paths.db_path)
    for record in registry.list_open_incidents():
        typer.echo(render_incident_row(record))


@incident_app.command("summarize")
def summarize_incidents() -> None:
    settings = load_settings()
    registry = IncidentRegistry(settings.paths.db_path)
    typer.echo(render_incident_summary(registry.summarize_open_incidents()))
```

- [ ] **Step 4: Re-run the CLI tests**

Run:

```bash
pytest tests/test_cli.py -q
```

Expected: CLI tests pass, including the new `incident` commands.

- [ ] **Step 5: Commit**

```bash
git add src/autoresearch/cli.py tests/test_cli.py
git commit -m "feat: add incident cli commands"
```

## Task 6: Update Operator Docs And Run Full Verification

**Files:**
- Modify: `docs/architecture.md`
- Modify: `docs/runbook.md`

- [ ] **Step 1: Add manual incident-flow documentation**

Update `docs/architecture.md` with a short Phase 4A section:

```md
## Incident Detection Flow

Phase 4A is manual and operator-triggered.

1. `autoresearch incident scan --job-id <job_id>`
2. If the bridge is attached, fetch fresh `qstat -fF JSON` and stdout/stderr tails
3. Persist evidence under `state/incidents/<job_id>/<scan_ts>/`
4. Normalize and classify deterministically
5. Upsert an `OPEN` incident by `job_id + category + fingerprint`

If the bridge is detached or stale, the scan falls back to the newest local snapshot. Phase 4A does not auto-resolve incidents and does not perform retries.
```

Update `docs/runbook.md` with operator commands:

~~~md
## Manual Incident Triage

Scan one job:

```bash
python -m autoresearch.cli incident scan --job-id <job_id>
```

List open incidents:

```bash
python -m autoresearch.cli incident list
```

Summarize open incidents:

```bash
python -m autoresearch.cli incident summarize
```

Evidence snapshots are stored under `state/incidents/<job_id>/<scan_ts>/`.
If the Polaris bridge is unavailable, scans reuse the newest local snapshot instead of fetching live scheduler or log data.
~~~

- [ ] **Step 2: Run targeted tests for the new modules**

Run:

```bash
pytest tests/test_db.py tests/test_incident_classifier.py tests/test_incident_registry.py tests/test_incident_fetch.py tests/test_cli.py -q
```

Expected: all targeted tests pass.

- [ ] **Step 3: Run full verification**

Run:

```bash
pytest -q
PYTHONPATH=src python -m autoresearch.cli incident --help
```

Expected:
- `pytest -q` passes
- CLI help shows `scan`, `list`, and `summarize`

- [ ] **Step 4: Commit**

```bash
git add docs/architecture.md docs/runbook.md
git commit -m "docs: add phase4a incident workflow"
```
