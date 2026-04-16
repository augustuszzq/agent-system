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
