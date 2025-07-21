import os
import sys
import time
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
from collections import deque
from queue import Queue


@dataclass
class CaseDisplayInfo:
    case_id: str
    status: str
    progress: float
    stage: str
    start_time: Optional[datetime]
    gpu_allocation: List[int]
    beam_info: str = ""
    transfer_info: str = ""
    error_message: str = ""


class StatusDisplay:
    """Real-time console status display for MOQUI automation system."""
    
    def __init__(self, update_interval: int = 2, log_queue: Optional[Queue] = None):
        self.update_interval = update_interval
        self.running = False
        self.display_thread: Optional[threading.Thread] = None
        self.lock = threading.Lock()
        
        # Display data
        self.cases: Dict[str, CaseDisplayInfo] = {}
        self.system_info: Dict[str, Any] = {}
        self.log_queue = log_queue
        self.log_messages = deque(maxlen=10)  # Store last 10 log messages
        self.last_update = datetime.now()
        
        # Display configuration
        self.use_rich = self._check_rich_available()
        self.terminal_width = self._get_terminal_width()
        
    def _check_rich_available(self) -> bool:
        """Check if rich library is available for enhanced display."""
        try:
            import rich
            return True
        except ImportError:
            return False
    
    def _get_terminal_width(self) -> int:
        """Get terminal width for display formatting."""
        try:
            return os.get_terminal_size().columns
        except OSError:
            return 80  # Default fallback
    
    def start(self) -> None:
        """Start the display thread."""
        if self.running:
            return
            
        self.running = True
        self.display_thread = threading.Thread(target=self._display_loop, daemon=True)
        self.display_thread.start()
    
    def stop(self) -> None:
        """Stop the display thread."""
        self.running = False
        if self.display_thread and self.display_thread.is_alive():
            self.display_thread.join(timeout=5)
            if self.display_thread.is_alive():
                import logging
                logging.warning("Display thread did not stop gracefully")
    
    def update_case_status(self, case_id: str, status: str, progress: float = 0.0, 
                          stage: str = "", gpu_allocation: List[int] = None, 
                          beam_info: str = "", transfer_info: str = "", 
                          error_message: str = "") -> None:
        """Update case status information."""
        with self.lock:
            if case_id not in self.cases:
                self.cases[case_id] = CaseDisplayInfo(
                    case_id=case_id,
                    status=status,
                    progress=progress,
                    stage=stage,
                    start_time=datetime.now() if status == "PROCESSING" else None,
                    gpu_allocation=gpu_allocation or []
                )
            else:
                case_info = self.cases[case_id]
                case_info.status = status
                case_info.progress = progress
                case_info.stage = stage
                case_info.gpu_allocation = gpu_allocation or case_info.gpu_allocation
                case_info.beam_info = beam_info
                case_info.transfer_info = transfer_info
                case_info.error_message = error_message
                
                if status == "PROCESSING" and case_info.start_time is None:
                    case_info.start_time = datetime.now()
    
    def remove_case(self, case_id: str) -> None:
        """Remove case from display."""
        with self.lock:
            if case_id in self.cases:
                del self.cases[case_id]
    
    def update_system_info(self, info: Dict[str, Any]) -> None:
        """Update system information."""
        with self.lock:
            self.system_info.update(info)
            self.last_update = datetime.now()
    
    def _update_logs(self) -> None:
        """Update log messages from the queue."""
        if not self.log_queue:
            return
        
        while not self.log_queue.empty():
            try:
                log_record = self.log_queue.get_nowait()
                self.log_messages.append(log_record.getMessage())
            except Exception:
                break

    def _display_loop(self) -> None:
        """Main display loop."""
        while self.running:
            try:
                self._update_logs()
                if self.use_rich:
                    self._display_rich()
                else:
                    self._display_basic()
                time.sleep(self.update_interval)
            except KeyboardInterrupt:
                break
            except Exception as e:
                import logging
                logging.error(f"Display error: {e}")
                time.sleep(self.update_interval)
    
    def _display_rich(self) -> None:
        """Display using rich library for enhanced formatting."""
        try:
            from rich.console import Console
            from rich.table import Table
            from rich.layout import Layout
            from rich.panel import Panel
            from rich.progress import Progress, BarColumn, TextColumn, TimeRemainingColumn
            from rich.live import Live
            
            console = Console()
            
            # Create main layout
            layout = Layout()
            layout.split_column(
                Layout(name="header", size=3),
                Layout(name="main"),
                Layout(name="logs", ratio=1),
                Layout(name="footer", size=5)
            )
            
            # Header with system info
            layout["header"].update(self._create_header_panel())
            
            # Main content with case table
            layout["main"].update(self._create_cases_table())

            # Logs panel
            layout["logs"].update(self._create_logs_panel())
            
            # Footer with system stats
            layout["footer"].update(self._create_footer_panel())
            
            # Clear screen and display
            console.clear()
            console.print(layout)
            
        except ImportError:
            # Fallback to basic display
            self._display_basic()
    
    def _display_basic(self) -> None:
        """Basic console display without rich library."""
        with self.lock:
            # Clear screen - compatible with both Windows and WSL
            try:
                if os.name == 'nt':
                    # Windows
                    os.system('cls')
                else:
                    # Linux/WSL - try ANSI first, fallback to clear command
                    try:
                        print('\033[2J\033[H', end='', flush=True)
                    except:
                        os.system('clear')
            except:
                # Fallback - just print newlines
                print('\n' * 50)
            
            # Header
            print("=" * self.terminal_width)
            print(f"[M] MOQUI Automation System - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print("=" * self.terminal_width)
            
            # System info
            if self.system_info:
                gpu_info = self.system_info.get('gpu_status', {})
                print(f"[System Status]:")
                print(f"   GPUs: {gpu_info.get('available_gpus', 0)} available / {gpu_info.get('total_gpus', 0)} total")
                print(f"   CPU: {self.system_info.get('cpu_percent', 0):.1f}%")
                print(f"   Memory: {self.system_info.get('memory_percent', 0):.1f}%")
                print()
            
            # Cases
            if self.cases:
                print("[Active Cases]:")
                print("-" * self.terminal_width)
                
                # Header
                print(f"{'Case ID':<12} {'Status':<12} {'Progress':<10} {'Stage':<20} {'GPUs':<8} {'Runtime':<10}")
                print("-" * self.terminal_width)
                
                # Cases
                for case_info in sorted(self.cases.values(), key=lambda x: x.case_id):
                    runtime = ""
                    if case_info.start_time:
                        runtime = str(datetime.now() - case_info.start_time).split('.')[0]
                    
                    gpu_str = ",".join(map(str, case_info.gpu_allocation)) if case_info.gpu_allocation else "None"
                    progress_str = f"{case_info.progress*100:.1f}%" if case_info.progress > 0 else "-"
                    
                    print(f"{case_info.case_id:<12} {case_info.status:<12} {progress_str:<10} "
                          f"{case_info.stage:<20} {gpu_str:<8} {runtime:<10}")
                    
                    # Additional info
                    if case_info.beam_info:
                        print(f"             --> {case_info.beam_info}")
                    if case_info.transfer_info:
                        print(f"             --> {case_info.transfer_info}")
                    if case_info.error_message:
                        print(f"             [Error] {case_info.error_message}")
            else:
                print("No active cases")
            
            print()
            print("-" * self.terminal_width)
            print("[Logs]:")
            for msg in self.log_messages:
                print(f"  {msg}")
            print("-" * self.terminal_width)
            print()

            print("=" * self.terminal_width)
            print(f"Last updated: {self.last_update.strftime('%H:%M:%S')} | Press Ctrl+C to stop")
    
    def _create_header_panel(self):
        """Create header panel for rich display."""
        try:
            from rich.panel import Panel
            from rich.text import Text
            
            current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            header_text = Text(f"[M] MOQUI Automation System - {current_time}", style="bold blue")
            return Panel(header_text, style="blue")
        except ImportError:
            return "MOQUI Automation System"
    
    def _create_cases_table(self):
        """Create cases table for rich display."""
        try:
            from rich.table import Table
            from rich.text import Text
            
            table = Table(title="[Active Cases]", show_header=True, header_style="bold magenta")
            table.add_column("Case ID", style="cyan", width=12)
            table.add_column("Status", style="green", width=12)
            table.add_column("Progress", style="yellow", width=12)
            table.add_column("Stage", style="blue", width=20)
            table.add_column("GPUs", style="red", width=8)
            table.add_column("Runtime", style="white", width=10)
            
            if not self.cases:
                table.add_row("No active cases", "", "", "", "", "")
                return table
            
            for case_info in sorted(self.cases.values(), key=lambda x: x.case_id):
                runtime = ""
                if case_info.start_time:
                    runtime = str(datetime.now() - case_info.start_time).split('.')[0]
                
                gpu_str = ",".join(map(str, case_info.gpu_allocation)) if case_info.gpu_allocation else "None"
                progress_str = f"{case_info.progress*100:.1f}%" if case_info.progress > 0 else "-"
                
                # Status styling
                status_style = "green" if case_info.status == "COMPLETED" else \
                              "red" if case_info.status == "FAILED" else \
                              "yellow" if case_info.status == "PROCESSING" else "white"
                
                table.add_row(
                    case_info.case_id,
                    Text(case_info.status, style=status_style),
                    progress_str,
                    case_info.stage,
                    gpu_str,
                    runtime
                )
            
            return table
        except ImportError:
            return "Cases table (rich not available)"

    def _create_logs_panel(self):
        """Create logs panel for rich display."""
        try:
            from rich.panel import Panel
            from rich.text import Text

            log_text = "\n".join(self.log_messages)
            return Panel(Text(log_text, style="white"), title="[Logs]", style="dim")
        except ImportError:
            return "Logs (rich not available)"
    
    def _create_footer_panel(self):
        """Create footer panel for rich display."""
        try:
            from rich.panel import Panel
            from rich.columns import Columns
            from rich.text import Text
            
            # System stats
            stats = []
            if self.system_info:
                gpu_info = self.system_info.get('gpu_status', {})
                stats.append(f"📊 GPUs: {gpu_info.get('available_gpus', 0)}/{gpu_info.get('total_gpus', 0)}")
                stats.append(f"💾 CPU: {self.system_info.get('cpu_percent', 0):.1f}%")
                stats.append(f"🔢 Memory: {self.system_info.get('memory_percent', 0):.1f}%")
            
            stats_text = " | ".join(stats) if stats else "System information not available"
            footer_text = Text(f"{stats_text} | Last updated: {self.last_update.strftime('%H:%M:%S')}")
            
            return Panel(footer_text, style="dim")
        except ImportError:
            return "Footer (rich not available)"
    
    def print_summary(self) -> None:
        """Print a one-time summary without starting the display loop."""
        if self.use_rich:
            try:
                from rich.console import Console
                console = Console()
                console.print(self._create_header_panel())
                console.print(self._create_cases_table())
                console.print(self._create_logs_panel())
                console.print(self._create_footer_panel())
                return
            except ImportError:
                pass
        
        # Fallback to basic display
        self._display_basic()


# Convenience functions for integration
def create_status_display(update_interval: int = 2, log_queue: Optional[Queue] = None) -> StatusDisplay:
    """Create and return a StatusDisplay instance."""
    return StatusDisplay(update_interval=update_interval, log_queue=log_queue)


def update_case_progress(display: StatusDisplay, case_id: str, stage: str, 
                        progress: float, additional_info: str = "") -> None:
    """Convenience function to update case progress."""
    display.update_case_status(
        case_id=case_id,
        status="PROCESSING",
        progress=progress,
        stage=stage,
        beam_info=additional_info
    )
