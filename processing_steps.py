from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from dataclasses import dataclass


@dataclass
class ProcessingContext:
    """Context object containing all resources needed for processing."""
    case_id: str
    logger: Any
    status_display: Any
    directory_manager: Any
    sftp_manager: Any
    remote_executor: Any
    job_scheduler: Any
    case_scanner: Any
    shared_state: Dict[str, Any]
    
    # Processing state
    progress: float = 0.0
    stage: str = ""
    local_path: str = ""
    remote_path: str = ""
    output_path: str = ""


class ProcessingStep(ABC):
    """Abstract base class for processing steps."""
    
    def __init__(self, name: str, progress_weight: float = 0.1):
        self.name = name
        self.progress_weight = progress_weight
    
    @abstractmethod
    def execute(self, context: ProcessingContext) -> bool:
        """Execute the processing step."""
        pass
    
    def _update_progress(self, context: ProcessingContext, stage: str, 
                        additional_info: Dict[str, str] = None) -> None:
        """Update processing progress."""
        context.progress = min(context.progress + self.progress_weight, 1.0)
        context.stage = stage
        
        # Update logger
        context.logger.log_case_progress(
            context.case_id, "PROCESSING", context.progress, 
            {"stage": stage}
        )
        
        # Update status display
        context.status_display.update_case_status(
            case_id=context.case_id,
            status="PROCESSING",
            progress=context.progress,
            stage=stage,
            **(additional_info or {})
        )


class CreateWorkspaceStep(ProcessingStep):
    """Step to create workspace directories."""
    
    def __init__(self):
        super().__init__("Create Workspace")
    
    def execute(self, context: ProcessingContext) -> bool:
        """Create workspace directories (idempotent)."""
        try:
            # This operation is idempotent - creating existing directories is safe
            if not context.directory_manager.create_case_workspace(context.case_id):
                context.logger.error(f"Failed to create workspace for case: {context.case_id}")
                return False
            
            context.logger.info(f"Workspace created/verified for case: {context.case_id}")
            return True
        except Exception as e:
            context.logger.error(f"Error creating workspace for case {context.case_id}: {e}")
            return False


class UploadDataStep(ProcessingStep):
    """Step to upload log data to remote server."""
    
    def __init__(self):
        super().__init__("Upload Data")
    
    def execute(self, context: ProcessingContext) -> bool:
        """Upload log data to remote server (idempotent)."""
        try:
            context.local_path = str(context.directory_manager.get_case_local_path(context.case_id))
            context.remote_path = context.directory_manager.get_case_remote_path(context.case_id)
            
            # Check if remote directory already exists and has content
            # This makes the upload idempotent by allowing overwrite
            if not context.sftp_manager.upload_directory(
                local_path=context.local_path, 
                remote_path=context.remote_path,
                status_display=context.status_display,
                case_id=context.case_id
            ):
                context.logger.error(f"Failed to upload data for case: {context.case_id}")
                return False
            
            # Save the remote path to the case status file
            context.case_scanner.update_case_status(
                case_id=context.case_id,
                status="PROCESSING",  # Keep the status as PROCESSING
                remote_path=context.remote_path
            )
            
            context.logger.info(f"Data uploaded/verified for case: {context.case_id}")
            return True
        except Exception as e:
            context.logger.error(f"Error uploading data for case {context.case_id}: {e}")
            return False


class RunInterpreterStep(ProcessingStep):
    """Step to run Python interpreter."""
    
    def __init__(self):
        super().__init__("Run Interpreter")
    
    def execute(self, context: ProcessingContext) -> bool:
        """Run Python interpreter (idempotent with output check)."""
        try:
            # Check if interpreter output already exists (for idempotency)
            workspace_path = context.directory_manager.get_case_remote_path(context.case_id)
            inputs_check = context.remote_executor.execute_command(f"test -d {workspace_path}/moqui_inputs")
            
            if inputs_check["exit_code"] == 0:
                context.logger.info(f"Interpreter output already exists for case {context.case_id}, skipping")
                return True
            
            # Let run_moqui_interpreter use the case_path as log_dir by default
            # This ensures find_dcm_file_in_logdir searches in the correct directory
            result = context.remote_executor.run_moqui_interpreter(
                context.case_id, 
                status_display=context.status_display
            )
            
            if not result.get("success", False):
                context.logger.error(f"Failed to run interpreter for case: {context.case_id}")
                return False
            
            # Store remote PID in case status for cleanup purposes
            remote_pid = result.get("remote_pid")
            if remote_pid:
                context.case_scanner.update_case_status(
                    case_id=context.case_id,
                    status="PROCESSING",
                    remote_pid=remote_pid
                )
            
            return True
        except Exception as e:
            context.logger.error(f"Error running interpreter for case {context.case_id}: {e}")
            return False


