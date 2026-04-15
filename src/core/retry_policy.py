"""Retry policy helpers for failed-case classification."""

from __future__ import annotations

from typing import Any

from src.domain.enums import CaseStatus

RETRYABLE_ERROR_PREFIX = "[RETRYABLE] "
PERMANENT_ERROR_PREFIX = "[PERMANENT] "
RETRYABLE_FAILED_CASE_MARKERS = (
    "Could not match delivery folders to RT plan beams",
    "CSV interpreting failed",
)


def mark_retryable_error_message(message: str) -> str:
    """Return a persisted error message that classifies the failure as retryable."""
    text = (message or "").strip()
    if not text:
        return RETRYABLE_ERROR_PREFIX.rstrip()
    if text.startswith(RETRYABLE_ERROR_PREFIX):
        return text
    return f"{RETRYABLE_ERROR_PREFIX}{text}"


def mark_permanent_error_message(message: str) -> str:
    """Return a persisted error message that classifies the failure as permanent."""
    text = (message or "").strip()
    if not text:
        return PERMANENT_ERROR_PREFIX.rstrip()
    if text.startswith(PERMANENT_ERROR_PREFIX):
        return text
    return f"{PERMANENT_ERROR_PREFIX}{text}"


def is_retryable_failed_case(case_data: Any) -> bool:
    """Return True only for FAILED cases with retryable failure markers."""
    if case_data is None or getattr(case_data, "status", None) != CaseStatus.FAILED:
        return False

    failure_category = (getattr(case_data, "failure_category", None) or "").strip().lower()
    if failure_category == "retryable":
        return True
    if failure_category == "permanent":
        return False

    error_message = (getattr(case_data, "error_message", None) or "").strip()
    if not error_message:
        return False
    if error_message.startswith(PERMANENT_ERROR_PREFIX):
        return False
    if error_message.startswith(RETRYABLE_ERROR_PREFIX):
        return True
    return any(marker in error_message for marker in RETRYABLE_FAILED_CASE_MARKERS)
