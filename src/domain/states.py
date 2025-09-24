# =====================================================================================
# Target File: src/domain/states.py
# Source Reference: src/states.py
# =====================================================================================
"""Defines the state machine for the workflow using the State pattern."""

from __future__ import annotations
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Optional, Callable, Any
from functools import wraps

if TYPE_CHECKING:
    from src.core.workflow_manager import WorkflowManager

from src.core.case_aggregator import update_case_status_from_beams
from src.domain.enums import BeamStatus, CaseStatus, WorkflowStep
from src.domain.errors import ProcessingError
from pathlib import Path


def handle_state_exceptions(func: Callable[..., Any]) -> Callable[..., Any]:
    """A decorator to handle common exceptions in WorkflowState execute methods.

    It logs the error, updates the beam status to FAILED, and returns a FailedState.
    """
    @wraps(func)
    def wrapper(state_instance: 'WorkflowState', context: 'WorkflowManager') -> Optional['WorkflowState']:
        try:
            return func(state_instance, context)
        except Exception as e:
            # The wrapped function's name is used to identify the state
            state_name = state_instance.get_state_name()
            error_msg = f"Error in state '{state_name}' for beam '{context.id}': {str(e)}"

            context.logger.error(error_msg, {
                "beam_id": context.id,
                "state": state_name,
                "exception_type": type(e).__name__
            })

            # Update the database status to indicate failure
            context.case_repo.update_beam_status(context.id, BeamStatus.FAILED, error_message=error_msg)

            # Transition to the FailedState to terminate the workflow gracefully
            return FailedState()

    return wrapper


class WorkflowState(ABC):
    """Abstract base class for workflow states implementing the State pattern."""

    @abstractmethod
    def execute(self, context: 'WorkflowManager') -> Optional[WorkflowState]:
        """Execute the current state and return the next state.

        Args:
            context ('WorkflowManager'): The workflow manager providing access to repositories and handlers.

        Returns:
            Optional[WorkflowState]: The next state to transition to, or None to terminate.
        """
        pass

    @abstractmethod
    def get_state_name(self) -> str:
        """Return the human-readable name of this state.

        Returns:
            str: The name of the state.
        """
        pass

class InitialState(WorkflowState):
    """Initial state for a new beam - validates beam structure and generates moqui_tps.in."""

    @handle_state_exceptions
    def execute(self, context: 'WorkflowManager') -> WorkflowState:
        """Perform initial validation for the beam and verify TPS file exists."""
        context.logger.info("Performing initial validation for beam", {
            "beam_id": context.id,
            "beam_path": str(context.path)
        })
        context.case_repo.update_beam_status(context.id, BeamStatus.VALIDATING)

        if not context.path.is_dir():
            raise ProcessingError(f"Beam path is not a valid directory: {context.path}")

        # Check if TPS file was generated at case level
        beam = context.case_repo.get_beam(context.id)
        if not beam:
            raise ProcessingError(f"Could not retrieve beam data for beam_id: {context.id}")

        # Look for TPS file in the parent case directory
        case_path = context.path.parent  # Beam path is usually case_path/beam_name
        tps_file = case_path / "moqui_tps.in"

        if not tps_file.exists():
            raise ProcessingError(f"moqui_tps.in file not found at case level: {tps_file}. TPS generation should have been completed at case level.")

        context.logger.info("Initial validation completed successfully for beam", {
            "beam_id": context.id,
            "tps_file": str(tps_file),
            "message": "TPS file found from case-level generation"
        })

        return FileUploadState()

    def get_state_name(self) -> str:
        return "Initial Validation"

