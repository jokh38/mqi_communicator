# =====================================================================================
# Target File: src/core/case_aggregator.py
# =====================================================================================
"""Case-level helpers and aggregation utilities.

- Aggregates beam statuses to a case status
- Queues cases and ensures logger availability
- Prepares beam jobs from a case folder using RT Plan validation
- Allocates GPUs for pending beams at case level
"""

import time
import multiprocessing as mp
from pathlib import Path
from typing import List, Dict, Any, Optional

from src.repositories.case_repo import CaseRepository
from src.repositories.gpu_repo import GpuRepository
from src.domain.enums import CaseStatus, BeamStatus, WorkflowStep
from src.domain.errors import ProcessingError
from src.infrastructure.logging_handler import StructuredLogger, LoggerFactory
from src.core.data_integrity_validator import DataIntegrityValidator
from src.utils.db_context import get_db_session


def update_case_status_from_beams(case_id: str, case_repo: CaseRepository, logger: StructuredLogger = None):
    """Checks the status of all beams for a given case and updates the parent case's status.

    - If all beams are COMPLETED, the case is COMPLETED.
    - If any beam is FAILED, the case is FAILED.
    - Otherwise, the case remains PROCESSING.

    Args:
        case_id (str): The ID of the parent case to check.
        case_repo (CaseRepository): An instance of the CaseRepository for database access.
        logger (StructuredLogger, optional): Logger instance. Falls back to case_repo.logger if not provided.
    """
    beams = case_repo.get_beams_for_case(case_id)

    if not beams:
        return

    log = logger if logger else case_repo.logger

    total_beams = len(beams)
    completed_beams = 0
    failed_beams = 0

    for beam in beams:
        if beam.status == BeamStatus.COMPLETED:
            completed_beams += 1
        elif beam.status == BeamStatus.FAILED:
            failed_beams += 1

    if failed_beams > 0:
        log.info(
            f"Case '{case_id}' has failed beams. Marking case as FAILED.",
            {"case_id": case_id, "failed_beams": failed_beams})
        case_repo.update_case_status(
            case_id, CaseStatus.FAILED,
            error_message=f"{failed_beams} beam(s) failed.")
    elif completed_beams == total_beams:
        log.info(
            f"All {total_beams} beams for case '{case_id}' are complete. "
            f"Marking case as COMPLETED.",
            {"case_id": case_id, "total_beams": total_beams})
        case_repo.update_case_status(case_id, CaseStatus.COMPLETED)
    else:
        try:
            progress = sum(max(0.0, min(100.0, getattr(b, "progress", 0.0))) for b in beams) / total_beams
        except Exception:
            progress = (completed_beams / total_beams) * 100
        case_repo.update_case_status(case_id, CaseStatus.PROCESSING, progress=progress)



def ensure_logger(name: str, settings) -> StructuredLogger:
    try:
        return LoggerFactory.get_logger(name)
    except RuntimeError:
        LoggerFactory.configure(settings)
        return LoggerFactory.get_logger(name)


def queue_case(
    case_id: str,
    case_path: Path,
    case_queue: mp.Queue,
    logger: StructuredLogger
) -> bool:
    """Queue a case for processing."""
    try:
        case_queue.put({
            'case_id': case_id,
            'case_path': str(case_path),
            'timestamp': time.time()
        })
        logger.info(f"Case {case_id} queued for processing")
        return True
    except Exception as e:
        logger.error(f"Failed to queue case {case_id}", {"error": str(e)})
        return False


