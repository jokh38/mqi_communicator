import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

# The states module will be created in the next step
# For now, this import will fail, which is part of the TDD process.
from src.domain.states import UploadResultToPCLocalDataState, PostprocessingState, CompletedState
from src.domain.enums import BeamStatus
from src.handlers.remote_handler import UploadResult
from src.core.workflow_manager import WorkflowManager

from src.domain.states import FailedState

@pytest.fixture
def mock_workflow_manager(tmp_path: Path) -> MagicMock:
    """Fixture for a mocked WorkflowManager."""
    manager = MagicMock(spec=WorkflowManager)

    # Mock handlers and repositories
    manager.remote_handler = MagicMock()
    manager.case_repo = MagicMock()
    manager.logger = MagicMock()

    # Mock context and properties
    manager.id = "beam-01"
    manager.shared_context = {}

    # Setup a dummy final result directory
    final_result_dir = tmp_path / "dcm_output"
    final_result_dir.mkdir()
    (final_result_dir / "test.dcm").touch()
    manager.shared_context["final_result_path"] = str(final_result_dir)

    # Mock repository methods to return the case_id
    mock_beam = MagicMock()
    mock_beam.parent_case_id = "case-abc"
    manager.case_repo.get_beam.return_value = mock_beam

    return manager

from src.domain.errors import ProcessingError

def test_upload_state_success(mock_workflow_manager: MagicMock):
    """
    Tests the UploadResultToPCLocalDataState for a successful upload.
    """
    # Arrange
    state = UploadResultToPCLocalDataState()
    mock_workflow_manager.remote_handler.upload_to_pc_localdata.return_value = UploadResult(success=True)

    # Act
    next_state = state.execute(mock_workflow_manager)

    # Assert
    # 1. Verify that the upload method was called correctly
    final_dir_path = Path(mock_workflow_manager.shared_context["final_result_path"])
    mock_workflow_manager.remote_handler.upload_to_pc_localdata.assert_called_once_with(
        local_path=final_dir_path,
        case_id="case-abc"
    )

    # 2. Verify transition to CompletedState
    assert isinstance(next_state, CompletedState), "Should transition to CompletedState on success"

def test_upload_state_failure_transitions_to_failed_state(mock_workflow_manager: MagicMock):
    """
    Tests that UploadResultToPCLocalDataState transitions to FailedState on upload failure.
    """
    # Arrange
    state = UploadResultToPCLocalDataState()
    error_message = "Connection timed out"
    mock_workflow_manager.remote_handler.upload_to_pc_localdata.return_value = UploadResult(
        success=False, error=error_message
    )

    # Act
    next_state = state.execute(mock_workflow_manager)

    # Assert
    assert isinstance(next_state, FailedState)


def test_postprocessing_state_passes_dir(tmp_path: Path):
    """
    Tests that PostprocessingState sets the final_result_path to a directory.
    """
    # Arrange
    manager = MagicMock(spec=WorkflowManager)
    manager.logger = MagicMock()
    manager.case_repo = MagicMock()
    manager.local_handler = MagicMock()
    manager.id = "beam-01"
    manager.path = tmp_path

    raw_file = tmp_path / "output.raw"
    raw_file.touch()

    dcm_output_dir = tmp_path / "dcm_output"
    # The state should create this directory

    manager.shared_context = {"raw_output_file": raw_file}
    # Simulate that the run_raw_to_dcm creates the directory and a file
    def side_effect(*args, **kwargs):
        output_dir = kwargs.get("output_dir")
        output_dir.mkdir(exist_ok=True)
        (output_dir / "test.dcm").touch()
        return MagicMock(success=True)

    manager.local_handler.run_raw_to_dcm.side_effect = side_effect

    state = PostprocessingState()

    # Act
    next_state = state.execute(manager)

    # Assert
    assert manager.shared_context["final_result_path"] == str(tmp_path / "dcm_output")
    assert isinstance(next_state, UploadResultToPCLocalDataState)

def test_upload_state_directory_not_found_transitions_to_failed_state(mock_workflow_manager: MagicMock):
    """
    Tests that the state transitions to FailedState when the final result directory is missing.
    """
    # Arrange
    state = UploadResultToPCLocalDataState()
    # Invalidate the path in the context
    mock_workflow_manager.shared_context["final_result_path"] = "/path/to/nonexistent/dir"

    # Act
    next_state = state.execute(mock_workflow_manager)

    # Assert
    assert isinstance(next_state, FailedState)
