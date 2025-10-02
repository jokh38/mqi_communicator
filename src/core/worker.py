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
            case_repo._db_connection.init_db()
            gpu_repo = GpuRepository(case_repo._db_connection, logger, settings)

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
