"""Contains logic for dispatching cases and beams for processing."""

import paramiko
from pathlib import Path
from typing import List, Dict, Any, Optional

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
from src.utils.db_context import get_db_session


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
    logger = LoggerFactory.get_logger(f"dispatcher_{case_id}")
    handler_name = "CsvInterpreter"  # This handler is local

    try:
        execution_handler = ExecutionHandler(settings=settings, mode="local")

        with get_db_session(settings, logger, handler_name=handler_name) as case_repo:
            logger.info(f"Starting case-level CSV interpreting for: {case_id}")
            case_repo.record_workflow_step(
                case_id=case_id,
                step=WorkflowStep.CSV_INTERPRETING,
                status="started",
                metadata={"message": "Running mqi_interpreter for the whole case."})

            # Update progress to indicate CSV interpreting started
            case_repo.update_case_status(
                case_id=case_id,
                status=CaseStatus.CSV_INTERPRETING,
                progress=10.0
            )

            # The command template now uses {input_path} for clarity.
            command = settings.get_command(
                "interpret_csv",
                handler_name=handler_name,
                case_id=case_id, # Still needed to resolve {csv_output_dir}
                input_path=str(case_path)
            )

            result = execution_handler.execute_command(command, cwd=case_path)

            if not result.success:
                error_message = (
                    f"Case-level CSV interpreting (mqi_interpreter) failed for '{case_id}'. "
                    f"Error: {result.error}")
                raise ProcessingError(error_message)

            csv_output_base = settings.get_path("csv_output_dir", handler_name=handler_name)
            # mqi_interpreter creates a subdirectory with case_id
            csv_output_dir = Path(csv_output_base) / case_id
            csv_files = list(csv_output_dir.glob("**/*.csv"))
            csv_count = len(csv_files)

            if csv_count == 0:
                logger.warning(
                    f"No CSV files found in the output directory {csv_output_dir} "
                    f"after case-level CSV interpreting.")

            # Mark interpreter as completed and update progress
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
        logger.error("Case-level CSV interpreting failed",
                     {"case_id": case_id, "error": str(e)})
        try:
            with get_db_session(settings, logger, handler_name=handler_name) as case_repo:
                case_repo.update_case_status(case_id,
                                             CaseStatus.FAILED,
                                             error_message=str(e))
                case_repo.record_workflow_step(
                    case_id=case_id,
                    step=WorkflowStep.CSV_INTERPRETING,
                    status="failed",
                    metadata={"error": str(e)})
        except Exception as db_e:
            logger.error(
                "Failed to update case status during error",
                {"case_id": case_id, "db_error": str(db_e)})
        return False


def run_case_level_upload(case_id: str, settings: Settings,
                          ssh_client: paramiko.SSHClient) -> bool:
    """
    Uploads all generated CSV files to each beam's remote directory.
    """
    logger = LoggerFactory.get_logger(f"dispatcher_{case_id}")
    # Use a remote handler context for getting remote paths
    remote_handler_name = "HpcJobSubmitter"
    local_handler_name = "CsvInterpreter"

    try:
        if not ssh_client:
            raise ProcessingError("SSH client is not available for upload.")

        execution_handler = ExecutionHandler(settings=settings,
                                             mode="remote",
                                             ssh_client=ssh_client)

        with get_db_session(settings, logger, handler_name=local_handler_name) as case_repo:
            logger.info(f"Starting case-level file upload for: {case_id}")
            case_repo.record_workflow_step(case_id=case_id,
                                           step=WorkflowStep.UPLOADING,
                                           status="started")

            csv_output_base = settings.get_path("csv_output_dir",
                                                handler_name=local_handler_name)
            # mqi_interpreter creates a subdirectory with case_id
            csv_dir = Path(csv_output_base) / case_id
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
                remote_beam_dir = settings.get_path("remote_beam_path",
                                                    handler_name=remote_handler_name,
                                                    case_id=beam.parent_case_id,
                                                    beam_id=beam.beam_id)
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
    except Exception as e:
        logger.error("Case-level file upload failed",
                     {"case_id": case_id, "error": str(e)})
        return False

# ... (rest of the file remains the same, but fixing db connection in tps_generation)

