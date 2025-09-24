import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

from src.handlers.remote_handler import RemoteHandler, UploadResult
from src.config.settings import Settings
from src.infrastructure.logging_handler import StructuredLogger
from src.utils.retry_policy import RetryPolicy

@pytest.fixture
def mock_settings() -> Settings:
    """Fixture for a mocked Settings object."""
    settings = MagicMock(spec=Settings)
    config = {
        "host": "testhost",
        "user": "testuser",
        "ssh_key_path": "/path/to/key",
        "remote_base_dir": "D:/MOQUI_RESULTS",
    }
    settings.get_pc_localdata_connection.return_value = config
    return settings

@pytest.fixture
def mock_logger() -> StructuredLogger:
    """Fixture for a mocked StructuredLogger."""
    return MagicMock(spec=StructuredLogger)

@pytest.fixture
def mock_retry_policy() -> RetryPolicy:
    """Fixture for a mocked RetryPolicy."""
    retry_policy = MagicMock(spec=RetryPolicy)
    # Make the retry policy execute the function directly without retries
    retry_policy.execute.side_effect = lambda func, **kwargs: func()
    return retry_policy

@pytest.fixture
def remote_handler(mock_settings, mock_logger, mock_retry_policy) -> RemoteHandler:
    """Fixture for a RemoteHandler instance with mocked dependencies."""
    return RemoteHandler(settings=mock_settings, logger=mock_logger, retry_policy=mock_retry_policy)

@patch('paramiko.SSHClient')
def test_upload_to_pc_localdata_success(mock_ssh_client_class, remote_handler: RemoteHandler, tmp_path: Path):
    """
    Tests the successful upload of a file to PC_localdata.
    This test will fail until the method is implemented.
    """
    # Arrange
    mock_ssh_instance = MagicMock()
    mock_sftp_instance = MagicMock()
    mock_ssh_client_class.return_value.__enter__.return_value = mock_ssh_instance
    mock_ssh_instance.open_sftp.return_value.__enter__.return_value = mock_sftp_instance

    local_file = tmp_path / "result.pdf"
    local_file.touch()
    case_id = "case-001"

    remote_handler._mkdir_p = MagicMock()

    # Act
    result = remote_handler.upload_to_pc_localdata(local_file, case_id)

    # Assert
    # 1. Check if a new SSH connection was made with the correct details
    mock_settings = remote_handler.settings
    pc_local_config = mock_settings.get_pc_localdata_connection()
    mock_ssh_instance.connect.assert_called_once_with(
        hostname=pc_local_config['host'],
        username=pc_local_config['user'],
        key_filename=pc_local_config['ssh_key_path'],
        timeout=30 # Assuming a default timeout
    )

    # 2. Check if the remote directory was created (via _mkdir_p logic)
    # The new method should reuse the _mkdir_p helper.
    # We can check the sftp calls it makes.
    remote_target_dir = f"{pc_local_config['remote_base_dir']}/{case_id}".replace("\\", "/")
    remote_handler._mkdir_p.assert_called_once_with(mock_sftp_instance, remote_target_dir)

    # 3. Check if the file was uploaded correctly
    remote_file_path = f"{remote_target_dir}/{local_file.name}"
    mock_sftp_instance.put.assert_called_once_with(str(local_file), remote_file_path)

    # 4. Check the result
    assert isinstance(result, UploadResult)
    assert result.success is True
    assert result.error is None


@patch('paramiko.SSHClient')
def test_upload_directory_to_pc_localdata_success(mock_ssh_client_class, remote_handler: RemoteHandler, tmp_path: Path):
    """
    Tests the successful upload of an entire directory to PC_localdata.
    """
    # Arrange
    mock_ssh_instance = MagicMock()
    mock_sftp_instance = MagicMock()
    mock_ssh_client_class.return_value.__enter__.return_value = mock_ssh_instance
    mock_ssh_instance.open_sftp.return_value.__enter__.return_value = mock_sftp_instance

    # Create a local directory structure to upload
    local_dir = tmp_path / "case-002-results"
    local_dir.mkdir()
    (local_dir / "file1.dcm").touch()
    nested_dir = local_dir / "nested"
    nested_dir.mkdir()
    (nested_dir / "file2.dcm").touch()

    case_id = "case-002"
    remote_handler._mkdir_p = MagicMock()

    # Act
    result = remote_handler.upload_to_pc_localdata(local_dir, case_id)

    # Assert
    assert result.success is True
    assert result.error is None

    # Check that remote directories were created
    pc_local_config = remote_handler.settings.get_pc_localdata_connection()
    base_remote_dir = f"{pc_local_config['remote_base_dir']}/{case_id}".replace("\\", "/")
    nested_remote_dir = f"{base_remote_dir}/nested"

    # Check that mkdir_p was called for base and nested directories
    assert remote_handler._mkdir_p.call_count == 2
    remote_handler._mkdir_p.assert_any_call(mock_sftp_instance, base_remote_dir)
    remote_handler._mkdir_p.assert_any_call(mock_sftp_instance, nested_remote_dir)

    # Check that both files were uploaded
    assert mock_sftp_instance.put.call_count == 2
    mock_sftp_instance.put.assert_any_call(str(local_dir / "file1.dcm"), f"{base_remote_dir}/file1.dcm")
    mock_sftp_instance.put.assert_any_call(str(nested_dir / "file2.dcm"), f"{nested_remote_dir}/file2.dcm")


def test_upload_to_pc_localdata_with_nonexistent_local_path(remote_handler: RemoteHandler, tmp_path: Path):
    """
    Tests that upload_to_pc_localdata returns a failure result when the local path does not exist.
    """
    # Arrange
    non_existent_path = tmp_path / "non_existent_dir"
    case_id = "case-003"

    # Act
    result = remote_handler.upload_to_pc_localdata(non_existent_path, case_id)

    # Assert
    assert result.success is False
    assert result.error is not None
    assert "does not exist" in result.error.lower()
