# =====================================================================================
# Target File: src/core/workflow_manager.py
# Source Reference: src/workflow_manager.py, src/worker.py
# =====================================================================================
"""Manages and orchestrates the entire workflow for a case using a state pattern."""

from typing import Optional, Any, Dict
from pathlib import Path
import multiprocessing as mp

from watchdog.events import FileSystemEventHandler

from src.repositories.case_repo import CaseRepository
from src.repositories.gpu_repo import GpuRepository
from src.handlers.execution_handler import ExecutionHandler
from src.infrastructure.logging_handler import StructuredLogger, LoggerFactory
from src.core.tps_generator import TpsGenerator
from src.domain.enums import BeamStatus
from src.domain.states import WorkflowState, InitialState
from src.utils.db_context import get_db_session
from src.core.case_aggregator import queue_case as _queue_case


class WorkflowManager:
    """Manages and orchestrates the entire workflow for a case according to the State pattern.

    This class is responsible for executing each `State` and transitioning to the next,
    using the injected `repositories` and `handlers`.
    """

    def __init__(
        self,
        case_repo: CaseRepository,
        gpu_repo: GpuRepository,
        execution_handler: ExecutionHandler,
        tps_generator: TpsGenerator,
        logger: StructuredLogger,
        id: str,
        path: Path,
        settings: Any,
    ):
        """Initializes the workflow manager with all required dependencies.

        Args:
            case_repo (CaseRepository): The case repository for database access.
            gpu_repo (GpuRepository): The GPU repository for database access.
            execution_handler (ExecutionHandler): The handler for command execution.
            tps_generator (TpsGenerator): The TPS generator service.
            logger (StructuredLogger): The structured logger.
            id (str): The unique identifier for the case or beam.
            path (Path): The path to the case or beam directory.
            settings (Any): The application settings.
        """
        self.case_repo = case_repo
        self.gpu_repo = gpu_repo
        self.execution_handler = execution_handler
        self.tps_generator = tps_generator
        self.logger = logger
        self.id = id
        self.path = path
        self.settings = settings
        self.current_state: Optional[WorkflowState] = InitialState()
        self.shared_context: Dict[str, Any] = {}

    def run_workflow(self) -> None:
        """Executes the complete workflow by managing state transitions."""
        self.logger.info(f"Starting workflow for: {self.id}")

        while self.current_state:
            state_name = self.current_state.get_state_name()
            self.logger.info(f"Executing state: {state_name}")

            try:
                next_state = self.current_state.execute(self)
                self._transition_to_next_state(next_state)
            except Exception as e:
                self._handle_workflow_error(e, f"Error during state: {state_name}")
                break

        self.logger.info(f"Workflow finished for: {self.id}")

    def _transition_to_next_state(self, next_state: WorkflowState) -> None:
        """Handles the transition from the current state to the next state.

        Args:
            next_state (WorkflowState): The next state in the workflow.
        """
        if next_state:
            self.logger.info(
                f"Transitioning from {self.current_state.get_state_name()} to {next_state.get_state_name()}"
            )
        else:
            self.logger.info(
                f"Transitioning from {self.current_state.get_state_name()} to None (workflow end)"
            )

        self.current_state = next_state

    def _handle_workflow_error(self, error: Exception, context: str) -> None:
        """Handles errors that occur during workflow execution.

        Args:
            error (Exception): The exception that occurred.
            context (str): A string describing the context in which the error occurred.
        """
        self.logger.error("Workflow error occurred", {
            "id": self.id,
            "context": context,
            "error": str(error),
            "error_type": type(error).__name__,
            "current_state":
            self.current_state.get_state_name() if self.current_state else "None"
        })

        try:
            # This logic is now beam-specific. The calling state should handle status updates.
            # For now, we'll assume the worker is for a beam and try to update beam status.
            # This part will need more refinement when states are refactored.
            self.case_repo.update_beam_status(
                self.id,
                BeamStatus.FAILED,
                error_message=str(error),
            )
            self.logger.info(f"Beam status updated to FAILED for: {self.id}")
        except Exception as db_error:
            self.logger.error("Failed to update status during error handling", {
                "id": self.id,
                "db_error": str(db_error)
            })

        self.current_state = None  # Stop the workflow


# ----------------------------------------------------------------------------
# File watcher utilities (moved from dispatcher with re-exports)
# ----------------------------------------------------------------------------

def scan_existing_cases(case_queue: mp.Queue,
                        settings,
                        logger: StructuredLogger) -> None:
    """Scan for existing cases at startup.
    Compares file system cases with database records and queues new cases.
    """
    try:
        case_dirs = settings.get_case_directories()
        scan_directory = case_dirs.get("scan")
        if not scan_directory or not scan_directory.exists():
            logger.warning(
                f"Scan directory does not exist or is not configured: {scan_directory}"
            )
            return

        with get_db_session(settings, logger) as case_repo:
            existing_case_ids = set(case_repo.get_all_case_ids())
            logger.info(
                f"Found {len(existing_case_ids)} cases already in database")

            filesystem_cases = []
            for item in scan_directory.iterdir():
                if item.is_dir():
                    case_id = item.name
                    filesystem_cases.append((case_id, item))
            logger.info(
                f"Found {len(filesystem_cases)} case directories in scan directory"
            )

            new_cases = []
            for case_id, case_path in filesystem_cases:
                if case_id not in existing_case_ids:
                    new_cases.append((case_id, case_path))

            if new_cases:
                logger.info(f"Found {len(new_cases)} new cases to process")
                for case_id, case_path in new_cases:
                    if _queue_case(case_id, case_path, case_queue, logger):
                        logger.info(f"Queued existing case for processing: {case_id}")
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
        """Initializes the CaseDetectionHandler."""
        super().__init__()
        self.case_queue = case_queue
        self.logger = logger

    def on_created(self, event) -> None:
        """Handles the 'created' event from the file system watcher."""
        if not event.is_directory:
            return
        case_path = Path(event.src_path)
        case_id = case_path.name
        self.logger.info(f"New case detected: {case_id} at {case_path}")
        _queue_case(case_id, case_path, self.case_queue, self.logger)
