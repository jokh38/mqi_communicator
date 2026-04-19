"""FastAPI application for the web dashboard."""

import asyncio
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask

from src.config.settings import Settings
from src.core.retry_policy import is_retryable_failed_case
from src.database.connection import DatabaseConnection
from src.handlers.execution_handler import ExecutionHandler
from src.infrastructure.logging_handler import StructuredLogger
from src.repositories.case_repo import CaseRepository
from src.repositories.gpu_repo import GpuRepository
from src.ui.provider import DashboardDataProvider


BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def _load_settings() -> Settings:
    """Load dashboard settings from env or default config path."""
    config_path = os.environ.get("MQI_CONFIG_PATH")
    return Settings(Path(config_path)) if config_path else Settings()


def _build_provider() -> DashboardDataProvider:
    """Create a dashboard provider bound to the configured database."""
    settings = _load_settings()
    logger = StructuredLogger("web_dashboard", settings.get_logging_config())

    db_path = Path(os.environ.get("DB_PATH", settings.get_database_path()))
    db_connection = DatabaseConnection(db_path, settings, logger)
    case_repo = CaseRepository(db_connection, logger)
    gpu_repo = GpuRepository(db_connection, logger, settings)
    execution_handler = ExecutionHandler(settings=settings)
    return DashboardDataProvider(
        case_repo,
        gpu_repo,
        logger,
        execution_handler=execution_handler,
        settings=settings,
    )


def _get_provider(request: Request) -> DashboardDataProvider:
    provider = getattr(request.app.state, "provider", None)
    if provider is None:
        provider = _build_provider()
        request.app.state.provider = provider
    return provider


def _get_case_queue(request: Request):
    return getattr(request.app.state, "case_queue", None)


def _find_delivery(provider: DashboardDataProvider, case_id: str, delivery_id: str) -> Optional[Dict[str, Any]]:
    """Resolve a single delivery view row for a case."""
    deliveries = provider.case_repo.get_deliveries_for_case(case_id)
    for delivery in deliveries:
        if delivery.delivery_id == delivery_id:
            return {
                "delivery_id": delivery.delivery_id,
                "delivery_path": delivery.delivery_path,
                "report_path": delivery.report_path,
            }
    return None


def _resolve_case_dicom_output_dir(provider: DashboardDataProvider, case_id: str) -> Optional[Path]:
    """Locate the final DICOM output directory for a case, if one exists."""
    raw_case = provider.case_repo.get_case(case_id)
    if raw_case is None:
        return None

    for label, path in provider._resolve_output_candidates(case_id, raw_case.case_path):
        if "dicom" not in label.lower():
            continue
        files = provider._collect_files(path, ["*.dcm"])
        if files:
            return path
    return None


def _filter_cases(
    cases: List[Dict[str, Any]],
    search: str,
    status: str,
    date_start: str,
    date_end: str,
) -> List[Dict[str, Any]]:
    """Apply lightweight case list filtering."""
    normalized_search = search.strip().lower()
    normalized_status = status.strip().lower()

    filtered_cases = []
    for case_data in cases:
        case = case_data["case_data"]
        display = case_data["case_display"]
        case_status = display["status"].value
        case_id = display["case_id"]

        if normalized_status and normalized_status != "all" and case_status != normalized_status:
            continue
        if normalized_search and normalized_search not in case_id.lower():
            continue
        if date_start and case.created_at.date().isoformat() < date_start:
            continue
        if date_end and case.created_at.date().isoformat() > date_end:
            continue

        filtered_cases.append(case_data)

    return filtered_cases


