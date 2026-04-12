from pathlib import Path
from unittest.mock import MagicMock

from src.database.connection import DatabaseConnection
from src.repositories.case_repo import CaseRepository


def _make_repo(tmp_path):
    logger = MagicMock()
    settings = MagicMock()
    settings.get_database_config.return_value = {}
    conn = DatabaseConnection(db_path=tmp_path / "test.db", settings=settings, logger=logger)
    conn.init_db()
    return CaseRepository(conn, logger)


def test_create_or_update_deliveries_persists_fraction_index(tmp_path):
    repo = _make_repo(tmp_path)

    case_id = "caseA"
    repo.add_case(case_id, tmp_path / case_id)
    repo.create_case_with_beams(
        case_id,
        str(tmp_path / case_id),
        [{"beam_id": f"{case_id}_beam_1", "beam_path": tmp_path / case_id / "b1", "beam_number": 1}],
    )

    repo.create_or_update_deliveries(
        case_id,
        [
            {
                "delivery_id": "d1",
                "beam_id": f"{case_id}_beam_1",
                "delivery_path": tmp_path / case_id / "2026032000000000",
                "delivery_timestamp": "2026-03-20T00:00:00",
                "delivery_date": "2026-03-20",
                "raw_beam_number": 1,
                "treatment_beam_index": 1,
                "is_reference_delivery": True,
                "fraction_index": 3,
            }
        ],
    )

    loaded = repo.get_deliveries_for_case(case_id)
    assert len(loaded) == 1
    assert loaded[0].fraction_index == 3


def test_create_or_update_deliveries_defaults_fraction_index_to_none(tmp_path):
    repo = _make_repo(tmp_path)
    case_id = "caseB"
    repo.add_case(case_id, tmp_path / case_id)
    repo.create_case_with_beams(
        case_id,
        str(tmp_path / case_id),
        [{"beam_id": f"{case_id}_beam_1", "beam_path": tmp_path / case_id / "b1", "beam_number": 1}],
    )

    repo.create_or_update_deliveries(
        case_id,
        [
            {
                "delivery_id": "d1",
                "beam_id": f"{case_id}_beam_1",
                "delivery_path": tmp_path / case_id / "2026032000000000",
                "delivery_timestamp": "2026-03-20T00:00:00",
                "delivery_date": "2026-03-20",
                "raw_beam_number": 1,
                "treatment_beam_index": 1,
                "is_reference_delivery": True,
            }
        ],
    )

    loaded = repo.get_deliveries_for_case(case_id)
    assert loaded[0].fraction_index is None
