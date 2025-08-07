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
        self.mock_remote_executor = MagicMock(spec=RemoteExecutor)
        self.gpu = GPUResource(
            gpu_id=0,
            remote_executor=self.mock_remote_executor,
            logger=self.mock_logger
        )

    def test_get_gpu_info_remote_failure(self):
        """
        Test that _get_gpu_info handles a remote command failure gracefully.
        """
        # 1. Arrange
        error_message = "nvidia-smi not found"
        failed_result = ExecutionResult(
            stdout="",
            stderr=error_message,
            exit_code=-1,
            execution_time=1.0,
            command="nvidia-smi -q -x",
            timeout_occurred=False
        )
        self.mock_remote_executor.execute.return_value = failed_result

        # 2. Act
        gpu_info = self.gpu._get_gpu_info()

        # 3. Assert
        self.assertIsNone(gpu_info)
        self.mock_remote_executor.execute.assert_called_once_with(
            "nvidia-smi -q -x", capture_output=True, timeout=10
        )
        self.mock_logger.error.assert_called_once()
        log_message = self.mock_logger.error.call_args[0][0]
        self.assertIn("nvidia-smi command failed with exit code -1", log_message)
        self.assertIn(error_message, log_message)

    def test_get_gpu_info_remote_timeout(self):
        """
        Test that a remote timeout is handled gracefully.
        """
        # 1. Arrange
        # Configure the executor to raise a timeout exception
        self.mock_remote_executor.execute.side_effect = socket.timeout("Command timed out")

        # 2. Act
        gpu_info = self.gpu._get_gpu_info(timeout=5)

        # 3. Assert
        self.assertIsNone(gpu_info)
        self.mock_logger.error.assert_called_once_with("Error getting GPU 0 info: Command timed out")

if __name__ == '__main__':
    unittest.main()
