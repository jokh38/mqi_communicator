from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from dataclasses import dataclass
import logging


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
        """Create workspace directories."""
        if not context.directory_manager.create_case_workspace(context.case_id):
            logging.error(f"Failed to create workspace for case: {context.case_id}")
            return False
        
        return True


class UploadDataStep(ProcessingStep):
    """Step to upload log data to remote server."""
    
    def __init__(self):
        super().__init__("Upload Data")
    
    def execute(self, context: ProcessingContext) -> bool:
        """Upload log data to remote server."""
        context.local_path = str(context.directory_manager.get_case_local_path(context.case_id))
        context.remote_path = context.directory_manager.get_case_remote_path(context.case_id)
        
        if not context.sftp_manager.upload_directory(context.local_path, context.remote_path):
            logging.error(f"Failed to upload data for case: {context.case_id}")
            return False
        
        return True


class RunInterpreterStep(ProcessingStep):
    """Step to run Python interpreter."""
    
    def __init__(self):
        super().__init__("Run Interpreter")
    
    def execute(self, context: ProcessingContext) -> bool:
        """Run Python interpreter."""
        if not context.remote_executor.run_moqui_interpreter(context.case_id):
            logging.error(f"Failed to run interpreter for case: {context.case_id}")
            return False
        
        return True


class ExecuteBeamCalculationsStep(ProcessingStep):
    """Step to execute beam calculations."""
    
    def __init__(self):
        super().__init__("Execute Beams")
    
    def execute(self, context: ProcessingContext) -> bool:
        """Execute beam calculations."""
        # Get next job from scheduler
        job = context.job_scheduler.get_next_job()
        if not job:
            logging.error(f"Failed to get job for case: {context.case_id}")
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
            logging.error(f"Failed to execute beams for case: {context.case_id}")
            return False
        
        return True


class RunConverterStep(ProcessingStep):
    """Step to run raw to DICOM converter."""
    
    def __init__(self):
        super().__init__("Run Converter")
    
    def execute(self, context: ProcessingContext) -> bool:
        """Run raw to DICOM converter."""
        if not context.remote_executor.run_raw_to_dicom_converter(context.case_id):
            logging.error(f"Failed to run converter for case: {context.case_id}")
            return False
        
        return True


class CreateOutputDirectoryStep(ProcessingStep):
    """Step to create output directory."""
    
    def __init__(self):
        super().__init__("Create Output")
    
    def execute(self, context: ProcessingContext) -> bool:
        """Create output directory."""
        if not context.directory_manager.create_output_directory(context.case_id):
            logging.error(f"Failed to create output directory for case: {context.case_id}")
            return False
        
        return True


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
            logging.error(error_msg)
            raise Exception(error_msg)
        
        return True


class DownloadResultsStep(ProcessingStep):
    """Step to download results."""
    
    def __init__(self):
        super().__init__("Download Results")
    
    def execute(self, context: ProcessingContext) -> bool:
        """Download results from remote server."""
        context.output_path = str(context.directory_manager.get_case_output_path(context.case_id))
        
        if not context.sftp_manager.download_directory(context.remote_path, context.output_path):
            logging.error(f"Failed to download results for case: {context.case_id}")
            return False
        
        return True


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
        """Execute the complete workflow."""
        steps = workflow or self.default_workflow
        
        try:
            for step in steps:
                logging.info(f"Executing step: {step.name} for case {context.case_id}")
                
                if not step.execute(context):
                    logging.error(f"Step {step.name} failed for case {context.case_id}")
                    return False
            
            return True
            
        except Exception as e:
            logging.error(f"Workflow execution failed for case {context.case_id}: {e}")
            return False
    
    def create_context(self, case_id: str, **resources) -> ProcessingContext:
        """Create a processing context with all required resources."""
        return ProcessingContext(
            case_id=case_id,
            **resources
        )