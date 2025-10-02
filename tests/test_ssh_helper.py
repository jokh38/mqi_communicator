"""Unit tests for SSH helper."""

import unittest
from unittest.mock import Mock, patch, MagicMock

from src.utils.ssh_helper import create_ssh_client


class TestSshHelper(unittest.TestCase):
    """Test cases for the SSH client helper."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_settings = Mock()
        self.mock_logger = Mock()
        self.valid_hpc_config = {
            "host": "hpc.example.com",
            "user": "testuser",
            "ssh_key_path": "/path/to/key",
            "port": 22,
            "connection_timeout_seconds": 30
        }

    @patch('src.utils.ssh_helper.paramiko.SSHClient')
    def test_create_ssh_client_success(self, mock_ssh_class):
        """Test successful SSH client creation."""
        # Arrange
        self.mock_settings.get_hpc_connection.return_value = self.valid_hpc_config
        mock_ssh_instance = Mock()
        mock_ssh_class.return_value = mock_ssh_instance

        # Act
        result = create_ssh_client(self.mock_settings, self.mock_logger)

        # Assert
        self.assertEqual(result, mock_ssh_instance)
        mock_ssh_instance.set_missing_host_key_policy.assert_called_once()
        mock_ssh_instance.connect.assert_called_once_with(
            hostname="hpc.example.com",
            username="testuser",
            key_filename="/path/to/key",
            port=22,
            timeout=30
        )
        self.mock_logger.info.assert_called()

    @patch('src.utils.ssh_helper.paramiko.SSHClient')
    def test_create_ssh_client_with_default_timeout(self, mock_ssh_class):
        """Test SSH client creation with default timeout."""
        # Arrange
        config_without_timeout = self.valid_hpc_config.copy()
        del config_without_timeout["connection_timeout_seconds"]
        self.mock_settings.get_hpc_connection.return_value = config_without_timeout
        mock_ssh_instance = Mock()
        mock_ssh_class.return_value = mock_ssh_instance

        # Act
        result = create_ssh_client(self.mock_settings, self.mock_logger)

        # Assert
        self.assertIsNotNone(result)
        mock_ssh_instance.connect.assert_called_once()
        # Verify default timeout of 30 was used
        call_args = mock_ssh_instance.connect.call_args
        self.assertEqual(call_args.kwargs['timeout'], 30)
        self.mock_logger.warning.assert_called()

    def test_create_ssh_client_missing_config(self):
        """Test SSH client creation with missing config."""
        # Arrange
        self.mock_settings.get_hpc_connection.return_value = None

        # Act
        result = create_ssh_client(self.mock_settings, self.mock_logger)

        # Assert
        self.assertIsNone(result)
        self.mock_logger.warning.assert_called()

    def test_create_ssh_client_incomplete_config(self):
        """Test SSH client creation with incomplete config."""
        # Arrange
        incomplete_config = {"host": "hpc.example.com"}  # Missing user and ssh_key_path
        self.mock_settings.get_hpc_connection.return_value = incomplete_config

        # Act
        result = create_ssh_client(self.mock_settings, self.mock_logger)

        # Assert
        self.assertIsNone(result)
        self.mock_logger.warning.assert_called()

    @patch('src.utils.ssh_helper.paramiko.SSHClient')
    def test_create_ssh_client_connection_failure(self, mock_ssh_class):
        """Test SSH client creation when connection fails."""
        # Arrange
        self.mock_settings.get_hpc_connection.return_value = self.valid_hpc_config
        mock_ssh_instance = Mock()
        mock_ssh_instance.connect.side_effect = Exception("Connection refused")
        mock_ssh_class.return_value = mock_ssh_instance

        # Act
        result = create_ssh_client(self.mock_settings, self.mock_logger)

        # Assert
        self.assertIsNone(result)
        self.mock_logger.error.assert_called()

    @patch('src.utils.ssh_helper.paramiko.SSHClient')
    def test_create_ssh_client_with_custom_port(self, mock_ssh_class):
        """Test SSH client creation with custom port."""
        # Arrange
        custom_config = self.valid_hpc_config.copy()
        custom_config["port"] = 2222
        self.mock_settings.get_hpc_connection.return_value = custom_config
        mock_ssh_instance = Mock()
        mock_ssh_class.return_value = mock_ssh_instance

        # Act
        result = create_ssh_client(self.mock_settings, self.mock_logger)

        # Assert
        self.assertIsNotNone(result)
        call_args = mock_ssh_instance.connect.call_args
        self.assertEqual(call_args.kwargs['port'], 2222)


if __name__ == '__main__':
    unittest.main()
