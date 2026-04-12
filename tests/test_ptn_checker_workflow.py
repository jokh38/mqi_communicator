from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.domain.enums import CaseStatus


def _case(case_id: str, status: CaseStatus):
    return SimpleNamespace(
        case_id=case_id,
        case_path=Path(f"/cases/{case_id}"),
        status=status,
        progress=100.0,
        created_at=datetime(2026, 1, 1, 0, 0, 0),
        updated_at=datetime(2026, 1, 1, 0, 0, 0),
        error_message=None,
        assigned_gpu=None,
        interpreter_completed=True,
        retry_count=0,
    )


def test_should_route_completed_case_with_stable_ptn_to_ptn_checker(tmp_path):
    from src.core.workflow_manager import should_route_case_to_ptn_checker

    case_path = tmp_path / "55061194"
    case_path.mkdir()
    ptn_file = case_path / "delivered.ptn"
    ptn_file.write_text("stable", encoding="utf-8")

    settings = MagicMock()
    settings.get_ptn_checker_config.return_value = {
        "stability_window_seconds": 30,
        "min_file_age_seconds": 5,
        "size_poll_interval_seconds": 1,
    }

    result = should_route_case_to_ptn_checker(
        case_data=_case("55061194", CaseStatus.COMPLETED),
        case_path=case_path,
        settings=settings,
        observed_ptn_files=[ptn_file],
    )

    if result is not True:
        raise AssertionError("Expected completed case with stable PTN file to route to PTN checker")


def test_should_not_route_non_completed_case_to_ptn_checker(tmp_path):
    from src.core.workflow_manager import should_route_case_to_ptn_checker

    case_path = tmp_path / "55061194"
    case_path.mkdir()
    ptn_file = case_path / "delivered.ptn"
    ptn_file.write_text("stable", encoding="utf-8")

    settings = MagicMock()
    settings.get_ptn_checker_config.return_value = {
        "stability_window_seconds": 30,
        "min_file_age_seconds": 5,
        "size_poll_interval_seconds": 1,
    }

    result = should_route_case_to_ptn_checker(
        case_data=_case("55061194", CaseStatus.PROCESSING),
        case_path=case_path,
        settings=settings,
        observed_ptn_files=[ptn_file],
    )

    if result is not False:
        raise AssertionError("Expected non-completed cases to stay on the existing MC path")
