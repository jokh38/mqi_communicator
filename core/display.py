"""
Status Display Module - Manages the console-based status display for the application.
"""

import os
import time
import threading
from datetime import datetime
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
from collections import deque
from queue import Queue

# Attempt to import rich for enhanced display
try:
    from rich.live import Live
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text
    from rich.table import Table
    from rich.layout import Layout
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False


@dataclass
class UpdateData:
    """Encapsulates all possible fields for a case status update."""
    case_id: str
    status: Optional[str] = None
    progress: float = 0.0
    stage: Optional[str] = None
    gpu_allocation: Optional[List[int]] = None
    beam_info: Optional[str] = None
    transfer_info: Optional[str] = None
    error_message: Optional[str] = None
    current_task: Optional[str] = None
    current_step: int = 0
    total_steps: Optional[int] = None
    detailed_progress: Optional[str] = None
    detailed_status: Optional[str] = None
    transfer_speed: Optional[float] = None  # MB/s
    transfer_action: Optional[str] = None   # "Uploading", "Downloading"
    current_file_index: Optional[int] = None
    total_files: Optional[int] = None


@dataclass
class CaseDisplayInfo:
    """Holds the display state for a single case."""
    case_id: str
    current_task: str = ""
    current_step: int = 0
    total_steps: int = 0
    detailed_progress: str = ""
    detailed_status: str = ""
    status: str = "IDLE"
    progress: float = 0.0
    stage: str = ""
    start_time: Optional[datetime] = None
    gpu_allocation: List[int] = field(default_factory=list)
    beam_info: str = ""
    transfer_info: str = ""
    error_message: str = ""

    @property
    def progress_display(self) -> str:
        """Returns progress as a 'current/total' string."""
        if self.total_steps > 0:
            return f"{self.current_step}/{self.total_steps}"
        return "0/0"


