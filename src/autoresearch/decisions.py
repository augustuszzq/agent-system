from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import sqlite3
import uuid

from autoresearch.db import connect_db


@dataclass(frozen=True)
class DecisionRecord:
    decision_id: str
    created_at: str
    target_type: str
    target_id: str
    decision: str
    rationale: str | None
    actor: str


class DecisionLog:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    def append(
        self,
        *,
        target_type: str,
        target_id: str,
        decision: str,
        rationale: str | None,
        actor: str,
    ) -> DecisionRecord:
        record = DecisionRecord(
            decision_id=f"decision_{uuid.uuid4().hex[:12]}",
            created_at=datetime.now(UTC).isoformat(),
            target_type=target_type,
            target_id=target_id,
            decision=decision,
            rationale=rationale,
            actor=actor,
        )
        with connect_db(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO decisions (
                    decision_id,
                    created_at,
                    target_type,
                    target_id,
                    decision,
                    rationale,
                    actor
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.decision_id,
                    record.created_at,
                    record.target_type,
                    record.target_id,
                    record.decision,
                    record.rationale,
                    record.actor,
                ),
            )
        return record

    def list_for_target(self, target_type: str, target_id: str) -> list[DecisionRecord]:
        with connect_db(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT decision_id, created_at, target_type, target_id,
                       decision, rationale, actor
                FROM decisions
                WHERE target_type = ? AND target_id = ?
                ORDER BY created_at ASC, decision_id ASC
                """,
                (target_type, target_id),
            ).fetchall()
        return [DecisionRecord(**dict(row)) for row in rows]

