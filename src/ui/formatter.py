# =====================================================================================
# Target File: src/ui/formatter.py
# Source Reference: src/display_handler.py
# =====================================================================================
"""Contains helper functions for formatting data for the UI."""

from typing import Dict, Any
from datetime import timedelta

from rich.text import Text

from src.domain.enums import (
    CaseStatus, GpuStatus, BeamStatus,
    BEAM_STAGE_MAPPING, CASE_STAGE_MAPPING, TOTAL_STAGES,
)

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
}

BEAM_STATUS_COLORS = {
    BeamStatus.PENDING: "yellow",
    BeamStatus.CSV_INTERPRETING: "cyan",
    BeamStatus.UPLOADING: "magenta",
    BeamStatus.TPS_GENERATION: "blue",
    BeamStatus.HPC_QUEUED: "bold yellow",
    BeamStatus.HPC_RUNNING: "bold blue",
    BeamStatus.DOWNLOADING: "bold cyan",
    BeamStatus.POSTPROCESSING: "bold magenta",
    BeamStatus.COMPLETED: "bold green",
    BeamStatus.FAILED: "bold red",
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


def get_beam_status_text(status: BeamStatus) -> Text:
    """Returns a rich Text object for a beam status.

    Args:
        status (BeamStatus): The beam status.

    Returns:
        Text: A `rich` Text object with appropriate color.
    """
    color = BEAM_STATUS_COLORS.get(status, "white")
    # Shorten status text for compact display
    display_text = status.value.replace("_", " ").upper()
    return Text(display_text, style=color)


def format_memory_usage(used_mb: int, total_mb: int) -> Text:
    """Formats memory usage like '1.0 / 4.0 GB'.

    Args:
        used_mb (int): The used memory in MB.
        total_mb (int): The total memory in MB.

    Returns:
        Text: A `rich` Text object.
    """
    used_gb = used_mb / 1024
    total_gb = total_mb / 1024
    return Text(f"{used_gb:.1f} / {total_gb:.1f} GB", style="white")


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


def format_stage_display(
    status,
    progress: float = 0.0,
    total_stages: int = TOTAL_STAGES,
) -> Text:
    """Format progress as stage number with intra-stage bar during HPC_RUNNING.

    Args:
        status: BeamStatus or CaseStatus enum.
        progress: Raw beam progress (0-100) from database.
        total_stages: Total pipeline stages (default 7).
    """
    if isinstance(status, BeamStatus):
        current_stage = BEAM_STAGE_MAPPING.get(status, 0)
    elif isinstance(status, CaseStatus):
        current_stage = CASE_STAGE_MAPPING.get(status, 0)
    else:
        current_stage = 0

    if status in (BeamStatus.FAILED, CaseStatus.FAILED):
        color = "bold red"
    elif current_stage >= total_stages:
        color = "bold green"
    elif current_stage >= total_stages - 1:
        color = "cyan"
    elif current_stage >= total_stages // 2:
        color = "blue"
    else:
        color = "yellow"

    stage_text = Text(f"{current_stage}/{total_stages}", style=color)

    # Show intra-stage progress bar only during HPC_RUNNING (MC calculation)
    if status == BeamStatus.HPC_RUNNING and progress > 0:
        intra = _intra_stage_progress(progress, stage_start=30.0, stage_end=90.0)
        filled = int(intra / 100 * 10)
        bar = "\u2588" * filled + "\u2500" * (10 - filled)
        return Text.assemble(
            stage_text,
            Text(f" [{bar}] ", style="dim"),
            Text(f"{intra:.0f}%", style=color),
        )

    # Show progress bar for active case statuses
    if isinstance(status, CaseStatus) and status in (
        CaseStatus.CSV_INTERPRETING, CaseStatus.PROCESSING, CaseStatus.POSTPROCESSING,
    ) and progress > 0:
        filled = int(progress / 100 * 10)
        bar = "\u2588" * filled + "\u2500" * (10 - filled)
        return Text.assemble(
            stage_text,
            Text(f" [{bar}] ", style="dim"),
            Text(f"{progress:.0f}%", style=color),
        )

    return stage_text


def _intra_stage_progress(
    raw_progress: float,
    stage_start: float = 30.0,
    stage_end: float = 90.0,
) -> float:
    """Convert stored beam progress (30-90 range) to 0-100 within-stage %."""
    if raw_progress <= stage_start:
        return 0.0
    if raw_progress >= stage_end:
        return 100.0
    return (raw_progress - stage_start) / (stage_end - stage_start) * 100.0


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


def format_error_message(error: str, max_length: int = 60) -> Text:
    """Formats an error message with red styling and truncation.

    Args:
        error (str): The error message.
        max_length (int, optional): Maximum length before truncation. Defaults to 60.

    Returns:
        Text: A `rich` Text object with error styling.
    """
    if not error:
        return Text("")

    # Truncate if too long
    if len(error) > max_length:
        error = error[:max_length-3] + "..."

    return Text(error, style="bold red")
