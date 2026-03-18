"""Defines the state machine for the workflow using the State pattern."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from functools import wraps
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

from src.config.constants import (
    PHASE_CSV_INTERPRETING,
    PHASE_UPLOADING,
    PHASE_HPC_QUEUED,
    PHASE_HPC_RUNNING,
    PHASE_DOWNLOADING,
    PHASE_COMPLETED,
    PROGRESS_COMPLETED,
)
from src.core.case_aggregator import update_case_status_from_beams
from src.domain.enums import BeamStatus, CaseStatus, WorkflowStep
from src.domain.errors import ProcessingError
from src.handlers.execution_handler import ExecutionHandler

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
                      (e.g., PHASE_CSV_INTERPRETING, PHASE_UPLOADING)
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
        # Initial validation - preserve existing beam status (already set by dispatcher)
        # Do not overwrite status here as CSV_INTERPRETING has already completed
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
        return FileUploadState()

    def get_state_name(self) -> str:
        return "Initial Validation"


class FileUploadState(WorkflowState):
    """ conditionally uploads beam-specific files to a dedicated directory on the HPC."""

    @handle_state_exceptions
    def execute(self, context: 'WorkflowManager') -> WorkflowState:
        # Get the execution mode for the HpcJobSubmitter
        handler_name = "HpcJobSubmitter"
        mode = context.settings.get_handler_mode(handler_name)

        if mode == 'remote':
            self._update_status(
                context, BeamStatus.UPLOADING, "Remote mode: Uploading beam files to HPC"
            )
            self._update_progress_from_config(context, PHASE_UPLOADING)

            beam = context.case_repo.get_beam(context.id)
            if not beam:
                raise ProcessingError(f"Could not retrieve beam data for beam_id: {context.id}")

            remote_beam_dir = context.settings.get_path(
                "remote_beam_path",
                handler_name=handler_name,
                case_id=beam.parent_case_id,
                beam_id=context.id
            )
            context.shared_context["remote_beam_dir"] = remote_beam_dir

            tps_file = context.shared_context.get("tps_file_path")
            if not tps_file:
                raise ProcessingError("TPS file path not found in shared context")
            if not isinstance(tps_file, Path):
                raise ProcessingError(f"Invalid TPS file path type: {type(tps_file)}")
            if not tps_file.exists():
                raise ProcessingError(f"TPS file not found: {tps_file}")

            result = context.execution_handler.upload_file(
                local_path=str(tps_file), remote_path=f"{remote_beam_dir}/{tps_file.name}"
            )
            if not result.success:
                raise ProcessingError(f"Failed to upload file {tps_file.name}: {result.error}")

            context.logger.info("Successfully uploaded files for beam", {"beam_id": context.id})
        else:
            # In local mode, skip the upload
            context.logger.info("Local mode: Skipping file upload.", {"beam_id": context.id})

        return HpcExecutionState()

    def get_state_name(self) -> str:
        return "File Upload"


class HpcExecutionState(WorkflowState):
    """HPC execution state: runs simulation and polls for completion."""

    def __init__(self, execution_handler: Optional[ExecutionHandler] = None):
        self._injected_handler = execution_handler

    @handle_state_exceptions
    def execute(self, context: 'WorkflowManager') -> WorkflowState:
        """Submits a MOQUI simulation via direct execution and polls for completion."""
        context.logger.info("Starting HPC simulation for beam",
                            {"beam_id": context.id})
        handler = self._injected_handler or context.execution_handler

        # Get beam info to construct TPS file path
        beam = context.case_repo.get_beam(context.id)
        if not beam:
            raise ProcessingError(f"Could not retrieve beam data for beam_id: {context.id}")

        # Use HpcJobSubmitter for path resolution in both local and remote modes
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
        # The moqui simulation (tps_env) writes DICOM files via gdcm::Writer
        # which requires the output directory to already exist — it does not
        # create it, and will crash with "Assertion `Ofstream->is_open()' failed"
        # if the directory is missing.
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

        # Execute simulation based on mode; default to remote unless explicitly 'local'
        mode = context.settings.get_handler_mode(handler_name)
        is_remote = not (isinstance(mode, str) and mode.lower() == "local")

        # Mark queued before submission
        self._update_status(context, BeamStatus.HPC_QUEUED, "Beam queued for HPC execution")
        self._update_progress_from_config(context, PHASE_HPC_QUEUED)

        if is_remote:
            # Remote mode: submit job via HPC scheduler
            submission = handler.submit_simulation_job(
                handler_name=handler_name,
                command_key="remote_submit_simulation",
                tps_input_file=tps_input_file,
                mqi_run_dir=mqi_run_dir,
                remote_log_path=remote_log_path,
                case_id=beam.parent_case_id,
                beam_id=context.id
            )
            if not getattr(submission, "success", False):
                raise ProcessingError(
                    f"Failed to submit HPC simulation: {getattr(submission, 'error', 'unknown error')}"
                )

            # Mark running when job starts/polling begins
            self._update_status(context, BeamStatus.HPC_RUNNING, "HPC simulation running")
            self._update_progress_from_config(context, PHASE_HPC_RUNNING)

            wait_res = handler.wait_for_job_completion(getattr(submission, "job_id", None))
            if getattr(wait_res, "failed", False):
                raise ProcessingError(getattr(wait_res, "error", "HPC job failed"))
        else:
            command = context.settings.get_command(
                handler_name=handler_name,
                command_key="remote_submit_simulation",
                tps_input_file=tps_input_file,
                mqi_run_dir=mqi_run_dir,
                remote_log_path=remote_log_path,
                case_id=beam.parent_case_id,
                beam_id=context.id,
            )
            self._update_status(context, BeamStatus.HPC_RUNNING, "Local simulation running")
            self._update_progress_from_config(context, PHASE_HPC_RUNNING)

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


        context.logger.info("HPC simulation completed successfully",
                            {"beam_id": context.id})
        return DownloadState()

    def get_state_name(self) -> str:
        return "HPC Execution"



class DownloadState(WorkflowState):
    """Locates or downloads the native DICOM result for a beam."""

    @handle_state_exceptions
    def execute(self, context: 'WorkflowManager') -> WorkflowState:
        self._update_status(
            context, BeamStatus.DOWNLOADING, "Starting result handling for beam"
        )
        self._update_progress_from_config(context, PHASE_DOWNLOADING)

        beam = context.case_repo.get_beam(context.id)
        if not beam:
            raise ProcessingError(f"Could not retrieve beam data for beam_id: {context.id}")

        local_handler_name = "PostProcessor"
        local_result_base = context.settings.get_path(
            "simulation_output_dir",
            handler_name=local_handler_name,
            case_id=beam.parent_case_id
        )

        beam_number = beam.beam_number
        if beam_number is None:
            raise ProcessingError(f"Beam number missing for beam_id: {context.id}")

        local_result_path = Path(local_result_base)

        remote_handler_name = "HpcJobSubmitter"
        mode = context.settings.get_handler_mode(remote_handler_name)

        if mode == 'remote':
            context.logger.info("Remote mode: Downloading native DICOM results from HPC", {"beam_id": context.id})

            remote_result_path = context.settings.get_path(
                "remote_beam_result_path",
                handler_name=remote_handler_name,
                case_id=beam.parent_case_id,
                beam_id=context.id,
                beam_number=beam_number
            )

            result = context.execution_handler.download_directory(
                remote_dir=remote_result_path,
                local_dir=str(local_result_path)
            )
            if not result.success:
                raise ProcessingError(f"Failed to download DICOM result directory: {result.error}")

            context.logger.info(
                "Beam result downloaded successfully",
                {"beam_id": context.id, "path": str(local_result_path)},
            )

            # Cleanup remote directory
            remote_beam_dir = context.shared_context.get("remote_beam_dir")
            if remote_beam_dir:
                cleanup_result = context.execution_handler.cleanup(
                    handler_name=remote_handler_name,
                    remote_path=remote_beam_dir
                )
                if cleanup_result.success:
                    context.logger.info("Cleaned up remote directory", {"beam_id": context.id, "remote_dir": remote_beam_dir})
                else:
                    context.logger.warning("Failed to cleanup remote directory", {"beam_id": context.id, "error": cleanup_result.error})

        else:  # local mode
            context.logger.info("Local mode: Using native DICOM output directory.", {"beam_id": context.id})

        if not local_result_path.exists():
            raise ProcessingError(
                f"Native DICOM result directory not found at expected path: {local_result_path}"
            )

        context.shared_context["final_result_path"] = str(local_result_path)

        return UploadResultToPCLocalDataState()

    def get_state_name(self) -> str:
        return "Download Results"


class UploadResultToPCLocalDataState(WorkflowState):
    """Uploads the final result to PC_localdata by executing a local command."""

    @handle_state_exceptions
    def execute(self, context: "WorkflowManager") -> Optional["WorkflowState"]:
        context.logger.info("Uploading final results", {"beam_id": context.id})
        final_result_path = context.shared_context.get("final_result_path")
        if not final_result_path or not Path(final_result_path).exists():
            raise ProcessingError(f"Final result path not found: {final_result_path}")

        beam = context.case_repo.get_beam(context.id)
        if not beam:
            raise ProcessingError(f"Could not retrieve beam data for beam_id: {context.id}")

        # Use the execution handler from the context per tests
        result = context.execution_handler.upload_to_pc_localdata(
            local_path=Path(final_result_path),
            case_id=beam.parent_case_id,
            settings=context.settings
        )

        if not result.success:
            raise ProcessingError(f"Failed to upload result to PC_localdata: {result.error}")

        context.logger.info("Successfully uploaded result to PC_localdata.", {"beam_id": context.id})
        return CompletedState()

    def get_state_name(self) -> str:
        return "UploadingResultToPCLocalData"



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
