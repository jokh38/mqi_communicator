# =====================================================================================
# Target File: src/core/case_aggregator.py
# =====================================================================================
"""Case-level helpers and aggregation utilities.

- Aggregates beam statuses to a case status
- Queues cases and ensures logger availability
- Prepares beam jobs from a case folder using RT Plan validation
- Allocates GPUs for pending beams at case level
"""

import re
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


def _normalize_beam_identifier(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _extract_numeric_suffix(value: str) -> Optional[int]:
    match = re.search(r"(\d+)$", value)
    return int(match.group(1)) if match else None


def _extract_planinfo_beam_number(beam_path: Path) -> Optional[int]:
    planinfo_path = beam_path / "PlanInfo.txt"
    if not planinfo_path.exists():
        return None

    try:
        for raw_line in planinfo_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            key, separator, value = line.partition(",")
            if separator and key.strip() == "DICOM_BEAM_NUMBER":
                return int(value.strip())
    except (OSError, ValueError):
        return None

    return None


def _parse_required_planinfo(beam_path: Path) -> Optional[Dict[str, Any]]:
    planinfo_path = beam_path / "PlanInfo.txt"
    if not planinfo_path.exists():
        return None

    values: Dict[str, str] = {}
    try:
        for raw_line in planinfo_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            key, separator, value = line.partition(",")
            if not separator:
                continue
            values[key.strip()] = value.strip()
    except OSError:
        return None

    patient_id = values.get("DICOM_PATIENT_ID")
    beam_number_raw = values.get("DICOM_BEAM_NUMBER")
    if not patient_id or beam_number_raw is None:
        return None

    try:
        beam_number = int(beam_number_raw)
    except ValueError:
        return None

    return {
        "patient_id": patient_id,
        "beam_number": beam_number,
    }


def _match_metadata_by_raw_beam_number(
    raw_beam_number: int, beam_metadata: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    numbered_matches = [
        metadata
        for metadata in beam_metadata
        if metadata.get("beam_number") == raw_beam_number
    ]
    if len(numbered_matches) == 1:
        return numbered_matches[0]
    return None


def _map_beam_folder_to_metadata(
    beam_path: Path, beam_metadata: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    folder_name = beam_path.name
    folder_key = _normalize_beam_identifier(folder_name)
    folder_number = _extract_numeric_suffix(folder_name)
    planinfo_beam_number = _extract_planinfo_beam_number(beam_path)

    if planinfo_beam_number is not None:
        numbered_matches = [
            metadata
            for metadata in beam_metadata
            if metadata.get("beam_number") == planinfo_beam_number
        ]
        if len(numbered_matches) == 1:
            return numbered_matches[0]

    if folder_number is not None:
        numbered_matches = [
            metadata for metadata in beam_metadata if metadata.get("beam_number") == folder_number
        ]
        if len(numbered_matches) == 1:
            return numbered_matches[0]

    exact_matches = [
        metadata
        for metadata in beam_metadata
        if _normalize_beam_identifier(str(metadata.get("beam_name", ""))) == folder_key
    ]
    if len(exact_matches) == 1:
        return exact_matches[0]

    fuzzy_matches = [
        metadata
        for metadata in beam_metadata
        if folder_key in _normalize_beam_identifier(str(metadata.get("beam_name", "")))
        or _normalize_beam_identifier(str(metadata.get("beam_name", ""))) in folder_key
    ]
    if len(fuzzy_matches) == 1:
        return fuzzy_matches[0]

    return None


def _resolve_treatment_beam_index(
    matched_metadata: Dict[str, Any], beam_metadata: List[Dict[str, Any]]
) -> Optional[int]:
    matched_beam_number = matched_metadata.get("beam_number")

    if matched_beam_number is not None:
        numbered_matches = [
            index
            for index, metadata in enumerate(beam_metadata, start=1)
            if metadata.get("beam_number") == matched_beam_number
        ]
        if len(numbered_matches) == 1:
            return numbered_matches[0]

    matched_name = _normalize_beam_identifier(str(matched_metadata.get("beam_name", "")))
    if matched_name:
        named_matches = [
            index
            for index, metadata in enumerate(beam_metadata, start=1)
            if _normalize_beam_identifier(str(metadata.get("beam_name", ""))) == matched_name
        ]
        if len(named_matches) == 1:
            return named_matches[0]

    return None


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

        beam_folders.sort(key=lambda path: path.name)

        actual_beam_count = len(beam_folders)
        logger.info(f"Found {actual_beam_count} actual beam folders after filtering")

        if actual_beam_count != expected_beam_count:
            logger.error(
                f"Data transfer incomplete or incorrect: Expected {expected_beam_count} beams "
                f"from RT Plan, but found {actual_beam_count} beam data folders"
            )
            return []

        beam_info = validator.get_beam_information(case_path)
        treatment_beams = beam_info.get("beams", [])
        rtplan_patient_id = str(beam_info.get("patient_id", "")).strip()
        if len(treatment_beams) != actual_beam_count:
            logger.error(
                "Beam metadata count mismatch during beam preparation",
                {"expected": actual_beam_count, "metadata_count": len(treatment_beams)},
            )
            return []

        unresolved_folders = []
        seen_planinfo_beam_numbers = set()

        for beam_path in beam_folders:
            beam_name = beam_path.name
            beam_id = f"{case_id}_{beam_name}"
            planinfo = _parse_required_planinfo(beam_path)
            if not planinfo:
                logger.error(
                    "Missing or invalid required PlanInfo data",
                    {"beam_folder": beam_name},
                )
                return []

            if rtplan_patient_id and planinfo["patient_id"] != rtplan_patient_id:
                logger.error(
                    "PlanInfo patient ID does not match RT plan patient ID",
                    {
                        "beam_folder": beam_name,
                        "planinfo_patient_id": planinfo["patient_id"],
                        "rtplan_patient_id": rtplan_patient_id,
                    },
                )
                return []

            raw_beam_number = int(planinfo["beam_number"])
            if raw_beam_number in seen_planinfo_beam_numbers:
                logger.error(
                    "Duplicate DICOM beam number found across PlanInfo files",
                    {"beam_folder": beam_name, "beam_number": raw_beam_number},
                )
                return []
            seen_planinfo_beam_numbers.add(raw_beam_number)

            matched_metadata = _match_metadata_by_raw_beam_number(
                raw_beam_number, treatment_beams
            )
            treatment_beam_index = None
            if matched_metadata:
                treatment_beam_index = _resolve_treatment_beam_index(
                    matched_metadata, treatment_beams
                )

            if not matched_metadata or treatment_beam_index is None:
                unresolved_folders.append(beam_name)
                continue
            beam_jobs.append(
                {
                    "beam_id": beam_id,
                    "beam_path": beam_path,
                    "beam_number": int(treatment_beam_index),
                }
            )

        if unresolved_folders:
            logger.error(
                "Failed to map one or more beam folders to RT Plan beam metadata",
                {"unresolved_folders": unresolved_folders},
            )
            return []

        # Sort beam_jobs by beam_number to match database ordering (W-3 enhancement)
        # This ensures beam_jobs order matches the GPU assignments from dispatcher
        beam_jobs.sort(key=lambda job: job["beam_number"])

        logger.info(f"Successfully prepared {len(beam_jobs)} beam jobs for case: {case_id}")

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
