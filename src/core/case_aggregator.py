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
import hashlib
import multiprocessing as mp
from pathlib import Path
from typing import List, Dict, Any, Optional

from src.repositories.case_repo import CaseRepository
from src.repositories.gpu_repo import GpuRepository
from src.domain.enums import CaseStatus, BeamStatus
from src.domain.errors import ProcessingError
from src.infrastructure.logging_handler import StructuredLogger, LoggerFactory
from src.core.retry_policy import (
    PERMANENT_ERROR_PREFIX,
    RETRYABLE_ERROR_PREFIX,
    mark_permanent_error_message,
    mark_retryable_error_message,
)
from src.core.data_integrity_validator import DataIntegrityValidator
from src.core.fraction_grouper import (
    CaseDeliveryResult,
    group_deliveries_into_fractions,
    select_reference_fraction,
    get_fraction_event_logger,
    log_fraction_event,
)
from src.utils.db_context import get_db_session
from src.utils.planinfo import (
    parse_delivery_timestamp as _parse_delivery_timestamp,
    parse_planinfo_beam_number,
)


def _normalize_beam_identifier(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _extract_numeric_suffix(value: str) -> Optional[int]:
    match = re.search(r"(\d+)$", value)
    return int(match.group(1)) if match else None


def _extract_planinfo_beam_number(beam_path: Path) -> Optional[int]:
    return parse_planinfo_beam_number(beam_path)


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


def _delivery_id(case_id: str, delivery_path: Path) -> str:
    digest = hashlib.sha1(str(delivery_path).encode("utf-8")).hexdigest()[:12]
    return f"{case_id}_delivery_{digest}"


def _select_fallback_reference_fraction(fractions):
    for fraction in fractions:
        if fraction.status == "partial":
            return fraction
    for fraction in fractions:
        if fraction.status == "anomaly":
            return fraction
    return None


def prepare_case_delivery_data(
    case_id: str, case_path: Path, settings
) -> CaseDeliveryResult:
    """Prepare grouped delivery information for simulation and PTN analysis."""
    logger = LoggerFactory.get_logger(f"dispatcher_{case_id}")
    delivery_records: List[Dict[str, Any]] = []
    fraction_logger = get_fraction_event_logger()

    logger.info(f"Scanning for beams for case: {case_id}")
    validator = DataIntegrityValidator(logger)

    rtplan_path = validator.find_rtplan_file(case_path)
    if not rtplan_path:
        logger.error(f"No RT Plan file found in case directory: {case_path}")
        return CaseDeliveryResult(
            beam_jobs=[],
            delivery_records=[],
            fractions=[],
            status="ready",
            pending_reason="missing_rtplan",
        )

    try:
        expected_beam_count = validator.parse_rtplan_beam_count(rtplan_path)
        logger.info(f"RT Plan indicates {expected_beam_count} treatment beams for case {case_id}")
    except ProcessingError as e:
        logger.error(f"Failed to parse RT Plan file: {str(e)}")
        return CaseDeliveryResult(
            beam_jobs=[],
            delivery_records=[],
            fractions=[],
            status="ready",
            pending_reason="rtplan_parse_failed",
        )

    if expected_beam_count == 0:
        logger.warning(f"RT plan indicates 0 beams for case {case_id}")
        return CaseDeliveryResult(
            beam_jobs=[],
            delivery_records=[],
            fractions=[],
            status="ready",
            pending_reason="zero_expected_beams",
            expected_beam_count=expected_beam_count,
        )

    dicom_parent_dir = rtplan_path.parent
    all_subdirs = [d for d in case_path.iterdir() if d.is_dir()]

    delivery_folders = []
    for subdir in all_subdirs:
        if subdir == dicom_parent_dir:
            continue
        has_ptn_files = list(subdir.glob("*.ptn"))
        has_planinfo = (subdir / "PlanInfo.txt").exists()
        if has_ptn_files or has_planinfo:
            delivery_folders.append(subdir)

    delivery_folders.sort(key=lambda path: path.name)
    if not delivery_folders:
        logger.error(f"No delivery folders found in case directory: {case_path}")
        return CaseDeliveryResult(
            beam_jobs=[],
            delivery_records=[],
            fractions=[],
            status="ready",
            pending_reason="missing_deliveries",
            expected_beam_count=expected_beam_count,
        )

    fractions = group_deliveries_into_fractions(
        delivery_folders, expected_beam_count=expected_beam_count
    )
    if any(fraction.status == "pending" for fraction in fractions):
        logger.info(
            "Case has pending fractions; deferring processing",
            {"case_id": case_id, "fractions": len(fractions)},
        )
        return CaseDeliveryResult(
            beam_jobs=[],
            delivery_records=[],
            fractions=fractions,
            status="pending",
            pending_reason="fraction_window_open",
            expected_beam_count=expected_beam_count,
        )

    beam_info = validator.get_beam_information(case_path)
    treatment_beams = beam_info.get("beams", [])
    rtplan_patient_id = str(beam_info.get("patient_id", "")).strip()
    if len(treatment_beams) != expected_beam_count:
        logger.error(
            "Beam metadata count mismatch during delivery preparation",
            {"expected": expected_beam_count, "metadata_count": len(treatment_beams)},
        )
        return CaseDeliveryResult(
            beam_jobs=[],
            delivery_records=[],
            fractions=fractions,
            status="ready",
            pending_reason="beam_metadata_mismatch",
            expected_beam_count=expected_beam_count,
        )

    reference_fraction = select_reference_fraction(fractions)
    if reference_fraction is None:
        reference_fraction = _select_fallback_reference_fraction(fractions)
        if reference_fraction is not None:
            log_fraction_event(
                logger,
                fraction_logger,
                "partial_reference_used",
                "No complete fraction available; using earliest partial fraction as reference",
                {
                    "case_id": case_id,
                    "fraction_index": reference_fraction.index,
                    "expected_beam_count": expected_beam_count,
                    "delivered_count": len(reference_fraction.delivery_folders),
                    "beam_numbers": reference_fraction.beam_numbers,
                },
            )

    if reference_fraction is None:
        logger.error(
            "No complete or partial fraction available for reference selection",
            {"case_id": case_id, "fraction_count": len(fractions)},
        )
        return CaseDeliveryResult(
            beam_jobs=[],
            delivery_records=[],
            fractions=fractions,
            status="ready",
            pending_reason="no_reference_fraction",
            expected_beam_count=expected_beam_count,
        )

    reference_fraction_paths = {path.resolve() for path in reference_fraction.delivery_folders}
    reference_by_treatment_index: Dict[int, Dict[str, Any]] = {}
    unresolved_folders = []

    for fraction in fractions:
        duplicate_beam_numbers = sorted(
            {beam_number for beam_number in fraction.beam_numbers if beam_number and fraction.beam_numbers.count(beam_number) > 1}
        )
        if duplicate_beam_numbers:
            log_fraction_event(
                logger,
                fraction_logger,
                "duplicate_beam_number",
                "Duplicate beam number detected within fraction",
                {
                    "case_id": case_id,
                    "fraction_index": fraction.index,
                    "duplicate_beam_numbers": duplicate_beam_numbers,
                    "delivery_folders": [str(path) for path in fraction.delivery_folders],
                },
            )
        if fraction.status == "anomaly":
            log_fraction_event(
                logger,
                fraction_logger,
                "anomaly_fraction",
                "Fraction contains more delivered folders than expected beams",
                {
                    "case_id": case_id,
                    "fraction_index": fraction.index,
                    "expected_beam_count": expected_beam_count,
                    "delivered_count": len(fraction.delivery_folders),
                    "beam_numbers": fraction.beam_numbers,
                },
            )

    for fraction in fractions:
        for delivery_path in fraction.delivery_folders:
            planinfo = _parse_required_planinfo(delivery_path)
            if not planinfo:
                # PlanInfo.txt missing or invalid — try folder-based inference
                matched_metadata = _map_beam_folder_to_metadata(delivery_path, treatment_beams)
                if matched_metadata:
                    inferred_beam_number = matched_metadata.get("beam_number")
                    planinfo = {
                        "patient_id": rtplan_patient_id,
                        "beam_number": int(inferred_beam_number) if inferred_beam_number is not None else 0,
                    }
                    logger.warning(
                        "PlanInfo.txt missing; inferred beam from folder metadata",
                        {
                            "delivery_folder": delivery_path.name,
                            "inferred_beam_number": planinfo["beam_number"],
                        },
                    )
                else:
                    logger.warning(
                        "PlanInfo.txt missing and folder inference failed",
                        {"delivery_folder": delivery_path.name},
                    )
                    unresolved_folders.append(delivery_path.name)
                    continue

            if rtplan_patient_id and planinfo["patient_id"] != rtplan_patient_id:
                logger.error(
                    "PlanInfo patient ID does not match RT plan patient ID",
                    {
                        "delivery_folder": delivery_path.name,
                        "planinfo_patient_id": planinfo["patient_id"],
                        "rtplan_patient_id": rtplan_patient_id,
                    },
                )
                return CaseDeliveryResult(
                    beam_jobs=[],
                    delivery_records=[],
                    fractions=fractions,
                    status="ready",
                    pending_reason="patient_id_mismatch",
                    expected_beam_count=expected_beam_count,
                    error_detail=(
                        f"Delivery folder '{delivery_path.name}' patient ID "
                        f"'{planinfo['patient_id']}' does not match RT plan patient ID "
                        f"'{rtplan_patient_id}'"
                    ),
                )

            raw_beam_number = int(planinfo["beam_number"])

            # PlanInfo.txt DICOM_BEAM_NUMBER is the ground truth for beam
            # identity.  Resolve the treatment beam index directly from this
            # value — do not override with folder-based heuristics.
            matched_metadata = _match_metadata_by_raw_beam_number(
                raw_beam_number, treatment_beams
            )
            if matched_metadata:
                treatment_beam_index = _resolve_treatment_beam_index(
                    matched_metadata, treatment_beams
                )
            else:
                # Metadata lookup returned no unique match.  Compute the
                # treatment index directly from the raw beam number since
                # PlanInfo is authoritative.
                treatment_beam_index = None
                for idx, meta in enumerate(treatment_beams, start=1):
                    if meta.get("beam_number") == raw_beam_number:
                        treatment_beam_index = idx
                        break

                if treatment_beam_index is None:
                    logger.error(
                        "PlanInfo beam number not found in DICOM treatment beams",
                        {
                            "delivery_folder": delivery_path.name,
                            "planinfo_beam_number": raw_beam_number,
                            "dicom_beam_numbers": [
                                m.get("beam_number") for m in treatment_beams
                            ],
                        },
                    )

            if treatment_beam_index is None:
                unresolved_folders.append(delivery_path.name)
                continue

            delivery_timestamp = _parse_delivery_timestamp(delivery_path)
            if delivery_timestamp is None:
                logger.error(
                    "Could not determine delivery timestamp",
                    {"delivery_folder": delivery_path.name},
                )
                return CaseDeliveryResult(
                    beam_jobs=[],
                    delivery_records=[],
                    fractions=fractions,
                    status="ready",
                    pending_reason="missing_delivery_timestamp",
                    expected_beam_count=expected_beam_count,
                    error_detail=f"Delivery folder '{delivery_path.name}' is missing a valid irradiation timestamp",
                )

            beam_id = f"{case_id}_beam_{int(treatment_beam_index)}"
            record = {
                "delivery_id": _delivery_id(case_id, delivery_path),
                "beam_id": beam_id,
                "delivery_path": delivery_path,
                "delivery_timestamp": delivery_timestamp.isoformat(),
                "delivery_date": delivery_timestamp.strftime("%Y-%m-%d"),
                "raw_beam_number": raw_beam_number,
                "treatment_beam_index": int(treatment_beam_index),
                "is_reference_delivery": False,
                "fraction_index": fraction.index,
            }
            delivery_records.append(record)

            if delivery_path.resolve() in reference_fraction_paths:
                existing = reference_by_treatment_index.get(int(treatment_beam_index))
                if existing is None or delivery_timestamp < existing["timestamp"]:
                    reference_by_treatment_index[int(treatment_beam_index)] = {
                        "beam_id": beam_id,
                        "beam_path": delivery_path,
                        "beam_number": int(treatment_beam_index),
                        "timestamp": delivery_timestamp,
                    }

    if unresolved_folders:
        logger.error(
            "Failed to map one or more delivery folders to RT Plan beam metadata",
            {"unresolved_folders": unresolved_folders},
        )
        return CaseDeliveryResult(
            beam_jobs=[],
            delivery_records=[],
            fractions=fractions,
            status="ready",
            pending_reason="unresolved_folders",
            expected_beam_count=expected_beam_count,
            error_detail=(
                "Could not match delivery folders to RT plan beams: "
                + ", ".join(unresolved_folders)
            ),
        )

    if len(reference_by_treatment_index) != expected_beam_count:
        logger.error(
            "Reference fraction did not resolve to the expected unique treatment beam count",
            {
                "expected": expected_beam_count,
                "unique_delivered": len(reference_by_treatment_index),
                "reference_fraction_index": reference_fraction.index,
            },
        )
        return CaseDeliveryResult(
            beam_jobs=[],
            delivery_records=delivery_records,
            fractions=fractions,
            status="ready",
            pending_reason="reference_beam_count_mismatch",
            expected_beam_count=expected_beam_count,
            error_detail=(
                f"Reference fraction {reference_fraction.index} resolved "
                f"{len(reference_by_treatment_index)} unique treatment beams, expected {expected_beam_count}"
            ),
        )

    beam_jobs: List[Dict[str, Any]] = sorted(
        (
            {
                "beam_id": info["beam_id"],
                "beam_path": info["beam_path"],
                "beam_number": info["beam_number"],
            }
            for info in reference_by_treatment_index.values()
        ),
        key=lambda job: job["beam_number"],
    )

    reference_ids = {job["beam_id"]: str(job["beam_path"]) for job in beam_jobs}
    for record in delivery_records:
        if reference_ids.get(record["beam_id"]) == str(record["delivery_path"]):
            record["is_reference_delivery"] = True

    delivery_records.sort(
        key=lambda record: (
            record["delivery_timestamp"],
            record["treatment_beam_index"] or 0,
            str(record["delivery_path"]),
        )
    )
    logger.info(
        "Prepared reference beams and delivery records",
        {
            "case_id": case_id,
            "reference_beams": len(beam_jobs),
            "deliveries": len(delivery_records),
            "fractions": len(fractions),
        },
    )
    return CaseDeliveryResult(
        beam_jobs=beam_jobs,
        delivery_records=delivery_records,
        fractions=fractions,
        status="ready",
        expected_beam_count=expected_beam_count,
    )


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
    failed_beam_messages = []

    for beam in beams:
        if beam.status == BeamStatus.COMPLETED:
            completed_beams += 1
        elif beam.status == BeamStatus.FAILED:
            failed_beams += 1
            if getattr(beam, "error_message", None):
                failed_beam_messages.append(str(beam.error_message))

    if failed_beams > 0:
        log.info(
            f"Case '{case_id}' has failed beams. Marking case as FAILED.",
            {"case_id": case_id, "failed_beams": failed_beams})
        case_repo.update_case_status(
            case_id, CaseStatus.FAILED,
            error_message=_case_failure_message_from_beams(
                failed_beams, failed_beam_messages
            ))
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


def _case_failure_message_from_beams(
    failed_beams: int, failed_beam_messages: list[str]
) -> str:
    """Build a case-level failure message without losing retry classification."""
    if not failed_beam_messages:
        return f"{failed_beams} beam(s) failed."

    first_message = failed_beam_messages[0]
    if failed_beams == 1:
        return first_message

    message = f"{failed_beams} beam(s) failed. First failure: {first_message}"
    if any(text.startswith(PERMANENT_ERROR_PREFIX) for text in failed_beam_messages):
        return mark_permanent_error_message(message)
    if any(text.startswith(RETRYABLE_ERROR_PREFIX) for text in failed_beam_messages):
        return mark_retryable_error_message(message)
    return message



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
    logger: StructuredLogger,
    reason: Optional[str] = None,
) -> bool:
    """Queue a case for processing."""
    try:
        payload = {
            'case_id': case_id,
            'case_path': str(case_path),
            'timestamp': time.time()
        }
        if reason is not None:
            payload["reason"] = reason
        case_queue.put(payload)
        logger.info(f"Case {case_id} queued for processing")
        return True
    except Exception as e:
        logger.error(f"Failed to queue case {case_id}", {"error": str(e)})
        return False


