import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
import yaml
import paramiko

from src.core import dispatcher
from src.config.settings import Settings
from src.handlers.execution_handler import ExecutionHandler, ExecutionResult, UploadResult
from src.domain.models import BeamData

@pytest.fixture
def dispatcher_config_file(tmp_path: Path) -> Path:
    """Provides a config file for dispatcher tests."""
    config_data = {
        "ExecutionHandler": {
            "CsvInterpreter": "local",
            "HpcJobSubmitter": "remote",
        },
        "paths": {
            "base_directory": str(tmp_path),
            "local": {
                "database_path": "{base_directory}/mqi_test.db",
                "csv_output_dir": "{base_directory}/csv_output/{case_id}",
            },
            "remote": {
                "remote_case_path": "/hpc/cases/{case_id}",
                "remote_beam_path": "{remote_case_path}/{beam_id}",
            },
        },
        "executables": {
            "local": {
                "python": "/usr/bin/python3",
                "mqi_interpreter_script": "scripts/interpreter.py",
            },
        },
        "command_templates": {
            "local": {
                "interpret_csv": "{python} {mqi_interpreter_script} --input {input_path} --output {csv_output_dir}",
            },
        },
        "logging": {"log_level": "DEBUG", "log_dir": str(tmp_path / "logs")},
        "database": {"journal_mode": "WAL"},
    }
    config_file = tmp_path / "config.yaml"
    with open(config_file, "w") as f:
        yaml.dump(config_data, f)
    return config_file

@pytest.fixture
def settings(dispatcher_config_file: Path) -> Settings:
    """Fixture to provide a real Settings instance for dispatcher tests."""
    return Settings(config_path=dispatcher_config_file)

@patch("src.core.dispatcher.DatabaseConnection")
@patch("src.core.dispatcher.CaseRepository")
@patch("src.core.dispatcher.ExecutionHandler")
def test_run_csv_interpreting_uses_settings(
        mock_exec_handler_cls, mock_case_repo_cls,
        mock_db_conn_cls, settings):
    """
    Tests that run_case_level_csv_interpreting uses Settings to get the
    command and paths, and correctly overrides the input path.
    """
    # Arrange
    dispatcher.LoggerFactory.configure(settings) # Configure logger for the test
    mock_exec_handler_instance = MagicMock(spec=ExecutionHandler)
    mock_exec_handler_instance.execute_command.return_value = ExecutionResult(success=True, output="", error="", return_code=0)
    mock_exec_handler_cls.return_value = mock_exec_handler_instance

    case_id = "test_case_01"
    case_path = Path(f"/dummypath/{case_id}")

    # The dispatcher should call get_command with the correct `input_path`
    expected_command = settings.get_command(
        "interpret_csv",
        handler_name="CsvInterpreter",
        case_id=case_id,
        input_path=str(case_path)
    )

    # Act
    success = dispatcher.run_case_level_csv_interpreting(case_id, case_path, settings)

    # Assert
    assert success is True
    db_path_from_settings = settings.get_path("database_path", handler_name="CsvInterpreter")
    mock_db_conn_cls.assert_called_once()
    db_call_args = mock_db_conn_cls.call_args[1]
    assert db_call_args['db_path'] == Path(db_path_from_settings)
    assert db_call_args['settings'] is settings

    # Assert that ExecutionHandler was called correctly
    mock_exec_handler_cls.assert_called_once_with(settings=settings, mode="local")
    mock_exec_handler_instance.execute_command.assert_called_once_with(expected_command, cwd=case_path)


@patch("src.core.dispatcher.DatabaseConnection")
@patch("src.core.dispatcher.CaseRepository")
@patch("src.core.dispatcher.ExecutionHandler")
@patch("src.core.dispatcher.Path.glob")
def test_run_upload_uses_settings(
        mock_glob, mock_exec_handler_cls, mock_case_repo_cls,
        mock_db_conn_cls, settings, tmp_path):
    """
    Tests that run_case_level_upload uses Settings to generate the remote path.
    """
    # Arrange
    dispatcher.LoggerFactory.configure(settings) # Configure logger for the test
    mock_exec_handler_instance = MagicMock(spec=ExecutionHandler)
    mock_exec_handler_instance.upload_file.return_value = UploadResult(success=True)
    mock_exec_handler_cls.return_value = mock_exec_handler_instance
    mock_ssh_client = MagicMock(spec=paramiko.SSHClient)

    case_id = "test_case_01"

    csv_output_dir = Path(settings.get_path("csv_output_dir", handler_name="CsvInterpreter", case_id=case_id))
    csv_output_dir.mkdir(parents=True, exist_ok=True)
    mock_csv_file = csv_output_dir / "test.csv"
    mock_csv_file.touch()
    mock_glob.return_value = [mock_csv_file]

    mock_beam = BeamData(beam_id=f"{case_id}_beam_01", parent_case_id=case_id, beam_path=Path("."), status="pending", created_at="now", updated_at="now")
    mock_case_repo_instance = mock_case_repo_cls.return_value
    mock_case_repo_instance.get_beams_for_case.return_value = [mock_beam]

    # Act
    success = dispatcher.run_case_level_upload(case_id, settings, mock_ssh_client)

    # Assert
    assert success is True

    expected_remote_path = settings.get_path(
        "remote_beam_path",
        handler_name="HpcJobSubmitter",
        case_id=case_id,
        beam_id=mock_beam.beam_id
    ) + f"/{mock_csv_file.name}"

    mock_exec_handler_instance.upload_file.assert_called_once_with(
        local_path=str(mock_csv_file),
        remote_path=expected_remote_path
    )
    # Assert that ExecutionHandler was called correctly
    mock_exec_handler_cls.assert_called_once_with(settings=settings, mode="remote", ssh_client=mock_ssh_client)
