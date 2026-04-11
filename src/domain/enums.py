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
    TPS_GENERATION = "tps_generation"
    SIMULATION_RUNNING = "simulation_running"
    POSTPROCESSING = "postprocessing"
    COMPLETED = "completed"
    FAILED = "failed"


class WorkflowStep(Enum):
    """Enumeration of workflow steps."""
    PENDING = "pending"
    CSV_INTERPRETING = "csv_interpreting"
    TPS_GENERATION = "tps_generation"
    SIMULATION_RUNNING = "simulation_running"
    POSTPROCESSING = "postprocessing"
    COMPLETED = "completed"
    FAILED = "failed"


class GpuStatus(Enum):
    """Enumeration of GPU resource statuses."""
    IDLE = "idle"
    ASSIGNED = "assigned"


# Stage mappings for progress display (1-indexed)
# Each distinct pipeline phase gets its own stage number
BEAM_STAGE_MAPPING = {
    BeamStatus.PENDING: 0,
    BeamStatus.CSV_INTERPRETING: 1,
    BeamStatus.TPS_GENERATION: 2,
    BeamStatus.SIMULATION_RUNNING: 3,
    BeamStatus.POSTPROCESSING: 4,
    BeamStatus.COMPLETED: 4,
    BeamStatus.FAILED: 4,
}

CASE_STAGE_MAPPING = {
    CaseStatus.PENDING: 0,
    CaseStatus.CSV_INTERPRETING: 1,
    CaseStatus.PROCESSING: 3,
    CaseStatus.POSTPROCESSING: 4,
    CaseStatus.COMPLETED: 4,
    CaseStatus.FAILED: 4,
    CaseStatus.CANCELLED: 4,
}

TOTAL_STAGES = 4
