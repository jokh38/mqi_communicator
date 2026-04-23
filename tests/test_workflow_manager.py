from datetime import datetime
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.domain.enums import CaseStatus
from src.core.workflow_manager import (
    CaseDetectionHandler,
    _derive_case_identity,
    _discover_case_directories,
    derive_room_from_case_path,
    derive_room_from_path,
    scan_existing_cases,
)


def _case(case_id: str, status: CaseStatus, retry_count: int = 0, error_message: str = None):
    return SimpleNamespace(
        case_id=case_id,
        case_path=Path(f"/cases/{case_id}"),
        status=status,
        progress=0.0,
        created_at=datetime(2026, 1, 1, 0, 0, 0),
        updated_at=datetime(2026, 1, 1, 0, 0, 0),
        error_message=error_message,
        assigned_gpu=None,
        interpreter_completed=False,
        retry_count=retry_count,
    )


def _build_ready_case_tree(case_path: Path, *, marker: bool = True) -> None:
    delivery_path = case_path / "2026031923483900"
    delivery_path.mkdir(parents=True)
    (case_path / "RP.test.dcm").write_text("", encoding="ascii")
    (delivery_path / "PlanInfo.txt").write_text(
        "DICOM_PATIENT_ID,04198922\nDICOM_BEAM_NUMBER,2\n",
        encoding="ascii",
    )
    (delivery_path / "sample.ptn").write_text("", encoding="ascii")
    if marker:
        (case_path / "_CASE_READY").write_text("ready\n", encoding="ascii")


def test_scan_existing_cases_skips_failed_case(tmp_path):
    """Non-retryable FAILED cases should remain skipped."""
    case_path = tmp_path / "55061194"
    _build_ready_case_tree(case_path)

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


def test_scan_existing_cases_requeues_retryable_failed_case_without_incrementing_retry_count(tmp_path):
    case_path = tmp_path / "55061194"
    _build_ready_case_tree(case_path)

    settings = MagicMock()
    settings.get_case_directories.return_value = {"scan": tmp_path}
    settings.get_processing_config.return_value = {"max_case_retries": 3}
    logger = MagicMock()
    case_queue = MagicMock()
    case_repo = MagicMock()
    case_repo.get_all_case_ids.return_value = ["55061194"]
    case_repo.get_case.return_value = _case(
        "55061194",
        CaseStatus.FAILED,
        retry_count=1,
        error_message="[RETRYABLE] CSV interpreting failed",
    )

    context_manager = MagicMock()
    context_manager.__enter__.return_value = case_repo
    context_manager.__exit__.return_value = False

    with patch("src.core.workflow_manager.get_db_session", return_value=context_manager), \
         patch("src.core.workflow_manager._queue_case", return_value=True) as queue_mock:
        scan_existing_cases(case_queue, settings, logger)

    queue_mock.assert_called_once_with("55061194", case_path, case_queue, logger)
    case_repo.increment_retry_count.assert_not_called()


def test_scan_existing_cases_skips_completed_case(tmp_path):
    case_path = tmp_path / "55061194"
    _build_ready_case_tree(case_path)

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
    _build_ready_case_tree(case_path)

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
    _build_ready_case_tree(case_path)
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


def test_case_detection_handler_ignores_non_retryable_failed_directory_event(tmp_path):
    case_queue = MagicMock()
    logger = MagicMock()
    handler = CaseDetectionHandler(case_queue, logger)
    handler.settings = MagicMock()
    handler.case_repo = MagicMock()
    handler.case_repo.get_case.return_value = _case("55061194", CaseStatus.FAILED)

    case_path = tmp_path / "55061194"
    case_path.mkdir()
    event = SimpleNamespace(is_directory=True, src_path=str(case_path))

    with patch("src.core.workflow_manager._queue_case", return_value=True) as queue_mock:
        handler.on_created(event)

    queue_mock.assert_not_called()


def test_case_detection_handler_allows_retryable_failed_directory_event(tmp_path):
    case_queue = MagicMock()
    logger = MagicMock()
    handler = CaseDetectionHandler(case_queue, logger)
    handler.settings = MagicMock()
    handler.case_repo = MagicMock()
    handler.case_repo.get_case.return_value = _case(
        "55061194",
        CaseStatus.FAILED,
        error_message="Could not match delivery folders to RT plan beams",
    )

    case_path = tmp_path / "55061194"
    _build_ready_case_tree(case_path)
    event = SimpleNamespace(is_directory=True, src_path=str(case_path))

    with patch("src.core.workflow_manager._queue_case", return_value=True) as queue_mock:
        handler.on_created(event)

    queue_mock.assert_called_once_with("55061194", case_path, case_queue, logger)


def test_case_detection_handler_ignores_group_directory_without_ready_marker(tmp_path):
    case_queue = MagicMock()
    logger = MagicMock()
    handler = CaseDetectionHandler(case_queue, logger)
    handler.settings = MagicMock()
    handler.case_repo = MagicMock()

    group_path = tmp_path / "g1"
    group_path.mkdir()
    event = SimpleNamespace(is_directory=True, src_path=str(group_path))

    with patch("src.core.workflow_manager._queue_case", return_value=True) as queue_mock:
        handler.on_created(event)

    queue_mock.assert_not_called()


