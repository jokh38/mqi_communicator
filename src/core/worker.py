# =====================================================================================
# Target File: src/core/worker.py
# Source Reference: src/worker.py, src/main.py
# =====================================================================================
"""Main entry point for a worker process that handles a single beam."""

from pathlib import Path

from src.database.connection import DatabaseConnection
from src.repositories.case_repo import CaseRepository
from src.repositories.gpu_repo import GpuRepository
from src.handlers.execution_handler import ExecutionHandler
from src.infrastructure.logging_handler import StructuredLogger, LoggerFactory
from src.infrastructure.process_manager import CommandExecutor
from src.utils.retry_policy import RetryPolicy
from src.core.workflow_manager import WorkflowManager
import paramiko
from src.core.tps_generator import TpsGenerator
from src.config.settings import Settings


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

    db_connection = None
    try:
        _validate_beam_path(beam_path, logger)

        # Use database path from settings
        db_path = settings.get_database_path()
        db_connection = DatabaseConnection(
            db_path=db_path,
            settings=settings,
            logger=logger
        )
        # Initialize database schema
        db_connection.init_db()

        case_repo = CaseRepository(db_connection, logger)
        gpu_repo = GpuRepository(db_connection, logger, settings)

        # Create handler dependencies
        command_executor = CommandExecutor(
            logger, settings.get_processing_config().get('local_execution_timeout_seconds'))
        retry_policy = RetryPolicy(logger=logger, settings=settings)

        # Create ExecutionHandler based on settings
        workflow_mode = settings.execution_handler.get("Workflow", "local")

        ssh_client = None
        if workflow_mode == "remote":
            try:
                hpc_config = settings.get_hpc_connection()
                if not hpc_config:
                    raise ConnectionError("HPC connection settings not configured.")

                ssh_client = paramiko.SSHClient()
                ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                connection_timeout = hpc_config.get("connection_timeout_seconds")
                if connection_timeout is None:
                    logger.warning("HPC connection_timeout_seconds not found in config, defaulting to 30 seconds.")
                    connection_timeout = 30

                ssh_client.connect(
                    hostname=hpc_config.get("host"),
                    username=hpc_config.get("user"),
                    key_filename=hpc_config.get("ssh_key_path"),
                    port=hpc_config.get("port", 22),
                    timeout=connection_timeout,
                )
            except Exception as e:
                logger.error("Failed to establish HPC connection", {"error": str(e)})
                raise  # Re-raise the exception to stop the worker

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
        if db_connection:
            db_connection.close()
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
