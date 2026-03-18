# =====================================================================================
# Target File: src/domain/enums.py
# Source Reference: src/states.py
# =====================================================================================
"""Defines enumerations for statuses and modes used throughout the application."""

from enum import Enum


class CaseStatus(Enum):
    """Enumeration of possible case statuses."""
    PENDING = "pending"
    CSV_INTERPRETING = "csv_interpreting"
    PROCESSING = "processing"
    POSTPROCESSING = "postprocessing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class BeamStatus(Enum):
    """Enumeration of possible beam statuses.

    Mirrors CaseStatus but is specific to a beam's lifecycle.
    """
    PENDING = "pending"
    CSV_INTERPRETING = "csv_interpreting"
    UPLOADING = "uploading"
    TPS_GENERATION = "tps_generation"
    HPC_QUEUED = "hpc_queued"
    HPC_RUNNING = "hpc_running"
    DOWNLOADING = "downloading"
    POSTPROCESSING = "postprocessing"
    COMPLETED = "completed"
    FAILED = "failed"


class WorkflowStep(Enum):
    """Enumeration of workflow steps."""
    PENDING = "pending"
    CSV_INTERPRETING = "csv_interpreting"
    TPS_GENERATION = "tps_generation"
    UPLOADING = "uploading"
    HPC_SUBMISSION = "hpc_submission"
    SIMULATION_RUNNING = "simulation_running"
    POSTPROCESSING = "postprocessing"
    COMPLETED = "completed"
    FAILED = "failed"


class GpuStatus(Enum):
    """Enumeration of GPU resource statuses."""
    IDLE = "idle"
    ASSIGNED = "assigned"


class ProcessingMode(Enum):
    """Enumeration of processing modes."""
    LOCAL = "local"
    REMOTE = "remote"
    HYBRID = "hybrid"


# Stage mappings for progress display (1-indexed)
# Each distinct pipeline phase gets its own stage number
BEAM_STAGE_MAPPING = {
    BeamStatus.PENDING: 0,
    BeamStatus.CSV_INTERPRETING: 1,
    BeamStatus.UPLOADING: 2,
    BeamStatus.TPS_GENERATION: 3,
    BeamStatus.HPC_QUEUED: 4,
    BeamStatus.HPC_RUNNING: 4,      # Same stage: queued→running is sub-state
    BeamStatus.DOWNLOADING: 5,
    BeamStatus.POSTPROCESSING: 6,
    BeamStatus.COMPLETED: 6,
    BeamStatus.FAILED: 6,
}

CASE_STAGE_MAPPING = {
    CaseStatus.PENDING: 0,
    CaseStatus.CSV_INTERPRETING: 1,
    CaseStatus.PROCESSING: 4,       # Cases in PROCESSING have beams in HPC stages
    CaseStatus.POSTPROCESSING: 6,
    CaseStatus.COMPLETED: 6,
    CaseStatus.FAILED: 6,
    CaseStatus.CANCELLED: 6,
}

TOTAL_STAGES = 6
