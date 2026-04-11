"""Defines the state machine for the workflow using the State pattern."""

from __future__ import annotations

from abc import ABC, abstractmethod
from functools import wraps
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

from src.config.constants import (
    PHASE_HPC_RUNNING,
    PHASE_COMPLETED,
    PROGRESS_COMPLETED,
)
from src.core.case_aggregator import update_case_status_from_beams
from src.domain.enums import BeamStatus, CaseStatus
from src.domain.errors import ProcessingError

if TYPE_CHECKING:
    from src.core.workflow_manager import WorkflowManager


def handle_state_exceptions(func: Callable[..., Any]) -> Callable[..., Any]:
    """A decorator to handle common exceptions in WorkflowState execute methods."""

    @wraps(func)
    def wrapper(
            state_instance: 'WorkflowState',
            context: 'WorkflowManager') -> Optional['WorkflowState']:
        try:
            return func(state_instance, context)
        except Exception as e:
            state_name = state_instance.get_state_name()
            error_msg = f"Error in state '{state_name}' for beam '{context.id}': {str(e)}"

            context.logger.error(
                error_msg, {
                    "beam_id": context.id,
                    "state": state_name,
                    "exception_type": type(e).__name__
                })
            context.case_repo.update_beam_status(context.id,
                                                 BeamStatus.FAILED,
                                                 error_message=error_msg)
            return FailedState()

    return wrapper


class WorkflowState(ABC):
    """Abstract base class for workflow states implementing the State pattern."""

    def _update_status(self, context: 'WorkflowManager', status: BeamStatus,
                      message: str, error_message: str = None, log_level: str = "info") -> None:
        """Updates beam status and logs the transition.

        Args:
            context: WorkflowManager instance providing access to repository and logger
            status: New status to set for the beam
            message: Log message describing the status change
            error_message: Optional error message to store in database
            log_level: Logging level to use ("info" or "error")
        """
        context.case_repo.update_beam_status(
            beam_id=context.id,
            status=status,
            error_message=error_message
        )
        log_data = {
            "beam_id": context.id,
            "status": status.value,
            "state": self.get_state_name()
        }
        if log_level == "error":
            context.logger.error(message, log_data)
        else:
            context.logger.info(message, log_data)

    def _update_progress_from_config(
        self, context: 'WorkflowManager', phase_key: str, default_value: float = None
    ) -> None:
        """Updates beam progress from configuration, silently ignoring all errors.

        This method implements the defensive programming pattern for progress tracking:
        - Progress tracking errors should NEVER crash the workflow
        - All exceptions are silently caught and ignored
        - If config is missing or invalid, progress update is skipped

        Args:
            context: WorkflowManager instance providing access to settings and repository
            phase_key: The phase key to look up in coarse_phase_progress config
                      (e.g., PHASE_CSV_INTERPRETING, PHASE_HPC_RUNNING)
            default_value: Optional default progress value to use if config key is missing
        """
        try:
            config = context.settings.get_progress_tracking_config()
            phase_progress = config.get("coarse_phase_progress", {})
            p = phase_progress.get(phase_key, default_value)
            if p is not None:
                context.case_repo.update_beam_progress(context.id, float(p))
        except Exception:
            pass

    @abstractmethod
    def execute(
            self, context: 'WorkflowManager') -> Optional[WorkflowState]:
        """Execute the current state and return the next state."""
        pass

    @abstractmethod
    def get_state_name(self) -> str:
        """Return the human-readable name of this state."""
        pass


class InitialState(WorkflowState):
    """Initial state: validates beam structure and TPS file existence."""

    @handle_state_exceptions
    def execute(self, context: 'WorkflowManager') -> WorkflowState:
        context.logger.info("Performing initial validation for beam", {"beam_id": context.id})

        if not context.path.is_dir():
            raise ProcessingError(
                f"Beam path is not a valid directory: {context.path}")

        # Get beam info to find TPS file
        beam = context.case_repo.get_beam(context.id)
        if not beam:
            raise ProcessingError(f"Could not retrieve beam data for beam_id: {context.id}")

        # TPS files are stored in csv_output_dir/case_id/moqui_tps_{beam_id}.in
        csv_output_base = context.settings.get_path("csv_output_dir", handler_name="CsvInterpreter")
        tps_output_dir = Path(csv_output_base) / beam.parent_case_id
        tps_file = tps_output_dir / f"moqui_tps_{context.id}.in"

        if not tps_file.exists():
            raise ProcessingError(
                f"moqui_tps.in not found for beam {context.id}: {tps_file}.")

        context.shared_context["tps_file_path"] = tps_file
        context.logger.info("Initial validation completed successfully",
                            {"beam_id": context.id, "tps_file": str(tps_file)})
        return SimulationState()

    def get_state_name(self) -> str:
        return "Initial Validation"


