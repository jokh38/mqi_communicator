# =====================================================================================
# Target File: src/ui/formatter.py
# Source Reference: src/display_handler.py
# =====================================================================================
"""Contains helper functions for formatting data for the UI."""

from typing import Dict, Any
from datetime import timedelta

from rich.text import Text

from src.domain.enums import CaseStatus, GpuStatus

# Color mappings for different statuses
CASE_STATUS_COLORS = {
    CaseStatus.PENDING: "yellow",
    CaseStatus.CSV_INTERPRETING: "cyan",
    CaseStatus.PROCESSING: "bold blue",
    CaseStatus.POSTPROCESSING: "bold magenta",
    CaseStatus.COMPLETED: "bold green",
    CaseStatus.FAILED: "bold red",
}

GPU_STATUS_COLORS = {
    GpuStatus.IDLE: "green",
    GpuStatus.ASSIGNED: "yellow",
    GpuStatus.UNAVAILABLE: "red",
}


def get_case_status_text(status: CaseStatus) -> Text:
    """Returns a rich Text object for a case status.

    Args:
        status (CaseStatus): The case status.

    Returns:
        Text: A `rich` Text object with appropriate color.
    """
    color = CASE_STATUS_COLORS.get(status, "white")
    return Text(status.value.upper(), style=color)


def get_gpu_status_text(status: GpuStatus) -> Text:
    """Returns a rich Text object for a GPU status.

    Args:
        status (GpuStatus): The GPU status.

    Returns:
        Text: A `rich` Text object with appropriate color.
    """
    color = GPU_STATUS_COLORS.get(status, "white")
    return Text(status.value.upper(), style=color)


def format_memory_usage(used_mb: int, total_mb: int) -> Text:
    """Formats memory usage like '1024 / 4096 MB'.

    Args:
        used_mb (int): The used memory in MB.
        total_mb (int): The total memory in MB.

    Returns:
        Text: A `rich` Text object.
    """
    return Text(f"{used_mb} / {total_mb} MB", style="white")


def format_utilization(utilization: int) -> Text:
    """Formats utilization with a color based on value.

    Args:
        utilization (int): The utilization percentage.

    Returns:
        Text: A `rich` Text object with appropriate color.
    """
    color = "green"
    if utilization > 70:
        color = "yellow"
    if utilization > 90:
        color = "red"
    return Text(f"{utilization}%", style=color)


def format_temperature(temp: int) -> Text:
    """Formats temperature with a color based on value.

    Args:
        temp (int): The temperature in Celsius.

    Returns:
        Text: A `rich` Text object with appropriate color.
    """
    color = "green"
    if temp > 75:
        color = "yellow"
    if temp > 85:
        color = "red"
    return Text(f"{temp}°C", style=color)


def format_progress_bar(progress: float, width: int = 20) -> Text:
    """Creates a text-based progress bar.

    Args:
        progress (float): The progress percentage (0-100).
        width (int, optional): The width of the progress bar. Defaults to 20.

    Returns:
        Text: A `rich` Text object representing the progress bar.
    """
    if progress is None:
        progress = 0.0
    filled_width = int(progress / 100 * width)
    bar = "█" * filled_width + "─" * (width - filled_width)
    
    color = "yellow"
    if progress > 30:
        color = "cyan"
    if progress > 70:
        color = "blue"
    if progress == 100:
        color = "green"
        
    return Text(f"[{bar}] {progress:.1f}%", style=color)


def format_elapsed_time(seconds: float) -> str:
    """Formats elapsed seconds into a human-readable string like '1h 15m 30s'.

    Args:
        seconds (float): The elapsed time in seconds.

    Returns:
        str: A formatted string.
    """
    if seconds is None:
        return "N/A"
    delta = timedelta(seconds=int(seconds))
    return str(delta)