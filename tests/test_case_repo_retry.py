from pathlib import Path
from unittest.mock import MagicMock

from src.database.connection import DatabaseConnection
from src.domain.enums import BeamStatus, CaseStatus
from src.repositories.case_repo import CaseRepository


def _make_repo(tmp_path):
    logger = MagicMock()
    settings = MagicMock()
    settings.get_database_config.return_value = {}
    conn = DatabaseConnection(db_path=tmp_path / "test-retry.db", settings=settings, logger=logger)
    conn.init_db()
    return CaseRepository(conn, logger)


def test_reset_case_and_beams_for_retry_clears_stale_failure_state(tmp_path):
    repo = _make_repo(tmp_path)
    case_id = "case-retry"
    case_path = tmp_path / case_id
    repo.create_case_with_beams(
        case_id,
        str(case_path),
        [{"beam_id": f"{case_id}_beam_1", "beam_path": case_path / "beam1", "beam_number": 1}],
    )

    repo.update_case_status(case_id, CaseStatus.FAILED, progress=72.0, error_message="old case failure")
    repo.update_beam_status(f"{case_id}_beam_1", BeamStatus.FAILED, error_message="old beam failure")
    repo.update_beam_progress(f"{case_id}_beam_1", 88.0)

    repo.reset_case_and_beams_for_retry(case_id)

    case_data = repo.get_case(case_id)
    beam_data = repo.get_beam(f"{case_id}_beam_1")

    assert case_data.status == CaseStatus.PENDING
    assert case_data.progress == 0.0
    assert case_data.error_message == ""
    assert beam_data.status == BeamStatus.PENDING
    assert beam_data.progress == 0.0
    assert beam_data.error_message == ""
