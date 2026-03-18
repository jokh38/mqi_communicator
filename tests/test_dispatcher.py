import builtins
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.config.settings import Settings
from src.handlers.execution_handler import ExecutionHandler, ExecutionResult, UploadResult


def _import_module_without_paramiko(module_name: str, monkeypatch: pytest.MonkeyPatch):
    original_import = builtins.__import__

    def blocked_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "paramiko":
            raise ModuleNotFoundError("No module named 'paramiko'")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.delitem(sys.modules, "paramiko", raising=False)
    monkeypatch.delitem(sys.modules, module_name, raising=False)
    monkeypatch.delitem(sys.modules, "src.handlers.execution_handler", raising=False)
    monkeypatch.setattr(builtins, "__import__", blocked_import)
    return importlib.import_module(module_name)


@pytest.fixture
def mock_settings():
    settings = MagicMock(spec=Settings)
    settings.get_database_path.return_value = "dummy.db"
    settings.database = {}
    csv_output_dir = Path("tmp") / "csv_output"
    mqi_interpreter_dir = Path("opt") / "mqi_interpreter"
    python_executable = Path("usr") / "bin" / "python3"
    interpreter_script = Path("opt") / "main_cli.py"
    remote_case_root = Path("remote") / "cases"
    settings.get_path.side_effect = lambda key, **_: {
        "database_path": "dummy.db",
        "csv_output_dir": str(csv_output_dir),
        "mqi_interpreter_dir": str(mqi_interpreter_dir),
    }[key]
    settings.get_executable.side_effect = lambda key, **_: {
        "python": str(python_executable),
        "mqi_interpreter_script": str(interpreter_script),
    }[key]
    settings.get_hpc_paths.return_value = {"remote_case_path_template": str(remote_case_root)}
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
        f"cd {Path('opt') / 'mqi_interpreter'} && {Path('usr') / 'bin' / 'python3'} {Path('opt') / 'main_cli.py'} "
        f"--logdir {case_path} --outputdir {Path('tmp') / 'csv_output' / case_id}",
        cwd=case_path,
    )


@patch("src.core.dispatcher.get_db_session")
@patch("src.core.dispatcher.ExecutionHandler")
def test_run_case_level_upload_success(
    mock_exec_handler_cls,
    mock_get_db_session,
    mock_settings,
):
    dispatcher = importlib.import_module("src.core.dispatcher")
    mock_exec_handler_instance = MagicMock(spec=ExecutionHandler)
    mock_exec_handler_instance.upload_file.return_value = UploadResult(success=True)
    mock_exec_handler_cls.return_value = mock_exec_handler_instance
    mock_case_repo = MagicMock()
    mock_case_repo.get_beams_for_case.return_value = [
        SimpleNamespace(parent_case_id="test_case_01", beam_id="beam_01")
    ]
    context_manager = MagicMock()
    context_manager.__enter__.return_value = mock_case_repo
    context_manager.__exit__.return_value = False
    mock_get_db_session.return_value = context_manager

    mock_ssh_client = MagicMock()

    case_id = "test_case_01"
    with patch("src.core.dispatcher.Path.glob") as mock_glob:
        mock_csv_file = Path("tmp") / "csv_output" / case_id / "test.csv"
        mock_glob.return_value = [mock_csv_file]

        success = dispatcher.run_case_level_upload(case_id, mock_settings, mock_ssh_client)

    if success is not True:
        raise AssertionError("Upload should succeed")
    mock_exec_handler_instance.upload_file.assert_called_once_with(
        local_path=str(mock_csv_file),
        remote_path=f"{Path('remote') / 'cases'}/{case_id}/beam_01/{mock_csv_file.name}",
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
    case_repo.update_beam_number.assert_any_call("case-1_beam-b", 1)
    case_repo.update_beam_number.assert_any_call("case-1_beam-a", 2)


def test_dispatcher_module_import_does_not_require_paramiko_for_local_operations(monkeypatch):
    module = _import_module_without_paramiko("src.core.dispatcher", monkeypatch)

    if not hasattr(module, "run_case_level_csv_interpreting"):
        raise AssertionError("Dispatcher import should expose run_case_level_csv_interpreting")


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


def test_resolve_persisted_beam_number_prefers_existing_beam_number():
    dispatcher = importlib.import_module("src.core.dispatcher")
    beam = SimpleNamespace(
        beam_id="55061194_2025042401440800",
        beam_path=Path("cases") / "55061194" / "2025042401440800",
        beam_number=2,
    )

    result = dispatcher._resolve_persisted_beam_number(
        beam,
        beam_metadata=[{"beam_name": "non_matching_name", "beam_number": 99}],
    )

    if result != 2:
        raise AssertionError(f"Expected resolved beam number 2, got {result!r}")


def test_resolve_persisted_beam_number_uses_treatment_beam_index_for_timestamp_folders():
    dispatcher = importlib.import_module("src.core.dispatcher")
    beam = SimpleNamespace(
        beam_id="55061194_2025042401552900",
        beam_path=Path("cases") / "55061194" / "2025042401552900",
        beam_number=4,
    )

    result = dispatcher._resolve_persisted_beam_number(
        beam,
        beam_metadata=[
            {"beam_name": "beam a", "beam_number": 2},
            {"beam_name": "beam b", "beam_number": 3},
            {"beam_name": "beam c", "beam_number": 4},
        ],
    )

    if result != 3:
        raise AssertionError(f"Expected resolved beam number 3, got {result!r}")


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
    case_repo.update_beam_number.assert_any_call("55061194_2025042401440800", 1)
    case_repo.update_beam_number.assert_any_call("55061194_2025042401501400", 2)
    case_repo.update_beam_number.assert_any_call("55061194_2025042401552900", 3)
