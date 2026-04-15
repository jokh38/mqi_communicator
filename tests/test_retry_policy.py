from pathlib import Path
from types import SimpleNamespace

from src.core.retry_policy import (
    PERMANENT_ERROR_PREFIX,
    RETRYABLE_ERROR_PREFIX,
    is_retryable_failed_case,
    mark_permanent_error_message,
    mark_retryable_error_message,
)
from src.domain.enums import CaseStatus


def _case(
    *,
    status: CaseStatus = CaseStatus.FAILED,
    error_message: str | None = None,
    failure_category: str | None = None,
):
    return SimpleNamespace(
        case_id="case-123",
        case_path=Path("/cases/case-123"),
        status=status,
        error_message=error_message,
        failure_category=failure_category,
    )


def test_structured_retryable_category_overrides_legacy_message_heuristics():
    case_data = _case(
        error_message=f"{PERMANENT_ERROR_PREFIX} do not retry",
        failure_category="retryable",
    )

    assert is_retryable_failed_case(case_data) is True


def test_structured_permanent_category_blocks_retry_even_for_legacy_retryable_message():
    case_data = _case(
        error_message=f"{RETRYABLE_ERROR_PREFIX} temporary failure",
        failure_category="permanent",
    )

    assert is_retryable_failed_case(case_data) is False


def test_retry_policy_helpers_mark_messages_idempotently():
    retryable_message = mark_retryable_error_message("CSV interpreting failed")
    permanent_message = mark_permanent_error_message("Validation failed")

    assert retryable_message == f"{RETRYABLE_ERROR_PREFIX}CSV interpreting failed"
    assert mark_retryable_error_message(retryable_message) == retryable_message
    assert permanent_message == f"{PERMANENT_ERROR_PREFIX}Validation failed"
    assert mark_permanent_error_message(permanent_message) == permanent_message
