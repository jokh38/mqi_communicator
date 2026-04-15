# =====================================================================================
# Target File: src/core/worker.py
# Source Reference: src/worker.py, src/main.py
# =====================================================================================
"""Main entry point for a worker process that handles a single beam."""

import csv
import subprocess
from io import StringIO
from pathlib import Path
from typing import Dict, Set
from concurrent.futures import ProcessPoolExecutor, as_completed

from src.repositories.gpu_repo import GpuRepository
from src.handlers.execution_handler import ExecutionHandler
from src.infrastructure.logging_handler import StructuredLogger, LoggerFactory
from src.core.case_aggregator import allocate_gpus_for_pending_beams, update_case_status_from_beams
from src.core.workflow_manager import WorkflowManager
from src.core.tps_generator import TpsGenerator
from src.config.settings import Settings
from src.utils.db_context import get_db_session
from src.domain.enums import BeamStatus


def _query_busy_gpu_uuids(logger: StructuredLogger) -> Set[str]:
    """Return UUIDs of GPUs that currently have live compute apps.

    Used as a physical-idleness gate before launching a new moqui_SMC
    invocation: moqui aborts with "No free GPU remains after
    BeamLayerMultiGpu filtering" when another tps_env process is still
    holding the device, even if our DB thinks the GPU is free.
    """
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-compute-apps=pid,gpu_uuid,process_name,used_memory",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warning(
            "nvidia-smi compute-apps query failed; assuming GPUs are idle",
            {"error": str(exc)},
        )
        return set()

    if result.returncode != 0:
        logger.warning(
            "nvidia-smi compute-apps query returned non-zero exit",
            {"return_code": result.returncode, "stderr": (result.stderr or "").strip()},
        )
        return set()

    busy_uuids: Set[str] = set()
    for row in csv.reader(StringIO(result.stdout or "")):
        if len(row) < 2:
            continue
        gpu_uuid = row[1].strip()
        if gpu_uuid:
            busy_uuids.add(gpu_uuid)
    return busy_uuids


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
            gpu_repo = GpuRepository(case_repo.db, logger, settings)

            execution_handler = ExecutionHandler(settings=settings)

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


def _release_beam_gpu_assignment(beam_id: str, settings: Settings,
                                 logger: StructuredLogger) -> None:
    """Release any GPU reservation currently held by a beam."""
    try:
        with get_db_session(settings, logger) as case_repo:
            gpu_repo = GpuRepository(case_repo.db, logger, settings)
            gpu_repo.release_all_for_case(beam_id)
    except Exception as e:
        logger.error(f"Failed to release GPU assignment for beam {beam_id}", {"error": str(e)})


