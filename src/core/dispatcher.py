"""Contains logic for dispatching cases and beams for processing."""

from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

from src.config.settings import Settings
from src.handlers.execution_handler import ExecutionHandler
from src.infrastructure.logging_handler import StructuredLogger
from src.domain.enums import CaseStatus, WorkflowStep
from src.domain.errors import ProcessingError
from src.core.data_integrity_validator import DataIntegrityValidator
from src.infrastructure.logging_handler import LoggerFactory
from src.core.tps_generator import TpsGenerator
from src.repositories.gpu_repo import GpuRepository
from src.utils.db_context import get_db_session

from src.core.case_aggregator import ensure_logger as _ensure_logger
from src.core.case_aggregator import _normalize_beam_identifier
from src.core.case_aggregator import prepare_case_delivery_data
from src.core.workflow_manager import (
    scan_existing_cases,
    CaseDetectionHandler,
    derive_room_from_case_path,
)
from src.integrations.ptn_checker import PtnCheckerIntegration, PtnCheckerResult

def _resolve_treatment_beam_index_from_raw_number(
    raw_beam_number: int, beam_metadata: List[Dict[str, Any]]
) -> Optional[int]:
    numbered_matches = [
        index
        for index, metadata in enumerate(beam_metadata, start=1)
        if metadata.get("beam_number") == int(raw_beam_number)
    ]
    if len(numbered_matches) == 1:
        return numbered_matches[0]
    return None


def _resolve_raw_dicom_beam_number(beam: Any, beam_metadata: List[Dict[str, Any]]) -> Optional[int]:
    persisted_beam_number = getattr(beam, "beam_number", None)
    beam_candidates = []
    beam_path = getattr(beam, "beam_path", None)
    if beam_path:
        beam_candidates.append(Path(beam_path).name)

    beam_id = getattr(beam, "beam_id", "")
    if beam_id:
        beam_candidates.append(beam_id.split("_", 1)[-1])
        beam_candidates.append(beam_id)

    # Step 1: match by beam name/path against metadata
    for candidate in beam_candidates:
        candidate_key = _normalize_beam_identifier(candidate)
        for metadata in beam_metadata:
            metadata_key = _normalize_beam_identifier(str(metadata.get("beam_name", "")))
            if candidate_key == metadata_key:
                raw_number = metadata.get("beam_number")
                return int(raw_number) if raw_number is not None else None

    if persisted_beam_number is not None:
        persisted_int = int(persisted_beam_number)
        raw_numbers = [
            int(m["beam_number"])
            for m in beam_metadata
            if m.get("beam_number") is not None
        ]

        # Step 2: if persisted value IS a valid raw DICOM beam number, use it directly
        if persisted_int in raw_numbers:
            return persisted_int

        # Step 3: treat persisted value as a 1-indexed treatment beam position
        # and map to the corresponding raw DICOM beam number
        if 1 <= persisted_int <= len(beam_metadata):
            raw_number = beam_metadata[persisted_int - 1].get("beam_number")
            if raw_number is not None:
                return int(raw_number)

    return None


def _handle_dispatcher_error(
    case_id: str,
    error: Exception,
    workflow_step: WorkflowStep,
    settings: Settings,
    logger: StructuredLogger,
    handler_name: str = "CsvInterpreter"
) -> None:
    """Handle errors in dispatcher functions by logging and updating database status.

    Args:
        case_id: The case identifier
        error: The exception that occurred
        workflow_step: The workflow step where the error occurred
        settings: Application settings
        logger: Logger instance
        handler_name: Handler name for database session (default: CsvInterpreter)
    """
    logger.error(
        f"Error during {workflow_step.value}",
        {"case_id": case_id, "error": str(error)}
    )
    try:
        with get_db_session(settings, logger, handler_name=handler_name) as case_repo:
            case_repo.update_case_status(
                case_id,
                CaseStatus.FAILED,
                error_message=str(error)
            )
            case_repo.record_workflow_step(
                case_id=case_id,
                step=workflow_step,
                status="failed",
                metadata={"error": str(error)}
            )
    except Exception as db_e:
        logger.error(
            "Failed to update case status during error handling",
            {"case_id": case_id, "db_error": str(db_e)}
        )

