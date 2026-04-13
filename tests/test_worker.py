import importlib
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, patch

import pytest

from src.core import worker
from src.domain.enums import BeamStatus
from src.domain.enums import GpuStatus


@patch("src.core.worker.allocate_gpus_for_pending_beams")
@patch("src.repositories.gpu_repo.GpuRepository")
@patch("src.utils.db_context.get_db_session")
@patch("src.core.worker.TpsGenerator")
@patch("src.core.worker.submit_beam_worker")
def test_try_allocate_pending_beams_uses_persisted_beam_number(
    mock_submit_beam_worker,
    mock_tps_generator_cls,
    mock_get_db_session,
    mock_gpu_repo_cls,
    mock_allocate_gpus,
):
    mock_allocate_gpus.return_value = [{"gpu_uuid": "gpu-1"}]
    mock_tps_generator = MagicMock()
    mock_tps_generator.generate_tps_file_with_gpu_assignments.return_value = True
    mock_tps_generator_cls.return_value = mock_tps_generator
    mock_gpu_repo_cls.return_value = MagicMock()

    case_repo = MagicMock()
    case_repo.get_beams_for_case.return_value = [
        SimpleNamespace(beam_id="beam-02", beam_number=2),
        SimpleNamespace(beam_id="beam-10", beam_number=10),
    ]
    db_conn = MagicMock()

    repo_context = MagicMock()
    repo_context.__enter__.return_value = case_repo
    repo_context.__exit__.return_value = False
    db_context = MagicMock()
    db_context.__enter__.return_value = db_conn
    db_context.__exit__.return_value = False
    mock_get_db_session.side_effect = [repo_context, db_context]

    settings = MagicMock()
    settings.get_path.return_value = str(Path("tmp") / "csv_output")
    settings.get_handler_mode.return_value = "local"
    logger = MagicMock()
    executor = MagicMock()
    active_futures = {}
    pending_beams = {
        "case-1": {
            "pending_jobs": [{"beam_id": "beam-10", "beam_path": Path("cases") / "case-1" / "beam-10"}],
            "case_path": Path("cases") / "case-1",
        }
    }

    worker.try_allocate_pending_beams(
        pending_beams_by_case=pending_beams,
        executor=executor,
        active_futures=active_futures,
        settings=settings,
        logger=logger,
    )

    mock_tps_generator.generate_tps_file_with_gpu_assignments.assert_called_once()
    if mock_tps_generator.generate_tps_file_with_gpu_assignments.call_args.kwargs["beam_number"] != 10:
        raise AssertionError("Expected persisted beam number 10")


def test_try_allocate_pending_beams_uses_case_repo_db_for_gpu_updates():
    settings = MagicMock()
    settings.get_path.return_value = str(Path("tmp") / "csv_output")
    settings.get_handler_mode.return_value = "local"
    logger = MagicMock()
    executor = MagicMock()
    active_futures = {}
    pending_beams = {
        "case-1": {
            "pending_jobs": [{"beam_id": "beam-10", "beam_path": Path("cases") / "case-1" / "beam-10"}],
            "case_path": Path("cases") / "case-1",
        }
    }

    case_repo = MagicMock()
    case_repo.db = object()
    case_repo.get_beams_for_case.return_value = [
        SimpleNamespace(beam_id="beam-10", beam_number=10),
    ]

    repo_context = MagicMock()
    repo_context.__enter__.return_value = case_repo
    repo_context.__exit__.return_value = False

    tps_generator = MagicMock()
    tps_generator.generate_tps_file_with_gpu_assignments.return_value = True

    class StrictGpuRepo:
        def __init__(self, db_connection, logger_arg, settings_arg):
            if db_connection is not case_repo.db:
                raise AssertionError("GpuRepository should receive case_repo.db")
            if logger_arg is not logger:
                raise AssertionError("GpuRepository should receive the worker logger")
            if settings_arg is not settings:
                raise AssertionError("GpuRepository should receive worker settings")

        def assign_gpu_to_case(self, *_args, **_kwargs):
            return None

    with patch("src.core.worker.allocate_gpus_for_pending_beams", return_value=[{"gpu_uuid": "gpu-1"}]), \
         patch("src.core.worker.TpsGenerator", return_value=tps_generator), \
         patch("src.core.worker.submit_beam_worker"), \
         patch("src.core.worker.get_db_session", return_value=repo_context), \
         patch("src.utils.db_context.get_db_session", return_value=repo_context), \
         patch("src.core.worker.GpuRepository", StrictGpuRepo):
        worker.try_allocate_pending_beams(
            pending_beams_by_case=pending_beams,
            executor=executor,
            active_futures=active_futures,
            settings=settings,
            logger=logger,
        )


