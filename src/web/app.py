import os
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse

from src.config.settings import Settings
from src.infrastructure.logging_handler import StructuredLogger
from src.database.connection import DatabaseConnection
from src.repositories.case_repo import CaseRepository
from src.repositories.gpu_repo import GpuRepository
from src.ui.provider import DashboardDataProvider

app = FastAPI(title="MQI Communicator Dashboard")

# Get path to templates
BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Initialize provider
db_path = os.environ.get("DB_PATH", "mqi_communicator.db")
settings = Settings()
logger = StructuredLogger("web_dashboard", settings.get_logging_config())

db_connection = DatabaseConnection(Path(db_path), settings, logger)
case_repo = CaseRepository(db_connection, logger)
gpu_repo = GpuRepository(db_connection, logger, settings)

provider = DashboardDataProvider(case_repo, gpu_repo, logger)

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return templates.TemplateResponse("base.html", {"request": request})

@app.get("/ui/workflow", response_class=HTMLResponse)
async def workflow(request: Request):
    provider.refresh_all_data()
    stats = provider.get_system_stats()
    cases = provider.get_cases_with_beams_data()
    gpus = provider.get_gpu_data()
    return templates.TemplateResponse("workflow.html", {
        "request": request, 
        "stats": stats, 
        "cases": cases,
        "gpus": gpus
    })

@app.get("/ui/cases", response_class=HTMLResponse)
async def cases_list(request: Request, search: str = "", status: str = "All", date_start: str = "", date_end: str = ""):
    provider.refresh_all_data()
    cases = provider.get_cases_with_beams_data()
    
    # Filter cases based on search and status
    filtered_cases = []
    for case_data in cases:
        c_status = case_data['case_display']['status'].value
        c_id = case_data['case_display']['case_id']
        
        if status != "All" and c_status != status:
            continue
        if search and search.lower() not in c_id.lower():
            continue
            
        # Basic date filtering logic could be added here
        filtered_cases.append(case_data)
        
    return templates.TemplateResponse("cases.html", {
        "request": request,
        "cases": filtered_cases,
        "search": search,
        "status": status,
        "date_start": date_start,
        "date_end": date_end
    })

@app.get("/ui/cases/{case_id}/details", response_class=HTMLResponse)
async def case_details(request: Request, case_id: str):
    provider.refresh_all_data()
    cases = provider.get_cases_with_beams_data()
    case = next((c for c in cases if c['case_display']['case_id'] == case_id), None)
    
    return templates.TemplateResponse("case_detail.html", {
        "request": request,
        "case": case
    })
