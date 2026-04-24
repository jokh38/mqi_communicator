"""Regression tests for atomic case repository operations."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from src.database.connection import DatabaseConnection
from src.domain.enums import BeamStatus, CaseStatus
from src.repositories.case_repo import CaseRepository


def test_update_case_and_beams_status_rolls_back_case_when_beam_update_fails(tmp_path: Path) -> None:
    db_path = tmp_path / "case_repo_atomic.db"
    settings = Mock()
    settings.get_database_config.return_value = {
        "connection_timeout_seconds": 30,
        "journal_mode": "WAL",
        "synchronous": "NORMAL",
        "cache_size": -2000,
    }
    logger = Mock()
    db = DatabaseConnection(db_path=db_path, settings=settings, logger=logger)
    try:
        db.init_db()
        repo = CaseRepository(db, logger)
        repo.add_case("case-1", tmp_path / "case-1")
        repo.create_beam_record("beam-1", "case-1", tmp_path / "case-1" / "beam-1")
        db.connection.execute(
            """
            CREATE TRIGGER fail_beam_status_update
            BEFORE UPDATE OF status ON beams
            BEGIN
                SELECT RAISE(FAIL, 'forced beam update failure');
            END
            """
        )
        db.connection.commit()

        with pytest.raises(Exception, match="forced beam update failure"):
            repo.update_case_and_beams_status(
                "case-1",
                CaseStatus.PROCESSING,
                BeamStatus.CSV_INTERPRETING,
                progress=10.0,
            )

        case_row = db.connection.execute(
            "SELECT status, progress FROM cases WHERE case_id = ?",
            ("case-1",),
        ).fetchone()
    finally:
        db.close()

    if case_row["status"] != CaseStatus.PENDING.value:
        raise AssertionError(f"Expected rolled-back case status pending, got {case_row['status']!r}")
    if case_row["progress"] != 0.0:
        raise AssertionError(f"Expected rolled-back case progress 0.0, got {case_row['progress']!r}")
