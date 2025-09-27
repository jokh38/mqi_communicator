import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
import yaml
import time

from src.domain.states import InitialState, FileUploadState, HpcExecutionState, DownloadState, PostprocessingState, UploadResultToPCLocalDataState, CompletedState, FailedState
from src.core.workflow_manager import WorkflowManager
from src.config.settings import Settings
from src.handlers.execution_handler import JobSubmissionResult, ExecutionResult, UploadResult, DownloadResult

@pytest.fixture
def states_config_file(tmp_path: Path) -> Path:
    """Provides a comprehensive config file for all states tests."""
    config_data = {
        "ExecutionHandler": {
            "CsvInterpreter": "local",
            "HpcJobSubmitter": "remote",
            "PostProcessor": "local",
            "ResultUploader": "remote",
        },
        "paths": {
            "base_directory": str(tmp_path),
            "local": {
                "simulation_output_dir": "{base_directory}/raw_output/{case_id}",
                "final_dicom_dir": "{base_directory}/final_dicom/{case_id}",
            },
            "remote": {
                "remote_case_path": "/hpc/cases/{case_id}",
                "remote_beam_path": "{remote_case_path}/{beam_id}",
                "remote_beam_result_path": "{remote_beam_path}/output.raw",
            },
        },
        "executables": {
            "local": {"python": "python", "raw_to_dicom_script": "scripts/raw2dcm.py"},
        },
        "command_templates": {
            "remote": {
                "submit_simulation": "sbatch run.sh --case-id {case_id}",
                "check_job_status": "squeue -j {job_id}",
                "check_job_history": "sacct -j {job_id}",
                "cleanup": "rm -rf {remote_path}",
                "upload_result": "scp -r {local_path} user@host:{case_id}",
            },
            "local": {
                "post_process": "{python} {raw_to_dicom_script} --input {input_file} --output {output_dir}",
            },
        },
        "processing": {"hpc_poll_interval_seconds": 0.1, "hpc_job_timeout_seconds": 2},
        "logging": {"log_level": "DEBUG", "log_dir": str(tmp_path / "logs")},
    }
    config_file = tmp_path / "config.yaml"
    with open(config_file, "w") as f:
        yaml.dump(config_data, f)
    return config_file

@pytest.fixture
def settings(states_config_file: Path) -> Settings:
    return Settings(config_path=states_config_file)

@pytest.fixture
def mock_workflow_manager(settings, tmp_path) -> MagicMock:
    manager = MagicMock(spec=WorkflowManager)
    manager.id = "beam_01"
    manager.path = tmp_path / "case_01" / "beam_01"
    manager.settings = settings
    manager.logger = MagicMock()
    manager.case_repo = MagicMock()
    manager.execution_handler = MagicMock()
    manager.shared_context = {}
    manager.case_repo.get_beam.return_value = MagicMock(parent_case_id="case_01")
    return manager

# --- Test for each state in the workflow ---

def test_initial_state_success(mock_workflow_manager):
    """
    Tests InitialState by creating the actual directory and file it checks for,
    removing the need for fragile mocks and ensuring the test environment is realistic.
    """
    # Arrange: Create the directory and file so checks pass without mocks
    mock_workflow_manager.path.mkdir(parents=True, exist_ok=True)
    tps_file = mock_workflow_manager.path.parent / "moqui_tps.in"
    tps_file.touch()

    state = InitialState()

    # Act
    next_state = state.execute(mock_workflow_manager)

    # Assert: Check state transition first for better error messages
    assert isinstance(next_state, FileUploadState), f"State did not transition to FileUploadState, but to {type(next_state).__name__}"
    assert "tps_file_path" in mock_workflow_manager.shared_context
    assert mock_workflow_manager.shared_context["tps_file_path"] == tps_file

@patch("src.domain.states.Path.exists", return_value=True)
def test_file_upload_state_remote_mode(mock_exists, mock_workflow_manager):
    # ARRANGE: Simulate remote mode
    mock_workflow_manager.settings.get_handler_mode = MagicMock(return_value='remote')

    state = FileUploadState()
    handler = mock_workflow_manager.execution_handler
    handler.upload_file.return_value = UploadResult(success=True)
    tps_file = mock_workflow_manager.path.parent / "moqui_tps.in"
    mock_workflow_manager.shared_context["tps_file_path"] = tps_file

    # ACT
    next_state = state.execute(mock_workflow_manager)

    # ASSERT
    handler.upload_file.assert_called_once()
    assert isinstance(next_state, HpcExecutionState)

@patch("src.domain.states.Path.exists", return_value=True)
def test_file_upload_state_local_mode(mock_exists, mock_workflow_manager):
    # ARRANGE: Simulate local mode
    mock_workflow_manager.settings.get_handler_mode = MagicMock(return_value='local')

    state = FileUploadState()
    handler = mock_workflow_manager.execution_handler
    tps_file = mock_workflow_manager.path.parent / "moqui_tps.in"
    mock_workflow_manager.shared_context["tps_file_path"] = tps_file

    # ACT
    next_state = state.execute(mock_workflow_manager)

    # ASSERT
    handler.upload_file.assert_not_called()
    assert isinstance(next_state, HpcExecutionState)


