import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.config.settings import Settings
from src.handlers.execution_handler import ExecutionHandler, ExecutionResult


@pytest.fixture
def mock_settings():
    settings = MagicMock(spec=Settings)
    settings.get_database_path.return_value = "dummy.db"
    settings.database = {}
    csv_output_dir = Path("tmp") / "csv_output"
    mqi_interpreter_dir = Path("opt") / "mqi_interpreter"
    python_executable = Path("usr") / "bin" / "python3"
    interpreter_script = Path("opt") / "main_cli.py"
    settings.get_path.side_effect = lambda key, **_: {
        "database_path": "dummy.db",
        "csv_output_dir": str(csv_output_dir),
        "mqi_interpreter_dir": str(mqi_interpreter_dir),
    }[key]
    settings.get_executable.side_effect = lambda key, **_: {
        "python": str(python_executable),
        "mqi_interpreter_script": str(interpreter_script),
    }[key]
    settings.get_handler_mode.return_value = "local"
    mock_logging_config = MagicMock()
    mock_logging_config.log_level = "INFO"
    mock_logging_config.log_dir = Path("tmp") / "logs"
    mock_logging_config.max_file_size = 10
    mock_logging_config.backup_count = 5
    settings.logging = mock_logging_config
    return settings


@patch("src.core.dispatcher.get_db_session")
@patch("src.core.dispatcher.ExecutionHandler")
def test_run_case_level_csv_interpreting_success(
    mock_exec_handler_cls,
    mock_get_db_session,
    mock_settings,
):
    dispatcher = importlib.import_module("src.core.dispatcher")
    mock_exec_handler_instance = MagicMock(spec=ExecutionHandler)
    mock_exec_handler_instance.execute_command.return_value = ExecutionResult(
        success=True, output="", error="", return_code=0
    )
    mock_exec_handler_cls.return_value = mock_exec_handler_instance
    mock_case_repo = MagicMock()
    context_manager = MagicMock()
    context_manager.__enter__.return_value = mock_case_repo
    context_manager.__exit__.return_value = False
    mock_get_db_session.return_value = context_manager

    case_id = "test_case_01"
    case_path = Path("dummypath") / "test_case_01"

    success = dispatcher.run_case_level_csv_interpreting(case_id, case_path, mock_settings)

    if success is not True:
        raise AssertionError("CSV interpreting should succeed")
    mock_exec_handler_instance.execute_command.assert_called_once_with(
        [
            str(Path("usr") / "bin" / "python3"),
            str(Path("opt") / "main_cli.py"),
            "--logdir",
            str(case_path),
            "--outputdir",
            str(Path("tmp") / "csv_output" / case_id),
        ],
        cwd=Path("opt") / "mqi_interpreter",
    )


@patch("src.core.dispatcher.LoggerFactory.get_logger")
@patch("src.core.dispatcher.TpsGenerator")
@patch("src.core.dispatcher.DataIntegrityValidator")
@patch("src.core.dispatcher.GpuRepository")
@patch("src.core.dispatcher.get_db_session")
def test_run_case_level_tps_generation_persists_treatment_beam_indices(
    mock_get_db_session,
    mock_gpu_repo_cls,
    mock_validator_cls,
    mock_tps_generator_cls,
    mock_get_logger,
    mock_settings,
):
    dispatcher = importlib.import_module("src.core.dispatcher")
    logger = MagicMock()
    mock_get_logger.return_value = logger

    case_repo = MagicMock()
    case_repo.db.init_db.return_value = None
    case_repo.get_beams_for_case.return_value = [
        SimpleNamespace(beam_id="case-1_beam-b", beam_number=None, beam_path=Path("cases") / "case-1" / "beam-b"),
        SimpleNamespace(beam_id="case-1_beam-a", beam_number=None, beam_path=Path("cases") / "case-1" / "beam-a"),
    ]

    gpu_repo = MagicMock()
    gpu_repo.get_available_gpu_count.return_value = 2
    gpu_repo.find_and_lock_multiple_gpus.return_value = [
        {"gpu_uuid": "gpu-1"},
        {"gpu_uuid": "gpu-2"},
    ]

    validator = MagicMock()
    validator.get_beam_information.return_value = {
        "beams": [
            {"beam_name": "beam-b", "beam_number": 10},
            {"beam_name": "beam-a", "beam_number": 2},
        ]
    }
    validator.get_treatment_beam_numbers.return_value = [10, 2]

    tps_generator = MagicMock()
    tps_generator.generate_tps_file_with_gpu_assignments.return_value = True

    mock_gpu_repo_cls.return_value = gpu_repo
    mock_validator_cls.return_value = validator
    mock_tps_generator_cls.return_value = tps_generator

    context_manager = MagicMock()
    context_manager.__enter__.return_value = case_repo
    context_manager.__exit__.return_value = False
    mock_get_db_session.return_value = context_manager

    result = dispatcher.run_case_level_tps_generation(
        case_id="case-1",
        case_path=Path("cases") / "case-1",
        beam_count=2,
        settings=mock_settings,
    )

    # W-3 fix: gpu_assignments now include beam_id for proper matching
    if result != [{"gpu_uuid": "gpu-1", "beam_id": "case-1_beam-b"}, {"gpu_uuid": "gpu-2", "beam_id": "case-1_beam-a"}]:
        raise AssertionError(f"Unexpected GPU assignment result: {result!r}")
    case_repo.update_beam_number.assert_any_call("case-1_beam-b", 10)
    case_repo.update_beam_number.assert_any_call("case-1_beam-a", 2)


