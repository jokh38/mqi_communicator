# =====================================================================================
# Target File: src/core/workflow_manager.py
# Source Reference: src/workflow_manager.py, src/worker.py
# =====================================================================================
"""Manages and orchestrates the entire workflow for a case using a state pattern."""

from typing import Optional, Any, Dict, List
from pathlib import Path
import multiprocessing as mp
import time

from watchdog.events import FileSystemEventHandler

from src.repositories.case_repo import CaseRepository
from src.repositories.gpu_repo import GpuRepository
from src.handlers.execution_handler import ExecutionHandler
from src.infrastructure.logging_handler import StructuredLogger, LoggerFactory
from src.core.tps_generator import TpsGenerator
from src.domain.enums import BeamStatus, CaseStatus
from src.domain.states import WorkflowState, InitialState
from src.utils.db_context import get_db_session
from src.core.case_aggregator import queue_case as _queue_case


def _looks_like_case_directory(path: Path) -> bool:
    """Return True for a study directory containing an RTPLAN and delivery subfolders."""
    try:
        if not path.is_dir():
            return False
    except Exception:
        return False
    has_rtplan = any(child.is_file() and child.suffix.lower() == ".dcm" for child in path.iterdir())
    has_delivery_subdir = any(
        child.is_dir() and ((child / "PlanInfo.txt").exists() or any(child.glob("*.ptn")))
        for child in path.iterdir()
    )
    return has_rtplan and has_delivery_subdir


def _derive_case_identity(case_path: Path) -> tuple[str, Path]:
    """Resolve case_id/case_path from nested daily-log structures.

    Preferred structure:
      <scan_root>/<patient_id>/<study_uid>/<delivery_timestamp>
    """
    if _looks_like_case_directory(case_path):
        patient_candidate = case_path.parent.name if case_path.parent else case_path.name
        if patient_candidate and patient_candidate != case_path.name:
            return patient_candidate, case_path
        return case_path.name, case_path

    if case_path.parent and _looks_like_case_directory(case_path.parent):
        return _derive_case_identity(case_path.parent)

    return case_path.name, case_path


def _discover_case_directories(scan_directory: Path) -> List[tuple[str, Path]]:
    """Discover case roots recursively under the configured scan directory."""
    discovered: List[tuple[str, Path]] = []
    seen_paths = set()
    for candidate in [scan_directory, *scan_directory.rglob("*")]:
        if not isinstance(candidate, Path) or not candidate.exists() or not candidate.is_dir():
            continue
        if not _looks_like_case_directory(candidate):
            continue
        case_id, case_path = _derive_case_identity(candidate)
        normalized = str(case_path.resolve())
        if normalized in seen_paths:
            continue
        seen_paths.add(normalized)
        discovered.append((case_id, case_path))
    discovered.sort(key=lambda item: (item[0], str(item[1])))
    if discovered:
        return discovered

    # Backward-compatible fallback: treat immediate child directories as cases.
    fallback = []
    for item in scan_directory.iterdir():
        if item.is_dir():
            fallback.append((item.name, item))
    fallback.sort(key=lambda item: (item[0], str(item[1])))
    return fallback


