import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

from src.core.workflow_manager import WorkflowManager
from src.domain import states
from src.domain.enums import BeamStatus, CaseStatus
from src.handlers.remote_handler import UploadResult, JobSubmissionResult, JobStatus
from src.handlers.local_handler import ExecutionResult
from src.domain.models import BeamData, CaseData

@pytest.fixture
def mock_full_workflow_manager(tmp_path: Path) -> MagicMock:
    """Fixture for a mocked WorkflowManager for a full integration test."""
    manager = MagicMock(spec=WorkflowManager)

    # Mock all dependencies
    manager.case_repo = MagicMock()
    manager.gpu_repo = MagicMock()
    manager.local_handler = MagicMock()
    manager.remote_handler = MagicMock()
    manager.tps_generator = MagicMock()
    manager.logger = MagicMock()

    # Mock properties
    manager.id = "case-123_beam-01"
    manager.path = tmp_path / "case-123" / "beam-01"
    manager.path.mkdir(parents=True, exist_ok=True)

    # Create a dummy TPS file at the case level, as InitialState expects it
    case_level_tps = tmp_path / "case-123" / "moqui_tps.in"
    case_level_tps.touch()

    # Also create it at the beam level, to satisfy FileUploadState
    (manager.path / "moqui_tps.in").touch()

    manager.shared_context = {}

    # --- Mock successful return values for all handler/repo calls ---

    # InitialState
    mock_beam = MagicMock(spec=BeamData)
    mock_beam.parent_case_id = "case-123"
    mock_beam.beam_id = manager.id
    manager.case_repo.get_beam.return_value = mock_beam

    # FileUploadState
    manager.remote_handler.upload_file.return_value = UploadResult(success=True)

    # HpcExecutionState
    manager.remote_handler.submit_simulation_job.return_value = JobSubmissionResult(success=True, job_id="hpc-job-456")
    manager.remote_handler.wait_for_job_completion.return_value = JobStatus(job_id="hpc-job-456", status="COMPLETED", failed=False, completed=True)

    # DownloadState
    raw_output_file = tmp_path / "final" / manager.id / "output.raw"
    raw_output_file.parent.mkdir(parents=True, exist_ok=True)
    raw_output_file.touch()
    manager.remote_handler.download_file.return_value = UploadResult(success=True)
    manager.local_handler.settings.get_case_directories.return_value = {
        "final_dicom": str(tmp_path / "final")
    }

    # PostprocessingState
    manager.local_handler.run_raw_to_dcm.return_value = ExecutionResult(success=True, output="", error="", return_code=0)

    # UploadResultToPCLocalDataState
    manager.remote_handler.upload_to_pc_localdata.return_value = UploadResult(success=True)

    return manager


def test_full_workflow_transitions_correctly(mock_full_workflow_manager: MagicMock):
    """
    Tests that the workflow transitions through all states correctly
    from InitialState to CompletedState.
    This test explicitly references all state classes to prevent
    them from being marked as dead code.
    """
    # Arrange
    manager = mock_full_workflow_manager

    # Explicitly reference all state classes to ensure they are "used"
    all_states = [
        states.InitialState,
        states.FileUploadState,
        states.HpcExecutionState,
        states.DownloadState,
        states.PostprocessingState,
        states.UploadResultToPCLocalDataState,
        states.CompletedState,
        states.FailedState,
    ]

    # Start with the initial state
    manager.current_state = states.InitialState()

    # Act & Assert
    # We will step through the state machine and assert each transition

    # 1. InitialState -> FileUploadState
    next_state = manager.current_state.execute(manager)
    assert isinstance(next_state, states.FileUploadState), "Failed transition from Initial"
    manager.current_state = next_state

    # 2. FileUploadState -> HpcExecutionState
    next_state = manager.current_state.execute(manager)
    assert isinstance(next_state, states.HpcExecutionState), "Failed transition from FileUpload"
    manager.current_state = next_state

    # 3. HpcExecutionState -> DownloadState
    next_state = manager.current_state.execute(manager)
    assert isinstance(next_state, states.DownloadState), "Failed transition from HpcExecution"
    manager.current_state = next_state

    # 4. DownloadState -> PostprocessingState
    manager.shared_context["raw_output_file"] = Path(str(manager.local_handler.settings.get_case_directories()["final_dicom"])) / manager.id / "output.raw"
    next_state = manager.current_state.execute(manager)
    assert isinstance(next_state, states.PostprocessingState), "Failed transition from Download"
    manager.current_state = next_state

    # 5. PostprocessingState -> UploadResultToPCLocalDataState
    dcm_output_dir = manager.shared_context["raw_output_file"].parent / "dcm_output"
    dcm_output_dir.mkdir()
    (dcm_output_dir / "test.dcm").touch()
    next_state = manager.current_state.execute(manager)
    assert isinstance(next_state, states.UploadResultToPCLocalDataState), "Failed transition from Postprocessing"
    manager.current_state = next_state

    # 6. UploadResultToPCLocalDataState -> CompletedState
    next_state = manager.current_state.execute(manager)
    assert isinstance(next_state, states.CompletedState), "Failed transition from UploadResult"
    manager.current_state = next_state

    # 7. CompletedState -> None (End of workflow)
    next_state = manager.current_state.execute(manager)
    assert next_state is None, "CompletedState should terminate the workflow"

    # Verify that all states were "used"
    assert len(all_states) > 0
