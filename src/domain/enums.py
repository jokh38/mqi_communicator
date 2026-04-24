# =====================================================================================
# Target File: src/domain/enums.py
# Source Reference: src/states.py
# =====================================================================================
"""Defines enumerations for statuses and modes used throughout the application."""

from enum import Enum


class CaseStatus(Enum):
    """Enumeration of possible case statuses.

    CANCELLED is reserved for a future cancellation workflow and is not set by
    the current runtime.
    """
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
    """Enumeration of persisted workflow steps."""
    CSV_INTERPRETING = "csv_interpreting"
    TPS_GENERATION = "tps_generation"
    POSTPROCESSING = "postprocessing"


class StepStatus(str, Enum):
    """Enumeration of persisted workflow step status values."""
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    PENDING = "pending"
    PARTIAL = "partial"

    def __str__(self) -> str:
        return self.value


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
    CaseStatus.PROCESSING: 2,
    CaseStatus.POSTPROCESSING: 3,
    CaseStatus.COMPLETED: 4,
    CaseStatus.FAILED: 4,
    CaseStatus.CANCELLED: 4,
}

TOTAL_STAGES = 4