def test_run_case_level_tps_generation_releases_gpu_by_uuid_on_generation_failure(
    mock_settings,
):
    sys.modules.pop("src.core.dispatcher", None)
    dispatcher = importlib.import_module("src.core.dispatcher")
    logger = MagicMock()

    case_repo = MagicMock()
    case_repo.db.init_db.return_value = None
    case_repo.get_beams_for_case.return_value = [
        SimpleNamespace(beam_id="case-1_beam-a", beam_number=1, beam_path=Path("cases") / "case-1" / "beam-a"),
    ]

    gpu_repo = MagicMock()
    gpu_repo.get_available_gpu_count.return_value = 1
    gpu_repo.find_and_lock_multiple_gpus.return_value = [{"gpu_uuid": "gpu-1"}]

    validator = MagicMock()
    validator.get_beam_information.return_value = {"beams": [{"beam_name": "beam-a", "beam_number": 1}]}
    validator.get_treatment_beam_numbers.return_value = [1]

    tps_generator = MagicMock()
    tps_generator.generate_tps_file_with_gpu_assignments.return_value = False

    context_manager = MagicMock()
    context_manager.__enter__.return_value = case_repo
    context_manager.__exit__.return_value = False

    with patch("src.core.dispatcher.LoggerFactory.get_logger", return_value=logger), \
         patch("src.core.dispatcher.GpuRepository", return_value=gpu_repo), \
         patch("src.core.dispatcher.DataIntegrityValidator", return_value=validator), \
         patch("src.core.dispatcher.TpsGenerator", return_value=tps_generator), \
         patch("src.core.dispatcher.get_db_session", return_value=context_manager):
        result = dispatcher.run_case_level_tps_generation(
            case_id="case-1",
            case_path=Path("cases") / "case-1",
            beam_count=1,
            settings=mock_settings,
        )

    if result is not None:
        raise AssertionError(f"Expected TPS generation failure to return None, got {result!r}")
    gpu_repo.release_gpu.assert_called_once_with("gpu-1")


@patch("src.core.dispatcher.get_db_session")
@patch("src.core.dispatcher.ExecutionHandler")
def test_run_case_level_csv_interpreting_writes_grouped_room_output(
    mock_exec_handler_cls,
    mock_get_db_session,
    mock_settings,
):
    dispatcher = importlib.import_module("src.core.dispatcher")
    mock_exec_handler_instance = MagicMock(spec=ExecutionHandler)
    mock_exec_handler_instance.execute_command.return_value = ExecutionResult(
        success=True, output="", error="", return_code=0
    )
    mock_exec_handler_cls.return_value = mock_exec_handler_instance
    mock_case_repo = MagicMock()
    context_manager = MagicMock()
    context_manager.__enter__.return_value = mock_case_repo
    context_manager.__exit__.return_value = False
    mock_get_db_session.return_value = context_manager
    mock_settings.get_case_directories.return_value = {"scan": Path("dummypath")}

    case_id = "test_case_01"
    case_path = Path("dummypath") / "G1" / case_id

    success = dispatcher.run_case_level_csv_interpreting(case_id, case_path, mock_settings)

    if success is not True:
        raise AssertionError("CSV interpreting should succeed")
    mock_exec_handler_instance.execute_command.assert_called_once_with(
        [
            str(Path("usr") / "bin" / "python3"),
            str(Path("opt") / "main_cli.py"),
            "--logdir",
            str(case_path),
            "--outputdir",
            str(Path("tmp") / "csv_output" / "G1" / case_id),
        ],
        cwd=Path("opt") / "mqi_interpreter",
    )


