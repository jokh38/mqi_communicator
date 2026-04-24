"""Tests for enum and state-machine cleanup behavior."""

from src.domain.enums import (
    CASE_STAGE_MAPPING,
    TOTAL_STAGES,
    CaseStatus,
    StepStatus,
    WorkflowStep,
)


def test_case_stage_mapping_has_contiguous_processing_and_postprocessing_stages():
    assert CASE_STAGE_MAPPING[CaseStatus.PENDING] == 0
    assert CASE_STAGE_MAPPING[CaseStatus.CSV_INTERPRETING] == 1
    assert CASE_STAGE_MAPPING[CaseStatus.PROCESSING] == 2
    assert CASE_STAGE_MAPPING[CaseStatus.POSTPROCESSING] == 3
    assert CASE_STAGE_MAPPING[CaseStatus.COMPLETED] == 4
    assert CASE_STAGE_MAPPING[CaseStatus.FAILED] == 4
    assert CASE_STAGE_MAPPING[CaseStatus.CANCELLED] == 4
    assert TOTAL_STAGES == 4


def test_workflow_step_contains_only_real_pipeline_steps():
    assert {step.value for step in WorkflowStep} == {
        "csv_interpreting",
        "tps_generation",
        "postprocessing",
    }


def test_step_status_captures_persisted_workflow_step_status_values():
    assert {status.value for status in StepStatus} == {
        "started",
        "completed",
        "failed",
        "pending",
        "partial",
    }
    assert str(StepStatus.FAILED) == "failed"


def test_cancelled_case_status_is_reserved_for_future_cancellation():
    assert CaseStatus.CANCELLED.value == "cancelled"
    assert "reserved" in (CaseStatus.__doc__ or "").lower()
