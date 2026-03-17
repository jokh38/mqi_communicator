"""Tests for the simplified GPU status model."""

import pytest

from src.domain.enums import GpuStatus


def test_gpu_status_only_exposes_idle_and_assigned() -> None:
    assert [status.value for status in GpuStatus] == ["idle", "assigned"]


@pytest.mark.parametrize("legacy_status", ["busy", "unavailable"])
def test_legacy_gpu_status_values_are_invalid(legacy_status: str) -> None:
    with pytest.raises(ValueError):
        GpuStatus(legacy_status)
