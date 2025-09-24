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

    # Setup a dummy final result file
    final_result_file = tmp_path / "final_analysis.zip"
    final_result_file.touch()
    manager.shared_context["final_result_path"] = str(final_result_file)

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
    final_file_path = Path(mock_workflow_manager.shared_context["final_result_path"])
    mock_workflow_manager.remote_handler.upload_to_pc_localdata.assert_called_once_with(
        local_file=final_file_path,
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

def test_upload_state_file_not_found_transitions_to_failed_state(mock_workflow_manager: MagicMock):
    """
    Tests that the state transitions to FailedState when the final result file is missing.
    """
    # Arrange
    state = UploadResultToPCLocalDataState()
    # Invalidate the path in the context
    mock_workflow_manager.shared_context["final_result_path"] = "/path/to/nonexistent/file.zip"

    # Act
    next_state = state.execute(mock_workflow_manager)

    # Assert
    assert isinstance(next_state, FailedState)
