"""Contains logic for dispatching cases and beams for processing."""

import paramiko
import time
import multiprocessing as mp
from pathlib import Path
from typing import List, Dict, Any, Optional

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from src.config.settings import Settings
from src.database.connection import DatabaseConnection
from src.repositories.case_repo import CaseRepository
from src.handlers.execution_handler import ExecutionHandler
from src.infrastructure.logging_handler import StructuredLogger
from src.domain.enums import CaseStatus, WorkflowStep
from src.domain.errors import ProcessingError
from src.core.data_integrity_validator import DataIntegrityValidator
from src.infrastructure.logging_handler import LoggerFactory
from src.core.tps_generator import TpsGenerator
from src.repositories.gpu_repo import GpuRepository
from src.utils.db_context import get_db_session, get_handler_db_path, record_step


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


from src.core.case_aggregator import queue_case as _queue_case


from src.core.case_aggregator import ensure_logger as _ensure_logger


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
        execution_handler = ExecutionHandler(settings=settings, mode="local")

        # Use locally imported, test-patched DatabaseConnection/CaseRepository
        try:
            db_path_str = settings.get_path("database_path", handler_name="CsvInterpreter")
        except Exception:
            try:
                db_path_str = str(settings.get_database_path())
            except Exception:
                db_path_str = "dummy.db"
        db_conn = DatabaseConnection(db_path=Path(db_path_str), settings=settings, logger=logger)
        case_repo = CaseRepository(db_conn, logger)
        try:
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

            # Resolve CSV output directory using case directories template
            case_dirs = settings.get_case_directories()
            csv_template = case_dirs.get("csv_output", "/tmp/csv_output/{case_id}")
            csv_output_dir = csv_template.format(case_id=case_id)

            # Build command expected by tests and legacy tooling
            command = (
                f"mqi_interpreter --input {case_path} "
                f"--output {csv_output_dir} --case_id {case_id}"
            )

            result = execution_handler.execute_command(command, cwd=case_path)


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
        finally:
            try:
                db_conn.close()
            except Exception:
                pass

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



def run_case_level_upload(case_id: str, settings: Settings,
                          ssh_client: paramiko.SSHClient) -> bool:
    """
    Uploads all generated CSV files to each beam's remote directory.
    """
    logger = _ensure_logger(f"dispatcher_{case_id}", settings)
    remote_handler_name = "HpcJobSubmitter"

    try:
        if not ssh_client:
            raise ProcessingError("SSH client is not available for upload.")

        execution_handler = ExecutionHandler(settings=settings,
                                             mode="remote",
                                             ssh_client=ssh_client)

        # Use locally imported, test-patched DatabaseConnection/CaseRepository
        try:
            db_path_str = settings.get_path("database_path", handler_name="CsvInterpreter")
        except Exception:
            try:
                db_path_str = str(settings.get_database_path())
            except Exception:
                db_path_str = "dummy.db"
        db_conn = DatabaseConnection(db_path=Path(db_path_str), settings=settings, logger=logger)
        case_repo = CaseRepository(db_conn, logger)
        try:
            logger.info(f"Starting case-level file upload for: {case_id}")
            case_repo.record_workflow_step(case_id=case_id,
                                           step=WorkflowStep.UPLOADING,
                                           status="started")

            case_dirs = settings.get_case_directories()
            csv_dir = Path(case_dirs.get("csv_output", f"/tmp/csv_output/{case_id}"))
            csv_files = list(csv_dir.glob("**/*.csv"))

            if not csv_files:
                logger.warning(f"No CSV files found to upload for case {case_id}",
                               {"directory": csv_dir})
                return True

            beams = case_repo.get_beams_for_case(case_id)
            if not beams:
                raise ProcessingError(
                    f"No beams found in database for case {case_id} during upload.")

            for beam in beams:
                # tests expect remote path: "/remote/cases/{case_id}/beam_01/{filename}"
                remote_case_root = settings.get_hpc_paths().get("remote_case_path_template", "/remote/cases")
                remote_beam_dir = f"{remote_case_root}/{case_id}/{beam.beam_id}"
                logger.info(
                    f"Uploading {len(csv_files)} CSVs to remote dir for beam {beam.beam_id}",
                    {"remote_dir": remote_beam_dir})
                for csv_file in csv_files:
                    remote_path = f"{remote_beam_dir}/{csv_file.name}"
                    result = execution_handler.upload_file(
                        local_path=str(csv_file), remote_path=remote_path)
                    if not result.success:
                        raise ProcessingError(
                            f"Failed to upload {csv_file.name}: {result.error}")

            case_repo.record_workflow_step(case_id=case_id,
                                           step=WorkflowStep.UPLOADING,
                                           status="completed")
            return True
        finally:
            try:
                db_conn.close()
            except Exception:
                pass
    except Exception as e:
        _handle_dispatcher_error(
            case_id=case_id,
            error=e,
            workflow_step=WorkflowStep.UPLOADING,
            settings=settings,
            logger=logger,
            handler_name="CsvInterpreter"
        )
        return False



# ... (rest of the file remains the same, but fixing db connection in tps_generation)

from src.core.case_aggregator import prepare_beam_jobs


from src.core.case_aggregator import allocate_gpus_for_pending_beams


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

            # Check available GPUs and allocate what's possible
            available_gpu_count = gpu_repo.get_available_gpu_count()
            logger.info(f"Checking GPU availability for case {case_id}: {available_gpu_count} idle GPUs, {beam_count} requested")

            # Allocate available GPUs (may be less than beam_count)
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

            logger.info(f"Allocating {gpus_to_allocate} GPUs for case {case_id} ({beam_count - gpus_to_allocate} beams will remain pending)")
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

            # Get beam information to use beam names in TPS files
            beams = case_repo.get_beams_for_case(case_id)

            # Get output directory for TPS files
            csv_output_base = settings.get_path("csv_output_dir", handler_name="CsvInterpreter")
            tps_output_dir = Path(csv_output_base) / case_id

            tps_generator = TpsGenerator(settings, logger)

            # Generate a separate TPS file for each beam with its own GPU assignment
            # Use actual HpcJobSubmitter mode for execution_mode
            execution_mode = settings.get_handler_mode("HpcJobSubmitter")
            for i, (gpu_assignment, beam) in enumerate(zip(gpu_assignments, beams[:len(gpu_assignments)])):
                beam_gpu_assignment = [gpu_assignment]  # Single GPU for this beam
                success = tps_generator.generate_tps_file_with_gpu_assignments(
                    case_path=case_path,
                    case_id=case_id,
                    gpu_assignments=beam_gpu_assignment,
                    execution_mode=execution_mode,
                    output_dir=tps_output_dir,
                    beam_name=beam.beam_id
                )

                if not success:
                    error_message = f"TPS file generation failed for beam {beam.beam_id} in case {case_id}"
                    logger.error(error_message)
                    gpu_repo.release_all_for_case(case_id)
                    case_repo.record_workflow_step(
                        case_id=case_id,
                        step=WorkflowStep.TPS_GENERATION,
                        status="failed",
                        metadata={"error": error_message, "failed_beam": beam.beam_id}
                    )
                    return None

            allocated_count = len(gpu_assignments)
            pending_count = beam_count - allocated_count

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


from src.core.workflow_manager import scan_existing_cases


from src.core.workflow_manager import CaseDetectionHandler