class SimulationState(WorkflowState):
    """Simulation state: runs MOQUI simulation locally and monitors for completion."""

    @handle_state_exceptions
    def execute(self, context: 'WorkflowManager') -> WorkflowState:
        """Submits a MOQUI simulation via direct local execution and polls for completion."""
        context.logger.info("Starting simulation for beam",
                            {"beam_id": context.id})
        handler = context.execution_handler

        # Get beam info
        beam = context.case_repo.get_beam(context.id)
        if not beam:
            raise ProcessingError(f"Could not retrieve beam data for beam_id: {context.id}")

        handler_name = "HpcJobSubmitter"

        # Get TPS input file path from settings
        tps_input_file = context.settings.get_path(
            "tps_input_file",
            handler_name=handler_name,
            case_id=beam.parent_case_id,
            beam_id=context.id
        )

        # Get mqi_run_dir and remote_log_path for command template
        mqi_run_dir = context.settings.get_path("mqi_run_dir", handler_name=handler_name)
        remote_log_path = context.settings.get_path(
            "remote_log_path",
            handler_name=handler_name,
            case_id=beam.parent_case_id,
            beam_id=context.id
        )

        # Ensure simulation output directory exists before launching.
        beam_number = beam.beam_number
        if beam_number is not None:
            simulation_output_dir = context.settings.get_path(
                "simulation_output_dir",
                handler_name=handler_name,
                case_id=beam.parent_case_id
            )
            beam_output_dir = Path(simulation_output_dir)
            beam_output_dir.mkdir(parents=True, exist_ok=True)
            context.logger.info("Ensured simulation output directory exists",
                                {"beam_id": context.id, "path": str(beam_output_dir)})

        # Update status to running
        self._update_status(context, BeamStatus.SIMULATION_RUNNING, "Simulation running")
        self._update_progress_from_config(context, PHASE_HPC_RUNNING)

        # Build and execute the simulation command
        command = context.settings.get_command(
            handler_name=handler_name,
            command_key="remote_submit_simulation",
            tps_input_file=tps_input_file,
            mqi_run_dir=mqi_run_dir,
            remote_log_path=remote_log_path,
            case_id=beam.parent_case_id,
            beam_id=context.id,
        )

        # Launch simulation non-blocking so log can be monitored for progress
        sim_process = handler.start_local_process(command)

        # Monitor log file for progress and completion while process runs
        wait_res = handler.wait_for_job_completion(
            job_id=None,
            log_file_path=remote_log_path,
            beam_id=context.id,
            case_repo=context.case_repo,
            process=sim_process
        )
        if getattr(wait_res, "failed", False):
            raise ProcessingError(getattr(wait_res, "error", "Local simulation failed"))

        context.logger.info("Simulation completed successfully",
                            {"beam_id": context.id})
        return ResultValidationState()

    def get_state_name(self) -> str:
        return "Simulation Execution"


class ResultValidationState(WorkflowState):
    """Validates that the native DICOM result directory exists for a beam."""

    @handle_state_exceptions
    def execute(self, context: 'WorkflowManager') -> WorkflowState:
        self._update_status(
            context, BeamStatus.POSTPROCESSING, "Validating simulation results for beam"
        )

        beam = context.case_repo.get_beam(context.id)
        if not beam:
            raise ProcessingError(f"Could not retrieve beam data for beam_id: {context.id}")

        handler_name = "PostProcessor"
        local_result_base = context.settings.get_path(
            "simulation_output_dir",
            handler_name=handler_name,
            case_id=beam.parent_case_id
        )

        beam_number = beam.beam_number
        if beam_number is None:
            raise ProcessingError(f"Beam number missing for beam_id: {context.id}")

        local_result_path = Path(local_result_base)

        if not local_result_path.exists():
            raise ProcessingError(
                f"Native DICOM result directory not found at expected path: {local_result_path}"
            )

        context.shared_context["final_result_path"] = str(local_result_path)
        context.logger.info("Result validation completed", {"beam_id": context.id})

        return CompletedState()

    def get_state_name(self) -> str:
        return "Result Validation"


class CompletedState(WorkflowState):
    """Final completed state for a beam."""

    def execute(
            self, context: 'WorkflowManager') -> Optional[WorkflowState]:
        self._update_status(
            context, BeamStatus.COMPLETED, "Beam workflow completed successfully"
        )
        self._update_progress_from_config(context, PHASE_COMPLETED, PROGRESS_COMPLETED)
        beam = context.case_repo.get_beam(context.id)
        if beam:
            update_case_status_from_beams(beam.parent_case_id,
                                          context.case_repo)
        return None  # Terminal state

    def get_state_name(self) -> str:
        return "Completed"


class FailedState(WorkflowState):
    """Failed state for beam error handling."""

    def execute(
            self, context: 'WorkflowManager') -> Optional[WorkflowState]:
        self._update_status(context, BeamStatus.FAILED, "Beam workflow entered failed state", log_level="error")
        beam = context.case_repo.get_beam(context.id)
        if beam:
            update_case_status_from_beams(beam.parent_case_id,
                                          context.case_repo)
        return None  # Terminal state

    def get_state_name(self) -> str:
        return "Failed"
