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
from src.core.retry_policy import is_retryable_failed_case

_READY_MARKER = "_CASE_READY"


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


def _is_ready_case_directory(path: Path) -> bool:
    """Return True only for fully published case roots."""
    return _looks_like_case_directory(path) and (path / _READY_MARKER).exists()


def _derive_case_identity(case_path: Path) -> tuple[str, Path]:
    """Resolve case_id/case_path from nested daily-log structures.

    Preferred structure:
      <scan_root>/<patient_id>/<study_uid>/<delivery_timestamp>
    """
    if _looks_like_case_directory(case_path):
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

    # Legacy fallback: search one level below immediate children so grouped
    # layouts still resolve to study directories rather than room/group roots.
    fallback = []
    for item in scan_directory.iterdir():
        if not item.is_dir():
            continue
        if _looks_like_case_directory(item):
            fallback.append(_derive_case_identity(item))
            continue
        found_nested_case = False
        for child in item.iterdir():
            if child.is_dir() and _looks_like_case_directory(child):
                fallback.append(_derive_case_identity(child))
                found_nested_case = True
        if not found_nested_case and item.name not in {"G1", "G2"}:
            fallback.append((item.name, item))
    fallback.sort(key=lambda item: (item[0], str(item[1])))
    return fallback


def derive_room_from_case_path(case_path: Path, settings: Any) -> str:
    """Derive room grouping from a case path relative to the scan directory."""
    try:
        case_dirs = settings.get_case_directories()
        scan_dir = case_dirs.get("scan")
        if scan_dir:
            relative = case_path.resolve().relative_to(Path(scan_dir).resolve())
            if len(relative.parts) > 1 and relative.parts[0] in {"G1", "G2"}:
                return relative.parts[0]
    except Exception:
        pass
    return ""


def derive_room_from_path(case_path: Path, settings: Any | None = None) -> str:
    """Derive room grouping from a case, study, or beam path."""
    room = ""
    if settings is not None:
        room = derive_room_from_case_path(case_path, settings)
        if room:
            return room

    for candidate in (case_path, *case_path.parents):
        if candidate.name in {"G1", "G2"}:
            return candidate.name
    return ""


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

            retryable_statuses = {
                CaseStatus.PENDING,
                CaseStatus.CSV_INTERPRETING,
                CaseStatus.PROCESSING,
                CaseStatus.POSTPROCESSING,
            }
            non_retryable_statuses = {CaseStatus.COMPLETED, CaseStatus.CANCELLED}

            new_cases = []
            for case_id, case_path in filesystem_cases:
                if case_id not in existing_case_ids:
                    new_cases.append((case_id, case_path))
                    continue

                case_data = case_repo.get_case(case_id)
                case_status = getattr(case_data, "status", None)

                if case_status in retryable_statuses:
                    logger.info(
                        f"Found retryable in-flight case during startup scan: {case_id}",
                        {"case_id": case_id, "status": case_status.value}
                    )
                    new_cases.append((case_id, case_path))
                elif is_retryable_failed_case(case_data):
                    logger.info(
                        f"Found retryable failed case during startup scan: {case_id}",
                        {"case_id": case_id, "status": case_status.value}
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
        self._recent_queue_events: Dict[tuple[str, str], float] = {}
        self._debounce_seconds = 5.0

    def on_created(self, event) -> None:
        """Handles the 'created' event from the file system watcher."""
        self._handle_event(event)

    def on_modified(self, event) -> None:
        """Handles PTN modifications after file writes settle."""
        self._handle_event(event)

    def on_moved(self, event) -> None:
        """Handle atomic directory moves into the watched tree."""
        target_path = getattr(event, "dest_path", None)
        if target_path is None:
            return
        self._handle_event_for_path(
            path=Path(target_path),
            is_directory=event.is_directory,
            queue_reason=None,
            original_event=event,
        )

    def _handle_event(self, event) -> None:
        src_path = Path(event.src_path)
        if event.is_directory:
            self._handle_event_for_path(
                path=src_path,
                is_directory=True,
                queue_reason=None,
                original_event=event,
            )
            return

        if src_path.suffix.lower() != ".ptn" or self.settings is None:
            return

        self._handle_event_for_path(
            path=src_path.parent,
            is_directory=True,
            queue_reason="ptn_checker",
            original_event=event,
            observed_ptn_path=src_path,
        )

    def _handle_event_for_path(
        self,
        path: Path,
        is_directory: bool,
        queue_reason: Optional[str],
        original_event,
        observed_ptn_path: Optional[Path] = None,
    ) -> None:
        if self._should_ignore_path(path):
            return

        if not is_directory:
            return

        if queue_reason != "ptn_checker" and not _is_ready_case_directory(path):
            return

        case_id, case_path = _derive_case_identity(path)

        if queue_reason != "ptn_checker":
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

                status = getattr(case_data, "status", None) if case_data is not None else None
                terminal_statuses = {CaseStatus.COMPLETED, CaseStatus.CANCELLED}
                failed_and_non_retryable = (
                    status == CaseStatus.FAILED and not is_retryable_failed_case(case_data)
                )
                if status in terminal_statuses or failed_and_non_retryable:
                    self.logger.debug(
                        "Ignoring directory event for terminal case",
                        {
                            "case_id": case_id,
                            "status": status.value,
                            "src_path": str(getattr(original_event, "src_path", path)),
                        },
                    )
                    return

            if self._is_duplicate_queue_event(case_id, queue_reason):
                self.logger.debug(
                    "Ignoring duplicate case event inside debounce window",
                    {"case_id": case_id, "src_path": str(path)},
                )
                return

            self.logger.info(f"New case detected: {case_id} at {case_path}")
            _queue_case(case_id, case_path, self.case_queue, self.logger)
            return

        if self.settings is None:
            return

        if self.case_repo is not None:
            case_data = self.case_repo.get_case(case_id)
        else:
            with get_db_session(self.settings, self.logger) as case_repo:
                case_data = case_repo.get_case(case_id)

        if not should_route_case_to_ptn_checker(
            case_data=case_data,
            case_path=case_path,
            settings=self.settings,
            observed_ptn_files=[observed_ptn_path] if observed_ptn_path is not None else None,
        ):
            return

        if self._is_duplicate_queue_event(case_id, queue_reason):
            self.logger.debug(
                "Ignoring duplicate PTN checker event inside debounce window",
                {"case_id": case_id, "src_path": str(observed_ptn_path or path)},
            )
            return

        self.logger.info(f"Stable PTN detected for completed case: {case_id}")
        _queue_case(case_id, case_path, self.case_queue, self.logger, reason="ptn_checker")

    def _is_duplicate_queue_event(self, case_id: str, reason: Optional[str]) -> bool:
        cache_key = (reason or "default", case_id)
        now = time.monotonic()
        last_seen = self._recent_queue_events.get(cache_key)
        if last_seen is not None and now - last_seen < self._debounce_seconds:
            return True
        self._recent_queue_events[cache_key] = now
        return False

    def _should_ignore_path(self, path: Path) -> bool:
        for part in path.parts:
            lowered = part.lower()
            if lowered.endswith(".extracting") or lowered.endswith(".zip.tmp"):
                return True
            if ".backup." in lowered:
                return True
            if lowered.startswith("extract_") and lowered.endswith(".tmp"):
                return True
        return False
