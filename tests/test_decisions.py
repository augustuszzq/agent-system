from pathlib import Path

from autoresearch.db import init_db
from autoresearch.decisions import DecisionLog


def test_append_decision_persists_row(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"
    init_db(db_path)
    log = DecisionLog(db_path)

    record = log.append(
        target_type="retry_request",
        target_id="retry_123",
        decision="approve-retry",
        rationale="filesystem incident cleared",
        actor="operator",
    )

    rows = log.list_for_target("retry_request", "retry_123")

    assert rows == [record]
    assert record.actor == "operator"

