"""
Processing Steps Module - Workflow Engine for managing processing steps.

This module provides workflow management functionality for the MOQUI automation system.
"""

from typing import Dict, List, Any, Optional, Callable
from enum import Enum


class StepStatus(Enum):
    """Status of a processing step."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class ProcessingStep:
    """Represents a single processing step."""
    
    def __init__(self, step_id: str, name: str, description: str = "", 
                 dependencies: List[str] = None, required: bool = True):
        """Initialize a processing step."""
        self.step_id = step_id
        self.name = name
        self.description = description
        self.dependencies = dependencies or []
        self.required = required
        self.status = StepStatus.PENDING
        self.result = None
        self.error_message = ""
        self.start_time = None
        self.end_time = None
    
    def reset(self) -> None:
        """Reset step to pending state."""
        self.status = StepStatus.PENDING
        self.result = None
        self.error_message = ""
        self.start_time = None
        self.end_time = None


class WorkflowEngine:
    """Manages workflow execution and step dependencies."""
    
    def __init__(self):
        """Initialize workflow engine."""
        self.steps: Dict[str, ProcessingStep] = {}
        self.workflows: Dict[str, List[str]] = {}
        self.current_workflow = None
        
    def add_step(self, step: ProcessingStep) -> None:
        """Add a processing step to the engine."""
        self.steps[step.step_id] = step
    
    def create_workflow(self, workflow_id: str, step_ids: List[str]) -> bool:
        """Create a workflow with the specified steps."""
        # Validate that all steps exist
        for step_id in step_ids:
            if step_id not in self.steps:
                return False
        
        self.workflows[workflow_id] = step_ids
        return True
    
    def execute_workflow(self, workflow_id: str, context: Dict[str, Any] = None) -> bool:
        """Execute a workflow."""
        if workflow_id not in self.workflows:
            return False
        
        self.current_workflow = workflow_id
        step_ids = self.workflows[workflow_id]
        context = context or {}
        
        # Reset all steps in the workflow
        for step_id in step_ids:
            if step_id in self.steps:
                self.steps[step_id].reset()
        
        # Execute steps in order (simplified implementation)
        for step_id in step_ids:
            if step_id in self.steps:
                step = self.steps[step_id]
                step.status = StepStatus.RUNNING
                
                # Placeholder execution - in real implementation, this would
                # call the actual step execution function
                try:
                    # Simulate step execution
                    step.status = StepStatus.COMPLETED
                    step.result = f"Step {step_id} completed successfully"
                except Exception as e:
                    step.status = StepStatus.FAILED
                    step.error_message = str(e)
                    if step.required:
                        return False
        
        return True
    
    def get_step_status(self, step_id: str) -> Optional[StepStatus]:
        """Get the status of a specific step."""
        if step_id in self.steps:
            return self.steps[step_id].status
        return None
    
    def get_workflow_status(self, workflow_id: str) -> Dict[str, Any]:
        """Get the status of a workflow."""
        if workflow_id not in self.workflows:
            return {"error": "Workflow not found"}
        
        step_ids = self.workflows[workflow_id]
        step_statuses = {}
        
        for step_id in step_ids:
            if step_id in self.steps:
                step = self.steps[step_id]
                step_statuses[step_id] = {
                    "status": step.status.value,
                    "name": step.name,
                    "result": step.result,
                    "error_message": step.error_message
                }
        
        return {
            "workflow_id": workflow_id,
            "steps": step_statuses,
            "overall_status": self._calculate_overall_status(step_ids)
        }
    
    def _calculate_overall_status(self, step_ids: List[str]) -> str:
        """Calculate overall workflow status based on step statuses."""
        all_completed = True
        any_failed = False
        any_running = False
        
        for step_id in step_ids:
            if step_id in self.steps:
                status = self.steps[step_id].status
                if status == StepStatus.FAILED:
                    any_failed = True
                elif status == StepStatus.RUNNING:
                    any_running = True
                elif status != StepStatus.COMPLETED:
                    all_completed = False
        
        if any_failed:
            return "failed"
        elif any_running:
            return "running"
        elif all_completed:
            return "completed"
        else:
            return "pending"
    
    def abort_workflow(self, workflow_id: str) -> bool:
        """Abort a running workflow."""
        if workflow_id not in self.workflows:
            return False
        
        # Mark all running steps as failed
        step_ids = self.workflows[workflow_id]
        for step_id in step_ids:
            if step_id in self.steps:
                step = self.steps[step_id]
                if step.status == StepStatus.RUNNING:
                    step.status = StepStatus.FAILED
                    step.error_message = "Workflow aborted"
        
        return True