def test_worker_main_does_not_initialize_database_schema():
    settings = MagicMock()
    settings.execution_handler = {"Workflow": "local"}
    beam_id = "beam-10"
    beam_path = Path("cases") / "case-1" / beam_id

    case_repo = MagicMock()
    db = MagicMock()
    case_repo.db = db

    repo_context = MagicMock()
    repo_context.__enter__.return_value = case_repo
    repo_context.__exit__.return_value = False

    workflow_manager = MagicMock()

    with patch.object(worker, "_validate_beam_path"), \
         patch.object(worker, "get_db_session", return_value=repo_context), \
         patch.object(worker, "GpuRepository") as gpu_repo_cls, \
         patch.object(worker, "ExecutionHandler") as execution_handler_cls, \
         patch.object(worker, "TpsGenerator") as tps_generator_cls, \
         patch.object(worker, "WorkflowManager", return_value=workflow_manager), \
         patch.object(worker.LoggerFactory, "configure"), \
         patch.object(worker.LoggerFactory, "get_logger", return_value=MagicMock()):
        worker.worker_main(beam_id=beam_id, beam_path=beam_path, settings=settings)

    db.init_db.assert_not_called()
    gpu_repo_cls.assert_called_once_with(db, ANY, settings)
    execution_handler_cls.assert_called_once_with(settings=settings)
    tps_generator_cls.assert_called_once()
    workflow_manager.run_workflow.assert_called_once()


def test_monitor_completed_workers_releases_gpu_and_retries_pending_beams():
    future = MagicMock()
    future.result.return_value = None
    active_futures = {future: "beam-10"}
    pending_beams_by_case = {"case-1": {"pending_jobs": [{}], "case_path": Path("cases") / "case-1"}}
    executor = MagicMock()
    settings = MagicMock()
    logger = MagicMock()
    with patch.object(worker, "as_completed", return_value=[future]), \
         patch.object(worker, "_release_beam_gpu_assignment") as release_mock, \
         patch.object(worker, "try_allocate_pending_beams") as retry_mock:
        worker.monitor_completed_workers(
            active_futures=active_futures,
            pending_beams_by_case=pending_beams_by_case,
            executor=executor,
            settings=settings,
            logger=logger,
        )

    release_mock.assert_called_once_with("beam-10", settings, logger)
    retry_mock.assert_called_once_with(pending_beams_by_case, executor, active_futures, settings, logger)


def test_monitor_completed_workers_releases_gpu_on_failure():
    future = MagicMock()
    future.result.side_effect = RuntimeError("boom")
    active_futures = {future: "beam-10"}
    executor = MagicMock()
    settings = MagicMock()
    logger = MagicMock()
    with patch.object(worker, "as_completed", return_value=[future]), \
         patch.object(worker, "_release_beam_gpu_assignment") as release_mock, \
         patch.object(worker, "try_allocate_pending_beams") as retry_mock:
        worker.monitor_completed_workers(
            active_futures=active_futures,
            pending_beams_by_case={},
            executor=executor,
            settings=settings,
            logger=logger,
        )

    release_mock.assert_called_once_with("beam-10", settings, logger)
    retry_mock.assert_not_called()