def run_case_level_csv_interpreting(case_id: str, case_path: Path,
                                    settings: Settings) -> bool:
    """
    Runs the mqi_interpreter for the entire case to generate CSV files.

    Args:
        case_id (str): The ID of the case.
        case_path (Path): The file system path to the case directory.
        settings (Settings): The application settings object.

    Returns:
        bool: True if CSV interpreting was successful, False otherwise.
    """
    logger = _ensure_logger(f"dispatcher_{case_id}", settings)

    try:
        execution_handler = ExecutionHandler(settings=settings)

        with get_db_session(settings, logger, handler_name="CsvInterpreter") as case_repo:
            logger.info(f"Starting case-level CSV interpreting for: {case_id}")
            case_repo.record_workflow_step(
                case_id=case_id,
                step=WorkflowStep.CSV_INTERPRETING,
                status="started",
                metadata={"message": "Running mqi_interpreter for the whole case."})

            case_repo.update_case_status(
                case_id=case_id,
                status=CaseStatus.CSV_INTERPRETING,
                progress=10.0
            )

            # Resolve CSV output directory using settings path
            room = derive_room_from_case_path(case_path, settings)
            csv_output_base = settings.get_path("csv_output_dir", handler_name="CsvInterpreter")
            csv_output_dir = str(
                Path(csv_output_base) / room / case_id if room else Path(csv_output_base) / case_id
            )

            # Build command using config-defined Python and script paths
            python_exe = settings.get_executable("python", handler_name="CsvInterpreter")
            mqi_script = settings.get_executable("mqi_interpreter_script", handler_name="CsvInterpreter")
            mqi_interpreter_dir = settings.get_path("mqi_interpreter_dir", handler_name="CsvInterpreter")

            command = [
                str(python_exe),
                str(mqi_script),
                "--logdir",
                str(case_path),
                "--outputdir",
                str(csv_output_dir),
            ]

            result = execution_handler.execute_command(
                command,
                cwd=Path(mqi_interpreter_dir),
            )


            # Log command output
            if result.output:
                logger.info(f"CSV interpreter output for {case_id}", {"output": result.output})
            if result.error:
                logger.warning(f"CSV interpreter stderr for {case_id}", {"stderr": result.error})

            if not result.success:
                error_message = (
                    f"Case-level CSV interpreting (mqi_interpreter) failed for '{case_id}'. "
                    f"Error: {result.error}")
                raise ProcessingError(error_message)

            csv_files = list(Path(csv_output_dir).glob("**/*.csv"))

            csv_count = len(csv_files)

            if csv_count == 0:
                logger.warning(
                    f"No CSV files found in the output directory {csv_output_dir} "
                    f"after case-level CSV interpreting.")

            # Copy DICOM files to rtplan directory for TPS execution
            from src.core.data_integrity_validator import DataIntegrityValidator
            import shutil
            validator = DataIntegrityValidator(logger)
            rtplan_path = validator.find_rtplan_file(case_path)

            if rtplan_path:
                source_dicom_dir = rtplan_path.parent
                csv_output_path = Path(csv_output_dir)
                rtplan_target_dir = csv_output_path / "rtplan"
                rtplan_target_dir.mkdir(parents=True, exist_ok=True)

                # Copy all DICOM files from source to target
                for dicom_file in source_dicom_dir.glob("*"):
                    if dicom_file.is_file():
                        shutil.copy2(dicom_file, rtplan_target_dir / dicom_file.name)


                logger.info(f"Copied DICOM files to rtplan directory", {
                    "source": str(source_dicom_dir),
                    "target": str(rtplan_target_dir)
                })
            else:
                logger.warning(f"No RT Plan file found for case {case_id}, skipping DICOM copy")

            case_repo.mark_interpreter_completed(case_id)
            case_repo.update_case_status(
                case_id=case_id,
                status=CaseStatus.CSV_INTERPRETING,
                progress=25.0
            )

            logger.info(f"Case-level CSV interpreting completed for: {case_id} ({csv_count} CSV files generated)")
            case_repo.record_workflow_step(
                case_id=case_id,
                step=WorkflowStep.CSV_INTERPRETING,
                status="completed",
                metadata={
                    "message": "mqi_interpreter finished successfully",
                    "csv_files_generated": csv_count,
                    "execution_confirmed": True,
                    "exit_code": result.return_code
                })
            return True

    except Exception as e:
        _handle_dispatcher_error(
            case_id=case_id,
            error=e,
            workflow_step=WorkflowStep.CSV_INTERPRETING,
            settings=settings,
            logger=logger,
            handler_name="CsvInterpreter"
        )
        return False



