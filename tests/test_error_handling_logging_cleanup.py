from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import ast

import pytest
from pydicom.errors import InvalidDicomError

from src.core.case_aggregator import update_case_status_from_beams
from src.core.data_integrity_validator import DataIntegrityValidator
import src.core.dispatcher as dispatcher
from src.core.retry_policy import RETRYABLE_ERROR_PREFIX
from src.domain.errors import ProcessingError
from src.domain.enums import BeamStatus, CaseStatus, WorkflowStep, StepStatus
from src.domain.states import FailedState, InitialState, SimulationState


def _print_calls(source_path: Path) -> list[int]:
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "print"
    ]


def test_parse_rtplan_beam_count_preserves_invalid_dicom_cause(tmp_path: Path) -> None:
    validator = DataIntegrityValidator(logger=MagicMock())
    rtplan_path = tmp_path / "plan.dcm"
    rtplan_path.touch()

    original_error = InvalidDicomError("not a dicom")
    with patch("src.core.data_integrity_validator.pydicom.dcmread", side_effect=original_error):
        with pytest.raises(ProcessingError) as exc_info:
            validator.parse_rtplan_beam_count(rtplan_path)

    assert exc_info.value.__cause__ is original_error


def test_extract_gantry_number_preserves_unexpected_error_cause(tmp_path: Path) -> None:
    validator = DataIntegrityValidator(logger=MagicMock())
    case_path = tmp_path / "case"
    case_path.mkdir()
    rtplan_path = case_path / "plan.dcm"
    rtplan_path.touch()

    original_error = RuntimeError("reader crashed")
    with patch.object(validator, "find_rtplan_file", return_value=rtplan_path), patch(
        "src.core.data_integrity_validator.pydicom.dcmread", side_effect=original_error
    ):
        with pytest.raises(ProcessingError) as exc_info:
            validator.extract_gantry_number_from_rtplan(case_path)

    assert exc_info.value.__cause__ is original_error


def test_non_bootstrap_ui_and_settings_modules_do_not_use_direct_print() -> None:
    project_root = Path(__file__).resolve().parents[1]

    assert _print_calls(project_root / "src" / "ui" / "display.py") == []
    assert _print_calls(project_root / "src" / "config" / "settings.py") == []


def test_dispatcher_error_handler_uses_atomic_fail_case() -> None:
    logger = MagicMock()
    settings = MagicMock()
    case_repo = MagicMock()
    context_manager = MagicMock()
    context_manager.__enter__.return_value = case_repo
    context_manager.__exit__.return_value = False

    with patch.object(dispatcher, "get_db_session", return_value=context_manager) as get_db_session:
        dispatcher._handle_dispatcher_error(
            case_id="case-1",
            error=RuntimeError("csv failed"),
            workflow_step=WorkflowStep.CSV_INTERPRETING,
            settings=settings,
            logger=logger,
        )

    get_db_session.assert_called_once_with(
        settings, logger, handler_name="CsvInterpreter"
    )
    case_repo.fail_case.assert_called_once_with("case-1", error_message="csv failed")
    case_repo.update_case_status.assert_not_called()
    case_repo.record_workflow_step.assert_called_once_with(
        case_id="case-1",
        step=WorkflowStep.CSV_INTERPRETING,
        status=StepStatus.FAILED,
        metadata={"error": "csv failed"},
    )


def test_case_status_aggregation_preserves_retryable_beam_failure_marker() -> None:
    case_repo = MagicMock()
    case_repo.get_beams_for_case.return_value = [
        SimpleNamespace(
            beam_id="beam-1",
            status=BeamStatus.FAILED,
            progress=40.0,
            error_message=f"{RETRYABLE_ERROR_PREFIX}No free GPU remains after BeamLayerMultiGpu filtering",
        ),
        SimpleNamespace(
            beam_id="beam-2",
            status=BeamStatus.COMPLETED,
            progress=100.0,
            error_message=None,
        ),
    ]

    update_case_status_from_beams("case-1", case_repo, logger=MagicMock())

    case_repo.update_case_status.assert_called_once()
    call = case_repo.update_case_status.call_args
    assert call.args[:2] == ("case-1", CaseStatus.FAILED)
    assert call.kwargs["error_message"].startswith(RETRYABLE_ERROR_PREFIX)


