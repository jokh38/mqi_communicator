from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.core.workflow_manager import WorkflowManager
from src.domain.errors import PermanentFailureError, RetryableError
from src.domain.states import (
    CompletedState,
    InitialState,
    ResultValidationState,
    SimulationState,
    FailedState,
)
from src.handlers.execution_handler import ExecutionHandler


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


def test_simulation_state_uses_injected_handler(mock_workflow_manager, tmp_path):
    mock_handler = MagicMock(spec=ExecutionHandler)
    mock_handler.start_local_process.return_value = MagicMock(poll=lambda: None)
    mock_handler.wait_for_job_completion.return_value = MagicMock(failed=False)
    mock_workflow_manager.execution_handler = mock_handler
    state = SimulationState()

    sim_output_dir = str(tmp_path / "Output" / "case-abc" / "Dose")
    mock_workflow_manager.settings.get_path.side_effect = lambda key, **_: {
        "tps_input_file": "/tmp/input.in",
        "mqi_run_dir": "/tmp/mqi",
        "log_path": "/tmp/log.txt",
        "simulation_output_dir": sim_output_dir,
    }[key]
    mock_workflow_manager.settings.get_command.return_value = "sim_command"
    mock_workflow_manager.settings.get_handler_mode.return_value = "local"

    next_state = state.execute(mock_workflow_manager)

    mock_handler.start_local_process.assert_called_once()
    mock_handler.wait_for_job_completion.assert_called_once()
    assert type(next_state) is ResultValidationState


def test_simulation_state_failure_transitions_to_failed_state(mock_workflow_manager, tmp_path):
    mock_handler = MagicMock(spec=ExecutionHandler)
    mock_handler.start_local_process.return_value = MagicMock(poll=lambda: None)
    mock_handler.wait_for_job_completion.return_value = MagicMock(
        failed=True, error="Simulation failed"
    )
    mock_workflow_manager.execution_handler = mock_handler
    state = SimulationState()

    sim_output_dir = str(tmp_path / "Output" / "case-abc" / "Dose")
    mock_workflow_manager.settings.get_path.side_effect = lambda key, **_: {
        "tps_input_file": "/tmp/input.in",
        "mqi_run_dir": "/tmp/mqi",
        "log_path": "/tmp/log.txt",
        "simulation_output_dir": sim_output_dir,
    }[key]
    mock_workflow_manager.settings.get_command.return_value = "sim_command"
    mock_workflow_manager.settings.get_handler_mode.return_value = "local"

    next_state = state.execute(mock_workflow_manager)

    assert type(next_state) is FailedState


def test_result_validation_state_stores_final_dicom_path(tmp_path: Path):
    manager = MagicMock(spec=WorkflowManager)
    manager.logger = MagicMock()
    manager.case_repo = MagicMock()
    manager.settings = MagicMock()
    manager.settings.get_progress_tracking_config.return_value = {"coarse_phase_progress": {}}
    manager.settings.get_handler_mode.return_value = "local"
    manager.id = "beam-01"
    manager.path = tmp_path / "G1" / "case-abc" / "beam-01"
    manager.path.mkdir(parents=True)
    manager.shared_context = {}

    beam = SimpleNamespace(beam_id="beam-01", parent_case_id="case-abc", beam_number=10)
    manager.case_repo.get_beam.return_value = beam
    manager.case_repo.get_beams_for_case.return_value = [beam]

    simulation_output_dir = tmp_path / "native-output"
    simulation_output_dir.mkdir()
    (simulation_output_dir / "dose.dcm").touch()
    manager.settings.get_path.side_effect = lambda key, **_: {
        "simulation_output_dir": str(simulation_output_dir),
    }[key]
    manager.execution_handler = SimpleNamespace()

    next_state = ResultValidationState().execute(manager)

    assert manager.shared_context["final_result_path"] == str(simulation_output_dir)
    assert type(next_state) is CompletedState


def test_result_validation_state_accepts_raw_result_file(tmp_path: Path):
    manager = MagicMock(spec=WorkflowManager)
    manager.logger = MagicMock()
    manager.case_repo = MagicMock()
    manager.settings = MagicMock()
    manager.settings.get_progress_tracking_config.return_value = {"coarse_phase_progress": {}}
    manager.settings.get_handler_mode.return_value = "local"
    manager.id = "beam-01"
    manager.path = tmp_path / "G1" / "case-abc" / "beam-01"
    manager.path.mkdir(parents=True)
    manager.shared_context = {}

    beam = SimpleNamespace(beam_id="beam-01", parent_case_id="case-abc", beam_number=10)
    manager.case_repo.get_beam.return_value = beam
    manager.case_repo.get_beams_for_case.return_value = [beam]

    simulation_output_dir = tmp_path / "raw-output"
    simulation_output_dir.mkdir()
    (simulation_output_dir / "dose.raw").touch()
    manager.settings.get_path.side_effect = lambda key, **_: {
        "simulation_output_dir": str(simulation_output_dir),
    }[key]
    manager.execution_handler = SimpleNamespace()

    next_state = ResultValidationState().execute(manager)

    assert manager.shared_context["final_result_path"] == str(simulation_output_dir)
    assert type(next_state) is CompletedState