@patch("time.sleep")
def test_hpc_execution_state_polling_logic(mock_sleep, mock_workflow_manager):
    state = HpcExecutionState()
    handler = mock_workflow_manager.execution_handler
    handler.submit_simulation_job.return_value = JobSubmissionResult(success=True, job_id="12345")
    handler.execute_command.side_effect = [
        ExecutionResult(success=True, output="RUNNING", error="", return_code=0),
        ExecutionResult(success=True, output="COMPLETED", error="", return_code=0),
    ]
    next_state = state.execute(mock_workflow_manager)
    assert isinstance(next_state, DownloadState)
    assert handler.execute_command.call_count == 2

@patch("src.domain.states.Path.exists", return_value=True)
def test_download_state_remote_mode(mock_exists, mock_workflow_manager):
    # ARRANGE: Simulate remote mode for HPC, but local for PostProcessor
    handler_modes = {
        "HpcJobSubmitter": "remote",
        "PostProcessor": "local",
    }
    mock_workflow_manager.settings.get_handler_mode = MagicMock(side_effect=lambda name: handler_modes.get(name, 'local'))

    state = DownloadState()
    handler = mock_workflow_manager.execution_handler
    handler.download_file.return_value = DownloadResult(success=True)
    handler.cleanup.return_value = ExecutionResult(success=True, output="", error="", return_code=0)
    mock_workflow_manager.shared_context["remote_beam_dir"] = "/hpc/cases/case_01/beam_01"

    # ACT
    next_state = state.execute(mock_workflow_manager)

    # ASSERT
    handler.download_file.assert_called_once()
    handler.cleanup.assert_called_once()
    assert "raw_output_file" in mock_workflow_manager.shared_context
    assert isinstance(next_state, PostprocessingState)

@patch("src.domain.states.Path.exists", return_value=True)
def test_download_state_local_mode(mock_exists, mock_workflow_manager):
    # ARRANGE: Simulate local mode for all handlers
    mock_workflow_manager.settings.get_handler_mode = MagicMock(return_value='local')

    state = DownloadState()
    handler = mock_workflow_manager.execution_handler

    # ACT
    next_state = state.execute(mock_workflow_manager)

    # ASSERT
    handler.download_file.assert_not_called()
    handler.cleanup.assert_not_called()
    assert "raw_output_file" in mock_workflow_manager.shared_context
    assert isinstance(next_state, PostprocessingState)

@patch("src.domain.states.Path.unlink")
@patch("src.domain.states.Path.exists", return_value=True)
@patch("src.domain.states.Path.mkdir")
@patch("src.domain.states.Path.glob", return_value=[Path("dummy.dcm")])
def test_postprocessing_state_success(mock_glob, mock_mkdir, mock_exists, mock_unlink, mock_workflow_manager):
    state = PostprocessingState()
    handler = mock_workflow_manager.execution_handler
    handler.execute_command.return_value = ExecutionResult(success=True, output="", error="", return_code=0)
    raw_file = mock_workflow_manager.path.parent / "raw_output" / "output.raw"
    mock_workflow_manager.shared_context["raw_output_file"] = raw_file

    next_state = state.execute(mock_workflow_manager)

    handler.execute_command.assert_called_once()
    assert isinstance(next_state, UploadResultToPCLocalDataState)

@patch("src.domain.states.ExecutionHandler")
@patch("src.domain.states.Path.exists", return_value=True)
def test_upload_result_state_success(mock_exists, mock_local_handler_cls, mock_workflow_manager):
    """Tests that a local handler is used for the upload command."""
    state = UploadResultToPCLocalDataState()
    # Mock the new local handler instance that will be created
    mock_local_handler_instance = MagicMock()
    mock_local_handler_instance.upload_to_pc_localdata.return_value = ExecutionResult(success=True, output="", error="", return_code=0)
    mock_local_handler_cls.return_value = mock_local_handler_instance

    mock_workflow_manager.shared_context["final_result_path"] = "/tmp/final_results/case_01/beam_01"

    next_state = state.execute(mock_workflow_manager)

    # Assert that a new local handler was created and used, not the context's remote one
    mock_local_handler_cls.assert_called_once_with(settings=mock_workflow_manager.settings, mode="local")
    mock_local_handler_instance.upload_to_pc_localdata.assert_called_once()
    assert isinstance(next_state, CompletedState)

def test_completed_state_is_terminal(mock_workflow_manager):
    state = CompletedState()
    next_state = state.execute(mock_workflow_manager)
    assert next_state is None

def test_failed_state_is_terminal(mock_workflow_manager):
    state = FailedState()
    next_state = state.execute(mock_workflow_manager)
    assert next_state is None