def test_case_detection_handler_ignores_case_directory_until_ready_marker_exists(tmp_path):
    case_queue = MagicMock()
    logger = MagicMock()
    handler = CaseDetectionHandler(case_queue, logger)
    handler.settings = MagicMock()
    handler.case_repo = MagicMock()

    case_path = tmp_path / "1.2.3.4"
    _build_ready_case_tree(case_path, marker=False)
    event = SimpleNamespace(is_directory=True, src_path=str(case_path))

    with patch("src.core.workflow_manager._queue_case", return_value=True) as queue_mock:
        handler.on_created(event)

    queue_mock.assert_not_called()


def test_scan_existing_cases_discovers_nested_study_directories(tmp_path):
    scan_root = tmp_path / "G1"
    study_path = scan_root / "04198922" / "1.2.3.4"
    _build_ready_case_tree(study_path)

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

    queue_mock.assert_called_once_with("1.2.3.4", study_path, case_queue, logger)


def test_derive_case_identity_uses_study_uid_in_grouped_layout(tmp_path):
    study_path = tmp_path / "G2" / "1.2.840.10008.1.2.3"
    _build_ready_case_tree(study_path)

    case_id, case_path = _derive_case_identity(study_path)

    assert case_id == "1.2.840.10008.1.2.3"
    assert case_path == study_path


def test_discover_case_directories_does_not_fallback_to_group_directory(tmp_path):
    scan_root = tmp_path
    study_path = scan_root / "G2" / "1.2.840.10008.1.2.3"
    delivery_path = study_path / "2026031923483900"
    delivery_path.mkdir(parents=True)
    (study_path / "RP.test.dcm").write_text("", encoding="ascii")
    (delivery_path / "PlanInfo.txt").write_text(
        "DICOM_PATIENT_ID,04198922\nDICOM_BEAM_NUMBER,2\n",
        encoding="ascii",
    )
    (delivery_path / "sample.ptn").write_text("", encoding="ascii")

    discovered = _discover_case_directories(scan_root)

    assert discovered == [("1.2.840.10008.1.2.3", study_path)]


def test_case_detection_handler_ignores_extracting_directory_events(tmp_path):
    case_queue = MagicMock()
    logger = MagicMock()
    handler = CaseDetectionHandler(case_queue, logger)

    event = SimpleNamespace(
        is_directory=True,
        src_path=str(tmp_path / "extract_123.tmp" / "study"),
    )

    with patch("src.core.workflow_manager._queue_case", return_value=True) as queue_mock:
        handler.on_created(event)

    queue_mock.assert_not_called()


def test_case_detection_handler_queues_moved_case_directory(tmp_path):
    case_queue = MagicMock()
    logger = MagicMock()
    handler = CaseDetectionHandler(case_queue, logger)

    study_path = tmp_path / "G1" / "1.2.840.10008.1.2.3"
    _build_ready_case_tree(study_path)

    event = SimpleNamespace(
        is_directory=True,
        src_path=str(tmp_path / "staging" / "ignored"),
        dest_path=str(study_path),
    )

    with patch("src.core.workflow_manager._queue_case", return_value=True) as queue_mock:
        handler.on_moved(event)

    queue_mock.assert_called_once_with("1.2.840.10008.1.2.3", study_path, case_queue, logger)


def test_case_detection_handler_queues_case_when_ready_marker_is_created(tmp_path):
    case_queue = MagicMock()
    logger = MagicMock()
    handler = CaseDetectionHandler(case_queue, logger)

    study_path = tmp_path / "G2" / "1.2.840.10008.1.2.3"
    _build_ready_case_tree(study_path)
    event = SimpleNamespace(is_directory=False, src_path=str(study_path / "_CASE_READY"))

    with patch("src.core.workflow_manager._queue_case", return_value=True) as queue_mock:
        handler.on_created(event)

    queue_mock.assert_called_once_with("1.2.840.10008.1.2.3", study_path, case_queue, logger)


def test_case_detection_handler_debounces_duplicate_directory_events(tmp_path):
    case_queue = MagicMock()
    logger = MagicMock()
    handler = CaseDetectionHandler(case_queue, logger)

    study_path = tmp_path / "G1" / "1.2.840.10008.1.2.3"
    _build_ready_case_tree(study_path)
    event = SimpleNamespace(is_directory=True, src_path=str(study_path))

    with patch("src.core.workflow_manager._queue_case", return_value=True) as queue_mock:
        handler.on_created(event)
        handler.on_created(event)

    queue_mock.assert_called_once_with("1.2.840.10008.1.2.3", study_path, case_queue, logger)


def test_derive_room_helpers_support_grouped_and_flat_paths(tmp_path):
    scan_root = tmp_path / "SHI_log"
    grouped_case_path = scan_root / "G1" / "04198922" / "1.2.3.4"
    grouped_case_path.mkdir(parents=True)
    flat_case_path = scan_root / "55061194"
    flat_case_path.mkdir(parents=True)

    settings = MagicMock()
    settings.get_case_directories.return_value = {"scan": scan_root}

    assert derive_room_from_case_path(grouped_case_path, settings) == "G1"
    assert derive_room_from_case_path(flat_case_path, settings) == ""
    assert derive_room_from_path(grouped_case_path, settings) == "G1"
    assert derive_room_from_path(flat_case_path, settings) == ""
