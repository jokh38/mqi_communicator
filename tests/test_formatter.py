import pytest
from rich.text import Text

from src.ui import formatter
from src.domain.enums import CaseStatus, GpuStatus

def test_get_case_status_text():
    """Tests that case status enums are converted to correctly styled Text."""
    text = formatter.get_case_status_text(CaseStatus.PROCESSING)
    assert isinstance(text, Text)
    assert text.plain == "PROCESSING"
    assert text.style == "bold blue"

    text_failed = formatter.get_case_status_text(CaseStatus.FAILED)
    assert text_failed.style == "bold red"

def test_get_gpu_status_text():
    """Tests that GPU status enums are converted to correctly styled Text."""
    text = formatter.get_gpu_status_text(GpuStatus.IDLE)
    assert isinstance(text, Text)
    assert text.plain == "IDLE"
    assert text.style == "green"

    text_busy = formatter.get_gpu_status_text(GpuStatus.BUSY)
    assert text_busy.style == "yellow" # Assuming 'BUSY' maps to a color, adjust if needed

def test_format_memory_usage():
    """Tests memory usage formatting."""
    text = formatter.format_memory_usage(1024, 4096)
    assert isinstance(text, Text)
    assert text.plain == "1024 / 4096 MB"

def test_format_utilization():
    """Tests utilization formatting and color coding."""
    text_low = formatter.format_utilization(50)
    assert text_low.plain == "50%"
    assert text_low.style == "green"

    text_med = formatter.format_utilization(75)
    assert text_med.style == "yellow"

    text_high = formatter.format_utilization(95)
    assert text_high.style == "red"

def test_format_temperature():
    """Tests temperature formatting and color coding."""
    text_low = formatter.format_temperature(60)
    assert text_low.plain == "60°C"
    assert text_low.style == "green"

    text_med = formatter.format_temperature(80)
    assert text_med.style == "yellow"

    text_high = formatter.format_temperature(90)
    assert text_high.style == "red"

def test_format_progress_bar():
    """Tests progress bar generation."""
    text = formatter.format_progress_bar(50.0, width=10)
    assert isinstance(text, Text)
    assert "█████─────" in text.plain
    assert "50.0%" in text.plain

    text_full = formatter.format_progress_bar(100.0, width=10)
    assert "██████████" in text_full.plain
    assert text_full.style == "green"

def test_format_elapsed_time():
    """Tests elapsed time formatting."""
    # 90 seconds = 1 minute 30 seconds
    formatted_time = formatter.format_elapsed_time(90)
    assert "1:30" in formatted_time or "0:01:30" in formatted_time

    # 3661 seconds = 1 hour 1 minute 1 second
    formatted_time_hr = formatter.format_elapsed_time(3661)
    assert "1:01:01" in formatted_time_hr

    # Test None input
    formatted_time_none = formatter.format_elapsed_time(None)
    assert formatted_time_none == "N/A"
