from types import SimpleNamespace
from unittest.mock import MagicMock

from src.domain.enums import GpuStatus
from src.domain.models import GpuResource
from src.ui.provider import DashboardDataProvider


def test_provider_marks_gpu_assigned_when_live_compute_exists_without_db_reservation():
    case_repo = MagicMock()
    gpu_repo = MagicMock()
    logger = MagicMock()
    execution_handler = MagicMock()
    execution_handler.execute_command.return_value = SimpleNamespace(
        success=True,
        output="GPU-live, 1234, tps_env, 1500\n",
        error="",
        return_code=0,
    )

    gpu_repo.get_all_gpu_resources.return_value = [
        GpuResource(
            uuid="GPU-live",
            gpu_index=5,
            name="RTX A5000",
            memory_total=24564,
            memory_used=1523,
            memory_free=23041,
            temperature=45,
            utilization=0,
            core_clock=0,
            status=GpuStatus.IDLE,
            assigned_case=None,
            last_updated=None,
        ),
    ]
    case_repo.get_all_active_cases_with_beams.return_value = []

    provider = DashboardDataProvider(
        case_repo,
        gpu_repo,
        logger,
        execution_handler=execution_handler,
    )

    provider.refresh_all_data()

    gpu = provider.get_gpu_data()[0]
    if gpu["status"] != GpuStatus.ASSIGNED:
        raise AssertionError(f"Expected effective assigned status, got {gpu!r}")
    if gpu["has_live_compute"] is not True:
        raise AssertionError(f"Expected live compute flag, got {gpu!r}")
    if gpu["status_detail"] != "live compute":
        raise AssertionError(f"Expected live compute status detail, got {gpu!r}")
