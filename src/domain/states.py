"""Defines the state machine for the workflow using the State pattern."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from functools import wraps
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

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
        context.logger.info("Performing initial validation for beam",
                            {"beam_id": context.id})
        # Use a valid status. CSV_INTERPRETING is the first logical step after validation.
        context.case_repo.update_beam_status(context.id,
                                             BeamStatus.CSV_INTERPRETING)

        if not context.path.is_dir():
            raise ProcessingError(
                f"Beam path is not a valid directory: {context.path}")

        case_path = context.path.parent
        tps_file = case_path / "moqui_tps.in"
        if not tps_file.exists():
            raise ProcessingError(
                f"moqui_tps.in not found at case level: {tps_file}.")

        context.shared_context["tps_file_path"] = tps_file
        context.logger.info("Initial validation completed successfully",
                            {"beam_id": context.id})
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
            context.logger.info("Remote mode: Uploading beam files to HPC", {"beam_id": context.id})
            context.case_repo.update_beam_status(context.id, BeamStatus.UPLOADING)

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

    @handle_state_exceptions
    def execute(self, context: 'WorkflowManager') -> WorkflowState:
        """Submits a MOQUI job and polls for completion."""
        context.logger.info("Starting HPC simulation for beam",
                            {"beam_id": context.id})
        handler = context.execution_handler
        handler_name = "HpcJobSubmitter"  # For remote commands

        # Submit job
        job_result = handler.submit_simulation_job(
            handler_name=handler_name,
            command_key='remote_submit_simulation',
            case_id=context.id
        )
        if not job_result.success or not job_result.job_id:
            raise ProcessingError(
                f"Failed to submit HPC job: {job_result.error}")

        job_id = job_result.job_id
        context.case_repo.assign_hpc_job_id_to_beam(context.id, job_id)
        context.case_repo.update_beam_status(context.id,
                                             BeamStatus.HPC_QUEUED)
        context.logger.info("HPC job submitted, polling for completion",
                            {"beam_id": context.id, "job_id": job_id})
        context.case_repo.update_beam_status(context.id,
                                             BeamStatus.HPC_RUNNING)

        # Poll for completion
        self._wait_for_job_completion(context, job_id, handler_name)

        context.logger.info("HPC simulation completed successfully",
                            {"beam_id": context.id, "job_id": job_id})
        return DownloadState()

    def _wait_for_job_completion(self, context: 'WorkflowManager',
                                 job_id: str, handler_name: str):
        """Polls HPC for job completion using commands from Settings."""
        proc_config = context.settings.get_processing_config()
        timeout = proc_config.get("hpc_job_timeout_seconds")
        interval = proc_config.get("hpc_poll_interval_seconds")
        start_time = time.time()

        while time.time() - start_time < timeout:
            # Check primary status command (squeue)
            status_cmd = context.settings.get_command("check_job_status",
                                                      handler_name=handler_name,
                                                      job_id=job_id)
            result = context.execution_handler.execute_command(status_cmd)

            status = result.output.strip().upper() if result.success else ""

            if status in ["COMPLETED", "COMPLETING"]:
                return  # Success
            if status in ["FAILED", "CANCELLED", "TIMEOUT", "NODE_FAIL"]:
                raise ProcessingError(f"HPC job failed with status: {status}")

            # If job not in squeue, check history (sacct)
            if not status:
                history_cmd = context.settings.get_command(
                    "check_job_history",
                    handler_name=handler_name,
                    job_id=job_id)
                hist_result = context.execution_handler.execute_command(
                    history_cmd)
                if hist_result.success and "COMPLETED" in hist_result.output.upper(
                ):
                    return  # Success

            time.sleep(interval)

        raise ProcessingError(f"HPC job timed out after {timeout} seconds")

    def get_state_name(self) -> str:
        return "HPC Execution"


class DownloadState(WorkflowState):
    """Downloads the raw result file from the HPC and cleans up the remote directory."""

    @handle_state_exceptions
    def execute(self, context: 'WorkflowManager') -> WorkflowState:
        context.logger.info("Starting result handling for beam", {"beam_id": context.id})
        context.case_repo.update_beam_status(context.id, BeamStatus.DOWNLOADING)

        beam = context.case_repo.get_beam(context.id)
        if not beam:
            raise ProcessingError(f"Could not retrieve beam data for beam_id: {context.id}")

        local_handler_name = "PostProcessor"
        local_raw_dir = context.settings.get_path(
            "simulation_output_dir",
            handler_name=local_handler_name,
            case_id=beam.parent_case_id
        )
        local_file_path = Path(local_raw_dir) / "output.raw"

        remote_handler_name = "HpcJobSubmitter"
        mode = context.settings.get_handler_mode(remote_handler_name)

        if mode == 'remote':
            context.logger.info("Remote mode: Downloading results from HPC", {"beam_id": context.id})

            remote_file_path = context.settings.get_path(
                "remote_beam_result_path",
                handler_name=remote_handler_name,
                case_id=beam.parent_case_id,
                beam_id=context.id
            )

            result = context.execution_handler.download_file(
                remote_path=remote_file_path,
                local_path=str(local_file_path)
            )
            if not result.success:
                raise ProcessingError(f"Failed to download result file: {result.error}")

            context.logger.info("Beam result downloaded successfully", {"beam_id": context.id, "path": str(local_file_path)})

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

        else: # local mode
            context.logger.info("Local mode: Skipping download, using local file.", {"beam_id": context.id})

        if not local_file_path.exists():
            raise ProcessingError(f"Result file 'output.raw' not found at expected local path: {local_file_path}")

        context.shared_context["raw_output_file"] = local_file_path

        return PostprocessingState()

    def get_state_name(self) -> str:
        return "Download Results"


class PostprocessingState(WorkflowState):
    """Runs RawToDCM locally on the beam's output."""

    @handle_state_exceptions
    def execute(self, context: 'WorkflowManager') -> WorkflowState:
        context.logger.info("Running RawToDCM postprocessing",
                            {"beam_id": context.id})
        context.case_repo.update_beam_status(context.id,
                                             BeamStatus.POSTPROCESSING)
        handler_name = "PostProcessor"

        input_file = context.shared_context.get("raw_output_file")
        if not input_file or not Path(input_file).exists():
            raise ProcessingError(
                f"Raw output file not found: {input_file}")

        output_dir = Path(input_file).parent / "dcm_output"
        output_dir.mkdir(parents=True, exist_ok=True)

        command = context.settings.get_command("post_process",
                                               handler_name=handler_name,
                                               input_file=str(input_file),
                                               output_dir=str(output_dir))

        result = context.execution_handler.execute_command(command)

        if not result.success:
            raise ProcessingError(
                f"RawToDCM failed for beam {context.id}: {result.error}")

        if not any(output_dir.glob("*.dcm")):
            raise ProcessingError(
                "No DCM files generated in postprocessing.")

        context.logger.info("Postprocessing completed successfully",
                            {"beam_id": context.id})
        context.shared_context["final_result_path"] = str(output_dir)
        Path(input_file).unlink() # Clean up raw file

        return UploadResultToPCLocalDataState()

    def get_state_name(self) -> str:
        return "Postprocessing"


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

        # This command must run locally, so we create a local handler instance.
        # The main handler in the context is for remote (HPC) operations.
        local_handler = ExecutionHandler(settings=context.settings, mode="local")

        result = local_handler.upload_to_pc_localdata(
            handler_name="ResultUploader", # This handler is configured as 'local'
            local_path=str(Path(final_result_path)),
            case_id=beam.parent_case_id
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
        context.logger.info("Beam workflow completed successfully",
                            {"beam_id": context.id})
        context.case_repo.update_beam_status(context.id, BeamStatus.COMPLETED)
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
        context.logger.error("Beam workflow entered failed state",
                             {"beam_id": context.id})
        context.case_repo.update_beam_status(context.id, BeamStatus.FAILED)
        beam = context.case_repo.get_beam(context.id)
        if beam:
            update_case_status_from_beams(beam.parent_case_id,
                                          context.case_repo)
        return None  # Terminal state

    def get_state_name(self) -> str:
        return "Failed"