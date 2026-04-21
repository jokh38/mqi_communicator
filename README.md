# MQI Communicator

MQI Communicator watches a scan directory for new treatment cases, prepares case data for MOQUI Monte Carlo runs, dispatches per-beam workers, tracks progress in SQLite, and exposes a dashboard for live monitoring.

## What The Application Does

At startup the application:

1. Loads `config/config.yaml` or a config file path passed to `main.py`.
2. Initializes the SQLite database.
3. Reclaims any stale previous instance via the process registry.
4. Scans the configured case directory for existing untracked cases.
5. Starts a filesystem watcher for newly created case directories.
6. Starts the dashboard process if `ui.auto_start` is enabled.
7. Starts GPU monitoring.
8. Starts a background service monitor thread for dashboard and GPU health.
9. Runs a process pool for per-beam workers.

For each case, the current pipeline is:

1. Discover beam folders and validate input completeness against DICOM RT Plan.
2. Run `mqi_interpreter/main_cli.py` locally to generate CSV output.
3. Copy RTPLAN-side DICOM files into the generated CSV output tree.
4. Allocate available GPUs (partial allocation supported) and generate one `moqui_tps_<beam_id>.in` file per allocated beam.
5. Dispatch one worker process per allocated beam; remaining beams are queued and dispatched as GPUs become available.
6. Execute the beam workflow via a state machine:
   - **Initial Validation**: verify beam directory and TPS file existence
   - **Simulation Execution**: launch MOQUI simulation locally, monitor log file for batch progress
   - **Result Validation**: verify native DICOM result directory exists
   - **Completed** or **Failed**: update case status aggregated from all beams

## Entry Points

