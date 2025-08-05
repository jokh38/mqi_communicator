from typing import Dict, Any, Optional
from datetime import datetime
from .base import StatefulObject


class Job(StatefulObject):
    """Job model that encapsulates all properties and state logic for a job."""
    
    def __init__(self, job_id: str, case_id: str = "", command: str = ""):
        super().__init__(job_id)
        self.case_id = case_id
        self.command = command
        self.priority = 0
        self.created_time = self.last_updated
        self.scheduled_time = ""
        self.start_time = ""
        self.end_time = ""
        self.execution_time = 0.0
        self.result_code = None
        self.stdout = ""
        self.stderr = ""
        self.resource_requirements: Dict[str, Any] = {}
        self.allocated_resources: Dict[str, Any] = {}
        self.retry_count = 0
        self.max_retries = 3
        
    def schedule(self, scheduled_time: Optional[str] = None) -> None:
        """Transition job to scheduled state."""
        self._transition_to("SCHEDULED")
        self.scheduled_time = scheduled_time or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    def start_execution(self, allocated_resources: Optional[Dict[str, Any]] = None) -> None:
        """Transition job to running state."""
        self._transition_to("RUNNING")
        self.start_time = self.last_updated
        if allocated_resources:
            self.allocated_resources = allocated_resources
    
    def complete(self, result_code: int = 0, stdout: str = "", stderr: str = "") -> None:
        """Transition job to completed state."""
        self._transition_to("COMPLETED")
        self.end_time = self.last_updated
        self.result_code = result_code
        self.stdout = stdout
        self.stderr = stderr
        
        # Calculate execution time
        if self.start_time:
            try:
                start = datetime.strptime(self.start_time, "%Y-%m-%d %H:%M:%S")
                end = datetime.strptime(self.end_time, "%Y-%m-%d %H:%M:%S")
                self.execution_time = (end - start).total_seconds()
            except ValueError:
                self.execution_time = 0.0
    
    def fail(self, error_message: str = "", result_code: int = -1, 
             stdout: str = "", stderr: str = "") -> None:
        """Transition job to failed state."""
        self._transition_to("FAILED")
        self.end_time = self.last_updated
        self.result_code = result_code
        self.stdout = stdout
        self.stderr = stderr
        
        # Calculate execution time if started
        if self.start_time:
            try:
                start = datetime.strptime(self.start_time, "%Y-%m-%d %H:%M:%S")
                end = datetime.strptime(self.end_time, "%Y-%m-%d %H:%M:%S")
                self.execution_time = (end - start).total_seconds()
            except ValueError:
                self.execution_time = 0.0
    
    def cancel(self) -> None:
        """Transition job to cancelled state."""
        self._transition_to("CANCELLED")
        self.end_time = self.last_updated
    
    def retry(self) -> bool:
        """Attempt to retry the job if retries are available."""
        if self.retry_count < self.max_retries:
            self.retry_count += 1
            self._transition_to("PENDING")
            self.start_time = ""
            self.end_time = ""
            self.result_code = None
            self.stdout = ""
            self.stderr = ""
            self.execution_time = 0.0
            return True
        return False
    
    def set_resource_requirements(self, requirements: Dict[str, Any]) -> None:
        """Set resource requirements for the job."""
        self.resource_requirements = requirements
    
    def set_priority(self, priority: int) -> None:
        """Set job priority."""
        self.priority = priority
    
    def is_pending(self) -> bool:
        """Check if job is pending."""
        return self.status == "PENDING"
    
    def is_scheduled(self) -> bool:
        """Check if job is scheduled."""
        return self.status == "SCHEDULED"
    
    def is_running(self) -> bool:
        """Check if job is running."""
        return self.status == "RUNNING"
    
    def is_completed(self) -> bool:
        """Check if job is completed."""
        return self.status == "COMPLETED"
    
    def is_failed(self) -> bool:
        """Check if job has failed."""
        return self.status == "FAILED"
    
    def is_cancelled(self) -> bool:
        """Check if job is cancelled."""
        return self.status == "CANCELLED"
    
    def has_finished(self) -> bool:
        """Check if job has finished (completed, failed, or cancelled)."""
        return self.status in ["COMPLETED", "FAILED", "CANCELLED"]
    
    def can_retry(self) -> bool:
        """Check if job can be retried."""
        return self.retry_count < self.max_retries and self.is_failed()
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert job to dictionary for storage."""
        return {
            "job_id": self.obj_id,
            "case_id": self.case_id,
            "command": self.command,
            "status": self.status,
            "priority": self.priority,
            "created_time": self.created_time,
            "scheduled_time": self.scheduled_time,
            "start_time": self.start_time,
            "last_updated": self.last_updated,
            "end_time": self.end_time,
            "execution_time": self.execution_time,
            "result_code": self.result_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "resource_requirements": self.resource_requirements,
            "allocated_resources": self.allocated_resources,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Job':
        """Create job from dictionary."""
        job = cls(data["job_id"], data.get("case_id", ""), data.get("command", ""))
        job.status = data.get("status", "PENDING")
        job.priority = data.get("priority", 0)
        job.created_time = data.get("created_time", job.created_time)
        job.scheduled_time = data.get("scheduled_time", "")
        job.start_time = data.get("start_time", "")
        job.last_updated = data.get("last_updated", job.last_updated)
        job.end_time = data.get("end_time", "")
        job.execution_time = data.get("execution_time", 0.0)
        job.result_code = data.get("result_code", None)
        job.stdout = data.get("stdout", "")
        job.stderr = data.get("stderr", "")
        job.resource_requirements = data.get("resource_requirements", {})
        job.allocated_resources = data.get("allocated_resources", {})
        job.retry_count = data.get("retry_count", 0)
        job.max_retries = data.get("max_retries", 3)
        return job