# =====================================================================================
# Target File: src/core/worker.py
# Source Reference: src/worker.py, src/main.py
# =====================================================================================
"""Main entry point for a worker process that handles a single beam."""

from pathlib import Path
from typing import Dict, Optional, List, Any
from concurrent.futures import ProcessPoolExecutor, as_completed

from src.database.connection import DatabaseConnection
from src.repositories.case_repo import CaseRepository
from src.repositories.gpu_repo import GpuRepository
from src.handlers.execution_handler import ExecutionHandler
from src.infrastructure.logging_handler import StructuredLogger, LoggerFactory
from src.core.workflow_manager import WorkflowManager
import paramiko
from src.core.tps_generator import TpsGenerator
from src.config.settings import Settings
from src.utils.db_context import get_db_session
from src.utils.ssh_helper import create_ssh_client


def worker_main(beam_id: str, beam_path: Path, settings: Settings) -> None:
    """Acts as the "assembly line" that creates all dependency objects for a single beam
    and injects them into the WorkflowManager to start the process.

    This function is executed by a worker process for each beam.

    Args:
        beam_id (str): Unique identifier for the beam.
        beam_path (Path): Path to the beam directory.
        settings (Settings): Settings object containing all configuration.
    """
    # Since workers run in separate processes, the factory must be configured here.
    LoggerFactory.configure(settings)
    logger = LoggerFactory.get_logger(f"worker_{beam_id}")

    try:
        _validate_beam_path(beam_path, logger)

        with get_db_session(settings, logger) as case_repo:
            # Initialize database schema
            case_repo.db.init_db()
            gpu_repo = GpuRepository(case_repo.db, logger, settings)

            # Create ExecutionHandler based on settings
            workflow_mode = settings.execution_handler.get("Workflow", "local")

            ssh_client = None
            if workflow_mode == "remote":
                ssh_client = create_ssh_client(settings, logger)
                if not ssh_client:
                    raise ConnectionError("HPC connection settings not configured.")

            execution_handler = ExecutionHandler(settings=settings, mode=workflow_mode, ssh_client=ssh_client)

            # Create TPS generator
            tps_generator = TpsGenerator(settings, logger)

            workflow_manager = WorkflowManager(
                case_repo=case_repo,
                gpu_repo=gpu_repo,
                execution_handler=execution_handler,
                tps_generator=tps_generator,
                logger=logger,
                id=beam_id,
                path=beam_path,
                settings=settings,
            )

            workflow_manager.run_workflow()

    except Exception as e:
        logger.error(
            f"Worker failed for beam {beam_id}", {
                "error": str(e),
                "error_type": type(e).__name__
            })
        # Optionally re-raise or handle specific exceptions
        raise
    finally:
        logger.info(f"Worker finished for beam {beam_id}")


def _validate_beam_path(beam_path: Path, logger: StructuredLogger) -> None:
    """Performs 'Fail-Fast' validation of the beam path.

    Args:
        beam_path (Path): Path to validate.
        logger (StructuredLogger): Logger instance for error reporting.

    Raises:
        ValueError: If the path is invalid or inaccessible.
    """
    if not beam_path.exists():
        logger.error(
            f"Validation failed: Beam path does not exist: {beam_path}")
        raise ValueError(f"Beam path does not exist: {beam_path}")
    if not beam_path.is_dir():
        logger.error(
            f"Validation failed: Beam path is not a directory: {beam_path}")
        raise ValueError(f"Beam path is not a directory: {beam_path}")


def submit_beam_worker(executor: ProcessPoolExecutor, beam_id: str,
                       beam_path: Path, settings: Settings,
                       active_futures: Dict, logger: StructuredLogger) -> None:
    """Submits a beam worker and tracks its future.

    Args:
        executor (ProcessPoolExecutor): The executor to submit to.
        beam_id (str): The beam ID.
        beam_path (Path): The beam path.
        settings (Settings): Application settings.
        active_futures (Dict): Dictionary tracking active futures.
        logger (StructuredLogger): Logger instance.
    """
    logger.info(f"Submitting beam worker for: {beam_id}")
    future = executor.submit(worker_main,
                            beam_id=beam_id,
                            beam_path=beam_path,
                            settings=settings)
    active_futures[future] = beam_id