def test_resolve_raw_dicom_beam_number_prefers_existing_beam_number():
    dispatcher = importlib.import_module("src.core.dispatcher")
    beam = SimpleNamespace(
        beam_id="55061194_2025042401440800",
        beam_path=Path("cases") / "55061194" / "2025042401440800",
        beam_number=2,
    )

    # persisted beam_number=2 is NOT a valid raw DICOM beam number (only 99
    # exists), and the 3-step resolution cannot map it — returns None.
    result = dispatcher._resolve_raw_dicom_beam_number(
        beam,
        beam_metadata=[{"beam_name": "non_matching_name", "beam_number": 99}],
    )

    if result is not None:
        raise AssertionError(f"Expected None for unresolvable beam number, got {result!r}")


def test_resolve_raw_dicom_beam_number_uses_matching_metadata_for_timestamp_folders():
    dispatcher = importlib.import_module("src.core.dispatcher")
    beam = SimpleNamespace(
        beam_id="55061194_2025042401552900",
        beam_path=Path("cases") / "55061194" / "2025042401552900",
        beam_number=4,
    )

    result = dispatcher._resolve_raw_dicom_beam_number(
        beam,
        beam_metadata=[
            {"beam_name": "2025042401440800", "beam_number": 2},
            {"beam_name": "2025042401501400", "beam_number": 3},
            {"beam_name": "2025042401552900", "beam_number": 4},
        ],
    )

    if result != 4:
        raise AssertionError(f"Expected resolved raw DICOM beam number 4, got {result!r}")


def test_run_case_level_tps_generation_uses_treatment_beam_indices_for_timestamp_folders(
    mock_settings,
):
    dispatcher = importlib.import_module("src.core.dispatcher")
    logger = MagicMock()

    case_repo = MagicMock()
    case_repo.db.init_db.return_value = None
    case_repo.get_beams_for_case.return_value = [
        SimpleNamespace(
            beam_id="55061194_2025042401440800",
            beam_number=1,
            beam_path=Path("cases") / "55061194" / "2025042401440800",
        ),
        SimpleNamespace(
            beam_id="55061194_2025042401501400",
            beam_number=2,
            beam_path=Path("cases") / "55061194" / "2025042401501400",
        ),
        SimpleNamespace(
            beam_id="55061194_2025042401552900",
            beam_number=3,
            beam_path=Path("cases") / "55061194" / "2025042401552900",
        ),
    ]

    gpu_repo = MagicMock()
    gpu_repo.get_available_gpu_count.return_value = 3
    gpu_repo.find_and_lock_multiple_gpus.return_value = [
        {"gpu_uuid": "gpu-1", "gpu_id": 0},
        {"gpu_uuid": "gpu-2", "gpu_id": 1},
        {"gpu_uuid": "gpu-3", "gpu_id": 2},
    ]

    validator = MagicMock()
    validator.get_beam_information.return_value = {
        "beams": [
            {"beam_name": "beam a", "beam_number": 2},
            {"beam_name": "beam b", "beam_number": 3},
            {"beam_name": "beam c", "beam_number": 4},
        ]
    }
    validator.get_treatment_beam_numbers.return_value = [2, 3, 4]

    tps_generator = MagicMock()
    tps_generator.generate_tps_file_with_gpu_assignments.return_value = True

    context_manager = MagicMock()
    context_manager.__enter__.return_value = case_repo
    context_manager.__exit__.return_value = False
    mock_settings.get_handler_mode.return_value = "local"

    with patch.object(dispatcher.LoggerFactory, "get_logger", return_value=logger), \
         patch.object(dispatcher, "TpsGenerator", return_value=tps_generator), \
         patch.object(dispatcher, "DataIntegrityValidator", return_value=validator), \
         patch.object(dispatcher, "GpuRepository", return_value=gpu_repo), \
         patch.object(dispatcher, "get_db_session", return_value=context_manager):
        result = dispatcher.run_case_level_tps_generation(
            case_id="55061194",
            case_path=Path("cases") / "55061194",
            beam_count=3,
            settings=mock_settings,
        )

    # W-3 fix: gpu_assignments now include beam_id for proper matching
    expected_result = [
        {"gpu_uuid": "gpu-1", "gpu_id": 0, "beam_id": "55061194_2025042401440800"},
        {"gpu_uuid": "gpu-2", "gpu_id": 1, "beam_id": "55061194_2025042401501400"},
        {"gpu_uuid": "gpu-3", "gpu_id": 2, "beam_id": "55061194_2025042401552900"},
    ]
    if result != expected_result:
        raise AssertionError(f"Unexpected GPU assignment result: {result!r}")
    case_repo.update_beam_number.assert_any_call("55061194_2025042401440800", 2)
    case_repo.update_beam_number.assert_any_call("55061194_2025042401501400", 3)
    case_repo.update_beam_number.assert_any_call("55061194_2025042401552900", 4)


