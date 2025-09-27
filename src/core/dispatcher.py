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
    db_connection = None
    handler_name = "CsvInterpreter"  # This handler is local

    try:
        execution_handler = ExecutionHandler(settings=settings, mode="local")

        db_path_str = settings.get_path("database_path", handler_name=handler_name)
        db_connection = DatabaseConnection(db_path=Path(db_path_str),
                                           settings=settings,
                                           logger=logger)
        case_repo = CaseRepository(db_connection, logger)

        logger.info(f"Starting case-level CSV interpreting for: {case_id}")
        case_repo.record_workflow_step(
            case_id=case_id,
            step=WorkflowStep.CSV_INTERPRETING,
            status="started",
            metadata={"message": "Running mqi_interpreter for the whole case."})

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

        csv_output_dir = settings.get_path("csv_output_dir", handler_name=handler_name, case_id=case_id)
        if not any(Path(csv_output_dir).glob("*.csv")):
            logger.warning(
                f"No CSV files found in the output directory {csv_output_dir} "
                f"after case-level CSV interpreting.")

        logger.info(f"Case-level CSV interpreting completed for: {case_id}")
        case_repo.record_workflow_step(
            case_id=case_id,
            step=WorkflowStep.CSV_INTERPRETING,
            status="completed",
            metadata={"message": "mqi_interpreter finished successfully."})
        return True

    except Exception as e:
        logger.error("Case-level CSV interpreting failed",
                     {"case_id": case_id, "error": str(e)})
        if db_connection:
            try:
                case_repo = CaseRepository(db_connection, logger)
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
    finally:
        if db_connection:
            db_connection.close()


def run_case_level_upload(case_id: str, settings: Settings,
                          ssh_client: paramiko.SSHClient) -> bool:
    """
    Uploads all generated CSV files to each beam's remote directory.
    """
    logger = LoggerFactory.get_logger(f"dispatcher_{case_id}")
    db_connection = None
    # Use a remote handler context for getting remote paths
    remote_handler_name = "HpcJobSubmitter"
    local_handler_name = "CsvInterpreter"

    try:
        if not ssh_client:
            raise ProcessingError("SSH client is not available for upload.")

        execution_handler = ExecutionHandler(settings=settings,
                                             mode="remote",
                                             ssh_client=ssh_client)

        db_path_str = settings.get_path("database_path", handler_name=local_handler_name)
        db_connection = DatabaseConnection(db_path=Path(db_path_str),
                                           settings=settings,
                                           logger=logger)
        case_repo = CaseRepository(db_connection, logger)

        logger.info(f"Starting case-level file upload for: {case_id}")
        case_repo.record_workflow_step(case_id=case_id,
                                       step=WorkflowStep.UPLOADING,
                                       status="started")

        csv_dir = settings.get_path("csv_output_dir",
                                    handler_name=local_handler_name,
                                    case_id=case_id)
        csv_files = list(Path(csv_dir).glob("*.csv"))

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
    finally:
        if db_connection:
            db_connection.close()

# ... (rest of the file remains the same, but fixing db connection in tps_generation)

def prepare_beam_jobs(
    case_id: str, case_path: Path, settings: Settings
) -> List[Dict[str, Any]]:
    """Scans a case directory for beams and returns a list of jobs to be processed by workers.

    This function validates data transfer completion by checking RT plan beam count
    against actual subdirectories before preparing beam jobs.

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

        # Step 1: Validate data transfer completion
        validator = DataIntegrityValidator(logger)
        is_valid, error_message = validator.validate_data_transfer_completion(case_id, case_path)

        if not is_valid:
            logger.error(f"Data transfer validation failed for case {case_id}: {error_message}")
            return []  # Return empty list to prevent processing of incomplete case

        # Step 2: Scan for beam subdirectories (validation passed)
        beam_paths = [d for d in case_path.iterdir() if d.is_dir()]

        if not beam_paths:
            logger.warning("No beam subdirectories found.", {"case_id": case_id})
            return []

        logger.info(f"Found {len(beam_paths)} beams to process.", {"case_id": case_id})

        # Step 3: Prepare beam jobs
        for beam_path in beam_paths:
            beam_name = beam_path.name
            beam_id = f"{case_id}_{beam_name}"
            beam_jobs.append({"beam_id": beam_id, "beam_path": beam_path})

        logger.info(f"Successfully prepared {len(beam_jobs)} beam jobs for case: {case_id}")

        # Step 4: Log additional beam information for reference
        beam_info = validator.get_beam_information(case_path)
        if beam_info.get("beam_count", 0) > 0:
            logger.info(f"RT plan information - Patient ID: {beam_info.get('patient_id')}, "
                       f"Plan: {beam_info.get('plan_label')}, Expected beams: {beam_info.get('beam_count')}")

    except Exception as e:
        logger.error("Failed to prepare beam jobs", {"case_id": case_id, "error": str(e)})
        return []  # Return empty list on error

    return beam_jobs


def run_case_level_tps_generation(
    case_id: str, case_path: Path, beam_count: int, settings: Settings
) -> Optional[List[Dict[str, Any]]]:
    """Generates moqui_tps.in file at case level with dynamic GPU assignments."""
    logger = LoggerFactory.get_logger(f"tps_dispatcher_{case_id}")
    db_connection = None
    handler_name = "CsvInterpreter" # local handler for db path
    try:
        logger.info(f"Starting case-level TPS generation for case: {case_id}")

        db_path_str = settings.get_path("database_path", handler_name=handler_name)
        db_connection = DatabaseConnection(db_path=Path(db_path_str),
                                           settings=settings,
                                           logger=logger)
        db_connection.init_db()

        gpu_repo = GpuRepository(db_connection, logger, settings)
        case_repo = CaseRepository(db_connection, logger)

        case_repo.record_workflow_step(
            case_id=case_id,
            step=WorkflowStep.TPS_GENERATION,
            status="started",
            metadata={"message": f"Generating TPS file with {beam_count} beam assignments."}
        )

        logger.info(f"Allocating {beam_count} GPUs for case {case_id}")
        gpu_assignments = gpu_repo.find_and_lock_multiple_gpus(
            case_id=case_id,
            num_gpus=beam_count
        )

        if not gpu_assignments:
            error_message = f"Failed to allocate {beam_count} GPUs for case {case_id}"
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

        logger.info(f"Case-level TPS generation completed successfully for: {case_id}")
        case_repo.record_workflow_step(
            case_id=case_id,
            step=WorkflowStep.TPS_GENERATION,
            status="completed",
            metadata={
                "message": f"Generated TPS file with {beam_count} beam-to-GPU assignments",
                "gpu_assignments": gpu_assignments
            }
        )

        return gpu_assignments

    except Exception as e:
        logger.error("Case-level TPS generation failed", {"case_id": case_id, "error": str(e)})
        if db_connection:
            try:
                gpu_repo = GpuRepository(db_connection, logger, settings)
                gpu_repo.release_all_for_case(case_id)
            except Exception as cleanup_error:
                logger.error("Failed to cleanup GPU allocations", {"error": str(cleanup_error)})
        return None
    finally:
        if db_connection:
            db_connection.close()