import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta

from src.domain.enums import GpuStatus
from src.domain.models import GpuResource
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
    if monitor.execution_handler != mock_execution_handler:
        raise AssertionError("GpuMonitor should keep the provided execution handler")

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

    assert mock_execution_handler.execute_command.call_args_list[0].kwargs == {
        "command": "nvidia-smi-test"
    }

    if mock_gpu_repo.update_resources.call_count != 1:
        raise AssertionError("GPU resources should be updated exactly once")


def test_fetch_and_update_gpus_persists_live_compute_state(
    mock_logger,
    mock_gpu_repo,
    mock_execution_handler,
):
    mock_execution_handler.execute_command.side_effect = [
        MagicMock(
            success=True,
            output=(
                "0, GPU-ae1a4e4a, NVIDIA GeForce RTX 3090, 24576, 1024, 23552, 30, 5\n"
                "1, GPU-ae1a4e4b, NVIDIA GeForce RTX 3090, 24576, 2048, 22528, 35, 10\n"
            ),
            error="",
            return_code=0,
        ),
        MagicMock(
            success=True,
            output="4321, GPU-ae1a4e4b, tps_env, 1500\n",
            error="",
            return_code=0,
        ),
    ]

    monitor = GpuMonitor(
        logger=mock_logger,
        execution_handler=mock_execution_handler,
        gpu_repository=mock_gpu_repo,
        command="nvidia-smi-test",
        update_interval=10,
    )

    monitor._fetch_and_update_gpus()

    persisted_rows = mock_gpu_repo.update_resources.call_args.args[0]
    assert persisted_rows[0]["uuid"] == "GPU-ae1a4e4a"
    assert persisted_rows[0]["has_live_compute"] is False
    assert persisted_rows[1]["uuid"] == "GPU-ae1a4e4b"
    assert persisted_rows[1]["has_live_compute"] is True


@pytest.mark.parametrize(
    ("expected_phrase",),
    [
        ("local GPU data",),
    ],
)
def test_fetch_and_update_gpus_logs_execution_mode(
    expected_phrase,
    mock_logger,
    mock_gpu_repo,
    mock_execution_handler,
):
    monitor = GpuMonitor(
        logger=mock_logger,
        execution_handler=mock_execution_handler,
        gpu_repository=mock_gpu_repo,
        command="nvidia-smi-test",
        update_interval=10,
    )

    monitor._fetch_and_update_gpus()

    if not any(
        call.args and expected_phrase in call.args[0]
        for call in mock_logger.info.call_args_list
    ):
        raise AssertionError(f"Expected log phrase not found: {expected_phrase}")


def test_reconcile_stale_assignments_releases_only_unbacked_assigned_gpus(
    mock_logger,
    mock_gpu_repo,
    mock_execution_handler,
):
    mock_execution_handler.execute_command.side_effect = [
        MagicMock(
            success=True,
            output="0, GPU-ae1a4e4a, /usr/bin/python, 512\n",
            error="",
            return_code=0,
        )
    ]
    mock_gpu_repo.get_all_gpu_resources.return_value = [
        GpuResource(
            uuid="GPU-ae1a4e4a",
            gpu_index=0,
            name="RTX 3090",
            memory_total=24576,
            memory_used=1024,
            memory_free=23552,
            temperature=30,
            utilization=5,
            core_clock=0,
            status=GpuStatus.ASSIGNED,            assigned_case="beam-1",
            last_updated=None,
        ),
        GpuResource(
            uuid="GPU-stale",
            gpu_index=1,
            name="RTX 3090",
            memory_total=24576,
            memory_used=14,
            memory_free=24562,
            temperature=31,
            utilization=0,
            core_clock=0,            status=GpuStatus.ASSIGNED,
            assigned_case="beam-2",
            last_updated=None,
        ),
        GpuResource(
            uuid="GPU-idle",
            gpu_index=2,
            name="RTX 3090",
            memory_total=24576,
            memory_used=14,
            memory_free=24562,
            temperature=31,
            utilization=0,
            core_clock=0,            status=GpuStatus.IDLE,
            assigned_case=None,
            last_updated=None,
        ),
    ]
    case_repo = MagicMock()

    monitor = GpuMonitor(
        logger=mock_logger,
        execution_handler=mock_execution_handler,
        gpu_repository=mock_gpu_repo,
        command="nvidia-smi-test",
        update_interval=10,
    )

    reclaimed = monitor.reconcile_stale_assignments(case_repo)

    mock_gpu_repo.release_gpu.assert_called_once_with("GPU-stale")
    case_repo.clear_assigned_gpu_by_uuid.assert_called_once_with("GPU-stale")
    if reclaimed != ["GPU-stale"]:
        raise AssertionError(f"Unexpected reclaimed GPUs: {reclaimed!r}")


def test_reconcile_stale_assignments_preserves_recent_assignments_during_grace_period(
    mock_logger,
    mock_gpu_repo,
    mock_execution_handler,
):
    mock_execution_handler.execute_command.return_value = MagicMock(
        success=True,
        output="",
        error="",
        return_code=0,
    )
    now = datetime.now()
    mock_gpu_repo.get_all_gpu_resources.return_value = [
        GpuResource(
            uuid="GPU-recent",
            gpu_index=0,
            name="RTX 3090",
            memory_total=24576,
            memory_used=1024,
            memory_free=23552,
            temperature=30,
            utilization=5,
            core_clock=0,
            status=GpuStatus.ASSIGNED,            assigned_case=None,
            last_updated=now - timedelta(seconds=15),
        ),
        GpuResource(
            uuid="GPU-stale",
            gpu_index=1,
            name="RTX 3090",
            memory_total=24576,
            memory_used=14,
            memory_free=24562,
            temperature=31,
            utilization=0,
            core_clock=0,            status=GpuStatus.ASSIGNED,
            assigned_case=None,
            last_updated=now - timedelta(seconds=120),
        ),
    ]
    case_repo = MagicMock()

    monitor = GpuMonitor(
        logger=mock_logger,
        execution_handler=mock_execution_handler,
        gpu_repository=mock_gpu_repo,
        command="nvidia-smi-test",
        update_interval=10,
        assignment_grace_period_seconds=60,
    )

    reclaimed = monitor.reconcile_stale_assignments(case_repo)

    mock_gpu_repo.release_gpu.assert_called_once_with("GPU-stale")
    case_repo.clear_assigned_gpu_by_uuid.assert_called_once_with("GPU-stale")
    if reclaimed != ["GPU-stale"]:
        raise AssertionError(f"Unexpected reclaimed GPUs: {reclaimed!r}")