class FileUploadState(WorkflowState):
    """File upload state - uploads beam-specific files to a dedicated directory on the HPC."""

    @handle_state_exceptions
    def execute(self, context: 'WorkflowManager') -> WorkflowState:
        """Uploads moqui_tps.in and all ``*.csv`` files from the local beam directory to the HPC."""
        context.logger.info("Uploading beam files to HPC", {"beam_id": context.id})
        context.case_repo.update_beam_status(context.id, BeamStatus.UPLOADING)

        beam = context.case_repo.get_beam(context.id)
        if not beam:
            raise ProcessingError(f"Could not retrieve beam data for beam_id: {context.id}")

        hpc_paths = context.local_handler.settings.get_hpc_paths()
        remote_base_dir = hpc_paths.get("remote_case_path_template")
        if not remote_base_dir:
            raise ProcessingError("`remote_case_path_template` not configured in settings.")

        remote_beam_dir = f"{remote_base_dir}/{beam.parent_case_id}/{context.id}"
        context.shared_context["remote_beam_dir"] = remote_beam_dir

        local_beam_path = context.path
        files_to_upload = []  # CSVs are now uploaded at the case level by the main process.
        tps_file = local_beam_path / "moqui_tps.in"

        if not tps_file.exists():
            raise ProcessingError(f"moqui_tps.in not found in local beam directory: {local_beam_path}")

        files_to_upload.append(tps_file)

        if not files_to_upload:
            context.logger.warning("No files found in local beam directory to upload.", {"beam_id": context.id})
            return HpcExecutionState()

        for file_path in files_to_upload:
            result = context.remote_handler.upload_file(
                local_file=file_path, remote_dir=remote_beam_dir
            )
            if not result.success:
                raise ProcessingError(f"Failed to upload file {file_path.name}: {result.error}")

        context.logger.info(f"Successfully uploaded {len(files_to_upload)} files for beam", {
            "beam_id": context.id,
            "remote_beam_dir": remote_beam_dir
        })

        return HpcExecutionState()

    def get_state_name(self) -> str:
        return "File Upload"


class HpcExecutionState(WorkflowState):
    """HPC execution state - runs MOQUI simulation on HPC for a single beam."""

    @handle_state_exceptions
    def execute(self, context: 'WorkflowManager') -> WorkflowState:
        """Submits a MOQUI simulation job for the beam and polls for completion."""
        context.logger.info("Starting HPC simulation for beam", {"beam_id": context.id})

        # GPUs are already allocated at case level, no need to allocate individually
        # The beam should use the GPU assignment from the case-level TPS file
        beam = context.case_repo.get_beam(context.id)
        if not beam:
            raise ProcessingError(f"Could not retrieve beam data for beam_id: {context.id}")

        context.logger.info("Using case-level GPU allocation for simulation", {
            "beam_id": context.id,
            "case_id": beam.parent_case_id
        })

        remote_beam_dir = context.shared_context.get("remote_beam_dir")
        if not remote_beam_dir:
            raise ProcessingError("Remote beam directory not found in shared context.")

        # Submit job without individual GPU allocation (GPUs managed at case level)
        job_result = context.remote_handler.submit_simulation_job(
            beam_id=context.id,
            remote_beam_dir=remote_beam_dir,
            gpu_uuid=None  # GPU allocation handled in TPS file
        )

        if not job_result.success:
            raise ProcessingError(f"Failed to submit HPC job: {job_result.error}")

        job_id = job_result.job_id
        context.case_repo.assign_hpc_job_id_to_beam(context.id, job_id)
        context.case_repo.update_beam_status(context.id, BeamStatus.HPC_QUEUED)

        context.logger.info("HPC job submitted, polling for completion", {"beam_id": context.id, "job_id": job_id})

        context.case_repo.update_beam_status(context.id, BeamStatus.HPC_RUNNING)
        job_status = context.remote_handler.wait_for_job_completion(
            job_id=job_id,
            timeout_seconds=3600
        )

        if job_status.failed:
            raise ProcessingError(f"HPC job failed: {job_status.error_message}")

        context.logger.info("HPC simulation completed successfully for beam", {"beam_id": context.id, "job_id": job_id})

        return DownloadState()

    def get_state_name(self) -> str:
        return "HPC Execution"


class DownloadState(WorkflowState):
    """Download state - downloads the raw result file from the HPC for a single beam."""

    @handle_state_exceptions
    def execute(self, context: 'WorkflowManager') -> WorkflowState:
        """Downloads the 'output.raw' file from the remote beam directory to a local results directory."""
        context.logger.info("Downloading results from HPC for beam", {"beam_id": context.id})
        context.case_repo.update_beam_status(context.id, BeamStatus.DOWNLOADING)

        beam = context.case_repo.get_beam(context.id)
        if not beam:
            raise ProcessingError(f"Could not retrieve beam data for beam_id: {context.id}")

        remote_beam_dir = context.shared_context.get("remote_beam_dir")
        if not remote_beam_dir:
            raise ProcessingError("Remote beam directory not found in shared context.")

        case_dirs = context.local_handler.settings.get_case_directories()
        local_result_dir_template = case_dirs.get("final_dicom")
        if not local_result_dir_template:
             raise ProcessingError("`final_dicom_directory` not configured in settings.")

        local_result_dir = Path(str(local_result_dir_template).format(case_id=beam.parent_case_id))
        local_beam_result_dir = local_result_dir / beam.beam_id
        local_beam_result_dir.mkdir(parents=True, exist_ok=True)

        remote_file_path = f"{remote_beam_dir}/output.raw"
        result = context.remote_handler.download_file(
            remote_file_path=remote_file_path,
            local_dir=local_beam_result_dir
        )

        if not result.success:
            raise ProcessingError(f"Failed to download output.raw: {result.error}")

        main_output_file = local_beam_result_dir / "output.raw"
        if not main_output_file.exists():
            raise ProcessingError("Main output file 'output.raw' was not downloaded.")

        context.shared_context["raw_output_file"] = main_output_file

        context.remote_handler.cleanup_remote_directory(remote_beam_dir)

        context.logger.info("Beam result downloaded successfully", {"beam_id": context.id})
        return PostprocessingState()

    def get_state_name(self) -> str:
        return "Download Results"