- Application entrypoint: `main.py`
- Dashboard entrypoint: `python -m src.ui.dashboard <database_path> [--config <config>]`
- Web dashboard: served by FastAPI/uvicorn, see [Web Dashboard](#web-dashboard) below

Run the application with:

```bash
python main.py
```

Or with an explicit config file:

```bash
python main.py config/config.yaml
```

## Architecture Overview

The codebase follows a layered architecture:

```
domain (enums, models, errors, state machine)
  <- core (business logic: dispatching, TPS generation, workflow, workers)
    <- handlers (execution abstraction: local command execution, file transfer)
      <- repositories (data access: SQLite via CaseRepository, GpuRepository)
        <- infrastructure (cross-cutting: logging, GPU monitor, process registry, UI)
```

Key patterns in use:

- **State Pattern**: Beam workflows use `WorkflowState` ABC with concrete states: `InitialState`, `SimulationState`, `ResultValidationState`, `CompletedState`, `FailedState`. Each state executes and returns the next state (or `None` for terminal states).
- **Repository Pattern**: Database access through `CaseRepository` and `GpuRepository`, both extending `BaseRepository`.
- **Process Pool**: Main process uses `ProcessPoolExecutor` for parallel beam workers. Each worker creates its own `WorkflowManager` with injected dependencies.
- **Service Monitor**: A background daemon thread periodically monitors dashboard process health (auto-restart on failure) and reconciles stale GPU assignments.
- **Process Registry**: Prevents duplicate instances by tracking the active main process in `.runtime/main_process.json`. On startup, stale prior instances are terminated.

## Configuration

The code reads a single YAML configuration file through [`src/config/settings.py`](src/config/settings.py), with Pydantic validation models defined in [`src/config/pydantic_models.py`](src/config/pydantic_models.py). The active repository config is [`config/config.yaml`](config/config.yaml).

Important sections used by the code:

- `ExecutionHandler`
  Selects `local` or `remote` mode per subsystem.
- `paths`
  Defines scan, output, database, interpreter, and postprocessing paths.
- `executables`
  Defines Python and script locations for local and remote execution.
- `connections`
  Defines `pc_localdata` and HPC connection details.
- `command_templates`
  Defines shell commands used for simulation submission, cleanup, and result upload.
- `processing`
  Controls worker count, polling/timeout settings, and `max_case_retries` (default 3).
- `gpu`
  Controls GPU monitor command, interval, and `assignment_grace_period_seconds`.
- `ui`
  Controls dashboard auto-start and optional `ttyd` web exposure.
- `tps_generator`
  Controls validation rules, default path templates, and required parameters for generated TPS input files.
- `moqui_tps_parameters`
  Base parameters used to generate `moqui_tps_<beam_id>.in`.
- `progress_tracking`
  Maps workflow phases to progress percentages via `coarse_phase_progress`.
- `completion_patterns`
  Defines `success_pattern` and `failure_patterns` for simulation log monitoring.

Notes about the checked-in config:

- All processing handlers are currently set to `local`.
- `ui.auto_start` is enabled.
- `ui.web.enabled` is enabled, so the dashboard launcher expects `ttyd` to be installed and port `8080` to be available.
- The default `paths.base_directory` is `/home/SMC/MOQUI_SMC`, so the sample config is not directly runnable on a fresh machine without editing paths.

## Dashboard

The terminal dashboard is launched as a separate process by [`src/infrastructure/ui_process_manager.py`](src/infrastructure/ui_process_manager.py).

Current behavior:

- If `ui.web.enabled` is `false`, it starts a terminal dashboard process.
- If `ui.web.enabled` is `true`, it wraps the dashboard process with `ttyd`.
- The dashboard reads from the same SQLite database used by the main application.

## Web Dashboard

A FastAPI web application in [`src/web/app.py`](src/web/app.py) provides an HTTP dashboard for monitoring cases, beams, and GPU status through a browser.

Routes:

| Route | Description |
|---|---|
| `GET /` | Base HTML landing page |
| `GET /ui/workflow` | Workflow overview: system stats, all cases with beams, GPU status |
| `GET /ui/cases` | Filterable case list (query params: `search`, `status`, `date_start`, `date_end`) |
| `GET /ui/cases/{case_id}/details` | Case detail view with beam list and workflow step history |

Templates are stored in [`src/web/templates/`](src/web/templates/) using Jinja2: `base.html`, `workflow.html`, `cases.html`, `case_detail.html`.

Data access is provided by [`src/ui/provider.py`](src/ui/provider.py) (`DashboardDataProvider`), which wraps `CaseRepository` and `GpuRepository`.

The web dashboard state is read from SQLite and refreshes on each page load. There are no WebSocket or live-update mechanisms.

## Data And Outputs

The current code uses these configured path families:

- scan directory: `paths.local.scan_directory`
- CSV output root: `paths.local.csv_output_dir`
- raw simulation output: `paths.local.simulation_output_dir`
- final DICOM output root: `paths.local.final_dicom_dir`
- database path: `paths.local.database_path`

To clear the SQLite state database for a fresh start, stop the app first and run:

```bash
bash scripts/clear_db.sh
```

You can also pass an explicit config path:

```bash
bash scripts/clear_db.sh config/config.yaml
```

Generated TPS files are written as:

```text
<csv_output_dir>/<case_id>/moqui_tps_<beam_id>.in
```

In local `ResultUploader` mode, final upload is simulated by copying into:

```text
./localdata_uploads/<case_id>/
```

This is the current implementation in [`src/handlers/execution_handler.py`](src/handlers/execution_handler.py); it does not perform a real network upload when that handler is `local`.

## Dependencies

Runtime Python packages listed in [`requirements.txt`](requirements.txt):

- `watchdog` — file system monitoring for detecting new case directories
- `rich` — terminal UI for the dashboard display
- `PyYAML` — YAML configuration file parsing
- `pydicom` — DICOM file handling for RT plan reading and validation
- `fastapi` — web dashboard framework
- `uvicorn` — ASGI server for the web dashboard
- `jinja2` — HTML template rendering for the web dashboard
- `python-multipart` — form data parsing for FastAPI

Development dependencies:

- `pytest` — test suite
- `black` — code formatting
- `flake8` — linting
- `mypy` — type checking

Note: `paramiko` is listed in requirements.txt but is not used by the current local-only `ExecutionHandler`.

System prerequisites used by the code are documented in
[`docs/system-prerequisites.md`](docs/system-prerequisites.md).

Operational dependencies used by the code include:

- `nvidia-smi` for GPU monitoring
- `ttyd` if `ui.web.enabled: true`
- Access to the external `mqi_interpreter` and `RawToDCM` scripts referenced in config

Install dependencies with:

```bash
pip install -r requirements.txt
```

## Repository Layout

- [`main.py`](main.py): application bootstrap and service lifecycle
- [`src/core`](src/core): dispatching, TPS generation, worker logic, workflow orchestration, data integrity validation
  - [`worker.py`](src/core/worker.py): per-beam worker process entry point, pending beam allocation
  - [`dispatcher.py`](src/core/dispatcher.py): case-level CSV interpreting and TPS generation orchestration
  - [`tps_generator.py`](src/core/tps_generator.py): dynamic `moqui_tps.in` file generation with GPU assignments
  - [`workflow_manager.py`](src/core/workflow_manager.py): state machine execution, case scanning, filesystem watcher handler
  - [`case_aggregator.py`](src/core/case_aggregator.py): beam status aggregation, beam job preparation, GPU allocation for pending beams
  - [`data_integrity_validator.py`](src/core/data_integrity_validator.py): DICOM RT Plan parsing, beam count validation, gantry number extraction
- [`src/domain`](src/domain): enums, DTOs, workflow states, custom errors
  - [`enums.py`](src/domain/enums.py): `CaseStatus`, `BeamStatus`, `WorkflowStep`, `GpuStatus`, stage mappings
  - [`models.py`](src/domain/models.py): `CaseData`, `BeamData`, `GpuResource`, `WorkflowStepRecord`, `BeamJob`, `CaseJob`
  - [`states.py`](src/domain/states.py): `WorkflowState` ABC and concrete state classes (Initial, Simulation, ResultValidation, Completed, Failed)
  - [`errors.py`](src/domain/errors.py): exception hierarchy (`MQIError`, `ProcessingError`, `WorkflowError`, etc.)
- [`src/handlers`](src/handlers): local command execution and file transfer abstraction
  - [`execution_handler.py`](src/handlers/execution_handler.py): `ExecutionHandler` with subprocess management, log-based progress tracking, file upload/download
- [`src/repositories`](src/repositories): SQLite-backed data access
  - [`case_repo.py`](src/repositories/case_repo.py): case and beam CRUD, workflow step recording
  - [`gpu_repo.py`](src/repositories/gpu_repo.py): GPU resource tracking, allocation, and deallocation
  - [`base.py`](src/repositories/base.py): abstract `BaseRepository` with shared query execution
- [`src/infrastructure`](src/infrastructure): cross-cutting services
  - [`logging_handler.py`](src/infrastructure/logging_handler.py): structured logging with `StructuredLogger` and `LoggerFactory`
  - [`gpu_monitor.py`](src/infrastructure/gpu_monitor.py): background GPU monitoring via `nvidia-smi`, stale assignment reconciliation
  - [`process_registry.py`](src/infrastructure/process_registry.py): duplicate instance prevention and stale process reclamation
  - [`ui_process_manager.py`](src/infrastructure/ui_process_manager.py): dashboard process lifecycle (start/stop/restart)
- [`src/ui`](src/ui): terminal dashboard process and rendering
- [`src/web`](src/web): FastAPI web dashboard application and Jinja2 templates
- [`src/config`](src/config): configuration loading, Pydantic validation models, constants
- [`src/database`](src/database): SQLite connection management
- [`src/utils`](src/utils): database session context manager, path utilities
- [`config/config.yaml`](config/config.yaml): runtime configuration
- [`scripts`](scripts): operational scripts (`clear_db.sh`, `initialize_test.sh`)
- [`tests`](tests): test suite

## Current Limits And Implementation Notes

This section documents behavior that is visible in the code today and is easy to misstate:

- The current code does not read environment variables for config overrides (except `MQI_CONFIG_PATH` in the web dashboard).
- The current code uses the `ui` config section, not `dashboard`.
- HPC connection settings are read from `connections.hpc`, not `hpc_connection`.
- Result upload behavior depends on the `ResultUploader` handler mode.
- Remote simulation submission exists, but remote job completion monitoring is currently much lighter than the local log-based progress tracking path.
- `paramiko` is listed in `requirements.txt` but the current `ExecutionHandler` is local-only and does not use SSH.
- The web dashboard reads state from SQLite on each page load; there are no live-update mechanisms.
- The process registry prevents duplicate instances by writing to `.runtime/main_process.json` and terminating stale processes on startup.
- Case retry: on startup, cases in `PENDING`, `CSV_INTERPRETING`, `PROCESSING`, or `POSTPROCESSING` status are re-queued up to `max_case_retries` (default 3). `COMPLETED`, `FAILED`, and `CANCELLED` cases are never automatically retried.

## Quick Start Checklist

1. Edit [`config/config.yaml`](config/config.yaml) for your environment.
2. Ensure the configured directories and external scripts actually exist.
3. Install Python dependencies with `pip install -r requirements.txt`.
4. Install the required system prerequisites in [`docs/system-prerequisites.md`](docs/system-prerequisites.md).
5. Start the app with `python main.py`.
