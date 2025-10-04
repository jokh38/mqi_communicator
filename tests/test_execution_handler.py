import pytest
from unittest.mock import patch, MagicMock
import paramiko
from pathlib import Path
import logging
import os
import shutil

# This is a placeholder for the new handler.
# We will create the actual file in the next step.
from src.handlers.execution_handler import ExecutionHandler

@pytest.fixture
def mock_ssh_client():
    """Fixture for a mocked SSHClient."""
    mock_ssh = MagicMock(spec=paramiko.SSHClient)
    # Configure exec_command to return a 3-tuple of mocks
    mock_ssh.exec_command.return_value = (MagicMock(), MagicMock(), MagicMock())
    mock_ssh.open_sftp.return_value = MagicMock(spec=paramiko.SFTPClient)
    return mock_ssh

def test_handler_initialization_local_mode():
    """Test that the handler correctly initializes in 'local' mode."""
    handler = ExecutionHandler(mode="local")
    assert handler.mode == "local"

def test_handler_initialization_remote_mode():
    """Test that the handler correctly initializes in 'remote' mode."""
    handler = ExecutionHandler(mode="remote", ssh_client=MagicMock())
    assert handler.mode == "remote"

@patch("subprocess.run")
def test_execute_command_local_mode(mock_run):
    """Test command execution in 'local' mode."""
    handler = ExecutionHandler(mode="local")
    handler.execute_command("ls -l")
    mock_run.assert_called_once_with("ls -l", shell=True, check=True, capture_output=True, text=True, cwd=None)

@patch("shutil.copy")
def test_upload_file_local_mode(mock_copy):
    """Test file upload (copy) in 'local' mode."""
    handler = ExecutionHandler(mode="local")
    handler.upload_file("a.txt", "b.txt")
    mock_copy.assert_called_once_with("a.txt", "b.txt")

def test_execute_command_remote_mode(mock_ssh_client):
    """Test command execution in 'remote' mode."""
    handler = ExecutionHandler(mode="remote", ssh_client=mock_ssh_client)
    handler.execute_command("ls -l")
    mock_ssh_client.exec_command.assert_called_once_with("ls -l")

def test_upload_file_remote_mode(mock_ssh_client):
    """Test file upload in 'remote' mode."""
    handler = ExecutionHandler(mode="remote", ssh_client=mock_ssh_client)
    handler.upload_file("a.txt", "b.txt")
    mock_ssh_client.open_sftp.assert_called_once()
    mock_sftp = mock_ssh_client.open_sftp()
    mock_sftp.put.assert_called_once_with("a.txt", "b.txt")

def test_run_raw_to_dcm_remote_mode_correct_mock(mock_ssh_client):
    """
    Given: ExecutionHandler is in 'remote' mode.
    When: run_raw_to_dcm is called.
    Then: It should execute the raw_to_dcm command remotely via the ssh_client's exec_command using config paths.
    """
    # Arrange
    handler = ExecutionHandler(mode='remote', ssh_client=mock_ssh_client)
    case_id = "test_case_001"
    hpc_path = "/remote/path/to/hpc/data"

    # Act
    handler.run_raw_to_dcm(case_id, hpc_path)

    # Assert - command should now use config-defined paths
    # The actual command will contain python executable and script path from config
    called_command = mock_ssh_client.exec_command.call_args[0][0]
    assert "python" in called_command or "/usr/bin/python3" in called_command
    assert "moqui_raw2dicom.py" in called_command
    assert f"--case_id {case_id}" in called_command
    assert f"--hpc_path {hpc_path}" in called_command
    mock_ssh_client.exec_command.assert_called_once()

@patch("subprocess.run")
def test_run_raw_to_dcm_local_mode_adapted(mock_run):
    """
    Given: ExecutionHandler is in 'local' mode.
    When: run_raw_to_dcm is called with the new signature.
    Then: It should execute the corresponding local command using config paths.
    """
    # Arrange
    handler = ExecutionHandler(mode='local')
    case_id = "test_case_001"
    # In local mode, hpc_path is interpreted as the local case directory.
    local_case_path_str = "local/path/test_case_001"
    local_case_path = Path(local_case_path_str)

    # Act
    handler.run_raw_to_dcm(case_id, local_case_path_str)

    # Assert - command should now use config-defined python and script paths
    called_command = mock_run.call_args[0][0]
    assert "python" in called_command or "/usr/bin/python3" in called_command
    assert "moqui_raw2dicom.py" in called_command
    assert f"--input {local_case_path / f'{case_id}.raw'}" in called_command
    assert f"--output {local_case_path / 'dicom'}" in called_command
    assert "--dosetype 2d" in called_command
    mock_run.assert_called_once()
    assert mock_run.call_args[1]["cwd"] == local_case_path

def test_submit_simulation_job_local_mode_raises_clear_error():
    """
    Given: ExecutionHandler is in 'local' mode.
    When: submit_simulation_job is called.
    Then: It should raise NotImplementedError with a clear message.
    """
    # Arrange
    handler = ExecutionHandler(mode='local')
    expected_message = "Local mode does not support simulation job submission."

    # Act & Assert
    with pytest.raises(NotImplementedError, match=expected_message):
        handler.submit_simulation_job("some/script/path.sh")

def test_submit_simulation_job_remote_mode_refactored(mock_ssh_client):
    """
    Given: ExecutionHandler is in 'remote' mode.
    When: submit_simulation_job is called with the new signature.
    Then: It should execute the sbatch command on the remote script.
    """
    # Arrange
    handler = ExecutionHandler(mode='remote', ssh_client=mock_ssh_client)
    script_path = "/remote/path/to/submit_job.sh"

    # Mock the return value of exec_command to simulate sbatch output
    mock_stdin, mock_stdout, mock_stderr = MagicMock(), MagicMock(), MagicMock()
    mock_stdout.read.return_value = b"Submitted batch job 12345"
    mock_stdout.channel.recv_exit_status.return_value = 0  # Success exit code
    mock_stderr.read.return_value = b""
    mock_ssh_client.exec_command.return_value = (mock_stdin, mock_stdout, mock_stderr)

    # Act
    result = handler.submit_simulation_job(script_path)

    # Assert
    expected_command = f"sbatch {script_path}"
    mock_ssh_client.exec_command.assert_called_once_with(expected_command)
    assert result.success
    assert result.job_id == "12345"

def test_upload_to_pc_localdata_local_mode_logs_simulation(caplog, tmp_path):
    """
    Given: ExecutionHandler is in 'local' mode.
    When: upload_to_pc_localdata is called.
    Then: It should log that it is simulating the upload via a local copy.
    """
    # Arrange
    handler = ExecutionHandler(mode='local')
    source_file = tmp_path / "source.txt"
    source_file.write_text("test content")
    case_id = "case_123"

    # Act
    with caplog.at_level(logging.INFO):
        handler.upload_to_pc_localdata(source_file, case_id)

    # Assert
    assert f"Simulating upload by copying to local directory for case {case_id}" in caplog.text
    # Clean up the created directory
    shutil.rmtree(f"./localdata_uploads/{case_id}")