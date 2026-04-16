import sqlite3
from pathlib import Path

import pytest

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


def test_init_db_migrates_existing_incidents_table_without_updated_at(
    tmp_path: Path,
) -> None:
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


def test_init_db_migrated_incidents_table_rejects_missing_updated_at(
    tmp_path: Path,
) -> None:
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
        conn.commit()
    finally:
        conn.close()

    init_db(db_path)

    conn = sqlite3.connect(db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO incidents (
                    incident_id, run_id, job_id, severity, category, fingerprint,
                    evidence_json, auto_action, status, created_at, resolved_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "inc_missing_updated_at",
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
    finally:
        conn.close()


def test_init_db_migrates_nullable_existing_updated_at_column(
    tmp_path: Path,
) -> None:
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
              updated_at TEXT,
              resolved_at TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO incidents (
                incident_id, run_id, job_id, severity, category, fingerprint,
                evidence_json, auto_action, status, created_at, updated_at,
                resolved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "inc_nullable_updated_at",
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
            ("inc_nullable_updated_at",),
        ).fetchone()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO incidents (
                    incident_id, run_id, job_id, severity, category, fingerprint,
                    evidence_json, auto_action, status, created_at, resolved_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "inc_missing_updated_at_after_nullable_migration",
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
    finally:
        conn.close()

    assert row == ("2026-04-16T00:00:00+00:00", "2026-04-16T00:00:00+00:00")
