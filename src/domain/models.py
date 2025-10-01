# =====================================================================================
# Target File: src/domain/models.py
# Source Reference: Data structures from various original files
# =====================================================================================
"""Defines Data Transfer Objects (DTOs) for the application's domain models."""

from dataclasses import dataclass
from typing import Optional, Dict, Any, List
from pathlib import Path
from datetime import datetime

from src.domain.enums import CaseStatus, GpuStatus, WorkflowStep, BeamStatus

@dataclass
class CaseData:
    """Data Transfer Object for case information."""
    case_id: str
    case_path: Path
    status: CaseStatus
    progress: float
    created_at: datetime
    updated_at: Optional[datetime] = None
    error_message: Optional[str] = None
    assigned_gpu: Optional[str] = None
    interpreter_completed: bool = False

@dataclass
class BeamData:
    """Data Transfer Object for beam information."""
    beam_id: str
    parent_case_id: str
    beam_path: Path
    status: BeamStatus
    created_at: datetime
    updated_at: Optional[datetime] = None
    hpc_job_id: Optional[str] = None

@dataclass
class GpuResource:
    """Data Transfer Object for GPU resource information."""
    uuid: str
    gpu_index: int
    name: str
    memory_total: int
    memory_used: int
    memory_free: int
    temperature: int
    utilization: int
    status: GpuStatus
    assigned_case: Optional[str] = None
    last_updated: Optional[datetime] = None

@dataclass
class WorkflowStepRecord:
    """Data Transfer Object for workflow step tracking."""
    case_id: str
    step: WorkflowStep
    status: str
    started_at: datetime
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

@dataclass
class SystemStats:
    """Data Transfer Object for system statistics."""
    total_cases: int
    active_cases: int
    completed_cases: int
    failed_cases: int
    total_gpus: int
    available_gpus: int
    busy_gpus: int
    last_updated: datetime