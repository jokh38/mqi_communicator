from datetime import datetime
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.domain.enums import CaseStatus
from src.core.workflow_manager import CaseDetectionHandler, scan_existing_cases


def _case(case_id: str, status: CaseStatus, retry_count: int = 0):
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
        retry_count=retry_count,
    )


def test_scan_existing_cases_skips_failed_case(tmp_path):
    """W-4 fix: FAILED cases are non-retryable and should be skipped."""
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
         patch("src.core.workflow_manager._queue_case") as queue_mock:
        scan_existing_cases(case_queue, settings, logger)

    queue_mock.assert_not_called()


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
    settings.get_processing_config.return_value = {"max_case_retries": 3}
    logger = MagicMock()
    case_queue = MagicMock()
    case_repo = MagicMock()
    case_repo.get_all_case_ids.return_value = ["55061194"]
    case_repo.get_case.return_value = _case("55061194", CaseStatus.PROCESSING, retry_count=0)

    context_manager = MagicMock()
    context_manager.__enter__.return_value = case_repo
    context_manager.__exit__.return_value = False

    with patch("src.core.workflow_manager.get_db_session", return_value=context_manager), \
         patch("src.core.workflow_manager._queue_case", return_value=True) as queue_mock:
        scan_existing_cases(case_queue, settings, logger)

    queue_mock.assert_called_once_with("55061194", case_path, case_queue, logger)


def test_case_detection_handler_queues_completed_case_when_ptn_file_stabilizes(tmp_path):
    case_queue = MagicMock()
    logger = MagicMock()
    handler = CaseDetectionHandler(case_queue, logger)
    handler.settings = MagicMock()
    handler.settings.get_ptn_checker_config.return_value = {
        "stability_window_seconds": 30,
        "min_file_age_seconds": 5,
        "size_poll_interval_seconds": 1,
    }
    handler.case_repo = MagicMock()
    handler.case_repo.get_case.return_value = _case("55061194", CaseStatus.COMPLETED)

    case_path = tmp_path / "55061194"
    case_path.mkdir()
    ptn_file = case_path / "delivered.ptn"
    ptn_file.write_text("stable", encoding="utf-8")
    event = SimpleNamespace(is_directory=False, src_path=str(ptn_file))

    with patch("src.core.workflow_manager._queue_case", return_value=True) as queue_mock:
        handler.on_created(event)

    queue_mock.assert_called_once_with("55061194", case_path, case_queue, logger, reason="ptn_checker")


def test_case_detection_handler_ignores_non_completed_case_ptn_event(tmp_path):
    case_queue = MagicMock()
    logger = MagicMock()
    handler = CaseDetectionHandler(case_queue, logger)
    handler.settings = MagicMock()
    handler.settings.get_ptn_checker_config.return_value = {
        "stability_window_seconds": 30,
        "min_file_age_seconds": 0,
        "size_poll_interval_seconds": 1,
    }
    handler.case_repo = MagicMock()
    handler.case_repo.get_case.return_value = _case("55061194", CaseStatus.PROCESSING)

    case_path = tmp_path / "55061194"
    case_path.mkdir()
    ptn_file = case_path / "delivered.ptn"
    ptn_file.write_text("stable", encoding="utf-8")
    now = time.time() - 10
    with patch.object(Path, "stat", return_value=SimpleNamespace(st_size=6, st_mtime=now)):
        event = SimpleNamespace(is_directory=False, src_path=str(ptn_file))
        with patch("src.core.workflow_manager._queue_case", return_value=True) as queue_mock:
            handler.on_created(event)

    queue_mock.assert_not_called()


def test_scan_existing_cases_discovers_nested_study_directories(tmp_path):
    scan_root = tmp_path / "G1"
    study_path = scan_root / "04198922" / "1.2.3.4"
    delivery_path = study_path / "2026031923483900"
    delivery_path.mkdir(parents=True)
    (study_path / "RP.test.dcm").write_text("", encoding="ascii")
    (delivery_path / "PlanInfo.txt").write_text(
        "DICOM_PATIENT_ID,04198922\nDICOM_BEAM_NUMBER,2\n",
        encoding="ascii",
    )
    (delivery_path / "sample.ptn").write_text("", encoding="ascii")

    settings = MagicMock()
    settings.get_case_directories.return_value = {"scan": scan_root}
    settings.get_processing_config.return_value = {"max_case_retries": 3}
    logger = MagicMock()
    case_queue = MagicMock()
    case_repo = MagicMock()
    case_repo.get_all_case_ids.return_value = []

    context_manager = MagicMock()
    context_manager.__enter__.return_value = case_repo
    context_manager.__exit__.return_value = False

    with patch("src.core.workflow_manager.get_db_session", return_value=context_manager), \
         patch("src.core.workflow_manager._queue_case", return_value=True) as queue_mock:
        scan_existing_cases(case_queue, settings, logger)

    queue_mock.assert_called_once_with("04198922", study_path, case_queue, logger)