def monitor_completed_workers(active_futures: Dict, pending_beams_by_case: Dict,
                              executor: ProcessPoolExecutor, settings: Settings,
                              logger: StructuredLogger) -> None:
    """Monitors and handles completed worker futures.

    Args:
        active_futures (Dict): Dictionary tracking active worker futures.
        pending_beams_by_case (Dict): Dictionary tracking pending beams by case_id.
        executor (ProcessPoolExecutor): The executor for dispatching workers.
        settings (Settings): Application settings.
        logger (StructuredLogger): Logger instance.
    """
    completed_futures = []
    for future in as_completed(active_futures.keys(), timeout=0.1):
        completed_futures.append(future)

    for future in completed_futures:
        beam_id = active_futures.pop(future)
        try:
            future.result()  # Raise exception if worker failed
            logger.info(f"Beam worker {beam_id} completed successfully")

            # Check if there are pending beams waiting for GPUs
            if pending_beams_by_case:
                try_allocate_pending_beams(pending_beams_by_case, executor,
                                          active_futures, settings, logger)

        except Exception as e:
            logger.error(f"Beam worker {beam_id} failed", {"error": str(e)})


def try_allocate_pending_beams(pending_beams_by_case: Dict, executor: ProcessPoolExecutor,
                               active_futures: Dict, settings: Settings,
                               logger: StructuredLogger) -> None:
    """Attempts to allocate GPUs for pending beams and dispatch workers.

    Args:
        pending_beams_by_case (Dict): Dictionary tracking pending beams by case_id.
        executor (ProcessPoolExecutor): The ProcessPoolExecutor for dispatching workers.
        active_futures (Dict): Dictionary tracking active worker futures.
        settings (Settings): Application settings.
        logger (StructuredLogger): Logger instance.
    """
    from src.core.dispatcher import allocate_gpus_for_pending_beams

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
            settings=settings
        )

        if new_gpu_assignments is None:
            logger.error(f"Error allocating GPUs for pending beams of case {case_id}")
            continue

        if not new_gpu_assignments:
            # No GPUs available yet, keep waiting
            continue

        # Update TPS file with new GPU assignments
        tps_generator = TpsGenerator(settings, logger)

        # We need to append to existing TPS file or regenerate it
        # For now, regenerate the TPS file with the new assignments
        num_allocated = len(new_gpu_assignments)
        jobs_to_dispatch = pending_jobs[:num_allocated]
        remaining_jobs = pending_jobs[num_allocated:]

        # Get output directory for TPS files
        from src.utils.db_context import get_db_session
        with get_db_session(settings, logger, handler_name="HpcJobSubmitter") as case_repo:
            beams = case_repo.get_beams_for_case(case_id)
            csv_output_base = settings.get_path("csv_output_dir", handler_name="CsvInterpreter")
            tps_output_dir = Path(csv_output_base) / case_id

            # Generate a separate TPS file for each newly allocated beam
            all_success = True
            for gpu_assignment, job in zip(new_gpu_assignments, jobs_to_dispatch):
                beam_id = job["beam_id"]
                # Find the beam data matching this beam_id
                beam_data = next((b for b in beams if b.beam_id == beam_id), None)
                if not beam_data:
                    logger.error(f"Could not find beam data for {beam_id}")
                    all_success = False
                    continue

                beam_gpu_assignment = [gpu_assignment]  # Single GPU for this beam
                # Use actual HpcJobSubmitter mode for execution_mode
                execution_mode = settings.get_handler_mode("HpcJobSubmitter")
                success = tps_generator.generate_tps_file_with_gpu_assignments(
                    case_path=case_path,
                    case_id=case_id,
                    gpu_assignments=beam_gpu_assignment,
                    execution_mode=execution_mode,
                    output_dir=tps_output_dir,
                    beam_name=beam_data.beam_id
                )

                if not success:
                    logger.error(f"Failed to update TPS file for beam {beam_id} of case {case_id}")
                    all_success = False

        if not all_success:
            logger.error(f"Failed to update TPS files for some pending beams of case {case_id}")
            continue

        logger.info(f"Allocated {num_allocated} additional GPUs for case {case_id}, dispatching workers")

        # Dispatch workers for newly allocated beams
        for job in jobs_to_dispatch:
            beam_id = job["beam_id"]
            beam_path = job["beam_path"]
            submit_beam_worker(executor, beam_id, beam_path, settings, active_futures, logger)

        # Update pending jobs list
        if remaining_jobs:
            pending_beams_by_case[case_id]["pending_jobs"] = remaining_jobs
        else:
            cases_to_remove.append(case_id)

    # Clean up completed cases
    for case_id in cases_to_remove:
        del pending_beams_by_case[case_id]
        logger.info(f"All beams for case {case_id} have been allocated")
