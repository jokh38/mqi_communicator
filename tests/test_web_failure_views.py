from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from src.domain.enums import BeamStatus, CaseStatus
from src.web.app import create_app


def _failed_case_view(tmp_path: Path):
    now = datetime.now(timezone.utc)
    return {
        "case_data": SimpleNamespace(
            case_id="case-failed",
            case_path=tmp_path / "case-failed",
            status=CaseStatus.FAILED,
            progress=42.0,
            created_at=now,
            updated_at=now,
            error_message="TPS generation failed",
            failure_category="retryable",
            failure_phase="tps_generation",
            failure_details={
                "summary": "Beam mapping failed",
                "beam_errors": [{"beam_id": "beam-02", "message": "Beam 02 log missing"}],
            },
            assigned_gpu=None,
            interpreter_completed=True,
            retry_count=1,
        ),
        "case_display": {
            "case_id": "case-failed",
            "status": CaseStatus.FAILED,
            "status_label": "Failed",
            "progress": 42.0,
            "assigned_gpu": None,
            "elapsed_time": 12.5,
            "beam_count": 1,
            "interpreter_done": True,
            "error_message": "TPS generation failed",
            "failure_category": "retryable",
            "failure_phase": "tps_generation",
            "failure_details": {
                "summary": "Beam mapping failed",
                "beam_errors": [{"beam_id": "beam-02", "message": "Beam 02 log missing"}],
            },
            "retry_count": 1,
            "result_summary": {
                "terminal_status": "Failed",
                "has_saved_output": False,
                "output_locations": [],
            },
        },
        "beams": [
            {
                "beam_id": "beam-02",
                "status": BeamStatus.FAILED,
                "progress": 42.0,
                "elapsed_time": 12.5,
                "hpc_job_id": None,
                "error_message": "Beam 02 log missing",
            }
        ],
    }


def test_cases_list_renders_case_level_error_column(tmp_path: Path):
    provider = MagicMock()
    provider.refresh_all_data.return_value = None
    provider.case_repo.get_all_cases_with_beams.return_value = []
    provider._process_cases_with_beams_data.return_value = [_failed_case_view(tmp_path)]

    app = create_app()
    app.state.provider = provider
    client = TestClient(app)

    response = client.get("/ui/cases")

    assert response.status_code == 200
    assert "Error" in response.text
    assert "TPS generation failed" in response.text


def test_case_detail_renders_case_failure_summary_and_beam_level_errors(tmp_path: Path):
    provider = MagicMock()
    case_view = _failed_case_view(tmp_path)
    provider.case_repo.get_case.return_value = case_view["case_data"]
    provider.case_repo.get_beams_for_case.return_value = [
        SimpleNamespace(
            beam_id="beam-02",
            parent_case_id="case-failed",
            beam_path=tmp_path / "case-failed" / "beam-02",
            beam_number=2,
            status=BeamStatus.FAILED,
            progress=42.0,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            hpc_job_id=None,
            error_message="Beam 02 log missing",
        )
    ]
    provider.case_repo.get_deliveries_for_case.return_value = []
    provider.case_repo.get_workflow_steps.return_value = []
    provider._process_cases_with_beams_data.return_value = [case_view]

    app = create_app()
    app.state.provider = provider
    client = TestClient(app)

    response = client.get("/ui/cases/case-failed/details")

    assert response.status_code == 200
    assert "TPS generation failed" in response.text
    assert "retryable" in response.text
    assert "tps_generation" in response.text
    assert "Beam 02 log missing" in response.text


def test_workflow_view_renders_retryable_permanent_counts_and_phase_summary(tmp_path: Path):
    provider = MagicMock()
    provider.refresh_all_data.return_value = None
    provider.get_gpu_data.return_value = []
    provider.get_cases_with_beams_data.return_value = [_failed_case_view(tmp_path)]
    provider.get_system_stats.return_value = {
        "pending": 0,
        "total_gpus": 0,
        "last_update": datetime.now(timezone.utc),
        "retryable_failed": 1,
        "permanent_failed": 2,
        "failure_phases": {"tps_generation": 1, "simulation": 2},
    }

    app = create_app()
    app.state.provider = provider
    client = TestClient(app)

    response = client.get("/ui/workflow")

    assert response.status_code == 200
    assert "Retryable Failures" in response.text
    assert ">1<" in response.text
    assert "Permanent Failures" in response.text
    assert ">2<" in response.text
    assert "tps_generation" in response.text
    assert "simulation" in response.text