def prepare_beam_jobs(
    case_id: str, case_path: Path, settings
) -> List[Dict[str, Any]]:
    """Return unique reference beam jobs for dose computation.

    When a case contains repeated daily deliveries, the earliest delivery for each
    treatment beam becomes the reference beam job for moqui_SMC.
    """
    try:
        return prepare_case_delivery_data(case_id, case_path, settings).beam_jobs
    except Exception as e:
        logger = LoggerFactory.get_logger(f"dispatcher_{case_id}")
        logger.error("Failed to prepare beam jobs", {"case_id": case_id, "error": str(e)})
        return []


def allocate_gpus_for_pending_beams(
    case_id: str,
    num_pending_beams: int,
    settings,
    requested_gpu_count: Optional[int] = None,
    use_all_available: bool = False,
) -> Optional[List[Dict[str, Any]]]:
    """Attempts to allocate GPUs for pending beams of a case.

    Args:
        case_id: The case identifier.
        num_pending_beams: Number of beams still waiting for GPU allocation.
        settings: Application settings.
        requested_gpu_count: Exact number of GPUs to allocate (capped at available).
            Ignored when ``use_all_available`` is True.
        use_all_available: When True, allocate every idle GPU regardless of
            ``num_pending_beams``.  Used by beam-by-beam multigpu mode so that
            each sequential beam gets all 10 GPUs via BeamLayerMultiGpu.
    """
    logger = LoggerFactory.get_logger(f"gpu_allocator_{case_id}")
    handler_name = "CsvInterpreter"

    try:
        with get_db_session(settings, logger, handler_name=handler_name) as case_repo:
            gpu_repo = GpuRepository(case_repo.db, logger, settings)

            available_gpu_count = gpu_repo.get_available_gpu_count()
            if use_all_available:
                gpus_to_allocate = available_gpu_count
            elif requested_gpu_count is not None:
                gpus_to_allocate = min(requested_gpu_count, available_gpu_count)
            else:
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
