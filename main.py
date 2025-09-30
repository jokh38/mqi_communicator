#!/usr/bin/env python3
# =====================================================================================
# Target File: main.py
# Source Reference: src/main.py
# =====================================================================================

"""
Main entry point for the MQI Communicator application.

This script is responsible for:
- Watching for new case directories.
- Managing a worker process pool to handle case processing.
- Coordinating the application startup and shutdown.
- Handling top-level error recovery.
"""

import sys
import signal
import time
import multiprocessing as mp
import paramiko
from pathlib import Path
from typing import Optional, NoReturn, Dict
import threading
from concurrent.futures import ProcessPoolExecutor, as_completed

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from src.config.settings import Settings
from src.infrastructure.logging_handler import StructuredLogger, LoggerFactory
from src.infrastructure.ui_process_manager import UIProcessManager
from src.database.connection import DatabaseConnection
from src.repositories.gpu_repo import GpuRepository
from src.repositories.case_repo import CaseRepository
from src.infrastructure.gpu_monitor import GpuMonitor
from src.handlers.execution_handler import ExecutionHandler
from src.core.worker import worker_main
from src.core.dispatcher import prepare_beam_jobs, run_case_level_csv_interpreting, run_case_level_upload, run_case_level_tps_generation, allocate_gpus_for_pending_beams
from src.domain.enums import CaseStatus, BeamStatus

def scan_existing_cases(case_queue: mp.Queue,
                        settings: Settings,
                        logger: StructuredLogger) -> None:
    """Scan for existing cases at startup.
    Compares file system cases with database records and queues new cases.
    Args:
        case_queue (mp.Queue): The queue to add new cases to.
        settings (Settings): The application settings.
        logger (StructuredLogger): The logger for recording events.
    """
    try:
        # Get scan directory from settings
        case_dirs = settings.get_case_directories()
        scan_directory = case_dirs.get("scan")
        if not scan_directory or not scan_directory.exists():
            logger.warning(
                f"Scan directory does not exist or is not configured: {scan_directory}"
            )
            return
        # Initialize database connection and case repository
        db_path = settings.get_database_path()
        with DatabaseConnection(db_path=db_path,
                                settings=settings,
                                logger=logger) as db_connection:
            case_repo = CaseRepository(db_connection, logger)
            # Get all case IDs from database
            existing_case_ids = set(case_repo.get_all_case_ids())
            logger.info(
                f"Found {len(existing_case_ids)} cases already in database")
            # Scan file system for case directories
            filesystem_cases = []
            for item in scan_directory.iterdir():
                if item.is_dir():
                    case_id = item.name
                    filesystem_cases.append((case_id, item))
            logger.info(
                f"Found {len(filesystem_cases)} case directories in scan directory"
            )
            # Find cases that are in file system but not in database
            new_cases = []
            for case_id, case_path in filesystem_cases:
                if case_id not in existing_case_ids:
                    new_cases.append((case_id, case_path))
            # Add new cases to processing queue
            if new_cases:
                logger.info(f"Found {len(new_cases)} new cases to process")
                for case_id, case_path in new_cases:
                    try:
                        case_queue.put({
                            'case_id': case_id,
                            'case_path': str(case_path),
                            'timestamp': time.time()
                        })
                        logger.info(
                            f"Queued existing case for processing: {case_id}")
                    except Exception as e:
                        logger.error(
                            f"Failed to queue existing case {case_id}",
                            {"error": str(e)})
            else:
                logger.info("No new cases found during startup scan")
    except Exception as e:
        logger.error("Failed to scan existing cases during startup",
                     {"error": str(e)})