def prepare_beam_jobs(
    case_id: str, case_path: Path, settings
) -> List[Dict[str, Any]]:
    """Scans a case directory for beams and returns a list of jobs to be processed by workers.

    Validates expected beams using RT Plan file, filters actual beam folders,
    and returns a list of {beam_id, beam_path} dicts.
    """
    logger = LoggerFactory.get_logger(f"dispatcher_{case_id}")
    beam_jobs: List[Dict[str, Any]] = []

    try:
        logger.info(f"Scanning for beams for case: {case_id}")
        validator = DataIntegrityValidator(logger)

        rtplan_path = validator.find_rtplan_file(case_path)
        if not rtplan_path:
            logger.error(f"No RT Plan file found in case directory: {case_path}")
            return []

        try:
            expected_beam_count = validator.parse_rtplan_beam_count(rtplan_path)
            logger.info(f"RT Plan indicates {expected_beam_count} treatment beams for case {case_id}")
        except ProcessingError as e:
            logger.error(f"Failed to parse RT Plan file: {str(e)}")
            return []

        if expected_beam_count == 0:
            logger.warning(f"RT plan indicates 0 beams for case {case_id}")
            return []

        dicom_parent_dir = rtplan_path.parent
        all_subdirs = [d for d in case_path.iterdir() if d.is_dir()]

        beam_folders = []
        for subdir in all_subdirs:
            if subdir == dicom_parent_dir:
                logger.debug(f"Excluding DICOM directory: {subdir.name}")
                continue

            has_ptn_files = list(subdir.glob("*.ptn"))
            matches_beam_naming = subdir.name.lower().startswith('beam_') or subdir.name.lower().startswith('field_')

            if has_ptn_files or matches_beam_naming:
                beam_folders.append(subdir)
                logger.debug(f"Identified beam folder: {subdir.name}")
            else:
                logger.debug(f"Excluding non-beam directory: {subdir.name}")

        actual_beam_count = len(beam_folders)
        logger.info(f"Found {actual_beam_count} actual beam folders after filtering")

        if actual_beam_count != expected_beam_count:
            logger.error(
                f"Data transfer incomplete or incorrect: Expected {expected_beam_count} beams "
                f"from RT Plan, but found {actual_beam_count} beam data folders"
            )
            return []

        for beam_path in beam_folders:
            beam_name = beam_path.name
            beam_id = f"{case_id}_{beam_name}"
            beam_jobs.append({"beam_id": beam_id, "beam_path": beam_path})

        logger.info(f"Successfully prepared {len(beam_jobs)} beam jobs for case: {case_id}")

        beam_info = DataIntegrityValidator(logger).get_beam_information(case_path)
        if beam_info.get("beam_count", 0) > 0:
            logger.info(
                f"RT plan information - Patient ID: {beam_info.get('patient_id')}, "
                f"Plan: {beam_info.get('plan_label')}, Expected beams: {beam_info.get('beam_count')}"
            )

    except Exception as e:
        logger.error("Failed to prepare beam jobs", {"case_id": case_id, "error": str(e)})
        return []

    return beam_jobs


def allocate_gpus_for_pending_beams(
    case_id: str, num_pending_beams: int, settings
) -> Optional[List[Dict[str, Any]]]:
    """Attempts to allocate GPUs for pending beams of a case."""

    logger = LoggerFactory.get_logger(f"gpu_allocator_{case_id}")
    handler_name = "CsvInterpreter"

    try:
        with get_db_session(settings, logger, handler_name=handler_name) as case_repo:
            gpu_repo = GpuRepository(case_repo.db, logger, settings)

            available_gpu_count = gpu_repo.get_available_gpu_count()
            gpus_to_allocate = min(num_pending_beams, available_gpu_count)

            if gpus_to_allocate == 0:
                logger.debug(f"No GPUs available for pending beams of case {case_id}")
                return []

            logger.info(f"Attempting to allocate {gpus_to_allocate} GPUs for pending beams of case {case_id}")
            gpu_assignments = gpu_repo.find_and_lock_multiple_gpus(
                case_id=case_id,
                num_gpus=gpus_to_allocate
            )

            if gpu_assignments:
                logger.info(f"Successfully allocated {len(gpu_assignments)} GPUs for pending beams of case {case_id}")

            return gpu_assignments if gpu_assignments else []

    except Exception as e:
        logger.error(f"Failed to allocate GPUs for pending beams of case {case_id}", {"error": str(e)})
        return None
