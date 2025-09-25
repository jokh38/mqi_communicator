import pytest
from unittest.mock import patch, MagicMock
import paramiko

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