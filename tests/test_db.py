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
