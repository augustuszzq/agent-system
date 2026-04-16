from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Iterator

from autoresearch.models import (
    DECISIONS_TABLE_SQL,
    INCIDENTS_TABLE_SQL,
    JOBS_TABLE_SQL,
    RETRY_REQUESTS_TABLE_SQL,
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


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row[1] for row in rows}


def _incident_updated_at_is_not_null(conn: sqlite3.Connection) -> bool:
    rows = conn.execute("PRAGMA table_info(incidents)").fetchall()
    for row in rows:
        if row[1] == "updated_at":
            return bool(row[3])
    return False


def _rebuild_incidents_table(conn: sqlite3.Connection) -> None:
    columns = _table_columns(conn, "incidents")
    if "updated_at" in columns:
        conn.execute(
            """
            CREATE TABLE incidents_new(
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
        )
        conn.execute(
            """
            INSERT INTO incidents_new (
                incident_id, run_id, job_id, severity, category, fingerprint,
                evidence_json, auto_action, status, created_at, updated_at,
                resolved_at
            )
            SELECT
                incident_id, run_id, job_id, severity, category, fingerprint,
                evidence_json, auto_action, status, created_at,
                COALESCE(updated_at, created_at),
                resolved_at
            FROM incidents
            """
        )
    else:
        conn.execute(
            """
            CREATE TABLE incidents_new(
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
        )
        conn.execute(
            """
            INSERT INTO incidents_new (
                incident_id, run_id, job_id, severity, category, fingerprint,
                evidence_json, auto_action, status, created_at, updated_at,
                resolved_at
            )
            SELECT
                incident_id, run_id, job_id, severity, category, fingerprint,
                evidence_json, auto_action, status, created_at,
                created_at,
                resolved_at
            FROM incidents
            """
        )

    conn.execute("DROP TABLE incidents")
    conn.execute("ALTER TABLE incidents_new RENAME TO incidents")


def _ensure_incidents_updated_at(conn: sqlite3.Connection) -> None:
    columns = _table_columns(conn, "incidents")
    if "updated_at" not in columns:
        _rebuild_incidents_table(conn)
        return

    if _incident_updated_at_is_not_null(conn):
        return

    _rebuild_incidents_table(conn)


def init_db(db_path: Path) -> None:
    with connect_db(db_path) as conn:
        conn.execute(RUNS_TABLE_SQL)
        conn.execute(JOBS_TABLE_SQL)
        conn.execute(INCIDENTS_TABLE_SQL)
        conn.execute(DECISIONS_TABLE_SQL)
        conn.execute(RETRY_REQUESTS_TABLE_SQL)
        _ensure_incidents_updated_at(conn)
