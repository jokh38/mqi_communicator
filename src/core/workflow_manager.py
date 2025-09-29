# =====================================================================================
# Target File: src/core/workflow_manager.py
# Source Reference: src/workflow_manager.py, src/worker.py
# =====================================================================================
"""Manages and orchestrates the entire workflow for a case using a state pattern."""

from typing import Optional, Any, Dict
from pathlib import Path

from src.repositories.case_repo import CaseRepository
from src.repositories.gpu_repo import GpuRepository
from src.handlers.execution_handler import ExecutionHandler
from src.infrastructure.logging_handler import StructuredLogger
from src.core.tps_generator import TpsGenerator
from src.domain.enums import BeamStatus
from src.domain.states import WorkflowState, InitialState


class WorkflowManager:
    """Manages and orchestrates the entire workflow for a case according to the State pattern.

    This class is responsible for executing each `State` and transitioning to the next,
    using the injected `repositories` and `handlers`.
    """

    def __init__(
        self,
        case_repo: CaseRepository,
        gpu_repo: GpuRepository,
        execution_handler: ExecutionHandler,
        tps_generator: TpsGenerator,
        logger: StructuredLogger,
        id: str,
        path: Path,
        settings: Any,
    ):
        """Initializes the workflow manager with all required dependencies.

        Args:
            case_repo (CaseRepository): The case repository for database access.
            gpu_repo (GpuRepository): The GPU repository for database access.
            execution_handler (ExecutionHandler): The handler for command execution.
            tps_generator (TpsGenerator): The TPS generator service.
            logger (StructuredLogger): The structured logger.
            id (str): The unique identifier for the case or beam.
            path (Path): The path to the case or beam directory.
            settings (Any): The application settings.
        """
        self.case_repo = case_repo
        self.gpu_repo = gpu_repo
        self.execution_handler = execution_handler
        self.tps_generator = tps_generator
        self.logger = logger
        self.id = id
        self.path = path
        self.settings = settings
        self.current_state: Optional[WorkflowState] = InitialState()
        self.shared_context: Dict[str, Any] = {}

    def run_workflow(self) -> None:
        """Executes the complete workflow by managing state transitions."""
        self.logger.info(f"Starting workflow for: {self.id}")

        while self.current_state:
            state_name = self.current_state.get_state_name()
            self.logger.info(f"Executing state: {state_name}")

            try:
                next_state = self.current_state.execute(self)
                self._transition_to_next_state(next_state)
            except Exception as e:
                self._handle_workflow_error(e, f"Error during state: {state_name}")
                break

        self.logger.info(f"Workflow finished for: {self.id}")

    def _transition_to_next_state(self, next_state: WorkflowState) -> None:
        """Handles the transition from the current state to the next state.

        Args:
            next_state (WorkflowState): The next state in the workflow.
        """
        if next_state:
            self.logger.info(
                f"Transitioning from {self.current_state.get_state_name()} to {next_state.get_state_name()}"
            )
        else:
            self.logger.info(
                f"Transitioning from {self.current_state.get_state_name()} to None (workflow end)"
            )

        self.current_state = next_state

    def _handle_workflow_error(self, error: Exception, context: str) -> None:
        """Handles errors that occur during workflow execution.

        Args:
            error (Exception): The exception that occurred.
            context (str): A string describing the context in which the error occurred.
        """
        self.logger.error("Workflow error occurred", {
            "id": self.id,
            "context": context,
            "error": str(error),
            "error_type": type(error).__name__,
            "current_state":
            self.current_state.get_state_name() if self.current_state else "None"
        })

        try:
            # This logic is now beam-specific. The calling state should handle status updates.
            # For now, we'll assume the worker is for a beam and try to update beam status.
            # This part will need more refinement when states are refactored.
            self.case_repo.update_beam_status(
                self.id,
                BeamStatus.FAILED,
                error_message=str(error),
            )
            self.logger.info(f"Beam status updated to FAILED for: {self.id}")
        except Exception as db_error:
            self.logger.error("Failed to update status during error handling", {
                "id": self.id,
                "db_error": str(db_error)
            })

        self.current_state = None  # Stop the workflow