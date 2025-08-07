import unittest
from unittest.mock import MagicMock, patch
import socket
import paramiko

from executors.remote import RemoteExecutor
from executors.base import ExecutionResult
from remote.connection import ConnectionManager

class TestRemoteExecutor(unittest.TestCase):

    def setUp(self):
        """Set up for each test."""
        self.mock_logger = MagicMock()
        self.mock_connection_manager = MagicMock(spec=ConnectionManager)
        self.executor = RemoteExecutor(
            connection_manager=self.mock_connection_manager,
            logger=self.mock_logger
        )

    @patch('executors.remote.RemoteExecutor._get_ssh_client_context')
    def test_execute_channel_closed_failure(self, mock_get_ssh_client_context):
        """
        Test that a 'Channel closed' error is handled gracefully.
        """
        # 1. Arrange
        # Mock the SSH client context to simulate a connection
        mock_ssh_client = MagicMock()
        mock_get_ssh_client_context.return_value.__enter__.return_value = mock_ssh_client

        # Configure exec_command to raise the specific SSHException
        mock_ssh_client.exec_command.side_effect = paramiko.SSHException("Channel closed.")

        # 2. Act
        result = self.executor.execute("some command")

        # 3. Assert
        # The current implementation should catch the generic exception and return exit_code -1
        self.assertEqual(result.exit_code, -1)
        self.assertIn("Connection/execution error: Channel closed.", result.stderr)

        # Verify that an error was logged
        self.mock_logger.error.assert_called_once_with(
            "Remote command execution error: some command, error: Channel closed."
        )

if __name__ == '__main__':
    unittest.main()