class ExecuteBeamCalculationsStep(ProcessingStep):
    """Step to execute beam calculations."""
    
    def __init__(self):
        super().__init__("Execute Beams")
    
    def execute(self, context: ProcessingContext) -> bool:
        """Execute beam calculations."""
        # Get next job from scheduler
        job = context.job_scheduler.get_next_job()
        if not job:
            context.logger.error(f"Failed to get job for case: {context.case_id}")
            return False
        
        # Update context with GPU allocation
        gpu_allocation = job.get('gpu_allocation', [])
        context.status_display.update_case_status(
            case_id=context.case_id,
            status="PROCESSING",
            stage="Calculating beams",
            gpu_allocation=gpu_allocation,
            beam_info=f"Using GPUs: {gpu_allocation}"
        )
        
        # Execute beam calculations (this would integrate with actual beam execution)
        # For now, we'll simulate completion
        success = True  # This should be replaced with actual beam execution logic
        
        # Complete the job
        context.job_scheduler.complete_job(context.case_id, success)
        
        if not success:
            context.logger.error(f"Failed to execute beams for case: {context.case_id}")
            return False
        
        return True


class RunConverterStep(ProcessingStep):
    """Step to run raw to DICOM converter."""
    
    def __init__(self):
        super().__init__("Run Converter")
    
    def execute(self, context: ProcessingContext) -> bool:
        """Run raw to DICOM converter (idempotent with output check)."""
        try:
            # Check if converter output already exists (for idempotency)
            workspace_path = context.directory_manager.get_case_remote_path(context.case_id)
            dicom_check = context.remote_executor.execute_command(f"test -f {workspace_path}/moqui_output/RTDOSE.dcm")
            
            if dicom_check["exit_code"] == 0:
                context.logger.info(f"DICOM output already exists for case {context.case_id}, skipping")
                return True
            
            if not context.remote_executor.run_raw_to_dicom_converter(context.case_id, status_display=context.status_display):
                context.logger.error(f"Failed to run converter for case: {context.case_id}")
                return False
            
            return True
        except Exception as e:
            context.logger.error(f"Error running converter for case {context.case_id}: {e}")
            return False


class CreateOutputDirectoryStep(ProcessingStep):
    """Step to create output directory."""
    
    def __init__(self):
        super().__init__("Create Output")
    
    def execute(self, context: ProcessingContext) -> bool:
        """Create output directory (idempotent)."""
        try:
            # This operation is idempotent - creating existing directories is safe
            if not context.directory_manager.create_output_directory(context.case_id):
                context.logger.error(f"Failed to create output directory for case: {context.case_id}")
                return False
            
            context.logger.info(f"Output directory created/verified for case: {context.case_id}")
            return True
        except Exception as e:
            context.logger.error(f"Error creating output directory for case {context.case_id}: {e}")
            return False


class CheckDiskSpaceStep(ProcessingStep):
    """Step to check disk space before download."""
    
    def __init__(self):
        super().__init__("Check Disk Space")
    
    def execute(self, context: ProcessingContext) -> bool:
        """Check local disk space before download."""
        estimated_download_size = context.job_scheduler.estimate_case_disk_usage(context.case_id)
        local_space = context.job_scheduler.check_local_disk_space(estimated_download_size)
        
        if not local_space["sufficient"]:
            error_msg = (f"Insufficient local disk space for case {context.case_id}: "
                        f"need {estimated_download_size}GB, have {local_space['free_gb']}GB")
            context.logger.error(error_msg)
            raise Exception(error_msg)
        
        return True


