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

from src.config.settings import Settings
from src.infrastructure.logging_handler import StructuredLogger, LoggerFactory
from src.infrastructure.ui_process_manager import UIProcessManager
from src.database.connection import DatabaseConnection
from src.repositories.gpu_repo import GpuRepository
from src.repositories.case_repo import CaseRepository
from src.infrastructure.gpu_monitor import GpuMonitor
from src.handlers.execution_handler import ExecutionHandler
from src.core.worker import submit_beam_worker, monitor_completed_workers
from src.core.dispatcher import (prepare_beam_jobs, run_case_level_csv_interpreting,
                                 run_case_level_upload, run_case_level_tps_generation,
                                 scan_existing_cases, CaseDetectionHandler)
from src.domain.enums import CaseStatus, BeamStatus
from src.utils.db_context import get_db_session
from src.utils.ssh_helper import create_ssh_client


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
            with get_db_session(self.settings, self.logger) as case_repo:
                case_repo._db_connection.init_db()
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
    
    def _discover_beams(self, case_id: str, case_path: Path, case_repo: CaseRepository):
        """Discovers beams and validates data transfer completion.

        Args:
            case_id (str): The case ID.
            case_path (Path): The case path.
            case_repo (CaseRepository): The case repository.

        Returns:
            List[Dict[str, Any]]: List of beam job dictionaries, or empty list if discovery failed.
        """
        self.logger.info(f"Discovering beams and validating data transfer for case: {case_id}")
        beam_jobs = prepare_beam_jobs(case_id, case_path, self.settings)

        if not beam_jobs:
            case_repo.add_case(case_id, case_path)
            self.logger.error(f"No beams found or data transfer incomplete for case {case_id}")
            case_repo.fail_case(case_id, "No beams found or data transfer incomplete")
            return []

        case_repo.create_case_with_beams(case_id, str(case_path), beam_jobs)
        self.logger.info(f"Created {len(beam_jobs)} beam records in DB for case {case_id}")
        return beam_jobs

    def _run_csv_interpreting(self, case_id: str, case_path: Path, case_repo: CaseRepository) -> bool:
        """Runs case-level CSV interpretation.

        Args:
            case_id (str): The case ID.
            case_path (Path): The case path.
            case_repo (CaseRepository): The case repository.

        Returns:
            bool: True if CSV interpreting succeeded, False otherwise.
        """
        case_repo.update_case_and_beams_status(
            case_id,
            CaseStatus.CSV_INTERPRETING,
            BeamStatus.CSV_INTERPRETING,
            progress=10.0
        )
        self.logger.info(f"Starting case-level CSV interpreting for {case_id}")
        interpreting_success = run_case_level_csv_interpreting(case_id, case_path, self.settings)

        if not interpreting_success:
            self.logger.error(f"CSV interpreting failed for case {case_id}")
            case_repo.fail_case(case_id, "CSV interpreting failed")

        return interpreting_success

    def _run_tps_generation(self, case_id: str, case_path: Path, beam_count: int, case_repo: CaseRepository):
        """Generates TPS file with GPU assignments.

        Args:
            case_id (str): The case ID.
            case_path (Path): The case path.
            beam_count (int): Number of beams requiring GPU assignments.
            case_repo (CaseRepository): The case repository.

        Returns:
            Optional[List[Dict[str, Any]]]: List of GPU assignments, or None if generation failed.
        """
        case_repo.update_case_and_beams_status(
            case_id,
            CaseStatus.PROCESSING,
            BeamStatus.TPS_GENERATION,
            progress=30.0
        )
        self.logger.info(f"Starting case-level TPS generation for {case_id}")
        gpu_assignments = run_case_level_tps_generation(case_id, case_path, beam_count, self.settings)

        if gpu_assignments is None:
            self.logger.error(f"TPS generation failed for case {case_id}")
            case_repo.fail_case(case_id, "TPS generation failed")
            return None

        # Handle partial GPU allocation
        if len(gpu_assignments) < beam_count:
            self.logger.info(f"Partial GPU allocation for {case_id}: {len(gpu_assignments)}/{beam_count} beams can proceed")
        elif len(gpu_assignments) == 0:
            self.logger.warning(f"No GPUs available for {case_id}. All beams will remain pending.")
            case_repo.update_beams_status_by_case_id(case_id, BeamStatus.PENDING.value)

        return gpu_assignments

    def _run_file_upload(self, case_id: str, case_repo: CaseRepository) -> bool:
        """Uploads files to HPC if needed.

        Args:
            case_id (str): The case ID.
            case_repo (CaseRepository): The case repository.

        Returns:
            bool: True if file upload succeeded or was skipped, False otherwise.
        """
        handler_modes = self.settings.execution_handler.values()

        if "remote" not in handler_modes:
            self.logger.info("No remote handlers configured. Skipping case-level file upload.")
            return True

        case_repo.update_beams_status_by_case_id(case_id, BeamStatus.UPLOADING.value)
        self.logger.info(f"Starting case-level file upload for {case_id}")
        upload_success = run_case_level_upload(case_id, self.settings, self.ssh_client)

        if not upload_success:
            self.logger.error(f"File upload failed for case {case_id}")
            case_repo.fail_case(case_id, "File upload failed")

        return upload_success

    def _dispatch_workers(self, case_id: str, case_path: Path, beam_jobs: list,
                         gpu_assignments: list, case_repo: CaseRepository,
                         executor: ProcessPoolExecutor, active_futures: Dict,
                         pending_beams_by_case: Dict) -> None:
        """Dispatches worker processes for beams with GPU assignments.

        Args:
            case_id (str): The case ID.
            case_path (Path): The case path.
            beam_jobs (list): List of all beam job dictionaries.
            gpu_assignments (list): List of GPU assignment dictionaries.
            case_repo (CaseRepository): The case repository.
            executor (ProcessPoolExecutor): The executor for dispatching workers.
            active_futures (Dict): Dictionary tracking active worker futures.
            pending_beams_by_case (Dict): Dictionary tracking pending beams by case_id.
        """
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
            submit_beam_worker(executor, job["beam_id"], job["beam_path"],
                             self.settings, active_futures, self.logger)


    def _process_new_case(self, case_data: dict, executor: ProcessPoolExecutor,
                         active_futures: Dict, pending_beams_by_case: Dict) -> None:
        """Processes a new case from the queue.

        Args:
            case_data (dict): Dictionary containing case_id, case_path, timestamp.
            executor (ProcessPoolExecutor): The executor for dispatching workers.
            active_futures (Dict): Dictionary tracking active worker futures.
            pending_beams_by_case (Dict): Dictionary tracking pending beams by case_id.
        """
        case_id = case_data["case_id"]
        case_path = Path(case_data["case_path"])
        self.logger.info(f"Processing new case: {case_id}")

        # The main process now orchestrates the initial case-level steps
        # and updates the database so the UI can reflect the status.
        with get_db_session(self.settings, self.logger) as case_repo:
            # Step 1: Discover beams and validate data transfer completion
            beam_jobs = self._discover_beams(case_id, case_path, case_repo)
            if not beam_jobs:
                return

            # Step 2: Run case-level CSV interpreting
            if not self._run_csv_interpreting(case_id, case_path, case_repo):
                return

            # Step 3: Generate TPS file with dynamic GPU assignments
            gpu_assignments = self._run_tps_generation(case_id, case_path, len(beam_jobs), case_repo)
            if gpu_assignments is None:
                return

            if len(gpu_assignments) == 0:
                return

            # Step 4: Run case-level file upload to HPC if any handler is remote
            if not self._run_file_upload(case_id, case_repo):
                return

            # Step 5: Dispatch individual workers for simulation
            self._dispatch_workers(case_id, case_path, beam_jobs, gpu_assignments,
                                  case_repo, executor, active_futures, pending_beams_by_case)

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
                    # Process new cases from queue
                    try:
                        case_data = self.case_queue.get(timeout=1.0)
                        self._process_new_case(case_data, executor, active_futures, pending_beams_by_case)
                    except mp.queues.Empty:
                        pass  # Queue timeout, continue
                    except Exception as e:
                        self.logger.error(f"Error processing case from queue", {"error": str(e)})

                    # Monitor completed workers
                    monitor_completed_workers(active_futures, pending_beams_by_case,
                                            executor, self.settings, self.logger)

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
        self.ssh_client = create_ssh_client(self.settings, self.logger)

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