class PostprocessingState(WorkflowState):
    """Postprocessing state - runs RawToDCM locally for a single beam's output."""

    @handle_state_exceptions
    def execute(self, context: 'WorkflowManager') -> WorkflowState:
        """Execute local postprocessing using RawToDCM on the downloaded raw file."""
        context.logger.info("Running RawToDCM postprocessing for beam", {"beam_id": context.id})
        context.case_repo.update_beam_status(context.id, BeamStatus.POSTPROCESSING)

        input_file = context.shared_context.get("raw_output_file")
        if not input_file or not input_file.exists():
            raise ProcessingError(f"Raw output file not found in shared context or does not exist: {input_file}")

        output_dir = input_file.parent / "dcm_output"
        output_dir.mkdir(exist_ok=True)

        result = context.local_handler.run_raw_to_dcm(
            input_file=input_file,
            output_dir=output_dir,
            case_path=context.path
        )

        if not result.success:
            raise ProcessingError(f"RawToDCM failed for beam {context.id}: {result.error}")

        dcm_files = list(output_dir.glob("*.dcm"))
        if not dcm_files:
            raise ProcessingError("No DCM files generated in postprocessing for beam.")
            
        context.logger.info("Postprocessing completed successfully for beam", {
            "beam_id": context.id,
            "output_dir": str(output_dir),
            "dcm_files_count": len(dcm_files)
        })

        input_file.unlink()

        return CompletedState()

    def get_state_name(self) -> str:
        return "Postprocessing"

class CompletedState(WorkflowState):
    """Final completed state for a beam."""

    def execute(self, context: 'WorkflowManager') -> Optional[WorkflowState]:
        """Handles completion tasks for the beam."""
        context.logger.info("Beam workflow completed successfully", {
            "beam_id": context.id,
            "beam_path": str(context.path)
        })
        
        # Final status update for the beam
        context.case_repo.update_beam_status(context.id, BeamStatus.COMPLETED)
        
        # Record workflow completion for the parent case
        beam = context.case_repo.get_beam(context.id)
        if beam:
            context.case_repo.record_workflow_step(
                case_id=beam.parent_case_id,
                step=WorkflowStep.COMPLETED,
                status="completed",
                metadata={"beam_id": context.id, "message": "Beam workflow successfully completed."}
            )
            # After beam is done, check if the parent case is now complete
            update_case_status_from_beams(beam.parent_case_id, context.case_repo)

        return None  # Terminal state

    def get_state_name(self) -> str:
        return "Completed"

class FailedState(WorkflowState):
    """Failed state for beam error handling."""

    def execute(self, context: 'WorkflowManager') -> Optional[WorkflowState]:
        """Handles failure cleanup for the beam."""
        context.logger.error("Beam workflow entered failed state", {
            "beam_id": context.id
        })
        
        # The status is likely already FAILED, but we ensure it here.
        context.case_repo.update_beam_status(context.id, BeamStatus.FAILED)
        
        # Release any allocated GPU resources for this specific beam
        try:
            # The 'assigned_case' in gpu_resources now holds the beam_id
            context.gpu_repo.release_all_for_case(context.id)
            context.logger.info("Released GPU resources for failed beam", {
                "beam_id": context.id
            })
        except Exception as e:
            context.logger.warning("Failed to release GPU resources during cleanup for beam", {
                "beam_id": context.id,
                "error": str(e)
            })
        
        # Record failure in workflow steps for the parent case
        beam = context.case_repo.get_beam(context.id)
        if beam:
            context.case_repo.record_workflow_step(
                case_id=beam.parent_case_id,
                step=WorkflowStep.FAILED,
                status="failed",
                metadata={"beam_id": context.id, "message": "Beam workflow terminated due to error."}
            )
            # After beam has failed, check if the parent case should be marked as failed
            update_case_status_from_beams(beam.parent_case_id, context.case_repo)
        
        return None  # Terminal state

    def get_state_name(self) -> str:
        return "Failed"