def test_run_case_level_tps_generation_multigpu_reserves_all_gpus_for_first_beam(
    mock_settings,
):
    dispatcher = importlib.import_module("src.core.dispatcher")
    logger = MagicMock()

    case_repo = MagicMock()
    case_repo.db.init_db.return_value = None
    case_repo.get_beams_for_case.return_value = [
        SimpleNamespace(beam_id="case-1_beam-a", beam_number=1, beam_path=Path("cases") / "case-1" / "beam-a"),
        SimpleNamespace(beam_id="case-1_beam-b", beam_number=2, beam_path=Path("cases") / "case-1" / "beam-b"),
    ]

    gpu_repo = MagicMock()
    gpu_repo.get_available_gpu_count.return_value = 3
    gpu_repo.find_and_lock_multiple_gpus.return_value = [
        {"gpu_uuid": "gpu-0", "gpu_id": 0},
        {"gpu_uuid": "gpu-1", "gpu_id": 1},
        {"gpu_uuid": "gpu-2", "gpu_id": 2},
    ]

    validator = MagicMock()
    validator.get_beam_information.return_value = {
        "beams": [
            {"beam_name": "beam-a", "beam_number": 1},
            {"beam_name": "beam-b", "beam_number": 2},
        ]
    }
    validator.get_treatment_beam_numbers.return_value = [1, 2]

    tps_generator = MagicMock()
    tps_generator.generate_tps_file_with_gpu_assignments.return_value = True

    context_manager = MagicMock()
    context_manager.__enter__.return_value = case_repo
    context_manager.__exit__.return_value = False

    mock_settings.get_handler_mode.return_value = "local"
    mock_settings.get_moqui_runtime_config.return_value = {
        "multigpu_enabled": True,
        "beam_uses_all_available_gpus": True,
        "max_gpus_per_beam": 3,
    }

    with patch.object(dispatcher.LoggerFactory, "get_logger", return_value=logger), \
         patch.object(dispatcher, "TpsGenerator", return_value=tps_generator), \
         patch.object(dispatcher, "DataIntegrityValidator", return_value=validator), \
         patch.object(dispatcher, "GpuRepository", return_value=gpu_repo), \
         patch.object(dispatcher, "get_db_session", return_value=context_manager):
        result = dispatcher.run_case_level_tps_generation(
            case_id="case-1",
            case_path=Path("cases") / "case-1",
            beam_count=2,
            settings=mock_settings,
        )

    expected_result = [
        {"gpu_uuid": "gpu-0", "gpu_id": 0, "beam_id": "case-1_beam-a"},
        {"gpu_uuid": "gpu-1", "gpu_id": 1, "beam_id": "case-1_beam-a"},
        {"gpu_uuid": "gpu-2", "gpu_id": 2, "beam_id": "case-1_beam-a"},
    ]
    if result != expected_result:
        raise AssertionError(f"Expected all GPUs to be reserved for the first beam only, got {result!r}")

    tps_generator.generate_tps_file_with_gpu_assignments.assert_called_once()
    call = tps_generator.generate_tps_file_with_gpu_assignments.call_args
    if call.kwargs["beam_name"] != "case-1_beam-a":
        raise AssertionError(f"Expected first beam TPS generation, got {call.kwargs['beam_name']!r}")
    if call.kwargs["gpu_assignments"] != expected_result:
        raise AssertionError(f"Expected multigpu beam assignment list, got {call.kwargs['gpu_assignments']!r}")


