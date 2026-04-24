import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx

from src.domain.enums import CaseStatus
from src.web.app import create_app


class ASGITestClient:
    def __init__(self, app):
        self.app = app
        self.app.state.run_sync_inline = True

    def get(self, path: str):
        return asyncio.run(self._request("GET", path))

    def post(self, path: str):
        return asyncio.run(self._request("POST", path))

    async def _request(self, method: str, path: str):
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.request(method, path)


def _case(
    tmp_path: Path,
    *,
    case_id: str = "case-failed",
    error_message: str = "TPS generation failed",
    failure_category: str | None = "retryable",
    retry_count: int = 1,
):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        case_id=case_id,
        case_path=tmp_path / case_id,
        status=CaseStatus.FAILED,
        progress=42.0,
        created_at=now,
        updated_at=now,
        error_message=error_message,
        failure_category=failure_category,
        failure_phase="tps_generation",
        failure_details={"summary": error_message},
        assigned_gpu=None,
        interpreter_completed=True,
        retry_count=retry_count,
    )


def test_case_detail_context_exposes_retry_eligibility(tmp_path: Path):
    provider = MagicMock()
    raw_case = _case(tmp_path, failure_category="retryable")
    provider.case_repo.get_case.return_value = raw_case
    provider.case_repo.get_beams_for_case.return_value = []
    provider.case_repo.get_deliveries_for_case.return_value = []
    provider.case_repo.get_workflow_steps.return_value = []
    provider._process_cases_with_beams_data.return_value = [
        {
            "case_data": raw_case,
            "case_display": {
                "case_id": raw_case.case_id,
                "status": raw_case.status,
                "status_label": "Failed",
                "progress": raw_case.progress,
                "assigned_gpu": None,
                "elapsed_time": 10.0,
                "beam_count": 0,
                "interpreter_done": True,
                "error_message": raw_case.error_message,
                "failure_category": raw_case.failure_category,
                "failure_phase": raw_case.failure_phase,
                "failure_details": raw_case.failure_details,
                "retry_count": raw_case.retry_count,
                "retry_eligible": True,
                "result_summary": {
                    "terminal_status": "Failed",
                    "has_saved_output": False,
                    "output_locations": [],
                },
            },
            "beams": [],
        }
    ]

    app = create_app()
    app.state.provider = provider
    client = ASGITestClient(app)

    response = client.get(f"/ui/cases/{raw_case.case_id}/details")

    assert response.status_code == 200
    assert "Retry Case" in response.text


def test_retry_post_endpoint_rejects_ineligible_case(tmp_path: Path):
    provider = MagicMock()
    raw_case = _case(tmp_path, failure_category="permanent")
    provider.case_repo.get_case.return_value = raw_case

    app = create_app()
    app.state.provider = provider
    app.state.case_queue = MagicMock()
    client = ASGITestClient(app)

    response = client.post(f"/ui/cases/{raw_case.case_id}/retry")

    assert response.status_code == 409
    provider.case_repo.reset_case_and_beams_for_retry.assert_not_called()
    provider.case_repo.increment_retry_count.assert_not_called()
    app.state.case_queue.put.assert_not_called()


def test_retry_post_endpoint_resets_increments_and_requeues_eligible_case(tmp_path: Path):
    provider = MagicMock()
    raw_case = _case(tmp_path, failure_category="retryable", retry_count=2)
    provider.case_repo.get_case.return_value = raw_case

    app = create_app()
    app.state.provider = provider
    app.state.case_queue = MagicMock()
    client = ASGITestClient(app)

    response = client.post(f"/ui/cases/{raw_case.case_id}/retry")

    assert response.status_code == 200
    provider.case_repo.reset_case_and_beams_for_retry.assert_called_once_with(raw_case.case_id)
    provider.case_repo.increment_retry_count.assert_called_once_with(raw_case.case_id)
    app.state.case_queue.put.assert_called_once()
    queued_payload = app.state.case_queue.put.call_args.args[0]
    assert queued_payload["case_id"] == raw_case.case_id
    assert queued_payload["case_path"] == str(raw_case.case_path)
    assert queued_payload["reason"] == "manual_retry"