class CaseDetectionHandler(FileSystemEventHandler):
    """Handles file system events to detect new case directories.
    This handler watches for directory creation events and queues new cases for processing.
    """

    def __init__(self, case_queue: mp.Queue, logger: StructuredLogger):
        """Initializes the CaseDetectionHandler.
        Args:
            case_queue (mp.Queue): The queue for new cases.
            logger (StructuredLogger): The logger for recording events.
        """
        super().__init__()
        self.case_queue = case_queue
        self.logger = logger

    def on_created(self, event) -> None:
        """Handles the 'created' event from the file system watcher.
        Checks if a directory was created and, if so, queues it as a new case.
        Args:
            event: The file system event.
        """
        if not event.is_directory:
            return
        case_path = Path(event.src_path)
        case_id = case_path.name
        self.logger.info(f"New case detected: {case_id} at {case_path}")
        try:
            # Add the case to the processing queue
            self.case_queue.put({
                'case_id': case_id,
                'case_path': str(case_path),
                'timestamp': time.time()
            })
            self.logger.info(f"Case {case_id} queued for processing")
        except Exception as e:
            self.logger.error(f"Failed to queue case {case_id}",
                              {"error": str(e)})


class MQIApplication:
    """Main application controller for the MQI Communicator.
    Manages the lifecycle of the application, including initialization,
    coordination of components, and graceful shutdown.
    """

    def __init__(self, config_path: Optional[Path] = None):
        """Initializes the MQIApplication instance.
        Args:
            config_path (Optional[Path]): The path to the configuration file.
        """
        # Resolve the config path to an absolute path to avoid issues in subprocesses.
        if config_path and config_path.exists():
            self.config_path = config_path.resolve()
        else:
            self.config_path = None
        self.settings = Settings(self.config_path)
        self.logger: Optional[StructuredLogger] = None
        self.case_queue = mp.Queue()
        self.observer: Optional[Observer] = None
        self.executor: Optional[ProcessPoolExecutor] = None
        self.ui_process_manager: Optional[UIProcessManager] = None
        self.gpu_monitor: Optional[GpuMonitor] = None
        self.monitor_db_connection: Optional[DatabaseConnection] = None
        self.ssh_client: Optional[paramiko.SSHClient] = None
        self.shutdown_event = threading.Event()
        self.service_monitor_thread: Optional[threading.Thread] = None

    def _create_db_connection(self) -> DatabaseConnection:
        """Creates a database connection with standard settings.

        Returns:
            DatabaseConnection: An initialized database connection.
        """
        db_path = self.settings.get_database_path()
        return DatabaseConnection(db_path=db_path, settings=self.settings, logger=self.logger)

    def _create_case_repository(self, db_conn: DatabaseConnection) -> CaseRepository:
        """Creates a case repository with the given database connection.

        Args:
            db_conn (DatabaseConnection): The database connection.

        Returns:
            CaseRepository: An initialized case repository.
        """
        return CaseRepository(db_conn, self.logger)

    def _validate_scan_directory(self) -> Optional[Path]:
        """Validates and returns the scan directory from settings.

        Returns:
            Optional[Path]: The scan directory path if valid, None otherwise.
        """
        case_dirs = self.settings.get_case_directories()
        scan_directory = case_dirs.get("scan")
        if not scan_directory or not scan_directory.exists():
            self.logger.error(f"Scan directory does not exist: {scan_directory}")
            return None
        return scan_directory

    def _fail_case(self, case_repo: CaseRepository, case_id: str, error_message: str) -> None:
        """Marks a case and all its beams as failed.

        Args:
            case_repo (CaseRepository): The case repository.
            case_id (str): The case ID.
            error_message (str): The error message to log.
        """
        self.logger.error(f"{error_message} for case {case_id}")
        case_repo.update_case_status(case_id, CaseStatus.FAILED, error_message=error_message)
        case_repo.update_beams_status_by_case_id(case_id, BeamStatus.FAILED.value)

    def _update_case_and_beams_status(self, case_repo: CaseRepository, case_id: str,
                                      case_status: CaseStatus, beam_status: BeamStatus,
                                      progress: float = None) -> None:
        """Updates both case and all beam statuses atomically.

        Args:
            case_repo (CaseRepository): The case repository.
            case_id (str): The case ID.
            case_status (CaseStatus): The new case status.
            beam_status (BeamStatus): The new beam status.
            progress (float, optional): The progress percentage.
        """
        case_repo.update_case_status(case_id, case_status, progress=progress)
        case_repo.update_beams_status_by_case_id(case_id, beam_status.value)

    def _submit_beam_worker(self, executor: ProcessPoolExecutor, beam_id: str,
                           beam_path: Path, active_futures: Dict) -> None:
        """Submits a beam worker and tracks its future.

        Args:
            executor (ProcessPoolExecutor): The executor to submit to.
            beam_id (str): The beam ID.
            beam_path (Path): The beam path.
            active_futures (Dict): Dictionary tracking active futures.
        """
        self.logger.info(f"Submitting beam worker for: {beam_id}")
        future = executor.submit(worker_main,
                                beam_id=beam_id,
                                beam_path=beam_path,
                                settings=self.settings)
        active_futures[future] = beam_id

    def initialize_logging(self) -> None:
        """Initializes the structured logging for the application.
        Exits the application if logging cannot be initialized.
        """
        try:
            # Configure the logger factory globally
            LoggerFactory.configure(self.settings)
            self.logger = LoggerFactory.get_logger("main")
            self.logger.info("MQI Communicator starting up")
        except Exception as e:
            print(f"Failed to initialize logging: {e}")
            sys.exit(1)

    def initialize_database(self) -> None:
        """Initializes the database connection and schema.
        Exits the application if the database cannot be initialized.
        """
        try:
            with self._create_db_connection() as db_connection:
                db_connection.init_db()
            self.logger.info("Database initialized successfully",
                           {"path": str(self.settings.get_database_path())})
        except Exception as e:
            self.logger.error("Failed to initialize database",
                              {"error": str(e)})
            sys.exit(1)

    def start_file_watcher(self) -> None:
        """Starts the file system watcher to detect new cases."""
        try:
            scan_directory = self._validate_scan_directory()
            if not scan_directory:
                return
            event_handler = CaseDetectionHandler(self.case_queue, self.logger)
            self.observer = Observer()
            self.observer.schedule(event_handler,
                                   str(scan_directory),
                                   recursive=False)
            self.observer.start()
            self.logger.info(f"Watching for new cases in: {scan_directory}")
        except Exception as e:
            self.logger.error("Failed to start file watcher", {"error": str(e)})

    def start_dashboard(self) -> None:
        """Starts the dashboard UI in a separate process if configured."""
        try:
            if not self.settings.get_ui_config().get("auto_start", False):
                self.logger.info("Dashboard auto-start disabled")
                return
            # Get database path and resolve to an absolute path to ensure the
            # subprocess can find it regardless of its working directory.
            db_path = self.settings.get_database_path().resolve()
            # Create UI process manager
            self.ui_process_manager = UIProcessManager(
                database_path=str(db_path),
                # Ensure config_path is a string for the subprocess arguments.
                config_path=str(self.config_path) if self.config_path else None,
                config=self.settings,
                logger=self.logger)
            # Start UI as separate process
            if self.ui_process_manager.start():
                self.logger.info("Dashboard UI process started successfully")

                # Provide web access information if enabled
                ui_config = self.settings.get_ui_config()
                web_config = ui_config.get("web", {})

                if web_config.get("enabled", False):
                    import socket
                    web_port = web_config.get("port", 8080)
                    bind_address = web_config.get("bind_address", "0.0.0.0")

                    # Determine accessible URLs
                    urls = []

                    if bind_address == "0.0.0.0":
                        # Listening on all interfaces
                        try:
                            hostname = socket.gethostname()
                            local_ip = socket.gethostbyname(hostname)

                            urls.append(f"http://localhost:{web_port}")
                            urls.append(f"http://{local_ip}:{web_port}")
                            if hostname != local_ip:
                                urls.append(f"http://{hostname}:{web_port}")
                        except Exception:
                            # Fallback if hostname resolution fails
                            urls.append(f"http://localhost:{web_port}")
                    else:
                        urls.append(f"http://{bind_address}:{web_port}")

                    # Log access information
                    self.logger.info("=" * 60)
                    self.logger.info("Web Dashboard is available at:")
                    for url in urls:
                        self.logger.info(f"  → {url}")
                    self.logger.info("=" * 60)
            else:
                self.logger.error("Failed to start dashboard UI process")
        except Exception as e:
            self.logger.error("Failed to start dashboard", {"error": str(e)})

    def start_gpu_monitor(self) -> None:
        """Starts the GPU monitoring service in a background thread."""
        try:
            self.logger.info("Initializing GPU monitoring service.")
            # Create a dedicated database connection for the monitor
            self.monitor_db_connection = self._create_db_connection()
            gpu_repo = GpuRepository(self.monitor_db_connection, self.logger, self.settings)

            # GpuMonitor의 실행 모드를 먼저 확인
            handler_mode = self.settings.execution_handler.get("GpuMonitor", "local")

            # remote 모드일 경우에만 ssh_client를 확인
            if handler_mode == "remote" and not self.ssh_client:
                self.logger.error("SSH client not available. Cannot start remote GPU monitor.")
                return

            # ExecutionHandler 생성 시 ssh_client를 조건부로 전달
            execution_handler = ExecutionHandler(settings=self.settings,
                                             mode=handler_mode,
                                             ssh_client=self.ssh_client if handler_mode == "remote" else None)

            # Get interval from settings
            gpu_config = self.settings.get_gpu_config()
            interval = gpu_config.get('monitor_interval', 60)
            if interval is None:
                self.logger.warning("GPU monitor_interval not found in config, defaulting to 60 seconds.")
                interval = 60
            command = gpu_config.get('gpu_monitor_command')

            self.gpu_monitor = GpuMonitor(logger=self.logger,
                                          execution_handler=execution_handler,
                                          gpu_repository=gpu_repo,
                                          command=command,
                                          update_interval=interval)
            self.gpu_monitor.start()
            self.logger.info("GPU monitoring service started.")
        except Exception as e:
            self.logger.error("Failed to start GPU monitor", {"error": str(e)})
    
    def _try_allocate_pending_beams(self, pending_beams_by_case: Dict, executor, active_futures: Dict) -> None:
        """Attempts to allocate GPUs for pending beams and dispatch workers.

        Args:
            pending_beams_by_case (Dict): Dictionary tracking pending beams by case_id.
            executor: The ProcessPoolExecutor for dispatching workers.
            active_futures (Dict): Dictionary tracking active worker futures.
        """
        cases_to_remove = []

        for case_id, pending_data in list(pending_beams_by_case.items()):
            pending_jobs = pending_data["pending_jobs"]
            case_path = pending_data["case_path"]

            if not pending_jobs:
                cases_to_remove.append(case_id)
                continue

            # Try to allocate GPUs for pending beams
            new_gpu_assignments = allocate_gpus_for_pending_beams(
                case_id=case_id,
                num_pending_beams=len(pending_jobs),
                settings=self.settings
            )

            if new_gpu_assignments is None:
                self.logger.error(f"Error allocating GPUs for pending beams of case {case_id}")
                continue

            if not new_gpu_assignments:
                # No GPUs available yet, keep waiting
                continue

            # Update TPS file with new GPU assignments
            from src.core.tps_generator import TpsGenerator
            tps_generator = TpsGenerator(self.settings, self.logger)

            # We need to append to existing TPS file or regenerate it
            # For now, regenerate the TPS file with the new assignments
            num_allocated = len(new_gpu_assignments)
            jobs_to_dispatch = pending_jobs[:num_allocated]
            remaining_jobs = pending_jobs[num_allocated:]

            # Update TPS file with additional GPU assignments
            # Note: This is a simplified approach - in production, you might want to append to existing TPS
            success = tps_generator.generate_tps_file_with_gpu_assignments(
                case_path=case_path,
                case_id=case_id,
                gpu_assignments=new_gpu_assignments,
                execution_mode="remote"
            )

            if not success:
                self.logger.error(f"Failed to update TPS file for pending beams of case {case_id}")
                continue

            self.logger.info(f"Allocated {num_allocated} additional GPUs for case {case_id}, dispatching workers")

            # Dispatch workers for newly allocated beams
            for job in jobs_to_dispatch:
                beam_id = job["beam_id"]
                beam_path = job["beam_path"]
                self._submit_beam_worker(executor, beam_id, beam_path, active_futures)

            # Update pending jobs list
            if remaining_jobs:
                pending_beams_by_case[case_id]["pending_jobs"] = remaining_jobs
            else:
                cases_to_remove.append(case_id)

        # Clean up completed cases
        for case_id in cases_to_remove:
            del pending_beams_by_case[case_id]
            self.logger.info(f"All beams for case {case_id} have been allocated")

    def run_worker_loop(self) -> None:
        """The main loop for processing cases from the queue.
        Manages a process pool to handle cases concurrently.
        """
        max_workers = self.settings.get_processing_config().get('max_workers')
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            self.executor = executor
            self.logger.info(f"Started worker pool with {max_workers} processes")
            active_futures = {}
            pending_beams_by_case = {}  # Track {case_id: {"case_path": Path, "pending_jobs": [job_dict]}}
            while not self.shutdown_event.is_set():
                try:
                    # Check for new cases
                    try:
                        # Get a new case from the queue
                        case_data = self.case_queue.get(timeout=1.0)
                        case_id = case_data["case_id"]
                        case_path = Path(case_data["case_path"])
                        self.logger.info(f"Processing new case: {case_id}")

                        # The main process now orchestrates the initial case-level steps
                        # and updates the database so the UI can reflect the status.
                        with self._create_db_connection() as db_conn:
                            case_repo = self._create_case_repository(db_conn)
                            # Step 1: Discover beams and validate data transfer completion
                            self.logger.info(f"Discovering beams and validating data transfer for case: {case_id}")
                            beam_jobs = prepare_beam_jobs(case_id, case_path, self.settings)
                            if not beam_jobs:
                                case_repo.add_case(case_id, case_path)
                                self._fail_case(case_repo, case_id, "No beams found or data transfer incomplete")
                                continue

                            case_repo.create_case_with_beams(case_id, str(case_path), beam_jobs)
                            self.logger.info(f"Created {len(beam_jobs)} beam records in DB for case {case_id}")

                            # Step 2: Run case-level CSV interpreting
                            self._update_case_and_beams_status(case_repo, case_id,
                                                              CaseStatus.CSV_INTERPRETING,
                                                              BeamStatus.CSV_INTERPRETING,
                                                              progress=10.0)
                            self.logger.info(f"Starting case-level CSV interpreting for {case_id}")
                            interpreting_success = run_case_level_csv_interpreting(case_id, case_path, self.settings)
                            if not interpreting_success:
                                self._fail_case(case_repo, case_id, "CSV interpreting failed")
                                continue

                            # Step 3: Generate TPS file with dynamic GPU assignments
                            self._update_case_and_beams_status(case_repo, case_id,
                                                              CaseStatus.PROCESSING,
                                                              BeamStatus.TPS_GENERATION,
                                                              progress=30.0)
                            self.logger.info(f"Starting case-level TPS generation for {case_id}")
                            gpu_assignments = run_case_level_tps_generation(case_id, case_path, len(beam_jobs), self.settings)
                            if gpu_assignments is None:
                                self._fail_case(case_repo, case_id, "TPS generation failed")
                                continue

                            # Handle partial GPU allocation
                            if len(gpu_assignments) < len(beam_jobs):
                                self.logger.info(f"Partial GPU allocation for {case_id}: {len(gpu_assignments)}/{len(beam_jobs)} beams can proceed")
                            elif len(gpu_assignments) == 0:
                                self.logger.warning(f"No GPUs available for {case_id}. All beams will remain pending.")
                                case_repo.update_beams_status_by_case_id(case_id, BeamStatus.PENDING.value)
                                continue

                            # Step 4: Run case-level file upload to HPC if any handler is remote
                            handler_modes = self.settings.execution_handler.values()
                            if "remote" in handler_modes:
                                case_repo.update_beams_status_by_case_id(case_id, BeamStatus.UPLOADING.value)
                                self.logger.info(f"Starting case-level file upload for {case_id}")
                                upload_success = run_case_level_upload(case_id, self.settings, self.ssh_client)
                                if not upload_success:
                                    self._fail_case(case_repo, case_id, "File upload failed")
                                    continue
                            else:
                                self.logger.info("No remote handlers configured. Skipping case-level file upload.")

                            # Step 5: Dispatch individual workers for simulation
                            case_repo.update_case_status(case_id, CaseStatus.PROCESSING, progress=50.0)

                            # Only dispatch workers for beams with GPU assignments
                            num_allocated = len(gpu_assignments)
                            allocated_jobs = beam_jobs[:num_allocated]
                            pending_jobs = beam_jobs[num_allocated:]

                            # Mark allocated beams as ready for processing
                            for job in allocated_jobs:
                                case_repo.update_beam_status(job["beam_id"], BeamStatus.PENDING)

                            # Keep remaining beams in PENDING with a note they're waiting for GPUs
                            for job in pending_jobs:
                                self.logger.info(f"Beam {job['beam_id']} pending GPU availability")
                                case_repo.update_beam_status(job["beam_id"], BeamStatus.PENDING)

                            # Track pending beams for later GPU allocation
                            if pending_jobs:
                                pending_beams_by_case[case_id] = {
                                    "case_path": case_path,
                                    "pending_jobs": pending_jobs
                                }

                            self.logger.info(f"Dispatching workers for case {case_id}: {num_allocated} beams with GPU, {len(pending_jobs)} pending")

                            for job in allocated_jobs:
                                self._submit_beam_worker(executor, job["beam_id"], job["beam_path"], active_futures)

                    except mp.queues.Empty:
                        pass  # Queue timeout, continue
                    except Exception as e:
                        self.logger.error(f"Error processing case from queue", {"error": str(e)})

                    # Check for completed workers
                    completed_futures = []
                    for future in as_completed(active_futures.keys(), timeout=0.1):
                        completed_futures.append(future)

                    for future in completed_futures:
                        beam_id = active_futures.pop(future)
                        try:
                            future.result()  # Raise exception if worker failed
                            self.logger.info(f"Beam worker {beam_id} completed successfully")

                            # Check if there are pending beams waiting for GPUs
                            if pending_beams_by_case:
                                self._try_allocate_pending_beams(pending_beams_by_case, executor, active_futures)

                        except Exception as e:
                            self.logger.error(f"Beam worker {beam_id} failed", {"error": str(e)})

                except KeyboardInterrupt:
                    self.logger.info("Received shutdown signal")
                    break
                except Exception as e:
                    self.logger.error("Error in worker loop", {"error": str(e)})
                # Prevent busy-waiting
                time.sleep(1)

    def _monitor_services(self) -> None:
        """Periodically monitors the health of critical background services."""
        self.logger.info("Service monitor thread started.")
        while not self.shutdown_event.is_set():
            try:
                # Monitor the dashboard UI process
                if self.ui_process_manager and self.settings.get_ui_config().get("auto_start", False) and not self.ui_process_manager.is_running():
                    self.logger.warning("Dashboard UI process is not running. Attempting to restart.")
                    if not self.ui_process_manager.restart():
                        self.logger.error("Failed to restart the dashboard UI process.")

                # Add other service checks here if needed (e.g., GPU monitor)

            except Exception as e:
                self.logger.error("Error in service monitoring thread", {"error": str(e)})

            # Use a configurable interval for service monitoring
            processing_config = self.settings.get_processing_config()
            monitor_interval = processing_config.get('hpc_poll_interval_seconds', 30)
            self.shutdown_event.wait(timeout=monitor_interval)

    def initialize_ssh_client(self) -> None:
        """Initializes the SSH client for remote connections."""
        try:
            hpc_config = self.settings.get_hpc_connection()
            if not hpc_config or not all(k in hpc_config for k in ["host", "user", "ssh_key_path"]):
                self.logger.warning("HPC connection details are not fully configured. Remote operations will be disabled.")
                return

            self.ssh_client = paramiko.SSHClient()
            self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self.logger.info("Connecting to HPC...", {"host": hpc_config["host"]})
            self.ssh_client.connect(
                hostname=hpc_config["host"],
                username=hpc_config["user"],
                key_filename=hpc_config["ssh_key_path"],
                port=hpc_config.get("port", 22),
                timeout=20
            )
            self.logger.info("Successfully connected to HPC.")
        except Exception as e:
            self.logger.error("Failed to establish SSH connection to HPC.", {"error": str(e)})
            self.ssh_client = None # Ensure client is None on failure

    def shutdown(self) -> None:
        """Performs a graceful shutdown of all application components."""
        self.logger.info("Shutting down MQI Communicator")
        self.shutdown_event.set()
        # Stop file watcher
        if self.observer:
            self.observer.stop()
            self.observer.join()
        # Wait for monitor thread to finish
        if self.service_monitor_thread and self.service_monitor_thread.is_alive():
            self.logger.info("Waiting for service monitor to stop...")
            self.service_monitor_thread.join(timeout=5)
        # Stop GPU monitor
        if self.gpu_monitor:
            self.logger.info("Stopping GPU monitor.")
            self.gpu_monitor.stop()
        if self.monitor_db_connection:
            self.monitor_db_connection.close()
        # Close SSH connection
        if self.ssh_client:
            self.logger.info("Closing HPC connection.")
            self.ssh_client.close()
        # Stop dashboard UI process
        if self.ui_process_manager:
            self.ui_process_manager.stop()
        # Executor shutdown handled by context manager
        self.logger.info("Shutdown complete")

    def run(self) -> NoReturn:
        """The main entry point for running the application.
        Initializes and starts all components, then enters the main processing loop.
        """
        try:
            # breakpoint() # Paused here
            
            # Initialize core components
            self.initialize_logging()
            self.initialize_database()
            # Conditionally initialize SSH if any handler is remote
            handler_modes = self.settings.execution_handler.values()
            if "remote" in handler_modes:
                self.initialize_ssh_client()
            # Scan for existing cases that haven't been processed yet
            self.logger.info("Scanning for existing cases at startup")
            scan_existing_cases(self.case_queue, self.settings, self.logger)
            # Start monitoring and UI
            self.start_file_watcher()
            self.start_dashboard()
            self.start_gpu_monitor()

            # Start a background thread to monitor services
            self.service_monitor_thread = threading.Thread(target=self._monitor_services, daemon=True)
            self.service_monitor_thread.start()
            # Run main processing loop
            self.run_worker_loop()
        except KeyboardInterrupt:
            pass
        except Exception as e:
            if self.logger:
                self.logger.error("Application failed", {"error": str(e)})
            else:
                print(f"Application failed: {e}")
        finally:
            self.shutdown()


def setup_signal_handlers(app: MQIApplication) -> None:
    """Sets up signal handlers for graceful application shutdown.
    Args:
        app (MQIApplication): The application instance to shut down on signal.
    """
    def signal_handler(signum, frame):
        # Using print() here can corrupt the rich display during shutdown.
        # It's better to use the logger if it's available.
        message = f"\nReceived signal {signum}, shutting down..."
        if app.logger:
            app.logger.info(message.strip())
        else:
            print(message)
        app.shutdown()
        sys.exit(0)
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)


def main() -> NoReturn:
    """The main entry point of the application.
    Parses command-line arguments for a configuration file,
    initializes and runs the MQIApplication.
    """
    # Determine config file path
    config_path = None
    if len(sys.argv) > 1:
        config_path = Path(sys.argv[1])
    else:
        # Default config location
        default_config = Path("config/config.yaml")
        if default_config.exists():
            config_path = default_config
    # Create and run application
    app = MQIApplication(config_path)
    setup_signal_handlers(app)
    app.run()


if __name__ == "__main__":
    main()