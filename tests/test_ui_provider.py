from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.domain.enums import CaseStatus, GpuStatus
from src.domain.models import GpuResource
from src.ui.provider import DashboardDataProvider


def test_provider_marks_gpu_assigned_when_live_compute_exists_without_request_time_shell_call():
    case_repo = MagicMock()
    gpu_repo = MagicMock()
    logger = MagicMock()
    execution_handler = MagicMock()
    execution_handler.execute_command.side_effect = AssertionError(
        "refresh_all_data should not shell out for live compute status"
    )

    gpu_repo.get_all_gpu_resources.return_value = [
        SimpleNamespace(
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
            has_live_compute=True,
        )
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


def test_provider_surfaces_finished_result_output_locations(tmp_path: Path):
    case_repo = MagicMock()
    gpu_repo = MagicMock()
    logger = MagicMock()

    output_root = tmp_path / "Output"
    case_id = "case-123"
    csv_case_dir = output_root / case_id / "Log_csv"
    dicom_case_dir = output_root / case_id / "Dose"
    csv_case_dir.mkdir(parents=True)
    dicom_case_dir.mkdir(parents=True)
    (csv_case_dir / "moqui_tps_beam-1.in").write_text("tps", encoding="utf-8")
    (dicom_case_dir / "dose.dcm").write_text("dcm", encoding="utf-8")

    settings = MagicMock()
    settings.get_path.side_effect = lambda path_name, handler_name=None, **kwargs: {
        ("CsvInterpreter", "csv_output_dir"): str(output_root),
        ("PostProcessor", "simulation_output_dir"): str(output_root / kwargs["case_id"] / "Dose"),
        ("PostProcessor", "final_dicom_dir"): str(output_root / kwargs["case_id"] / "Dose"),
    }[(handler_name, path_name)]

    case_repo.get_all_active_cases_with_beams.return_value = [
        {
            "case_data": SimpleNamespace(
                case_id=case_id,
                case_path=tmp_path / case_id,
                status=CaseStatus.COMPLETED,
                progress=75.0,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
                error_message=None,
                assigned_gpu=None,
                interpreter_completed=True,
            ),
            "beams": [],
        }
    ]
    gpu_repo.get_all_gpu_resources.return_value = []

    provider = DashboardDataProvider(case_repo, gpu_repo, logger, settings=settings)
    processed = provider._process_cases_with_beams_data(
        case_repo.get_all_active_cases_with_beams.return_value,
        include_result_summary=True,
    )

    case_display = processed[0]["case_display"]
    result_summary = case_display["result_summary"]

    if case_display["status_label"] != "Finished":
        raise AssertionError(f"Expected finished status label, got {case_display!r}")
    if result_summary["terminal_status"] != "Finished":
        raise AssertionError(f"Expected finished terminal status, got {result_summary!r}")
    if not result_summary["has_saved_output"]:
        raise AssertionError(f"Expected saved output locations, got {result_summary!r}")

    output_paths = {item["path"] for item in result_summary["output_locations"]}
    if str(csv_case_dir) not in output_paths:
        raise AssertionError(f"Missing CSV output location, got {result_summary!r}")
    if str(dicom_case_dir) not in output_paths:
        raise AssertionError(f"Missing DICOM output location, got {result_summary!r}")


def test_provider_includes_structured_failure_fields_in_case_display(tmp_path: Path):
    case_repo = MagicMock()
    gpu_repo = MagicMock()
    logger = MagicMock()

    case_repo.get_all_active_cases_with_beams.return_value = [
        {
            "case_data": SimpleNamespace(
                case_id="case-failed",
                case_path=tmp_path / "case-failed",
                status=CaseStatus.FAILED,
                progress=30.0,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
                error_message="TPS generation failed",
                failure_category="retryable",
                failure_phase="tps_generation",
                failure_details={
                    "summary": "Beam matching failed",
                    "beam_errors": [{"beam_id": "beam-2", "message": "Beam 2 failed"}],
                },
                assigned_gpu=None,
                interpreter_completed=True,
                retry_count=2,
            ),
            "beams": [
                {
                    "beam_id": "beam-2",
                    "status": CaseStatus.FAILED,
                    "progress": 30.0,
                    "created_at": datetime.now(timezone.utc),
                    "updated_at": datetime.now(timezone.utc),
                    "hpc_job_id": None,
                    "error_message": "Beam 2 failed",
                }
            ],
        }
    ]
    gpu_repo.get_all_gpu_resources.return_value = []

    provider = DashboardDataProvider(case_repo, gpu_repo, logger)
    provider.refresh_all_data()

    case_display = provider.get_cases_with_beams_data()[0]["case_display"]

    assert case_display["error_message"] == "TPS generation failed"
    assert case_display["failure_category"] == "retryable"
    assert case_display["failure_phase"] == "tps_generation"
    assert case_display["failure_details"]["summary"] == "Beam matching failed"
    assert case_display["retry_count"] == 2


def test_provider_guesses_grouped_room_output_location(tmp_path: Path):
    provider = DashboardDataProvider(
        case_repo=MagicMock(),
        gpu_repo=MagicMock(),
        logger=MagicMock(),
        settings=None,
    )

    case_path = tmp_path / "data" / "SHI_log" / "G1" / "04198922" / "1.2.3.4"
    grouped_output = tmp_path / "data" / "Output" / "G1" / "04198922" / "Log_csv"
    grouped_output.mkdir(parents=True)
    case_path.mkdir(parents=True)

    resolved = provider._guess_case_output_subdir(case_path, "04198922", "Log_csv")

    assert resolved == grouped_output


def test_process_cases_with_beams_data_skips_result_scans_when_not_requested(tmp_path: Path):
    case_repo = MagicMock()
    gpu_repo = MagicMock()
    logger = MagicMock()

    provider = DashboardDataProvider(case_repo, gpu_repo, logger)
    provider._build_result_summary = MagicMock(
        side_effect=AssertionError("list rendering should not build result summaries")
    )

    raw_case = SimpleNamespace(
        case_id="case-123",
        case_path=tmp_path / "case-123",
        status=CaseStatus.PROCESSING,
        progress=12.0,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        error_message=None,
        assigned_gpu=None,
        interpreter_completed=False,
        failure_category=None,
        failure_phase=None,
        failure_details=None,
        retry_count=0,
    )

    processed = provider._process_cases_with_beams_data(
        [{"case_data": raw_case, "beams": []}],
        include_result_summary=False,
    )

    assert processed[0]["case_display"]["result_summary"] is None