def _mark_beam_failed(beam_id: str, error: Exception, settings: Settings, logger: StructuredLogger) -> None:
    """Persist worker bootstrap failures that happen before workflow error handling exists."""
    try:
        with get_db_session(settings, logger) as case_repo:
            error_message = f"Worker failed before workflow completion: {error}"
            case_repo.update_beam_status(beam_id, BeamStatus.FAILED, error_message=error_message)
            beam = case_repo.get_beam(beam_id)
            if beam:
                update_case_status_from_beams(beam.parent_case_id, case_repo, logger)
    except Exception as db_error:
        logger.error(
            f"Failed to persist worker failure for beam {beam_id}",
            {"error": str(db_error), "original_error": str(error)},
        )


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
    completed_futures = [
        future for future in list(active_futures.keys()) if future.done()
    ]

    if not completed_futures:
        return

    for future in completed_futures:
        beam_id = active_futures.pop(future)
        try:
            future.result()  # Raise exception if worker failed
            _release_beam_gpu_assignment(beam_id, settings, logger)
            with get_db_session(settings, logger) as case_repo:
                beam = case_repo.get_beam(beam_id)

            if beam and beam.status == BeamStatus.FAILED:
                logger.warning(f"Beam worker {beam_id} finished but beam FAILED")
            else:
                logger.info(f"Beam worker {beam_id} completed successfully")

            # Check if there are pending beams waiting for GPUs
            if pending_beams_by_case:
                try_allocate_pending_beams(pending_beams_by_case, executor,
                                          active_futures, settings, logger)

        except Exception as e:
            _release_beam_gpu_assignment(beam_id, settings, logger)
            _mark_beam_failed(beam_id, e, settings, logger)
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
    cases_to_remove = []

    runtime_config = settings.get_moqui_runtime_config()
    if not isinstance(runtime_config, dict):
        runtime_config = {}
    multigpu_enabled = runtime_config.get("multigpu_enabled", False)
    beam_uses_all_available_gpus = runtime_config.get("beam_uses_all_available_gpus", False)

    # In beam-by-beam multigpu mode the next beam must wait until every GPU
    # is physically idle.  The DB may say a GPU is IDLE while moqui_SMC is
    # still holding it (DB-level locking drifts from hardware), in which case
    # launching the next beam immediately produces the
    # "No free GPU remains after BeamLayerMultiGpu filtering" crash.  Gate
    # the entire pending allocation step on a nvidia-smi check here; if any
    # compute app is still running, skip this pass and try again next tick.
    if multigpu_enabled and beam_uses_all_available_gpus and pending_beams_by_case:
        busy_uuids = _query_busy_gpu_uuids(logger)
        if busy_uuids:
            logger.debug(
                "Deferring pending beam dispatch: GPUs still running compute apps",
                {"busy_gpu_count": len(busy_uuids)},
            )
            return

    for case_id, pending_data in list(pending_beams_by_case.items()):
        pending_jobs = pending_data["pending_jobs"]
        case_path = pending_data["case_path"]

        if not pending_jobs:
            cases_to_remove.append(case_id)
            continue

        # Try to allocate GPUs for pending beams.
        # In beam-by-beam multigpu mode every sequential beam must get all
        # available GPUs (not just one per pending beam).
        use_all_available = multigpu_enabled and beam_uses_all_available_gpus
        new_gpu_assignments = allocate_gpus_for_pending_beams(
            case_id=case_id,
            num_pending_beams=len(pending_jobs),
            settings=settings,
            use_all_available=use_all_available,
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
        if multigpu_enabled and beam_uses_all_available_gpus:
            jobs_to_dispatch = pending_jobs[:1]
            remaining_jobs = pending_jobs[1:]
        else:
            num_allocated = len(new_gpu_assignments)
            jobs_to_dispatch = pending_jobs[:num_allocated]
            remaining_jobs = pending_jobs[num_allocated:]

        # Get output directory for TPS files
        from src.utils.db_context import get_db_session
        with get_db_session(settings, logger, handler_name="HpcJobSubmitter") as case_repo:
            beams = case_repo.get_beams_for_case(case_id)
            csv_output_base = settings.get_path("csv_output_dir", handler_name="CsvInterpreter")
            tps_output_dir = Path(csv_output_base) / case_id

            gpu_repo_inst = GpuRepository(case_repo.db, logger, settings)

            # Generate a separate TPS file for each newly allocated beam
            all_success = True
            execution_mode = settings.get_handler_mode("HpcJobSubmitter")
            if multigpu_enabled and beam_uses_all_available_gpus:
                job = jobs_to_dispatch[0]
                beam_id = job["beam_id"]
                beam_data = next((b for b in beams if b.beam_id == beam_id), None)
                if not beam_data:
                    logger.error(f"Could not find beam data for {beam_id}")
                    all_success = False
                else:
                    beam_number = beam_data.beam_number
                    if beam_number is None:
                        logger.error(f"Beam number missing for {beam_id}")
                        all_success = False
                    else:
                        for gpu_assignment in new_gpu_assignments:
                            gpu_repo_inst.assign_gpu_to_case(gpu_assignment["gpu_uuid"], beam_id)
                        success = tps_generator.generate_tps_file_with_gpu_assignments(
                            case_path=case_path,
                            case_id=case_id,
                            gpu_assignments=new_gpu_assignments,
                            execution_mode=execution_mode,
                            output_dir=tps_output_dir,
                            beam_name=beam_data.beam_id,
                            beam_number=beam_number
                        )
                        if not success:
                            logger.error(f"Failed to update TPS file for beam {beam_id} of case {case_id}")
                            all_success = False
            else:
                for gpu_assignment, job in zip(new_gpu_assignments, jobs_to_dispatch):
                    beam_id = job["beam_id"]
                    beam_data = next((b for b in beams if b.beam_id == beam_id), None)
                    if not beam_data:
                        logger.error(f"Could not find beam data for {beam_id}")
                        all_success = False
                        continue

                    beam_number = beam_data.beam_number
                    if beam_number is None:
                        logger.error(f"Beam number missing for {beam_id}")
                        all_success = False
                        continue

                    gpu_repo_inst.assign_gpu_to_case(gpu_assignment["gpu_uuid"], beam_id)

                    beam_gpu_assignment = [gpu_assignment]
                    success = tps_generator.generate_tps_file_with_gpu_assignments(
                        case_path=case_path,
                        case_id=case_id,
                        gpu_assignments=beam_gpu_assignment,
                        execution_mode=execution_mode,
                        output_dir=tps_output_dir,
                        beam_name=beam_data.beam_id,
                        beam_number=beam_number
                    )

                    if not success:
                        logger.error(f"Failed to update TPS file for beam {beam_id} of case {case_id}")
                        all_success = False

        if not all_success:
            logger.error(f"Failed to update TPS files for some pending beams of case {case_id}")
            continue

        logger.info(f"Allocated {len(new_gpu_assignments)} additional GPUs for case {case_id}, dispatching workers")

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
