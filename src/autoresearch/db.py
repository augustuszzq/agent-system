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