def test_monitor_completed_workers_marks_failed_beam_when_worker_crashes():
    future = MagicMock()
    future.done.return_value = True
    future.result.side_effect = RuntimeError("database is locked")
    active_futures = {future: "beam-10"}
    executor = MagicMock()
    settings = MagicMock()
    logger = MagicMock()
    case_repo = MagicMock()
    case_repo.get_beam.return_value = SimpleNamespace(parent_case_id="case-1")

    repo_context = MagicMock()
    repo_context.__enter__.return_value = case_repo
    repo_context.__exit__.return_value = False

    with patch.object(worker, "_release_beam_gpu_assignment") as release_mock, \
         patch.object(worker, "try_allocate_pending_beams") as retry_mock, \
         patch.object(worker, "get_db_session", return_value=repo_context), \
         patch.object(worker, "update_case_status_from_beams") as aggregate_mock:
        worker.monitor_completed_workers(
            active_futures=active_futures,
            pending_beams_by_case={},
            executor=executor,
            settings=settings,
            logger=logger,
        )

    release_mock.assert_called_once_with("beam-10", settings, logger)
    retry_mock.assert_not_called()
    case_repo.update_beam_status.assert_called_once()
    call = case_repo.update_beam_status.call_args
    if call.args[0] != "beam-10":
        raise AssertionError(f"Expected failed beam_id 'beam-10', got {call.args[0]!r}")
    if call.args[1] != BeamStatus.FAILED:
        raise AssertionError(f"Expected FAILED status, got {call.args[1]!r}")
    if "database is locked" not in call.kwargs["error_message"]:
        raise AssertionError(f"Expected error_message to include lock failure, got {call.kwargs['error_message']!r}")
    aggregate_mock.assert_called_once_with("case-1", case_repo, logger)


def test_monitor_completed_workers_handles_completed_future_while_others_still_running():
    completed_future = MagicMock()
    completed_future.done.return_value = True
    completed_future.result.return_value = None

    pending_future = MagicMock()
    pending_future.done.return_value = False

    active_futures = {
        completed_future: "beam-10",
        pending_future: "beam-11",
    }
    pending_beams_by_case = {}
    executor = MagicMock()
    settings = MagicMock()
    logger = MagicMock()

    with patch.object(worker, "_release_beam_gpu_assignment") as release_mock, \
         patch.object(worker, "try_allocate_pending_beams") as retry_mock:
        worker.monitor_completed_workers(
            active_futures=active_futures,
            pending_beams_by_case=pending_beams_by_case,
            executor=executor,
            settings=settings,
            logger=logger,
        )

    release_mock.assert_called_once_with("beam-10", settings, logger)
    retry_mock.assert_not_called()
    if completed_future in active_futures:
        raise AssertionError("Completed future should be removed from active_futures")
    if pending_future not in active_futures:
        raise AssertionError("Pending future should remain tracked")