def create_app() -> FastAPI:
    """Create the FastAPI app."""
    app = FastAPI(title="MQI Communicator Dashboard")

    @app.get("/", response_class=HTMLResponse)
    async def root(request: Request) -> HTMLResponse:
        return templates.TemplateResponse("base.html", {"request": request})

    @app.get("/ui/workflow", response_class=HTMLResponse)
    async def workflow(request: Request) -> HTMLResponse:
        provider = _get_provider(request)
        await asyncio.to_thread(provider.refresh_all_data)
        return templates.TemplateResponse(
            "workflow.html",
            {
                "request": request,
                "stats": provider.get_system_stats(),
                "cases": provider.get_cases_with_beams_data(),
                "gpus": provider.get_gpu_data(),
            },
        )

    @app.get("/ui/cases", response_class=HTMLResponse)
    async def cases_list(
        request: Request,
        search: str = "",
        status: str = "all",
        date_start: str = "",
        date_end: str = "",
        selected_case_id: str = "",
    ) -> HTMLResponse:
        provider = _get_provider(request)
        await asyncio.to_thread(provider.refresh_all_data)

        raw_cases = await asyncio.to_thread(provider.case_repo.get_all_cases_with_beams)
        processed_cases = provider._process_cases_with_beams_data(raw_cases)
        filtered_cases = _filter_cases(
            processed_cases,
            search=search,
            status=status,
            date_start=date_start,
            date_end=date_end,
        )

        return templates.TemplateResponse(
            "cases.html",
            {
                "request": request,
                "cases": filtered_cases,
                "search": search,
                "status": status,
                "date_start": date_start,
                "date_end": date_end,
                "selected_case_id": selected_case_id,
            },
        )

    @app.get("/ui/logs/{case_id}", response_class=HTMLResponse)
    async def log_modal(request: Request, case_id: str) -> HTMLResponse:
        provider = _get_provider(request)
        raw_case = provider.case_repo.get_case(case_id)
        workflow_steps = provider.case_repo.get_workflow_steps(case_id)

        case_display = None
        beam_display = []
        if raw_case is not None:
            processed = provider._process_cases_with_beams_data(
                [{"case_data": raw_case, "beams": [beam.__dict__ for beam in provider.case_repo.get_beams_for_case(case_id)]}]
            )[0]
            case_display = processed["case_display"]
            beam_display = processed["beams"]

        return templates.TemplateResponse(
            "modal_logs.html",
            {
                "request": request,
                "case_display": case_display,
                "beams": beam_display,
                "workflow_steps": workflow_steps,
            },
        )

    @app.get("/ui/cases/{case_id}/details", response_class=HTMLResponse)
    async def case_details(request: Request, case_id: str) -> HTMLResponse:
        provider = _get_provider(request)
        raw_case = provider.case_repo.get_case(case_id)
        beams = provider.case_repo.get_beams_for_case(case_id)
        deliveries = provider.case_repo.get_deliveries_for_case(case_id)
        workflow_steps = provider.case_repo.get_workflow_steps(case_id)

        case_view = None
        if raw_case is not None:
            case_view = provider._process_cases_with_beams_data(
                [{"case_data": raw_case, "beams": [beam.__dict__ for beam in beams]}]
            )[0]
            case_view["case_display"]["retry_eligible"] = is_retryable_failed_case(raw_case)

        delivery_view = [
            {
                "delivery_id": delivery.delivery_id,
                "beam_id": delivery.beam_id,
                "delivery_path": str(delivery.delivery_path),
                "delivery_timestamp": delivery.delivery_timestamp,
                "delivery_date": delivery.delivery_date,
                "raw_beam_number": delivery.raw_beam_number,
                "treatment_beam_index": delivery.treatment_beam_index,
                "is_reference_delivery": delivery.is_reference_delivery,
                "ptn_status": delivery.ptn_status,
                "ptn_last_run_at": delivery.ptn_last_run_at,
                "gamma_pass_rate": delivery.gamma_pass_rate,
                "gamma_mean": delivery.gamma_mean,
                "gamma_max": delivery.gamma_max,
                "evaluated_points": delivery.evaluated_points,
                "report_path": str(delivery.report_path) if delivery.report_path else None,
                "error_message": delivery.error_message,
            }
            for delivery in deliveries
        ]

        return templates.TemplateResponse(
            "case_detail.html",
            {
                "request": request,
                "case": case_view,
                "deliveries": delivery_view,
                "workflow_steps": workflow_steps,
            },
        )

    @app.post("/ui/cases/{case_id}/retry")
    async def retry_case(request: Request, case_id: str) -> JSONResponse:
        provider = _get_provider(request)
        case_queue = _get_case_queue(request)
        raw_case = provider.case_repo.get_case(case_id)
        if raw_case is None:
            raise HTTPException(status_code=404, detail="Case not found")
        if not is_retryable_failed_case(raw_case):
            raise HTTPException(status_code=409, detail="Case is not eligible for retry")
        if case_queue is None:
            raise HTTPException(status_code=503, detail="Case queue is not available")

        provider.case_repo.reset_case_and_beams_for_retry(case_id)
        provider.case_repo.increment_retry_count(case_id)
        case_queue.put(
            {
                "case_id": case_id,
                "case_path": str(raw_case.case_path),
                "timestamp": time.time(),
                "reason": "manual_retry",
            }
        )
        return JSONResponse({"status": "queued", "case_id": case_id})

    @app.get("/ui/cases/{case_id}/deliveries/{delivery_id}/report")
    async def open_delivery_report(
        request: Request,
        case_id: str,
        delivery_id: str,
        download: bool = False,
    ) -> FileResponse:
        provider = _get_provider(request)
        delivery = _find_delivery(provider, case_id, delivery_id)
        if delivery is None:
            raise HTTPException(status_code=404, detail="Delivery not found")

        report_path = delivery["report_path"]
        if report_path is None or not Path(report_path).exists():
            raise HTTPException(status_code=404, detail="Report PDF not found")

        report_file = Path(report_path)
        return FileResponse(
            path=report_file,
            media_type="application/pdf",
            filename=report_file.name,
            content_disposition_type="attachment" if download else "inline",
        )

    @app.get("/ui/cases/{case_id}/outputs/final-dicom")
    async def download_case_dicom_output(request: Request, case_id: str) -> FileResponse:
        provider = _get_provider(request)
        output_dir = _resolve_case_dicom_output_dir(provider, case_id)
        if output_dir is None or not output_dir.exists():
            raise HTTPException(status_code=404, detail="Final DICOM output not found")

        temp_dir = Path(tempfile.mkdtemp(prefix=f"{case_id}_dicom_"))
        archive_base = temp_dir / f"{case_id}_final_dicom"
        archive_path = Path(shutil.make_archive(str(archive_base), "zip", root_dir=str(output_dir)))

        return FileResponse(
            path=archive_path,
            media_type="application/zip",
            filename=archive_path.name,
            background=BackgroundTask(shutil.rmtree, temp_dir, ignore_errors=True),
        )

    return app


app = create_app()
