import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

from src.core import dispatcher
from src.config.settings import Settings
from src.handlers.execution_handler import ExecutionHandler, ExecutionResult, UploadResult

@pytest.fixture
def mock_settings():
    """Fixture for mocked Settings."""
    settings = MagicMock(spec=Settings)
    settings.get_database_path.return_value = "dummy.db"
    settings.get_case_directories.return_value = {
        "csv_output": "/tmp/csv_output/{case_id}"
    }
    mock_logging_config = MagicMock()
    mock_logging_config.log_level = "INFO"
    mock_logging_config.log_dir = Path("/tmp/logs")
    mock_logging_config.max_file_size = 10
    mock_logging_config.backup_count = 5
    settings.logging = mock_logging_config
    settings.database = {}
    return settings

@patch("src.core.dispatcher.DatabaseConnection")
@patch("src.core.dispatcher.CaseRepository")
@patch("src.core.dispatcher.ExecutionHandler")
def test_run_case_level_csv_interpreting_success(mock_exec_handler_cls, mock_case_repo_cls, mock_db_conn_cls, mock_settings):
    """
    Tests that run_case_level_csv_interpreting successfully calls
    the execution handler with the correct command.
    """
    # Arrange
    mock_exec_handler_instance = MagicMock(spec=ExecutionHandler)
    mock_exec_handler_instance.execute_command.return_value = ExecutionResult(success=True, output="", error="", return_code=0)
    mock_exec_handler_cls.return_value = mock_exec_handler_instance

    case_id = "test_case_01"
    case_path = Path("/dummypath/test_case_01")

    # Act
    success = dispatcher.run_case_level_csv_interpreting(case_id, case_path, mock_settings)

    # Assert
    assert success is True
    mock_exec_handler_instance.execute_command.assert_called_once()
    call_args, call_kwargs = mock_exec_handler_instance.execute_command.call_args
    # Updated to match new command format using config paths
    assert "python" in call_args[0] or "/usr/bin/python3" in call_args[0]
    assert "main_cli.py" in call_args[0]
    assert f"--logdir {case_path}" in call_args[0]
    assert f"--outputdir /tmp/csv_output/{case_id}" in call_args[0]
    assert call_kwargs["cwd"] == case_path

@patch("src.core.dispatcher.DatabaseConnection")
@patch("src.core.dispatcher.CaseRepository")
@patch("src.core.dispatcher.ExecutionHandler")
def test_run_case_level_upload_success(mock_exec_handler_cls, mock_case_repo_cls, mock_db_conn_cls, mock_settings):
    """
    Tests that run_case_level_upload successfully calls the execution handler's
    upload_file method for each CSV file.
    """
    # Arrange
    mock_exec_handler_instance = MagicMock(spec=ExecutionHandler)
    mock_exec_handler_instance.upload_file.return_value = UploadResult(success=True)
    mock_exec_handler_cls.return_value = mock_exec_handler_instance

    mock_ssh_client = MagicMock()

    # Mock settings for HPC paths
    mock_settings.get_hpc_paths.return_value = {
        "remote_case_path_template": "/remote/cases"
    }

    # Mock CaseRepository to return beams
    mock_beam = MagicMock()
    mock_beam.parent_case_id = "test_case_01"
    mock_beam.beam_id = "beam_01"
    mock_case_repo_instance = mock_case_repo_cls.return_value
    mock_case_repo_instance.get_beams_for_case.return_value = [mock_beam]

    case_id = "test_case_01"

    # Create dummy CSV files to be "uploaded"
    with patch("src.core.dispatcher.Path.glob") as mock_glob:
        mock_csv_file = Path(f"/tmp/csv_output/{case_id}/test.csv")
        mock_glob.return_value = [mock_csv_file]

        # Act
        success = dispatcher.run_case_level_upload(case_id, mock_settings, mock_ssh_client)

        # Assert
        assert success is True
        mock_exec_handler_instance.upload_file.assert_called_once_with(
            local_path=str(mock_csv_file),
            remote_path=f"/remote/cases/{case_id}/beam_01/{mock_csv_file.name}"
        )