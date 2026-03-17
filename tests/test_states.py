from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.core.workflow_manager import WorkflowManager
from src.domain.states import (
    CompletedState,
    DownloadState,
    FailedState,
    HpcExecutionState,
    UploadResultToPCLocalDataState,
)
from src.handlers.execution_handler import ExecutionHandler


UploadResult = MagicMock()


@pytest.fixture
def mock_workflow_manager(tmp_path: Path) -> MagicMock:
    manager = MagicMock(spec=WorkflowManager)
    manager.execution_handler = MagicMock()
    manager.case_repo = MagicMock()
    manager.logger = MagicMock()
    manager.settings = MagicMock()
    manager.settings.get_progress_tracking_config.return_value = {"coarse_phase_progress": {}}
    manager.id = "beam-01"
    manager.path = tmp_path
    manager.shared_context = {}

    final_result_dir = tmp_path / "dcm_output"
    final_result_dir.mkdir()
    (final_result_dir / "test.dcm").touch()
    manager.shared_context["final_result_path"] = str(final_result_dir)

    manager.case_repo.get_beam.return_value = SimpleNamespace(
        beam_id="beam-01",
        parent_case_id="case-abc",
        beam_number=10,
    )
    manager.case_repo.get_beams_for_case.return_value = [
        manager.case_repo.get_beam.return_value
    ]
    return manager


def test_upload_state_success(mock_workflow_manager: MagicMock):
    state = UploadResultToPCLocalDataState()
    mock_workflow_manager.execution_handler.upload_to_pc_localdata.return_value = UploadResult(
        success=True
    )

    next_state = state.execute(mock_workflow_manager)

    final_dir_path = Path(mock_workflow_manager.shared_context["final_result_path"])
    mock_workflow_manager.execution_handler.upload_to_pc_localdata.assert_called_once_with(
        local_path=final_dir_path,
        case_id="case-abc",
        settings=mock_workflow_manager.settings,
    )
    assert isinstance(next_state, CompletedState)


def test_upload_state_failure_transitions_to_failed_state(
    mock_workflow_manager: MagicMock,
):
    state = UploadResultToPCLocalDataState()
    mock_upload_result = MagicMock(success=False, error="Connection timed out")
    mock_workflow_manager.execution_handler.upload_to_pc_localdata.return_value = mock_upload_result

    next_state = state.execute(mock_workflow_manager)

    assert isinstance(next_state, FailedState)


def test_hpc_execution_state_uses_injected_handler(mock_workflow_manager):
    mock_handler = MagicMock(spec=ExecutionHandler)
    mock_handler.submit_simulation_job.return_value = MagicMock(success=True, job_id="12345")
    mock_handler.wait_for_job_completion.return_value = MagicMock(failed=False)
    state = HpcExecutionState(execution_handler=mock_handler)

    mock_workflow_manager.shared_context["remote_beam_dir"] = "/remote/test/dir"
    mock_workflow_manager.settings.get_path.side_effect = lambda key, **_: {
        "tps_input_file": "/tmp/input.in",
        "mqi_run_dir": "/tmp/mqi",
        "remote_log_path": "/tmp/log.txt",
    }[key]
    mock_workflow_manager.settings.get_handler_mode.return_value = "remote"

    next_state = state.execute(mock_workflow_manager)

    mock_handler.submit_simulation_job.assert_called_once()
    assert isinstance(next_state, DownloadState)


def test_hpc_execution_state_local_uses_pid_aware_wait(mock_workflow_manager):
    mock_handler = MagicMock(spec=ExecutionHandler)
    mock_handler.submit_simulation_job.return_value = MagicMock(success=True, local_pid=4321)
    mock_handler.wait_for_job_completion.return_value = MagicMock(failed=False)
    state = HpcExecutionState(execution_handler=mock_handler)

    mock_workflow_manager.settings.get_path.side_effect = lambda key, **_: {
        "tps_input_file": "/tmp/input.in",
        "mqi_run_dir": "/tmp/mqi",
        "remote_log_path": "/tmp/log.txt",
        "simulation_output_dir": "/tmp/native-output",
    }[key]
    mock_workflow_manager.settings.get_handler_mode.return_value = "local"

    next_state = state.execute(mock_workflow_manager)

    mock_handler.submit_simulation_job.assert_called_once()
    mock_handler.wait_for_job_completion.assert_called_once_with(
        job_id=None,
        log_file_path="/tmp/log.txt",
        beam_id="beam-01",
        case_repo=mock_workflow_manager.case_repo,
        local_pid=4321,
        expected_output_dir="/tmp/native-output/beam_10",
    )
    assert isinstance(next_state, DownloadState)


def test_download_state_stores_final_dicom_path_for_native_output(tmp_path: Path):
    manager = MagicMock(spec=WorkflowManager)
    manager.logger = MagicMock()
    manager.case_repo = MagicMock()
    manager.settings = MagicMock()
    manager.settings.get_progress_tracking_config.return_value = {"coarse_phase_progress": {}}
    manager.settings.get_handler_mode.return_value = "local"
    manager.id = "beam-01"
    manager.shared_context = {}

    beam = SimpleNamespace(beam_id="beam-01", parent_case_id="case-abc", beam_number=10)
    manager.case_repo.get_beam.return_value = beam
    manager.case_repo.get_beams_for_case.return_value = [beam]

    simulation_output_dir = tmp_path / "native-output"
    simulation_output_dir.mkdir()
    expected_dicom_dir = simulation_output_dir / "beam_10"
    expected_dicom_dir.mkdir()
    (expected_dicom_dir / "dose.dcm").touch()
    manager.settings.get_path.side_effect = lambda key, **_: {
        "simulation_output_dir": str(simulation_output_dir),
    }[key]
    manager.execution_handler = SimpleNamespace()

    next_state = DownloadState().execute(manager)

    assert manager.shared_context["final_result_path"] == str(expected_dicom_dir)
    assert isinstance(next_state, UploadResultToPCLocalDataState)


def test_download_state_transitions_directly_to_upload_state(tmp_path: Path):
    manager = MagicMock(spec=WorkflowManager)
    manager.logger = MagicMock()
    manager.case_repo = MagicMock()
    manager.settings = MagicMock()
    manager.settings.get_progress_tracking_config.return_value = {"coarse_phase_progress": {}}
    manager.settings.get_handler_mode.return_value = "local"
    manager.id = "beam-01"
    manager.shared_context = {}

    beam = SimpleNamespace(beam_id="beam-01", parent_case_id="case-abc", beam_number=10)
    manager.case_repo.get_beam.return_value = beam
    manager.case_repo.get_beams_for_case.return_value = [beam]

    simulation_output_dir = tmp_path / "native-output"
    simulation_output_dir.mkdir()
    expected_dicom_dir = simulation_output_dir / "beam_10"
    expected_dicom_dir.mkdir()
    (expected_dicom_dir / "dose.dcm").touch()
    manager.settings.get_path.side_effect = lambda key, **_: {
        "simulation_output_dir": str(simulation_output_dir),
    }[key]
    manager.execution_handler = SimpleNamespace()

    next_state = DownloadState().execute(manager)

    assert not hasattr(manager.execution_handler, "run_raw_to_dcm")
    assert isinstance(next_state, UploadResultToPCLocalDataState)


def test_upload_state_directory_not_found_transitions_to_failed_state(
    mock_workflow_manager: MagicMock,
):
    state = UploadResultToPCLocalDataState()
    mock_workflow_manager.shared_context["final_result_path"] = "/path/to/nonexistent/dir"

    next_state = state.execute(mock_workflow_manager)

    assert isinstance(next_state, FailedState)