class StatusDisplay:
    """Manages the console status display for the application."""

    def __init__(self, update_interval: int = 2, log_queue: Optional[Queue] = None):
        self.update_interval = update_interval
        self.log_queue = log_queue
        self.running = False
        self.display_thread: Optional[threading.Thread] = None
        self.lock = threading.Lock()

        # Display data
        self.cases: Dict[str, CaseDisplayInfo] = {}
        self.system_info: Dict[str, Any] = {}
        self.log_messages = deque(maxlen=10)
        self.last_update = datetime.now()

        # Display configuration
        self.use_rich = RICH_AVAILABLE
        self.live: Optional["Live"] = None
        self.terminal_width = self._get_terminal_width()

    def start(self) -> None:
        """Start the status display thread."""
        if not self.running:
            self.running = True
            self.display_thread = threading.Thread(target=self._display_loop, daemon=True, name="StatusDisplay")
            self.display_thread.start()

    def stop(self) -> None:
        """Stop the status display thread."""
        self.running = False
        if self.display_thread and self.display_thread.is_alive():
            self.display_thread.join(timeout=5)

    def update_case_status(self, data: UpdateData) -> None:
        """Update the status information for a single case."""
        with self.lock:
            if data.case_id not in self.cases:
                self.cases[data.case_id] = CaseDisplayInfo(
                    case_id=data.case_id,
                    start_time=datetime.now()
                )

            case_info = self.cases[data.case_id]

            # Update fields from data object
            if data.status:
                case_info.status = data.status
            if data.progress > 0:
                case_info.progress = data.progress
            if data.stage:
                case_info.stage = data.stage
            if data.current_task:
                case_info.current_task = data.current_task
            if data.current_step > 0:
                case_info.current_step = data.current_step
            if data.total_steps is not None:
                case_info.total_steps = data.total_steps
            if data.gpu_allocation is not None:
                case_info.gpu_allocation = data.gpu_allocation
            if data.beam_info is not None:
                case_info.beam_info = data.beam_info
            if data.transfer_info is not None:
                case_info.transfer_info = data.transfer_info
            if data.error_message is not None:
                case_info.error_message = data.error_message

            # Update formatted fields
            case_info.detailed_progress = self._format_detailed_progress(data, case_info)
            case_info.detailed_status = self._format_detailed_status(data, case_info)

    def remove_case(self, case_id: str) -> None:
        """Remove a case from the display."""
        with self.lock:
            if case_id in self.cases:
                del self.cases[case_id]

    def update_system_info(self, info: Dict[str, Any]) -> None:
        """Update the general system information."""
        with self.lock:
            self.system_info.update(info)
            self.last_update = datetime.now()

    def _format_detailed_progress(self, data: UpdateData, case_info: CaseDisplayInfo) -> str:
        """Format the detailed progress string."""
        if data.detailed_progress is not None:
            return data.detailed_progress
        if data.current_file_index is not None and data.total_files is not None:
            return f"{data.current_file_index}/{data.total_files}"
        return case_info.detailed_progress

    def _format_detailed_status(self, data: UpdateData, case_info: CaseDisplayInfo) -> str:
        """Format the detailed status string."""
        if data.detailed_status is not None:
            return data.detailed_status
        if data.error_message:
            return f"Error: {data.error_message[:30]}..."
        if data.transfer_action:
            if data.transfer_speed is not None:
                return f"{data.transfer_action} - {data.transfer_speed:.1f} MB/s"
            return data.transfer_action
        return case_info.detailed_status

    def _get_terminal_width(self) -> int:
        try:
            return os.get_terminal_size().columns
        except OSError:
            return 80

    def _update_logs(self) -> None:
        if not self.log_queue:
            return
        while not self.log_queue.empty():
            try:
                log_record = self.log_queue.get_nowait()
                self.log_messages.append(log_record.getMessage())
            except Exception:
                break

    def _display_loop(self) -> None:
        if self.use_rich:
            console = Console()
            with Live(console=console, screen=True, auto_refresh=False) as live:
                self.live = live
                while self.running:
                    self._update_logs()
                    renderable = self._create_rich_layout()
                    self.live.update(renderable, refresh=True)
                    time.sleep(self.update_interval)
        else:
            while self.running:
                self._update_logs()
                self._display_basic()
                time.sleep(self.update_interval)

    def _display_basic(self) -> None:
        with self.lock:
            os.system('cls' if os.name == 'nt' else 'clear')
            print("=" * self.terminal_width)
            print(f"[M] MOQUI Automation System - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print("=" * self.terminal_width)
            # ... (rest of basic display logic)

    def _create_rich_layout(self) -> "Layout":
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="main"),
            Layout(name="logs", ratio=1),
            Layout(name="footer", size=5)
        )
        layout["header"].update(self._create_header_panel())
        layout["main"].update(self._create_cases_table())
        layout["logs"].update(self._create_logs_panel())
        layout["footer"].update(self._create_footer_panel())
        return layout

    def _create_header_panel(self) -> "Panel":
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        header_text = Text(f"[M] MOQUI Automation System - {current_time}", style="bold blue")
        return Panel(header_text, style="blue")

    def _create_cases_table(self) -> "Table":
        table = Table(title="[Active Cases]", show_header=True, header_style="bold magenta")
        table.add_column("Case ID", style="cyan", width=15)
        table.add_column("Task", style="magenta", width=25)
        table.add_column("Progress", justify="center", width=8)
        table.add_column("Details", style="green", no_wrap=True, width=35)
        table.add_column("GPUs", style="red", width=8)
        table.add_column("Runtime", style="white", width=10)

        if not self.cases:
            table.add_row("No active cases", "", "", "", "", "")
            return table

        for case_info in sorted(self.cases.values(), key=lambda x: x.case_id):
            runtime = str(datetime.now() - case_info.start_time).split('.')[0] if case_info.start_time else ""
            gpu_str = ",".join(map(str, case_info.gpu_allocation))
            task_display = f"{case_info.progress_display} {case_info.current_task}" if case_info.current_task and case_info.current_step > 0 else case_info.current_task or case_info.stage or "Idle"
            details = case_info.detailed_status
            if case_info.error_message and details.startswith("Error:"):
                details = f"[bold red]{details}[/bold red]"

            table.add_row(
                case_info.case_id[:15],
                Text(task_display[:25], style="bold magenta" if case_info.status == "PROCESSING" else "dim"),
                Text(case_info.detailed_progress or "-", style="bold yellow"),
                details,
                gpu_str,
                runtime
            )
        return table

    def _create_logs_panel(self) -> "Panel":
        log_text = "\n".join(self.log_messages)
        return Panel(Text(log_text, style="white"), title="[Logs]", style="dim")

    def _create_footer_panel(self) -> "Panel":
        stats = []
        if self.system_info:
            gpu_info = self.system_info.get('gpu_status', {})
            stats.append(f"GPUs: {gpu_info.get('available_gpus', 0)}/{gpu_info.get('total_gpus', 0)}")
            stats.append(f"CPU: {self.system_info.get('cpu_percent', 0):.1f}%")
            stats.append(f"Memory: {self.system_info.get('memory_percent', 0):.1f}%")

        stats_text = " | ".join(stats) or "System information not available"
        footer_text = Text(f"{stats_text} | Last updated: {self.last_update.strftime('%H:%M:%S')}")
        return Panel(footer_text, style="dim")