def prepare_beam_jobs(
    case_id: str, case_path: Path, settings: Settings
) -> List[Dict[str, Any]]:
    """Scans a case directory for beams and returns a list of jobs to be processed by workers.

    This function identifies actual beam folders by:
    1. Locating the DICOM RT Plan file
    2. Extracting the expected beam count from the RT Plan
    3. Filtering subdirectories to identify only actual beam data folders
    4. Validating that the counts match before preparing beam jobs

    Args:
        case_id (str): The ID of the parent case.
        case_path (Path): The file system path to the case directory.
        settings (Settings): The application settings object.

    Returns:
        List[Dict[str, Any]]: A list of dictionaries, each representing a beam job to be executed.
        Returns an empty list if no beams are found, validation fails, or an error occurs.
    """
    logger = LoggerFactory.get_logger(f"dispatcher_{case_id}")
    beam_jobs = []

    try:
        logger.info(f"Scanning for beams for case: {case_id}")
        validator = DataIntegrityValidator(logger)

        # Step 1: Find DICOM RT Plan file
        rtplan_path = validator.find_rtplan_file(case_path)
        if not rtplan_path:
            error_message = f"No RT Plan file found in case directory: {case_path}"
            logger.error(error_message)
            return []

        # Step 2: Determine expected beam count from DICOM RT Plan
        try:
            expected_beam_count = validator.parse_rtplan_beam_count(rtplan_path)
            logger.info(f"RT Plan indicates {expected_beam_count} treatment beams for case {case_id}")
        except ProcessingError as e:
            error_message = f"Failed to parse RT Plan file: {str(e)}"
            logger.error(error_message)
            return []

        if expected_beam_count == 0:
            logger.warning(f"RT plan indicates 0 beams for case {case_id}")
            return []

        # Step 3: Identify and filter actual beam folders
        # Get the directory containing the DICOM file to exclude it
        dicom_parent_dir = rtplan_path.parent

        # Get all subdirectories in the case folder
        all_subdirs = [d for d in case_path.iterdir() if d.is_dir()]

        # Filter to get only beam data folders
        beam_folders = []
        for subdir in all_subdirs:
            # Exclude the DICOM directory
            if subdir == dicom_parent_dir:
                logger.debug(f"Excluding DICOM directory: {subdir.name}")
                continue

            # Check if this directory contains beam data
            # Criteria: presence of .ptn files or specific naming convention
            has_ptn_files = list(subdir.glob("*.ptn"))
            matches_beam_naming = subdir.name.lower().startswith('beam_') or subdir.name.lower().startswith('field_')

            if has_ptn_files or matches_beam_naming:
                beam_folders.append(subdir)
                logger.debug(f"Identified beam folder: {subdir.name}")
            else:
                logger.debug(f"Excluding non-beam directory: {subdir.name}")

        actual_beam_count = len(beam_folders)
        logger.info(f"Found {actual_beam_count} actual beam folders after filtering")

        # Step 4: Validate data integrity and create beam jobs
        if actual_beam_count != expected_beam_count:
            error_message = (
                f"Data transfer incomplete or incorrect: Expected {expected_beam_count} beams "
                f"from RT Plan, but found {actual_beam_count} beam data folders"
            )
            logger.error(error_message)
            return []

        # Create beam jobs from validated beam folders
        for beam_path in beam_folders:
            beam_name = beam_path.name
            beam_id = f"{case_id}_{beam_name}"
            beam_jobs.append({"beam_id": beam_id, "beam_path": beam_path})

        logger.info(f"Successfully prepared {len(beam_jobs)} beam jobs for case: {case_id}")

        # Log additional beam information for reference
        beam_info = validator.get_beam_information(case_path)
        if beam_info.get("beam_count", 0) > 0:
            logger.info(f"RT plan information - Patient ID: {beam_info.get('patient_id')}, "
                       f"Plan: {beam_info.get('plan_label')}, Expected beams: {beam_info.get('beam_count')}")

    except Exception as e:
        logger.error("Failed to prepare beam jobs", {"case_id": case_id, "error": str(e)})
        return []  # Return empty list on error

    return beam_jobs


def allocate_gpus_for_pending_beams(
    case_id: str, num_pending_beams: int, settings: Settings
) -> Optional[List[Dict[str, Any]]]:
    """Attempts to allocate GPUs for pending beams of a case.

    Args:
        case_id (str): The case identifier.
        num_pending_beams (int): Number of beams waiting for GPU allocation.
        settings (Settings): Application settings.

    Returns:
        Optional[List[Dict[str, Any]]]: List of new GPU assignments if successful, None on error, empty list if no GPUs available.
    """
    logger = LoggerFactory.get_logger(f"gpu_allocator_{case_id}")
    handler_name = "CsvInterpreter"

    try:
        with get_db_session(settings, logger, handler_name=handler_name) as case_repo:
            gpu_repo = GpuRepository(case_repo._db_connection, logger, settings)

            # Check available GPUs
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


def run_case_level_tps_generation(
    case_id: str, case_path: Path, beam_count: int, settings: Settings
) -> Optional[List[Dict[str, Any]]]:
    """Generates moqui_tps.in file at case level with dynamic GPU assignments."""
    logger = LoggerFactory.get_logger(f"tps_dispatcher_{case_id}")
    handler_name = "CsvInterpreter" # local handler for db path
    try:
        logger.info(f"Starting case-level TPS generation for case: {case_id}")

        with get_db_session(settings, logger, handler_name=handler_name) as case_repo:
            case_repo._db_connection.init_db()
            gpu_repo = GpuRepository(case_repo._db_connection, logger, settings)

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

            tps_generator = TpsGenerator(settings, logger)
            success = tps_generator.generate_tps_file_with_gpu_assignments(
                case_path=case_path,
                case_id=case_id,
                gpu_assignments=gpu_assignments,
                execution_mode="remote"
            )

            if not success:
                error_message = f"TPS file generation failed for case {case_id}"
                logger.error(error_message)
                gpu_repo.release_all_for_case(case_id)
                case_repo.record_workflow_step(
                    case_id=case_id,
                    step=WorkflowStep.TPS_GENERATION,
                    status="failed",
                    metadata={"error": error_message}
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
        logger.error("Case-level TPS generation failed", {"case_id": case_id, "error": str(e)})
        try:
            with get_db_session(settings, logger, handler_name=handler_name) as case_repo:
                gpu_repo = GpuRepository(case_repo._db_connection, logger, settings)
                gpu_repo.release_all_for_case(case_id)
        except Exception as cleanup_error:
            logger.error("Failed to cleanup GPU allocations", {"error": str(cleanup_error)})
        return None