def test_result_validation_state_missing_dir_transitions_to_failed(tmp_path: Path):
    manager = MagicMock(spec=WorkflowManager)
    manager.logger = MagicMock()
    manager.case_repo = MagicMock()
    manager.settings = MagicMock()
    manager.settings.get_progress_tracking_config.return_value = {"coarse_phase_progress": {}}
    manager.id = "beam-01"
    manager.shared_context = {}

    beam = SimpleNamespace(beam_id="beam-01", parent_case_id="case-abc", beam_number=10)
    manager.case_repo.get_beam.return_value = beam
    manager.case_repo.get_beams_for_case.return_value = [beam]

    nonexistent_dir = tmp_path / "nonexistent"
    manager.settings.get_path.side_effect = lambda key, **_: {
        "simulation_output_dir": str(nonexistent_dir),
    }[key]
    manager.execution_handler = SimpleNamespace()

    next_state = ResultValidationState().execute(manager)

    assert type(next_state) is FailedState


def test_retryable_error_marks_failed_beam_with_retryable_prefix(mock_workflow_manager, tmp_path):
    state = SimulationState()
    mock_workflow_manager.execution_handler = MagicMock(spec=ExecutionHandler)
    mock_workflow_manager.execution_handler.start_local_process.side_effect = RetryableError(
        "CSV interpreting failed"
    )

    sim_output_dir = str(tmp_path / "Output" / "case-abc" / "Dose")
    mock_workflow_manager.settings.get_path.side_effect = lambda key, **_: {
        "tps_input_file": "/tmp/input.in",
        "mqi_run_dir": "/tmp/mqi",
        "log_path": "/tmp/log.txt",
        "simulation_output_dir": sim_output_dir,
    }[key]
    mock_workflow_manager.settings.get_command.return_value = "sim_command"
    mock_workflow_manager.settings.get_handler_mode.return_value = "local"

    next_state = state.execute(mock_workflow_manager)

    assert type(next_state) is FailedState
    failed_call = mock_workflow_manager.case_repo.update_beam_status.call_args_list[-1]
    assert failed_call.args[1].value == "failed"
    assert failed_call.kwargs["error_message"].startswith("[RETRYABLE] ")


def test_permanent_failure_error_marks_failed_beam_with_permanent_prefix(
    mock_workflow_manager, tmp_path
):
    state = SimulationState()
    mock_workflow_manager.execution_handler = MagicMock(spec=ExecutionHandler)
    mock_workflow_manager.execution_handler.start_local_process.side_effect = PermanentFailureError(
        "Input validation failed"
    )

    sim_output_dir = str(tmp_path / "Output" / "case-abc" / "Dose")
    mock_workflow_manager.settings.get_path.side_effect = lambda key, **_: {
        "tps_input_file": "/tmp/input.in",
        "mqi_run_dir": "/tmp/mqi",
        "log_path": "/tmp/log.txt",
        "simulation_output_dir": sim_output_dir,
    }[key]
    mock_workflow_manager.settings.get_command.return_value = "sim_command"
    mock_workflow_manager.settings.get_handler_mode.return_value = "local"

    next_state = state.execute(mock_workflow_manager)

    assert type(next_state) is FailedState
    failed_call = mock_workflow_manager.case_repo.update_beam_status.call_args_list[-1]
    assert failed_call.args[1].value == "failed"
    assert failed_call.kwargs["error_message"].startswith("[PERMANENT] ")


def test_initial_state_reads_grouped_room_tps_file(tmp_path: Path):
    manager = MagicMock(spec=WorkflowManager)
    manager.logger = MagicMock()
    manager.case_repo = MagicMock()
    manager.settings = MagicMock()
    manager.settings.get_progress_tracking_config.return_value = {"coarse_phase_progress": {}}
    manager.id = "beam-01"
    manager.path = tmp_path / "G1" / "case-abc" / "beam-01"
    manager.path.mkdir(parents=True)
    manager.shared_context = {}

    manager.case_repo.get_beam.return_value = SimpleNamespace(
        beam_id="beam-01",
        parent_case_id="case-abc",
        beam_number=10,
    )
    manager.case_repo.get_case.return_value = SimpleNamespace(
        case_id="case-abc",
        case_path=tmp_path / "G1" / "case-abc",
    )

    output_root = tmp_path / "Output"
    tps_dir = output_root / "G1" / "case-abc" / "Log_csv"
    tps_dir.mkdir(parents=True)
    expected_tps_file = tps_dir / "moqui_tps_beam-01.in"
    expected_tps_file.write_text("test", encoding="utf-8")
    manager.settings.get_path.return_value = str(output_root)

    next_state = InitialState().execute(manager)

    assert manager.shared_context["tps_file_path"] == expected_tps_file
    assert type(next_state).__name__ == "SimulationState"