def test_simulation_gpu_exhaustion_is_marked_retryable(tmp_path: Path) -> None:
    manager = MagicMock()
    manager.id = "beam-1"
    manager.path = tmp_path / "case" / "beam-1"
    manager.path.mkdir(parents=True)
    manager.shared_context = {}
    manager.logger = MagicMock()
    manager.case_repo = MagicMock()
    manager.case_repo.get_beam.return_value = SimpleNamespace(
        beam_id="beam-1",
        parent_case_id="case-1",
        beam_number=1,
    )
    manager.settings = MagicMock()
    manager.settings.get_progress_tracking_config.return_value = {"coarse_phase_progress": {}}
    manager.settings.get_handler_mode.return_value = "local"
    manager.settings.get_path.side_effect = lambda key, **_: {
        "tps_input_file": "/tmp/moqui_tps_beam-1.in",
        "mqi_run_dir": "/tmp/moqui",
        "log_path": str(tmp_path / "sim.log"),
        "simulation_output_dir": str(tmp_path / "output"),
    }[key]
    manager.settings.get_command.return_value = "simulate"
    manager.execution_handler = MagicMock()
    manager.execution_handler.start_local_process.return_value = MagicMock()
    manager.execution_handler.wait_for_job_completion.return_value = SimpleNamespace(
        failed=True,
        error="No free GPU remains after BeamLayerMultiGpu filtering",
    )

    next_state = SimulationState().execute(manager)

    assert type(next_state) is FailedState
    failed_call = manager.case_repo.update_beam_status.call_args_list[-1]
    assert failed_call.kwargs["error_message"].startswith(RETRYABLE_ERROR_PREFIX)


def test_missing_tps_input_is_marked_permanent(tmp_path: Path) -> None:
    manager = MagicMock()
    manager.id = "beam-1"
    manager.path = tmp_path / "case" / "beam-1"
    manager.path.mkdir(parents=True)
    manager.shared_context = {}
    manager.logger = MagicMock()
    manager.case_repo = MagicMock()
    manager.case_repo.get_beam.return_value = SimpleNamespace(
        beam_id="beam-1",
        parent_case_id="case-1",
        beam_number=1,
    )
    manager.settings = MagicMock()
    manager.settings.get_progress_tracking_config.return_value = {"coarse_phase_progress": {}}
    manager.settings.get_path.return_value = str(tmp_path / "csv-output")

    next_state = InitialState().execute(manager)

    assert type(next_state) is FailedState
    failed_call = manager.case_repo.update_beam_status.call_args_list[-1]
    assert failed_call.kwargs["error_message"].startswith("[PERMANENT] ")


def _broad_exception_handlers(source_path: Path, function_names: set[str]) -> dict[str, list[int]]:
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    broad_handlers: dict[str, list[int]] = {}

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name not in function_names:
            continue
        lines = [
            handler.lineno
            for handler in ast.walk(node)
            if isinstance(handler, ast.ExceptHandler)
            and isinstance(handler.type, ast.Name)
            and handler.type.id == "Exception"
        ]
        broad_handlers[node.name] = lines

    return broad_handlers


def test_gpu_repository_database_paths_do_not_catch_broad_exception() -> None:
    project_root = Path(__file__).resolve().parents[1]

    assert _broad_exception_handlers(
        project_root / "src" / "repositories" / "gpu_repo.py",
        {"update_resources", "find_and_lock_multiple_gpus"},
    ) == {"update_resources": [], "find_and_lock_multiple_gpus": []}


def test_base_repository_query_execution_does_not_catch_broad_exception() -> None:
    project_root = Path(__file__).resolve().parents[1]

    assert _broad_exception_handlers(
        project_root / "src" / "repositories" / "base.py",
        {"_execute_query"},
    ) == {"_execute_query": []}
