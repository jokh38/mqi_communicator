import unittest
from unittest.mock import patch, MagicMock, ANY
from pathlib import Path
import tempfile
import pytest

# Import the function to be tested
from src.core.worker import worker_main
from src.config.settings import Settings

class TestWorker(unittest.TestCase):

    @patch('src.core.worker.TpsGenerator')
    @patch('src.core.worker.WorkflowManager')
    @patch('src.core.worker.paramiko')
    @patch('src.core.worker.GpuRepository')
    @patch('src.core.worker.CaseRepository')
    @patch('src.core.worker.DatabaseConnection')
    @patch('src.core.worker.LoggerFactory')
    @patch('src.core.worker.ExecutionHandler')
    @patch('src.core.worker.CommandExecutor')
    @patch('src.core.worker.RetryPolicy')
    def test_worker_main_runs_successfully(
        self,
        MockRetryPolicy,
        MockCommandExecutor,
        MockExecutionHandler,
        MockLoggerFactory,
        MockDbConnection,
        MockCaseRepo,
        MockGpuRepo,
        mock_paramiko,
        MockWorkflowManager,
        MockTpsGenerator
    ):
        """
        Test that worker_main runs without raising an exception after the fix.
        """
        # Arrange
        mock_settings = MagicMock()
        mock_settings.get_logging_config.return_value = {'log_level': 'DEBUG', 'log_dir': 'logs'}
        mock_settings.database = {}
        mock_settings.get_database_path.return_value = "dummy.db"
        mock_settings.execution_handler = {"Workflow": "local"}
        mock_settings.processing.local_execution_timeout_seconds = 60
        mock_settings.get_hpc_connection.return_value = None # No remote connection for this test

        # Create a temporary directory for the beam path
        with tempfile.TemporaryDirectory() as tmpdir:
            beam_path = Path(tmpdir)
            beam_id = "test_beam_01"

            # Act & Assert
            try:
                worker_main(beam_id=beam_id, beam_path=beam_path, settings=mock_settings)
            except Exception as e:
                self.fail(f"worker_main raised an unexpected exception: {e}")

        # Verify that ExecutionHandler was instantiated correctly
        MockExecutionHandler.assert_called_once_with(
            settings=mock_settings,
            mode="local",
            ssh_client=None
        )

if __name__ == '__main__':
    unittest.main()