def test_try_allocate_pending_beams_multigpu_dispatches_only_first_waiting_beam():
    settings = MagicMock()
    settings.get_path.return_value = str(Path("tmp") / "csv_output")
    settings.get_handler_mode.return_value = "local"
    settings.get_moqui_runtime_config.return_value = {
        "multigpu_enabled": True,
        "beam_uses_all_available_gpus": True,
        "max_gpus_per_beam": 3,
    }
    logger = MagicMock()
    executor = MagicMock()
    active_futures = {}
    pending_beams = {
        "case-1": {
            "pending_jobs": [
                {"beam_id": "beam-01", "beam_path": Path("cases") / "case-1" / "beam-01"},
                {"beam_id": "beam-02", "beam_path": Path("cases") / "case-1" / "beam-02"},
            ],
            "case_path": Path("cases") / "case-1",
        }
    }

    case_repo = MagicMock()
    case_repo.db = object()
    case_repo.get_beams_for_case.return_value = [
        SimpleNamespace(beam_id="beam-01", beam_number=1),
        SimpleNamespace(beam_id="beam-02", beam_number=2),
    ]

    repo_context = MagicMock()
    repo_context.__enter__.return_value = case_repo
    repo_context.__exit__.return_value = False

    tps_generator = MagicMock()
    tps_generator.generate_tps_file_with_gpu_assignments.return_value = True
    gpu_assignments = [
        {"gpu_uuid": "gpu-0", "gpu_id": 0},
        {"gpu_uuid": "gpu-1", "gpu_id": 1},
        {"gpu_uuid": "gpu-2", "gpu_id": 2},
    ]

    with patch("src.core.worker.allocate_gpus_for_pending_beams", return_value=gpu_assignments), \
         patch("src.core.worker.TpsGenerator", return_value=tps_generator), \
         patch("src.core.worker.submit_beam_worker") as submit_mock, \
         patch("src.core.worker.get_db_session", return_value=repo_context), \
         patch("src.utils.db_context.get_db_session", return_value=repo_context), \
         patch("src.core.worker.GpuRepository") as gpu_repo_cls:
        worker.try_allocate_pending_beams(
            pending_beams_by_case=pending_beams,
            executor=executor,
            active_futures=active_futures,
            settings=settings,
            logger=logger,
        )

    tps_generator.generate_tps_file_with_gpu_assignments.assert_called_once()
    call = tps_generator.generate_tps_file_with_gpu_assignments.call_args
    if call.kwargs["beam_name"] != "beam-01":
        raise AssertionError(f"Expected only the first beam to be dispatched, got {call.kwargs['beam_name']!r}")
    if call.kwargs["gpu_assignments"] != gpu_assignments:
        raise AssertionError(f"Expected all allocated GPUs on first beam, got {call.kwargs['gpu_assignments']!r}")
    submit_mock.assert_called_once_with(executor, "beam-01", Path("cases") / "case-1" / "beam-01", settings, active_futures, logger)
    remaining_jobs = pending_beams["case-1"]["pending_jobs"]
    if [job["beam_id"] for job in remaining_jobs] != ["beam-02"]:
        raise AssertionError(f"Expected second beam to remain queued, got {remaining_jobs!r}")


def test_try_allocate_pending_beams_multigpu_requests_all_available_gpus():
    settings = MagicMock()
    settings.get_path.return_value = str(Path("tmp") / "csv_output")
    settings.get_handler_mode.return_value = "local"
    settings.get_moqui_runtime_config.return_value = {
        "multigpu_enabled": True,
        "beam_uses_all_available_gpus": True,
        "max_gpus_per_beam": 4,
    }
    logger = MagicMock()
    executor = MagicMock()
    active_futures = {}
    pending_beams = {
        "case-1": {
            "pending_jobs": [
                {"beam_id": "beam-01", "beam_path": Path("cases") / "case-1" / "beam-01"},
            ],
            "case_path": Path("cases") / "case-1",
        }
    }

    case_repo = MagicMock()
    case_repo.db = object()
    case_repo.get_beams_for_case.return_value = [
        SimpleNamespace(beam_id="beam-01", beam_number=1),
    ]

    repo_context = MagicMock()
    repo_context.__enter__.return_value = case_repo
    repo_context.__exit__.return_value = False

    tps_generator = MagicMock()
    tps_generator.generate_tps_file_with_gpu_assignments.return_value = True
    gpu_assignments = [
        {"gpu_uuid": f"gpu-{idx}", "gpu_id": idx}
        for idx in range(6)
    ]

    with patch("src.core.worker.allocate_gpus_for_pending_beams", return_value=gpu_assignments) as allocate_mock, \
         patch("src.core.worker.TpsGenerator", return_value=tps_generator), \
         patch("src.core.worker.submit_beam_worker"), \
         patch("src.core.worker.get_db_session", return_value=repo_context), \
         patch("src.utils.db_context.get_db_session", return_value=repo_context), \
         patch("src.core.worker.GpuRepository"):
        worker.try_allocate_pending_beams(
            pending_beams_by_case=pending_beams,
            executor=executor,
            active_futures=active_futures,
            settings=settings,
            logger=logger,
        )

    if allocate_mock.call_args.kwargs["requested_gpu_count"] is not None:
        raise AssertionError(
            f"Expected all-available mode to avoid an artificial GPU cap, got {allocate_mock.call_args.kwargs!r}"
        )
