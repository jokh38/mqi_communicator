import pytest
from unittest.mock import MagicMock, patch

from src.infrastructure.gpu_monitor import GpuMonitor
from src.handlers.execution_handler import ExecutionHandler

@pytest.fixture
def mock_logger():
    return MagicMock()

@pytest.fixture
def mock_gpu_repo():
    return MagicMock()

@pytest.fixture
def mock_execution_handler():
    handler = MagicMock(spec=ExecutionHandler)
    handler.execute_command.return_value = MagicMock(success=True, output="""
0, GPU-ae1a4e4a, NVIDIA GeForce RTX 3090, 24576, 1024, 23552, 30, 5
1, GPU-ae1a4e4b, NVIDIA GeForce RTX 3090, 24576, 2048, 22528, 35, 10
""")
    return handler

def test_gpu_monitor_initialization(mock_logger, mock_gpu_repo, mock_execution_handler):
    """
    Tests that GpuMonitor can be initialized with an ExecutionHandler.
    """
    monitor = GpuMonitor(
        logger=mock_logger,
        execution_handler=mock_execution_handler,
        gpu_repository=mock_gpu_repo,
        command="nvidia-smi-test",
        update_interval=10
    )
    assert monitor.execution_handler == mock_execution_handler

@patch("threading.Thread")
def test_fetch_and_update_gpus_uses_execution_handler(mock_thread, mock_logger, mock_gpu_repo, mock_execution_handler):
    """
    Tests that the monitor uses the execution handler to fetch data.
    """
    monitor = GpuMonitor(
        logger=mock_logger,
        execution_handler=mock_execution_handler,
        gpu_repository=mock_gpu_repo,
        command="nvidia-smi-test",
        update_interval=10
    )

    monitor._fetch_and_update_gpus()

    mock_execution_handler.execute_command.assert_called_once_with(
        command="nvidia-smi-test"
    )

    assert mock_gpu_repo.update_resources.call_count == 1