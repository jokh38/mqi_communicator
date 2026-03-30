# MQI Communicator

MQI Communicator watches a scan directory for new treatment cases, prepares case data for MOQUI Monte Carlo runs, dispatches per-beam workers, tracks progress in SQLite, and exposes a dashboard for live monitoring.

This README reflects the current code in this repository. The older root document, `READMD.md`, is misnamed and appears to be encoding-damaged, so this file is the accurate project entry document.

## What The Application Does

At startup the application:

1. Loads `config/config.yaml` or a config file path passed to `main.py`.
2. Initializes the SQLite database.
3. Optionally initializes SSH if any execution handler is configured as `remote`.
4. Scans the configured case directory for existing untracked cases.
5. Starts a filesystem watcher for newly created case directories.
6. Starts the dashboard process if `ui.auto_start` is enabled.
7. Starts GPU monitoring.
8. Runs a process pool for per-beam workers.

For each case, the current pipeline is:

1. Discover beam folders and validate input completeness.
2. Run `mqi_interpreter/main_cli.py` locally to generate CSV output.
3. Copy RTPLAN-side DICOM files into the generated CSV output tree.
4. Allocate available GPUs and generate one `moqui_tps_<beam_id>.in` file per allocated beam.
5. Upload generated case files if the relevant handler mode is `remote`.
6. Dispatch one worker process per allocated beam.
7. Execute the beam workflow:
   - initial validation
   - optional file upload
   - simulation execution
   - result download or local pickup
   - Raw-to-DICOM postprocessing
   - final upload to `PC_localdata`

## Entry Points

- Application entrypoint: `main.py`
- Dashboard entrypoint: `python -m src.ui.dashboard <database_path> [--config <config>]`

Run the application with:

```bash
python main.py
```

Or with an explicit config file:

```bash
python main.py config/config.yaml
```

## Configuration

The code currently reads a single YAML configuration file through [`src/config/settings.py`](/C:/MOQUI_SMC/mqi_communicator/src/config/settings.py). The active repository config is [`config/config.yaml`](/C:/MOQUI_SMC/mqi_communicator/config/config.yaml).

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
  Controls worker count and polling/timeout settings.
- `gpu`
  Controls GPU monitor command and interval.
- `ui`
  Controls dashboard auto-start and optional `ttyd` web exposure.
- `tps_generator`
  Controls validation and default path templates for generated TPS input files.
- `moqui_tps_parameters`
  Base parameters used to generate `moqui_tps_<beam_id>.in`.

Notes about the checked-in config:

- All processing handlers are currently set to `local`.
- `ui.auto_start` is enabled.
- `ui.web.enabled` is enabled, so the dashboard launcher expects `ttyd` to be installed and port `8080` to be available.
- The default `paths.base_directory` is `/home/jokh38/MOQUI_SMC`, so the sample config is not directly runnable on a fresh machine without editing paths.

## Dashboard

The dashboard is launched as a separate process by [`src/infrastructure/ui_process_manager.py`](/C:/MOQUI_SMC/mqi_communicator/src/infrastructure/ui_process_manager.py).

Current behavior:

- If `ui.web.enabled` is `false`, it starts a terminal dashboard process.
- If `ui.web.enabled` is `true`, it wraps the dashboard process with `ttyd`.
- The dashboard reads from the same SQLite database used by the main application.

## Data And Outputs

The current code uses these configured path families:

- scan directory: `paths.local.scan_directory`
- CSV output root: `paths.local.csv_output_dir`
- raw simulation output: `paths.local.simulation_output_dir`
- final DICOM output root: `paths.local.final_dicom_dir`
- database path: `paths.local.database_path`

Generated TPS files are written as:

```text
<csv_output_dir>/<case_id>/moqui_tps_<beam_id>.in
```

In local `ResultUploader` mode, final upload is simulated by copying into:

```text
./localdata_uploads/<case_id>/
```

This is the current implementation in [`src/handlers/execution_handler.py`](/C:/MOQUI_SMC/mqi_communicator/src/handlers/execution_handler.py); it does not perform a real network upload when that handler is `local`.

## Dependencies

Python packages listed in [`requirements.txt`](/C:/MOQUI_SMC/mqi_communicator/requirements.txt):

- `watchdog`
- `rich`
- `paramiko`
- `PyYAML`
- `pydicom`

Operational dependencies used by the code:

- `nvidia-smi` for GPU monitoring
- `ttyd` if `ui.web.enabled: true`
- Access to the external `mqi_interpreter` and `RawToDCM` scripts referenced in config

Install dependencies with:

```bash
pip install -r requirements.txt
```

## Repository Layout

- [`main.py`](/C:/MOQUI_SMC/mqi_communicator/main.py): application bootstrap and service lifecycle
- [`src/core`](/C:/MOQUI_SMC/mqi_communicator/src/core): dispatching, TPS generation, worker logic, workflow orchestration
- [`src/domain`](/C:/MOQUI_SMC/mqi_communicator/src/domain): enums, DTOs, workflow states, errors
- [`src/handlers`](/C:/MOQUI_SMC/mqi_communicator/src/handlers): local/remote execution and file transfer abstraction
- [`src/repositories`](/C:/MOQUI_SMC/mqi_communicator/src/repositories): SQLite-backed case and GPU repositories
- [`src/infrastructure`](/C:/MOQUI_SMC/mqi_communicator/src/infrastructure): logging, GPU monitor, UI process manager
- [`src/ui`](/C:/MOQUI_SMC/mqi_communicator/src/ui): dashboard process and rendering
- [`config/config.yaml`](/C:/MOQUI_SMC/mqi_communicator/config/config.yaml): runtime configuration
- [`tests`](/C:/MOQUI_SMC/mqi_communicator/tests): test suite

## Current Limits And Implementation Notes

This section documents behavior that is visible in the code today and is easy to misstate:

- The root project document in the repository was `READMD.md`, not `README.md`.
- The current code does not read environment variables for config overrides.
- The current code uses the `ui` config section, not `dashboard`.
- HPC connection settings are read from `connections.hpc`, not `hpc_connection`.
- Result upload behavior depends on the `ResultUploader` handler mode.
- Remote simulation submission exists, but remote job completion monitoring is currently much lighter than the local log-based progress tracking path.

## Quick Start Checklist

1. Edit [`config/config.yaml`](/C:/MOQUI_SMC/mqi_communicator/config/config.yaml) for your environment.
2. Ensure the configured directories and external scripts actually exist.
3. Install Python dependencies with `pip install -r requirements.txt`.
4. Install `ttyd` or disable `ui.web.enabled`.
5. Start the app with `python main.py`.