def run_case_level_tps_generation(
    case_id: str, case_path: Path, beam_count: int, settings: Settings
) -> Optional[List[Dict[str, Any]]]:
    """Generates moqui_tps.in file at case level with dynamic GPU assignments."""
    logger = LoggerFactory.get_logger(f"tps_dispatcher_{case_id}")
    handler_name = "CsvInterpreter" # local handler for db path
    try:
        logger.info(f"Starting case-level TPS generation for case: {case_id}")

        with get_db_session(settings, logger, handler_name=handler_name) as case_repo:
            case_repo.db.init_db()
            gpu_repo = GpuRepository(case_repo.db, logger, settings)

            case_repo.record_workflow_step(
                case_id=case_id,
                step=WorkflowStep.TPS_GENERATION,
                status="started",
                metadata={"message": f"Generating TPS file with {beam_count} beam assignments."}
            )

            runtime_config = settings.get_moqui_runtime_config()
            if not isinstance(runtime_config, dict):
                runtime_config = {}
            multigpu_enabled = runtime_config.get("multigpu_enabled", False)
            beam_uses_all_available_gpus = runtime_config.get("beam_uses_all_available_gpus", False)

            # Check available GPUs and allocate what's possible
            available_gpu_count = gpu_repo.get_available_gpu_count()
            logger.info(f"Checking GPU availability for case {case_id}: {available_gpu_count} idle GPUs, {beam_count} requested")

            if multigpu_enabled and beam_uses_all_available_gpus:
                gpus_to_allocate = available_gpu_count
            else:
                gpus_to_allocate = min(beam_count, available_gpu_count)

            if gpus_to_allocate == 0:
                error_message = f"No GPUs available for case {case_id}. All beams will remain pending."
                logger.warning(error_message)
                case_repo.record_workflow_step(
                    case_id=case_id,
                    step=WorkflowStep.TPS_GENERATION,
                    status="pending",
                    metadata={
                        "message": error_message,
                        "beams_total": beam_count,
                        "beams_allocated": 0,
                        "beams_pending": beam_count
                    }
                )
                return []  # Return empty list, not None, to indicate partial success

            # In beam-by-beam multigpu mode every idle GPU is routed to the
            # first beam; only one beam leaves this call dispatched regardless
            # of how many GPUs were allocated.  The remaining beams (if any)
            # stay pending.  Guard against the underflow that used to produce
            # nonsensical "-6 beams will remain pending" log lines.
            if multigpu_enabled and beam_uses_all_available_gpus:
                beams_pending_after_dispatch = max(0, beam_count - 1)
            else:
                beams_pending_after_dispatch = max(0, beam_count - gpus_to_allocate)
            logger.info(
                f"Allocating {gpus_to_allocate} GPUs for case {case_id} "
                f"({beams_pending_after_dispatch} beams will remain pending)"
            )
            gpu_assignments = gpu_repo.find_and_lock_multiple_gpus(
                case_id=case_id,
                num_gpus=gpus_to_allocate
            )

            if not gpu_assignments:
                error_message = f"Failed to allocate GPUs for case {case_id} despite availability check"
                logger.error(error_message)
                case_repo.record_workflow_step(
                    case_id=case_id,
                    step=WorkflowStep.TPS_GENERATION,
                    status="failed",
                    metadata={"error": error_message}
                )
                return None

            # Assign first GPU to the case for UI display
            if gpu_assignments:
                case_repo.assign_gpu_to_case(case_id, gpu_assignments[0]["gpu_uuid"])

            # Get beam information to use beam names in TPS files
            beams = case_repo.get_beams_for_case(case_id)

            # Get actual treatment beam numbers from DICOM RT Plan (excluding setup beams)
            validator = DataIntegrityValidator(logger)
            beam_info = validator.get_beam_information(case_path)
            treatment_beam_numbers = validator.get_treatment_beam_numbers(case_path)

            if not treatment_beam_numbers:
                error_message = f"Failed to extract treatment beam numbers from DICOM RT Plan for case {case_id}"
                logger.error(error_message)
                case_repo.record_workflow_step(
                    case_id=case_id,
                    step=WorkflowStep.TPS_GENERATION,
                    status="failed",
                    metadata={"error": error_message}
                )
                return None

            if len(treatment_beam_numbers) != len(beams):
                error_message = (
                    f"Mismatch between DICOM beam count ({len(treatment_beam_numbers)}) "
                    f"and log folder beam count ({len(beams)}) for case {case_id}"
                )
                logger.error(error_message)
                case_repo.record_workflow_step(
                    case_id=case_id,
                    step=WorkflowStep.TPS_GENERATION,
                    status="failed",
                    metadata={"error": error_message}
                )
                return None

            beam_metadata = beam_info.get("beams", [])
            persisted_numbers = [
                int(beam.beam_number)
                for beam in beams
                if getattr(beam, "beam_number", None) is not None
            ]
            normalized_indices = list(range(1, len(beams) + 1))
            raw_treatment_numbers = [
                int(metadata["beam_number"])
                for metadata in beam_metadata
                if metadata.get("beam_number") is not None
            ]
            use_persisted_indices = (
                len(persisted_numbers) == len(beams)
                and sorted(persisted_numbers) == normalized_indices
            )
            use_raw_dicom_numbers = (
                len(persisted_numbers) == len(beams)
                and set(persisted_numbers) == set(raw_treatment_numbers)
            )
            for beam in beams:
                if use_persisted_indices:
                    beam_number = int(raw_treatment_numbers[int(beam.beam_number) - 1])
                elif use_raw_dicom_numbers and getattr(beam, "beam_number", None) is not None:
                    beam_number = int(beam.beam_number)
                else:
                    beam_number = _resolve_raw_dicom_beam_number(beam, beam_metadata)
                if beam_number is None:
                    raise ProcessingError(f"Could not resolve beam number for {beam.beam_id}")
                case_repo.update_beam_number(beam.beam_id, int(beam_number))
                beam.beam_number = int(beam_number)

            # Get output directory for TPS files
            room = derive_room_from_case_path(case_path, settings)
            csv_output_base = settings.get_path("csv_output_dir", handler_name="CsvInterpreter")
            tps_output_dir = Path(csv_output_base) / room / case_id if room else Path(csv_output_base) / case_id

            tps_generator = TpsGenerator(settings, logger)

            execution_mode = settings.get_handler_mode("HpcJobSubmitter")
            if multigpu_enabled and beam_uses_all_available_gpus:
                beam = beams[0]
                beam_number = beam.beam_number
                if beam_number is None:
                    raise ProcessingError(f"Beam number missing for {beam.beam_id}")

                for gpu_assignment in gpu_assignments:
                    gpu_repo.assign_gpu_to_case(gpu_assignment["gpu_uuid"], beam.beam_id)
                    gpu_assignment["beam_id"] = beam.beam_id

                success = tps_generator.generate_tps_file_with_gpu_assignments(
                    case_path=case_path,
                    case_id=case_id,
                    gpu_assignments=gpu_assignments,
                    execution_mode=execution_mode,
                    output_dir=tps_output_dir,
                    beam_name=beam.beam_id,
                    beam_number=beam_number,
                )

                if not success:
                    error_message = f"TPS file generation failed for beam {beam.beam_id} in case {case_id}"
                    logger.error(error_message)
                    for allocated_gpu in gpu_assignments:
                        gpu_uuid = allocated_gpu.get("gpu_uuid")
                        if gpu_uuid:
                            gpu_repo.release_gpu(gpu_uuid)
                    case_repo.record_workflow_step(
                        case_id=case_id,
                        step=WorkflowStep.TPS_GENERATION,
                        status="failed",
                        metadata={"error": error_message, "failed_beam": beam.beam_id}
                    )
                    return None
            else:
                for i, (gpu_assignment, beam) in enumerate(zip(gpu_assignments, beams[:len(gpu_assignments)])):
                    gpu_repo.assign_gpu_to_case(gpu_assignment["gpu_uuid"], beam.beam_id)
                    gpu_assignment["beam_id"] = beam.beam_id

                    beam_gpu_assignment = [gpu_assignment]
                    beam_number = beam.beam_number
                    if beam_number is None:
                        raise ProcessingError(f"Beam number missing for {beam.beam_id}")
                    success = tps_generator.generate_tps_file_with_gpu_assignments(
                        case_path=case_path,
                        case_id=case_id,
                        gpu_assignments=beam_gpu_assignment,
                        execution_mode=execution_mode,
                        output_dir=tps_output_dir,
                        beam_name=beam.beam_id,
                        beam_number=beam_number
                    )

                    if not success:
                        error_message = f"TPS file generation failed for beam {beam.beam_id} in case {case_id}"
                        logger.error(error_message)
                        for allocated_gpu in gpu_assignments:
                            gpu_uuid = allocated_gpu.get("gpu_uuid")
                            if gpu_uuid:
                                gpu_repo.release_gpu(gpu_uuid)
                        case_repo.record_workflow_step(
                            case_id=case_id,
                            step=WorkflowStep.TPS_GENERATION,
                            status="failed",
                            metadata={"error": error_message, "failed_beam": beam.beam_id}
                        )
                        return None

            allocated_count = len(gpu_assignments)
            # When beam_uses_all_available_gpus is on, len(gpu_assignments) is the
            # GPU count (e.g. 10) rather than the beam count.  Only the first beam
            # is actually dispatched, so expose that in the pending math instead
            # of letting beam_count - gpu_count go negative.
            if multigpu_enabled and beam_uses_all_available_gpus:
                beams_dispatched = 1 if allocated_count else 0
            else:
                beams_dispatched = allocated_count
            pending_count = max(0, beam_count - beams_dispatched)

            logger.info(f"Case-level TPS generation completed for: {case_id} ({allocated_count} allocated, {pending_count} pending)")
            case_repo.record_workflow_step(
                case_id=case_id,
                step=WorkflowStep.TPS_GENERATION,
                status="completed" if pending_count == 0 else "partial",
                metadata={
                    "message": f"Generated TPS file with {allocated_count} beam-to-GPU assignments ({pending_count} beams pending)",
                    "beams_total": beam_count,
                    "beams_allocated": allocated_count,
                    "beams_pending": pending_count,
                    "gpu_assignments": gpu_assignments
                }
            )

            return gpu_assignments

    except Exception as e:
        _handle_dispatcher_error(
            case_id=case_id,
            error=e,
            workflow_step=WorkflowStep.TPS_GENERATION,
            settings=settings,
            logger=logger,
            handler_name=handler_name
        )
        try:
            with get_db_session(settings, logger, handler_name=handler_name) as case_repo:
                gpu_repo = GpuRepository(case_repo.db, logger, settings)
                gpu_repo.release_all_for_case(case_id)
        except Exception as cleanup_error:
            logger.error("Failed to cleanup GPU allocations", {"error": str(cleanup_error)})
        return None


