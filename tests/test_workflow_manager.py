from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.domain.enums import CaseStatus
from src.core.workflow_manager import scan_existing_cases


def _case(case_id: str, status: CaseStatus):
    return SimpleNamespace(
        case_id=case_id,
        case_path=Path(f"/cases/{case_id}"),
        status=status,
        progress=0.0,
        created_at=datetime(2026, 1, 1, 0, 0, 0),
        updated_at=datetime(2026, 1, 1, 0, 0, 0),
        error_message=None,
        assigned_gpu=None,
        interpreter_completed=False,
    )


def test_scan_existing_cases_requeues_failed_case(tmp_path):
    case_path = tmp_path / "55061194"
    case_path.mkdir()

    settings = MagicMock()
    settings.get_case_directories.return_value = {"scan": tmp_path}
    logger = MagicMock()
    case_queue = MagicMock()
    case_repo = MagicMock()
    case_repo.get_all_case_ids.return_value = ["55061194"]
    case_repo.get_case.return_value = _case("55061194", CaseStatus.FAILED)

    context_manager = MagicMock()
    context_manager.__enter__.return_value = case_repo
    context_manager.__exit__.return_value = False

    with patch("src.core.workflow_manager.get_db_session", return_value=context_manager), \
         patch("src.core.workflow_manager._queue_case", return_value=True) as queue_mock:
        scan_existing_cases(case_queue, settings, logger)

    queue_mock.assert_called_once_with("55061194", case_path, case_queue, logger)


def test_scan_existing_cases_skips_completed_case(tmp_path):
    case_path = tmp_path / "55061194"
    case_path.mkdir()

    settings = MagicMock()
    settings.get_case_directories.return_value = {"scan": tmp_path}
    logger = MagicMock()
    case_queue = MagicMock()
    case_repo = MagicMock()
    case_repo.get_all_case_ids.return_value = ["55061194"]
    case_repo.get_case.return_value = _case("55061194", CaseStatus.COMPLETED)

    context_manager = MagicMock()
    context_manager.__enter__.return_value = case_repo
    context_manager.__exit__.return_value = False

    with patch("src.core.workflow_manager.get_db_session", return_value=context_manager), \
         patch("src.core.workflow_manager._queue_case") as queue_mock:
        scan_existing_cases(case_queue, settings, logger)

    queue_mock.assert_not_called()


def test_scan_existing_cases_requeues_processing_case(tmp_path):
    case_path = tmp_path / "55061194"
    case_path.mkdir()

    settings = MagicMock()
    settings.get_case_directories.return_value = {"scan": tmp_path}
    logger = MagicMock()
    case_queue = MagicMock()
    case_repo = MagicMock()
    case_repo.get_all_case_ids.return_value = ["55061194"]
    case_repo.get_case.return_value = _case("55061194", CaseStatus.PROCESSING)

    context_manager = MagicMock()
    context_manager.__enter__.return_value = case_repo
    context_manager.__exit__.return_value = False

    with patch("src.core.workflow_manager.get_db_session", return_value=context_manager), \
         patch("src.core.workflow_manager._queue_case", return_value=True) as queue_mock:
        scan_existing_cases(case_queue, settings, logger)

    queue_mock.assert_called_once_with("55061194", case_path, case_queue, logger)
