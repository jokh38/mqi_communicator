import unittest
from unittest.mock import MagicMock, patch, call
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
    def test_execute_retries_on_channel_closed(self, mock_get_ssh_client_context):
        """
        Test that RemoteExecutor retries the command once if a 'Channel closed' error occurs.
        """
        # 1. Arrange
        # Mock the SSH client to raise the exception on the first call, then succeed
        mock_ssh_client = MagicMock()
        mock_get_ssh_client_context.return_value.__enter__.return_value = mock_ssh_client

        # Simulate "Channel closed" on the first attempt, and success on the second
        mock_ssh_client.exec_command.side_effect = [
            paramiko.SSHException("Channel closed."),
            (MagicMock(), # stdin
             MagicMock(read=lambda: b'Success', channel=MagicMock(recv_exit_status=lambda: 0)), # stdout
             MagicMock(read=lambda: b'')) # stderr
        ]

        # 2. Act
        result = self.executor.execute("some command")

        # 3. Assert
        # Check that the command succeeded
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout, "Success")

        # Check that reconnect was called
        self.mock_connection_manager.reconnect.assert_called_once()

        # Check that exec_command was called twice
        self.assertEqual(mock_ssh_client.exec_command.call_count, 2)

        # Check that a warning was logged for the retry
        self.mock_logger.warning.assert_called_once_with(
            "SSH channel closed for command: some command. Reconnecting and retrying..."
        )

    @patch('executors.remote.RemoteExecutor._get_ssh_client_context')
    def test_execute_does_not_retry_on_other_ssh_exception(self, mock_get_ssh_client_context):
        """
        Test that RemoteExecutor does not retry on other SSH exceptions.
        """
        # 1. Arrange
        mock_ssh_client = MagicMock()
        mock_get_ssh_client_context.return_value.__enter__.return_value = mock_ssh_client

        # Configure exec_command to raise a different SSHException
        mock_ssh_client.exec_command.side_effect = paramiko.SSHException("Some other error")

        # 2. Act
        result = self.executor.execute("some command")

        # 3. Assert
        self.assertEqual(result.exit_code, -1)
        self.assertIn("Connection/execution error: Some other error", result.stderr)

        # Verify that reconnect was NOT called
        self.mock_connection_manager.reconnect.assert_not_called()

        # Verify that an error was logged
        self.mock_logger.error.assert_called_once_with(
            "Remote command execution error: some command, error: Some other error"
        )

if __name__ == '__main__':
    unittest.main()