def create_ptn_checker_integration(settings: Settings) -> PtnCheckerIntegration:
    ptn_config = settings.get_ptn_checker_config()
    return PtnCheckerIntegration(
        ptn_checker_path=Path(ptn_config["path"]),
    )


def resolve_ptn_checker_output_dir(case_id: str, case_path: Path, settings: Settings) -> Path:
    room = derive_room_from_case_path(case_path, settings)
    return Path(
        settings.get_path(
            "ptn_checker_output_dir",
            handler_name="PostProcessor",
            case_id=case_id,
            room=room,
            room_path=f"{room}/" if room else "",
        )
    )


def _summarize_point_gamma_metrics(analysis_data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Build weighted delivery-level gamma metrics from PTN checker report data."""
    if not analysis_data:
        return {}

    total_points = 0
    weighted_pass = 0.0
    weighted_mean = 0.0
    gamma_max = None

    for beam_name, beam_data in analysis_data.items():
        if str(beam_name).startswith("_") or not isinstance(beam_data, dict):
            continue
        for layer in beam_data.get("layers", []):
            results = layer.get("results", {})
            evaluated_points = int(results.get("evaluated_point_count", 0) or 0)
            pass_rate = float(results.get("pass_rate", 0.0) or 0.0) * 100.0
            gamma_mean = float(results.get("gamma_mean", 0.0) or 0.0)
            layer_gamma_max = float(results.get("gamma_max", 0.0) or 0.0)

            total_points += evaluated_points
            weighted_pass += pass_rate * evaluated_points
            weighted_mean += gamma_mean * evaluated_points
            gamma_max = layer_gamma_max if gamma_max is None else max(gamma_max, layer_gamma_max)

    if total_points <= 0:
        return {}

    return {
        "gamma_pass_rate": weighted_pass / total_points,
        "gamma_mean": weighted_mean / total_points,
        "gamma_max": gamma_max,
        "evaluated_points": total_points,
    }


def run_case_level_ptn_analysis(
    case_id: str,
    case_path: Path,
    settings: Settings,
) -> PtnCheckerResult:
    """Run PTN checker for each delivery of a completed case and persist DB results."""
    logger = LoggerFactory.get_logger(f"ptn_dispatcher_{case_id}")
    validator = DataIntegrityValidator(logger)
    integration = create_ptn_checker_integration(settings)
    default_output_dir = resolve_ptn_checker_output_dir(case_id, case_path, settings)
    rtplan_path = validator.find_rtplan_file(case_path)
    with get_db_session(settings, logger, handler_name="CsvInterpreter") as case_repo:
        deliveries = list(case_repo.get_deliveries_for_case(case_id) or [])
        if not deliveries:
            prepared = prepare_case_delivery_data(case_id, case_path, settings)
            if prepared.status == "ready" and prepared.beam_jobs:
                case_repo.create_case_with_beams(case_id, str(case_path), prepared.beam_jobs)
                case_repo.create_or_update_deliveries(case_id, prepared.delivery_records)
                deliveries = list(case_repo.get_deliveries_for_case(case_id) or [])

        case_repo.record_workflow_step(
            case_id=case_id,
            step=WorkflowStep.POSTPROCESSING,
            status="started",
            metadata={"message": "Starting PTN checker analysis for delivery records."},
        )

        if rtplan_path is None:
            result = PtnCheckerResult(
                success=False,
                status_code="FAILED_NO_DICOM",
                error_message="No RTPLAN DICOM file available for PTN analysis",
                output_dir=default_output_dir,
            )
            case_repo.record_ptn_checker_result(
                case_id=case_id,
                status_code=result.status_code,
                last_run_at=datetime.now(),
                error_message=result.error_message,
            )
            case_repo.record_workflow_step(
                case_id=case_id,
                step=WorkflowStep.POSTPROCESSING,
                status="failed",
                error_message=result.error_message,
                metadata={
                    "message": "PTN checker analysis finished.",
                    "status_code": result.status_code,
                    "output_dir": str(default_output_dir),
                },
            )
            return result

        if not deliveries:
            ptn_files = validator.find_ptn_files(case_path)
            if ptn_files:
                fallback_result = integration.run_analysis(
                    log_dir=ptn_files[0].parent,
                    dcm_file=rtplan_path,
                    output_dir=default_output_dir,
                )
                metrics = _summarize_point_gamma_metrics(fallback_result.analysis_data)
                case_repo.record_ptn_checker_result(
                    case_id=case_id,
                    status_code=fallback_result.status_code,
                    last_run_at=datetime.now(),
                    error_message=fallback_result.error_message,
                )
                case_repo.record_workflow_step(
                    case_id=case_id,
                    step=WorkflowStep.POSTPROCESSING,
                    status="completed" if fallback_result.success else "failed",
                    error_message=fallback_result.error_message,
                    metadata={
                        "message": "PTN checker analysis finished via fallback single-log execution.",
                        "status_code": fallback_result.status_code,
                        "output_dir": str(fallback_result.output_dir or default_output_dir),
                        "gamma_pass_rate": metrics.get("gamma_pass_rate"),
                        "gamma_mean": metrics.get("gamma_mean"),
                    },
                )
                return fallback_result

            result = PtnCheckerResult(
                success=False,
                status_code="FAILED_NO_PTN",
                error_message=f"No delivery records or PTN logs found under {case_path}",
                output_dir=default_output_dir,
            )
            case_repo.record_ptn_checker_result(
                case_id=case_id,
                status_code=result.status_code,
                last_run_at=datetime.now(),
                error_message=result.error_message,
            )
            case_repo.record_workflow_step(
                case_id=case_id,
                step=WorkflowStep.POSTPROCESSING,
                status="failed",
                error_message=result.error_message,
                metadata={
                    "message": "PTN checker analysis finished.",
                    "status_code": result.status_code,
                    "output_dir": str(default_output_dir),
                },
            )
            return result

        overall_status = "SUCCESS"
        overall_error = None
        overall_output_dir = default_output_dir
        success_count = 0
        for delivery in deliveries:
            result = integration.run_analysis(
                log_dir=delivery.delivery_path,
                dcm_file=rtplan_path,
                output_dir=default_output_dir,
            )
            metrics = _summarize_point_gamma_metrics(result.analysis_data)
            case_repo.record_delivery_analysis_result(
                delivery_id=delivery.delivery_id,
                status_code=result.status_code,
                last_run_at=datetime.now(),
                gamma_pass_rate=metrics.get("gamma_pass_rate"),
                gamma_mean=metrics.get("gamma_mean"),
                gamma_max=metrics.get("gamma_max"),
                evaluated_points=metrics.get("evaluated_points"),
                report_path=result.report_path,
                error_message=result.error_message,
            )
            if result.success:
                success_count += 1
            else:
                overall_status = result.status_code
                overall_error = result.error_message
            if result.output_dir is not None:
                overall_output_dir = result.output_dir

        result = PtnCheckerResult(
            success=success_count == len(deliveries),
            status_code=overall_status if success_count != len(deliveries) else "SUCCESS",
            error_message=overall_error,
            output_dir=overall_output_dir,
        )
        case_repo.record_ptn_checker_result(
            case_id=case_id,
            status_code=result.status_code,
            last_run_at=datetime.now(),
            error_message=result.error_message,
        )
        case_repo.record_workflow_step(
            case_id=case_id,
            step=WorkflowStep.POSTPROCESSING,
            status="completed" if result.success else "failed",
            error_message=result.error_message,
            metadata={
                "message": "PTN checker analysis finished.",
                "status_code": result.status_code,
                "output_dir": str(result.output_dir or default_output_dir),
                "delivery_count": len(deliveries),
                "delivery_success_count": success_count,
            },
        )
        return result
