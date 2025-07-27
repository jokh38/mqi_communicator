from typing import Dict, Any, List, Optional
from datetime import datetime
from .base import StatefulObject


class Case(StatefulObject):
    """Case model that encapsulates all properties and state logic for a case."""
    
    def __init__(self, case_id: str, case_path: str = ""):
        super().__init__(case_id)
        self.case_path = case_path
        self.start_time = self.last_updated
        self.end_time = ""
        self.retry_count = 0
        self.current_task = None
        self.last_completed_step = ""
        self.error_message = ""
        self.gpu_allocation: List[int] = []
        self.remote_path = ""
        self.remote_pid = None
        self.locked_gpus: List[int] = []
    
    def start_processing(self, current_task: str = "Starting processing", 
                        gpu_allocation: List[int] = None, 
                        remote_path: str = "", 
                        remote_pid: int = None,
                        locked_gpus: List[int] = None) -> None:
        """Transition case to processing state."""
        self._transition_to("PROCESSING")
        self.current_task = current_task
        self.gpu_allocation = gpu_allocation or []
        self.remote_path = remote_path
        self.remote_pid = remote_pid
        self.locked_gpus = locked_gpus or []
    
    def complete(self, last_completed_step: str = "All steps completed") -> None:
        """Transition case to completed state."""
        self._transition_to("COMPLETED")
        self.end_time = self.last_updated
        self.current_task = None
        self.last_completed_step = last_completed_step
    
    def fail(self, error_message: str, last_completed_step: str = "", retry_count: Optional[int] = None) -> None:
        """Transition case to failed state."""
        self._transition_to("FAILED") 
        self.end_time = self.last_updated
        self.current_task = None
        self.error_message = error_message
        self.last_completed_step = last_completed_step
        if retry_count is not None:
            self.retry_count = retry_count
    
    def reset(self) -> None:
        """Reset case status to allow reprocessing."""
        self._transition_to("NEW")
        self.retry_count = 0
        self.end_time = ""
        self.error_message = ""
        self.current_task = None
    
    def update_task(self, current_task: str) -> None:
        """Update current task and timestamp."""
        self.current_task = current_task
        self.last_updated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    def is_processing(self) -> bool:
        """Check if case is currently processing."""
        return self.status == "PROCESSING"
    
    def is_completed(self) -> bool:
        """Check if case is completed."""
        return self.status == "COMPLETED"
    
    def is_failed(self) -> bool:
        """Check if case has failed."""
        return self.status == "FAILED"
    
    def has_gpu_allocation(self) -> bool:
        """Check if case has GPU allocation."""
        return len(self.gpu_allocation) > 0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert case to dictionary for storage."""
        return {
            "case_id": self.obj_id,
            "case_path": self.case_path,
            "status": self.status,
            "start_time": self.start_time,
            "last_updated": self.last_updated,
            "end_time": self.end_time,
            "retry_count": self.retry_count,
            "current_task": self.current_task,
            "last_completed_step": self.last_completed_step,
            "error_message": self.error_message,
            "gpu_allocation": self.gpu_allocation,
            "remote_path": self.remote_path,
            "remote_pid": self.remote_pid,
            "locked_gpus": self.locked_gpus
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Case':
        """Create case from dictionary."""
        case = cls(data["case_id"], data.get("case_path", ""))
        case.status = data.get("status", "NEW")
        case.start_time = data.get("start_time", case.start_time)
        case.last_updated = data.get("last_updated", case.last_updated)
        case.end_time = data.get("end_time", "")
        case.retry_count = data.get("retry_count", 0)
        case.current_task = data.get("current_task", None)
        case.last_completed_step = data.get("last_completed_step", "")
        case.error_message = data.get("error_message", "")
        case.gpu_allocation = data.get("gpu_allocation", [])
        case.remote_path = data.get("remote_path", "")
        case.remote_pid = data.get("remote_pid", None)
        case.locked_gpus = data.get("locked_gpus", [])
        return case