def should_route_case_to_ptn_checker(
    case_data: Any,
    case_path: Path,
    settings: Any,
    observed_ptn_files: Optional[List[Path]] = None,
) -> bool:
    """Return True when a completed case with stable PTN arrivals should run PTN checker."""
    if getattr(case_data, "status", None) != CaseStatus.COMPLETED:
        return False

    observed_ptn_files = observed_ptn_files or sorted(case_path.rglob("*.ptn"))
    if not observed_ptn_files:
        return False
    return True


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

            filesystem_cases = _discover_case_directories(scan_directory)
            logger.info(
                f"Found {len(filesystem_cases)} case directories in scan directory"
            )

            # W-4 fix: Get max retry limit from config
            processing_config = settings.get_processing_config()
            max_retries = processing_config.get("max_case_retries", 3)

            retryable_statuses = {
                CaseStatus.PENDING,
                CaseStatus.CSV_INTERPRETING,
                CaseStatus.PROCESSING,
                CaseStatus.POSTPROCESSING,
            }
            # W-4 fix: Don't automatically retry FAILED or CANCELLED cases
            non_retryable_statuses = {CaseStatus.COMPLETED, CaseStatus.FAILED, CaseStatus.CANCELLED}

            new_cases = []
            for case_id, case_path in filesystem_cases:
                if case_id not in existing_case_ids:
                    new_cases.append((case_id, case_path))
                    continue

                case_data = case_repo.get_case(case_id)
                case_status = getattr(case_data, "status", None)
                retry_count = getattr(case_data, "retry_count", 0)

                # W-4 fix: Check retry limit
                if case_status in retryable_statuses:
                    if retry_count >= max_retries:
                        logger.warning(
                            f"Case {case_id} has exceeded max retries ({retry_count}/{max_retries}). Skipping.",
                            {"case_id": case_id, "status": case_status.value, "retry_count": retry_count}
                        )
                        continue
                    # W-4 fix: Increment retry count for retryable cases
                    case_repo.increment_retry_count(case_id)
                    logger.info(
                        f"Found retryable case during startup scan: {case_id} (retry {retry_count + 1}/{max_retries})",
                        {"case_id": case_id, "status": case_status.value, "retry_count": retry_count + 1}
                    )
                    new_cases.append((case_id, case_path))
                elif case_status in non_retryable_statuses:
                    logger.info(
                        f"Skipping existing case during startup scan: {case_id}",
                        {"case_id": case_id, "status": case_status.value}
                    )
                else:
                    logger.warning(
                        f"Case {case_id} has unknown startup status; skipping automatic requeue",
                        {
                            "case_id": case_id,
                            "status": getattr(case_status, "value", str(case_status)),
                        },
                    )

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
        self.settings = None
        self.case_repo = None

    def on_created(self, event) -> None:
        """Handles the 'created' event from the file system watcher."""
        self._handle_event(event)

    def on_modified(self, event) -> None:
        """Handles PTN modifications after file writes settle."""
        self._handle_event(event)

    def _handle_event(self, event) -> None:
        if event.is_directory:
            case_id, case_path = _derive_case_identity(Path(event.src_path))
            # Defense in depth: the file watcher emits events for every
            # directory mutation (including ones from our own pipeline writing
            # CSVs/TPS files into the tree), which can redeliver an already
            # terminal case to the queue.  In serial multigpu mode those
            # duplicates block the queue on a case that has nothing to do,
            # so skip them before they ever reach the worker loop.
            if self.settings is not None:
                try:
                    if self.case_repo is not None:
                        case_data = self.case_repo.get_case(case_id)
                    else:
                        with get_db_session(self.settings, self.logger) as case_repo:
                            case_data = case_repo.get_case(case_id)
                except Exception as exc:
                    self.logger.warning(
                        "Failed to check case status before queuing; queuing anyway",
                        {"case_id": case_id, "error": str(exc)},
                    )
                    case_data = None

                terminal_statuses = {
                    CaseStatus.COMPLETED,
                    CaseStatus.FAILED,
                    CaseStatus.CANCELLED,
                }
                if case_data is not None and getattr(case_data, "status", None) in terminal_statuses:
                    self.logger.debug(
                        "Ignoring directory event for terminal case",
                        {
                            "case_id": case_id,
                            "status": case_data.status.value,
                            "src_path": str(event.src_path),
                        },
                    )
                    return

            self.logger.info(f"New case detected: {case_id} at {case_path}")
            _queue_case(case_id, case_path, self.case_queue, self.logger)
            return

        src_path = Path(event.src_path)
        if src_path.suffix.lower() != ".ptn" or self.settings is None:
            return

        case_id, case_path = _derive_case_identity(src_path.parent)

        if self.case_repo is not None:
            case_data = self.case_repo.get_case(case_id)
        else:
            with get_db_session(self.settings, self.logger) as case_repo:
                case_data = case_repo.get_case(case_id)

        if not should_route_case_to_ptn_checker(
            case_data=case_data,
            case_path=case_path,
            settings=self.settings,
            observed_ptn_files=[src_path],
        ):
            return

        self.logger.info(f"Stable PTN detected for completed case: {case_id}")
        _queue_case(case_id, case_path, self.case_queue, self.logger, reason="ptn_checker")
