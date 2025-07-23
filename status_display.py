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
    # 새로운 구조화된 필드들
    current_task: str = ""      # "MOQUI Interpreter", "Beam Calculation", "DICOM Conversion"
    current_step: int = 0       # 현재 단계 (1, 2, 3, 4)
    total_steps: int = 4        # 전체 단계 수
    detailed_status: str = ""   # 상세 상태 ("Parsing RTPLAN...", "Processing beam 2/5 on GPU 3")
    
    # 기존 필드들 (호환성 유지)
    status: str = "IDLE"
    progress: float = 0.0
    stage: str = ""
    start_time: Optional[datetime] = None
    gpu_allocation: List[int] = None
    beam_info: str = ""
    transfer_info: str = ""
    error_message: str = ""
    
    def __post_init__(self):
        if self.gpu_allocation is None:
            self.gpu_allocation = []
    
    @property
    def progress_display(self) -> str:
        """진행상황을 1/4, 2/4 형태로 표시"""
        if self.total_steps > 0:
            return f"{self.current_step}/{self.total_steps}"
        return "0/0"


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
        if self.use_rich:
            from rich.live import Live
            self.live: Optional[Live] = None
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
    
    def update_case_status(self, case_id: str, status: str = "", progress: float = 0.0, 
                          stage: str = "", gpu_allocation: List[int] = None, 
                          beam_info: str = "", transfer_info: str = "", 
                          error_message: str = "",
                          # 새로운 매개변수들
                          current_task: str = "", current_step: int = 0,
                          total_steps: int = 4, detailed_status: str = "") -> None:
        """Update case status information with enhanced display structure."""
        with self.lock:
            if case_id not in self.cases:
                self.cases[case_id] = CaseDisplayInfo(
                    case_id=case_id,
                    status=status or "PROCESSING",
                    progress=progress,
                    stage=stage,
                    start_time=datetime.now() if status == "PROCESSING" else None,
                    gpu_allocation=gpu_allocation or [],
                    beam_info=beam_info,
                    transfer_info=transfer_info,
                    error_message=error_message,
                    # 새로운 필드들
                    current_task=current_task,
                    current_step=current_step,
                    total_steps=total_steps,
                    detailed_status=detailed_status
                )
            else:
                case_info = self.cases[case_id]
                if status:
                    case_info.status = status
                if progress > 0:
                    case_info.progress = progress
                if stage:
                    case_info.stage = stage
                if current_task:
                    case_info.current_task = current_task
                if current_step > 0:
                    case_info.current_step = current_step
                if total_steps > 0:
                    case_info.total_steps = total_steps
                if detailed_status:
                    case_info.detailed_status = detailed_status
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
        if self.use_rich:
            from rich.live import Live
            from rich.console import Console
            console = Console()
            with Live(console=console, screen=True, auto_refresh=False) as live:
                self.live = live
                while self.running:
                    try:
                        self._update_logs()
                        renderable = self._create_rich_layout()
                        self.live.update(renderable, refresh=True)
                        time.sleep(self.update_interval)
                    except KeyboardInterrupt:
                        break
                    except Exception as e:
                        import logging
                        logging.error(f"Display error: {e}")
                        time.sleep(self.update_interval)
        else:
            # Basic display loop
            while self.running:
                try:
                    self._update_logs()
                    self._display_basic()
                    time.sleep(self.update_interval)
                except KeyboardInterrupt:
                    break

    def _create_rich_layout(self):
        """Creates the entire rich layout object to be rendered."""
        from rich.layout import Layout
        
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

    def _display_rich(self) -> None:
        """This method is now a legacy placeholder; logic is in _display_loop."""
        # The main logic has been moved to _display_loop with the Live object.
        # This method can be kept for compatibility or removed.
        # For now, we'll just pass.
        pass
    
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
                
                # Header (새로운 구조)
                print(f"{'Case ID':<15} {'Task':<15} {'Progress':<10} {'Details':<35} {'GPUs':<8} {'Runtime':<10}")
                print("-" * self.terminal_width)
                
                # Cases
                for case_info in sorted(self.cases.values(), key=lambda x: x.case_id):
                    runtime = ""
                    if case_info.start_time:
                        runtime = str(datetime.now() - case_info.start_time).split('.')[0]
                    
                    gpu_str = ",".join(map(str, case_info.gpu_allocation)) if case_info.gpu_allocation else ""
                    
                    # 새로운 구조화된 표시 (Rich와 동일한 로직)
                    task_display = (case_info.current_task or case_info.stage or "Idle")[:15]
                    progress_display = case_info.progress_display if case_info.current_step > 0 else "-"
                    
                    # 상세 정보 우선순위: detailed_status > error > transfer_info > beam_info
                    details = case_info.detailed_status
                    if not details:
                        if case_info.error_message:
                            details = f"Error: {case_info.error_message[:30]}..."
                        elif case_info.transfer_info:
                            details = case_info.transfer_info[:35]
                        elif case_info.beam_info:
                            details = case_info.beam_info[:35]
                        else:
                            details = case_info.stage[:35] if case_info.stage else ""
                    
                    print(f"{case_info.case_id[:15]:<15} {task_display:<15} {progress_display:<10} "
                          f"{details[:35]:<35} {gpu_str:<8} {runtime:<10}")
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
        """Create cases table for rich display with enhanced structure."""
        try:
            from rich.table import Table
            from rich.text import Text
            
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
                runtime = ""
                if case_info.start_time:
                    runtime = str(datetime.now() - case_info.start_time).split('.')[0]
                
                gpu_str = ",".join(map(str, case_info.gpu_allocation)) if case_info.gpu_allocation else ""
                
                # 새로운 구조화된 표시
                task_display = case_info.current_task or case_info.stage or "Idle"
                progress_display = case_info.progress_display if case_info.current_step > 0 else "-"
                
                # 상세 정보 우선순위: detailed_status > error > transfer_info > beam_info
                details = case_info.detailed_status
                if not details:
                    if case_info.error_message:
                        details = f"[bold red]Error: {case_info.error_message[:30]}...[/bold red]"
                    elif case_info.transfer_info:
                        # Extract status and progress separately
                        transfer_text = case_info.transfer_info
                        if "(" in transfer_text and ")" in transfer_text:
                            # Split "Uploading (80/223)" into "Uploading" and "80/223"
                            status_part = transfer_text.split("(")[0].strip()
                            progress_part = transfer_text.split("(")[1].split(")")[0]
                            details = status_part[:35]
                            # Update progress display with the count
                            if progress_display == "-":
                                progress_display = progress_part
                        else:
                            details = transfer_text[:35]
                    elif case_info.beam_info:
                        details = case_info.beam_info[:35]
                    else:
                        details = case_info.stage[:35] if case_info.stage else ""

                # Task 스타일링
                task_style = "bold magenta" if case_info.status == "PROCESSING" else "dim"
                
                table.add_row(
                    case_info.case_id[:15],  # Case ID 잘림 방지
                    Text(task_display[:25], style=task_style),  # Increased from 15 to 25
                    Text(progress_display, style="bold yellow"),
                    details,
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
                stats.append(f"GPUs: {gpu_info.get('available_gpus', 0)}/{gpu_info.get('total_gpus', 0)}")
                stats.append(f"CPU: {self.system_info.get('cpu_percent', 0):.1f}%")
                stats.append(f"Memory: {self.system_info.get('memory_percent', 0):.1f}%")
            
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
