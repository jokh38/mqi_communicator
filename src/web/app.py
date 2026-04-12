"""FastAPI application for the web dashboard."""

import os
from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from src.config.settings import Settings
from src.database.connection import DatabaseConnection
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
    return DashboardDataProvider(case_repo, gpu_repo, logger)


def _get_provider(request: Request) -> DashboardDataProvider:
    provider = getattr(request.app.state, "provider", None)
    if provider is None:
        provider = _build_provider()
        request.app.state.provider = provider
    return provider


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
        provider.refresh_all_data()
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
    ) -> HTMLResponse:
        provider = _get_provider(request)
        provider.refresh_all_data()

        raw_cases = provider.case_repo.get_all_cases_with_beams()
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
            },
        )

    @app.get("/ui/logs/{case_id}", response_class=HTMLResponse)
    async def log_modal(request: Request, case_id: str) -> HTMLResponse:
        provider = _get_provider(request)
        raw_case = provider.case_repo.get_case(case_id)
        workflow_steps = provider.case_repo.get_workflow_steps(case_id)

        case_display = None
        if raw_case is not None:
            # Reusing the existing data processor for cases
            case_display = provider._process_cases_with_beams_data(
                [{"case_data": raw_case, "beams": []}]
            )[0]["case_display"]

        return templates.TemplateResponse(
            "modal_logs.html",
            {
                "request": request,
                "case_display": case_display,
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

    return app


app = create_app()
