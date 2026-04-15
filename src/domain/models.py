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
    failure_category: Optional[str] = None
    failure_phase: Optional[str] = None
    failure_details: Optional[Dict[str, Any]] = None
    assigned_gpu: Optional[str] = None
    interpreter_completed: bool = False
    retry_count: int = 0
    ptn_checker_run_count: int = 0
    ptn_checker_last_run_at: Optional[datetime] = None
    ptn_checker_status: Optional[str] = None

@dataclass
class BeamData:
    """Data Transfer Object for beam information."""
    beam_id: str
    parent_case_id: str
    beam_path: Path
    beam_number: Optional[int]
    status: BeamStatus
    progress: float
    created_at: datetime
    updated_at: Optional[datetime] = None
    hpc_job_id: Optional[str] = None
    error_message: Optional[str] = None


@dataclass
class DeliveryData:
    """Data Transfer Object for a single delivered beam log session."""

    delivery_id: str
    parent_case_id: str
    beam_id: str
    delivery_path: Path
    delivery_timestamp: datetime
    delivery_date: str
    raw_beam_number: Optional[int]
    treatment_beam_index: Optional[int]
    is_reference_delivery: bool
    fraction_index: Optional[int] = None
    ptn_status: Optional[str] = None
    ptn_last_run_at: Optional[datetime] = None
    gamma_pass_rate: Optional[float] = None
    gamma_mean: Optional[float] = None
    gamma_max: Optional[float] = None
    evaluated_points: Optional[int] = None
    report_path: Optional[Path] = None
    error_message: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


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
    core_clock: int
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
    last_updated: datetime

# -------------------------------------------------------------------------------------
# Phase 1 DTOs for job descriptions (additive; non-breaking)
# -------------------------------------------------------------------------------------

@dataclass
class BeamJob:
    """Lightweight descriptor for a beam processing job."""
    beam_id: str
    beam_path: Path

@dataclass
class CaseJob:
    """Lightweight descriptor for a case processing job with optional beams."""
    case_id: str
    case_path: Path
    beam_jobs: Optional[List[BeamJob]] = None
