import unittest
from unittest.mock import MagicMock, patch
import socket

from resources.gpu import GPUResource
from executors.remote import RemoteExecutor
from executors.base import ExecutionResult
from remote.connection import ConnectionManager

class TestGPUResource(unittest.TestCase):

    def setUp(self):
        """Set up for each test."""
        self.mock_logger = MagicMock()
        # We need a real RemoteExecutor to test the exception handling in it
        # but we mock the underlying connection manager
        self.mock_connection_manager = MagicMock(spec=ConnectionManager)
        self.remote_executor = RemoteExecutor(
            connection_manager=self.mock_connection_manager,
            logger=self.mock_logger
        )

    def test_get_gpu_info_remote_failure(self):
        """
        Test that _get_gpu_info handles a remote command failure gracefully.
        """
        # 1. Arrange
        mock_remote_executor = MagicMock(spec=RemoteExecutor)
        # Configure the mock executor to return a failed execution result
        error_message = "nvidia-smi not found"
        failed_result = ExecutionResult(
            stdout="",
            stderr=error_message,
            exit_code=-1,
            execution_time=1.0,
            command="nvidia-smi -q -x",
            timeout_occurred=False
        )
        mock_remote_executor.execute.return_value = failed_result

        # Create the GPUResource with the mocked executor and logger
        gpu = GPUResource(
            gpu_id=0,
            remote_executor=mock_remote_executor,
            logger=self.mock_logger
        )

        # 2. Act
        # Call the method under test
        gpu_info = gpu._get_gpu_info()

        # 3. Assert
        # Verify that the method returned None
        self.assertIsNone(gpu_info)

        # Verify that the remote executor was called correctly
        mock_remote_executor.execute.assert_called_once_with(
            "nvidia-smi -q -x", capture_output=True, timeout=10
        )

        # Verify that an error was logged
        self.mock_logger.error.assert_called_once()
        log_message = self.mock_logger.error.call_args[0][0]
        self.assertIn("nvidia-smi command failed with exit code -1", log_message)
        self.assertIn(error_message, log_message)

    @patch('executors.remote.RemoteExecutor._get_ssh_client_context')
    def test_get_gpu_info_remote_timeout(self, mock_get_ssh_client_context):
        """
        Test that a remote timeout is handled and results in an exit_code of -1.
        """
        # 1. Arrange
        # Mock the SSH client context
        mock_ssh_client = MagicMock()
        mock_get_ssh_client_context.return_value.__enter__.return_value = mock_ssh_client

        # Configure the exec_command to raise a socket.timeout
        mock_ssh_client.exec_command.side_effect = socket.timeout("Command timed out")

        # Create GPUResource with our real RemoteExecutor (which has a mocked connection)
        gpu = GPUResource(
            gpu_id=0,
            remote_executor=self.remote_executor,
            logger=self.mock_logger
        )

        # 2. Act
        gpu_info = gpu._get_gpu_info(timeout=5)

        # 3. Assert
        # Verify that the method returned None because of the timeout
        self.assertIsNone(gpu_info)

        # Verify that the logger was called with the timeout error
        self.mock_logger.error.assert_any_call("Remote command timed out after 5 seconds: nvidia-smi -q -x")

        # Verify that the gpu.py logger also logged the failure from the result
        self.mock_logger.error.assert_any_call("nvidia-smi command failed with exit code -1: Command timed out after 5 seconds")

if __name__ == '__main__':
    unittest.main()