class DownloadResultsStep(ProcessingStep):
    """Step to download results."""
    
    def __init__(self):
        super().__init__("Download Results")
    
    def execute(self, context: ProcessingContext) -> bool:
        """Download results from remote server (idempotent)."""
        try:
            context.output_path = str(context.directory_manager.get_case_output_path(context.case_id))
            
            # Check if results already downloaded (for idempotency)
            import os
            output_exists = os.path.exists(context.output_path) and os.listdir(context.output_path)
            
            if output_exists:
                context.logger.info(f"Results already downloaded for case {context.case_id}, skipping")
                return True
            
            if not context.sftp_manager.download_directory(
                remote_path=context.remote_path, 
                local_path=context.output_path,
                status_display=context.status_display,
                case_id=context.case_id
            ):
                context.logger.error(f"Failed to download results for case: {context.case_id}")
                return False
            
            context.logger.info(f"Results downloaded for case: {context.case_id}")
            return True
        except Exception as e:
            context.logger.error(f"Error downloading results for case {context.case_id}: {e}")
            return False


class CompleteProcessingStep(ProcessingStep):
    """Step to complete processing and update status."""
    
    def __init__(self):
        super().__init__("Complete Processing")
    
    def execute(self, context: ProcessingContext) -> bool:
        """Complete processing and update status."""
        # Update case status
        context.case_scanner.update_case_status(context.case_id, "COMPLETED")
        context.status_display.update_case_status(context.case_id, "COMPLETED", 1.0, "Finished successfully")
        
        # Remove from active cases
        context.shared_state['active_cases'].discard(context.case_id)
        
        return True


class WorkflowEngine:
    """Engine to execute workflow steps."""
    
    def __init__(self):
        self.default_workflow = [
            CreateWorkspaceStep(),
            UploadDataStep(),
            RunInterpreterStep(),
            ExecuteBeamCalculationsStep(),
            RunConverterStep(),
            CreateOutputDirectoryStep(),
            CheckDiskSpaceStep(),
            DownloadResultsStep(),
            CompleteProcessingStep()
        ]
    
    def execute_workflow(self, context: ProcessingContext, 
                        workflow: Optional[list] = None) -> bool:
        """Execute the complete workflow with step resumption support."""
        steps = workflow or self.default_workflow
        
        try:
            # Check if we need to resume from a specific step
            case_status = context.case_scanner.get_case_status(context.case_id)
            last_completed_step = case_status.get('last_completed_step', '') if case_status else ''
            
            start_index = 0
            if last_completed_step:
                # Find the index of the last completed step
                for i, step in enumerate(steps):
                    if step.name == last_completed_step:
                        start_index = i + 1  # Start from the next step
                        context.logger.info(f"Resuming workflow for case {context.case_id} from step {start_index + 1}: {steps[start_index].name if start_index < len(steps) else 'completed'}")
                        break
                else:
                    # Last completed step not found, start from beginning
                    context.logger.warning(f"Last completed step '{last_completed_step}' not found in workflow for case {context.case_id}, starting from beginning")
                    start_index = 0
            
            # Execute steps starting from the determined index
            for i in range(start_index, len(steps)):
                step = steps[i]
                context.logger.info(f"Executing step {i + 1}/{len(steps)}: {step.name} for case {context.case_id}")
                
                if not step.execute(context):
                    context.logger.error(f"Step {step.name} failed for case {context.case_id}")
                    return False
                
                # Mark step as completed in case status
                context.case_scanner.update_case_status(
                    case_id=context.case_id,
                    status="PROCESSING",
                    last_completed_step=step.name
                )
                context.logger.info(f"Completed step: {step.name} for case {context.case_id}")
            
            return True
            
        except Exception as e:
            context.logger.error(f"Workflow execution failed for case {context.case_id}: {e}")
            return False
    
    def create_context(self, case_id: str, **resources) -> ProcessingContext:
        """Create a processing context with all required resources."""
        return ProcessingContext(
            case_id=case_id,
            **resources
        )