def test_run_case_level_tps_generation_multigpu_ignores_max_gpu_cap_when_all_available_enabled(
    mock_settings,
):
    dispatcher = importlib.import_module("src.core.dispatcher")
    logger = MagicMock()

    case_repo = MagicMock()
    case_repo.db.init_db.return_value = None
    case_repo.get_beams_for_case.return_value = [
        SimpleNamespace(beam_id="case-1_beam-a", beam_number=1, beam_path=Path("cases") / "case-1" / "beam-a"),
    ]

    gpu_repo = MagicMock()
    gpu_repo.get_available_gpu_count.return_value = 8
    gpu_repo.find_and_lock_multiple_gpus.return_value = [
        {"gpu_uuid": f"gpu-{idx}", "gpu_id": idx}
        for idx in range(8)
    ]

    validator = MagicMock()
    validator.get_beam_information.return_value = {
        "beams": [{"beam_name": "beam-a", "beam_number": 1}]
    }
    validator.get_treatment_beam_numbers.return_value = [1]

    tps_generator = MagicMock()
    tps_generator.generate_tps_file_with_gpu_assignments.return_value = True

    context_manager = MagicMock()
    context_manager.__enter__.return_value = case_repo
    context_manager.__exit__.return_value = False

    mock_settings.get_handler_mode.return_value = "local"
    mock_settings.get_moqui_runtime_config.return_value = {
        "multigpu_enabled": True,
        "beam_uses_all_available_gpus": True,
        "max_gpus_per_beam": 4,
    }

    with patch.object(dispatcher.LoggerFactory, "get_logger", return_value=logger), \
         patch.object(dispatcher, "TpsGenerator", return_value=tps_generator), \
         patch.object(dispatcher, "DataIntegrityValidator", return_value=validator), \
         patch.object(dispatcher, "GpuRepository", return_value=gpu_repo), \
         patch.object(dispatcher, "get_db_session", return_value=context_manager):
        dispatcher.run_case_level_tps_generation(
            case_id="case-1",
            case_path=Path("cases") / "case-1",
            beam_count=1,
            settings=mock_settings,
        )

    gpu_repo.find_and_lock_multiple_gpus.assert_called_once_with(case_id="case-1", num_gpus=8)


def test_run_case_level_ptn_checker_analysis_records_success_without_failing_case(mock_settings):
    dispatcher = importlib.import_module("src.core.dispatcher")
    logger = MagicMock()
    case_repo = MagicMock()
    context_manager = MagicMock()
    context_manager.__enter__.return_value = case_repo
    context_manager.__exit__.return_value = False

    validator = MagicMock()
    validator.find_rtplan_file.return_value = Path("cases") / "case-1" / "RP.test.dcm"
    validator.find_ptn_files.return_value = [Path("cases") / "case-1" / "beam-1" / "delivered.ptn"]
    integration = MagicMock()
    integration.run_analysis.return_value = dispatcher.PtnCheckerResult(
        success=True,
        status_code="SUCCESS",
        error_message=None,
        output_dir=Path("cases") / "case-1" / "ptn_checker_output",
    )

    with patch.object(dispatcher.LoggerFactory, "get_logger", return_value=logger), \
         patch.object(dispatcher, "get_db_session", return_value=context_manager), \
         patch.object(dispatcher, "DataIntegrityValidator", return_value=validator), \
         patch.object(dispatcher, "create_ptn_checker_integration", return_value=integration):
        result = dispatcher.run_case_level_ptn_analysis(
            case_id="case-1",
            case_path=Path("cases") / "case-1",
            settings=mock_settings,
        )

    if result.success is not True:
        raise AssertionError(f"Expected PTN analysis success, got {result!r}")
    case_repo.record_ptn_checker_result.assert_called_once()
    case_repo.update_case_status.assert_not_called()


def test_run_case_level_ptn_checker_analysis_records_failure_in_ptn_fields_only(mock_settings):
    dispatcher = importlib.import_module("src.core.dispatcher")
    logger = MagicMock()
    case_repo = MagicMock()
    context_manager = MagicMock()
    context_manager.__enter__.return_value = case_repo
    context_manager.__exit__.return_value = False

    validator = MagicMock()
    validator.find_rtplan_file.return_value = None
    validator.find_ptn_files.return_value = [Path("cases") / "case-1" / "beam-1" / "delivered.ptn"]
    integration = MagicMock()

    with patch.object(dispatcher.LoggerFactory, "get_logger", return_value=logger), \
         patch.object(dispatcher, "get_db_session", return_value=context_manager), \
         patch.object(dispatcher, "DataIntegrityValidator", return_value=validator), \
         patch.object(dispatcher, "create_ptn_checker_integration", return_value=integration):
        result = dispatcher.run_case_level_ptn_analysis(
            case_id="case-1",
            case_path=Path("cases") / "case-1",
            settings=mock_settings,
        )

    if result.status_code != "FAILED_NO_DICOM":
        raise AssertionError(f"Expected FAILED_NO_DICOM result, got {result!r}")
    case_repo.record_ptn_checker_result.assert_called_once()
    case_repo.update_case_status.assert_not_called()
