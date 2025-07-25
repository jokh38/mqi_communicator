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
    # New structured fields
    current_task: Optional[str] = None
    current_step: int = 0
    total_steps: int = 4
    detailed_progress: Optional[str] = None
    detailed_status: Optional[str] = None
    # Raw data fields for formatting
    transfer_speed: Optional[float] = None  # MB/s
    transfer_action: Optional[str] = None   # "Uploading", "Downloading"
    current_file_index: Optional[int] = None
    total_files: Optional[int] = None


@dataclass
class CaseDisplayInfo:
    case_id: str
    # 새로운 구조화된 필드들
    current_task: str = ""      # "MOQUI Interpreter", "Beam Calculation", "DICOM Conversion"
    current_step: int = 0       # 현재 단계 (1, 2, 3, 4)
    total_steps: int = 4        # 전체 단계 수
    detailed_progress: str = "" # 상세 진행률 ("85/223", "2/5")
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
        self.system_monitor_thread: Optional[threading.Thread] = None
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
        """Start the display and system monitoring threads."""
        if self.running:
            return
            
        self.running = True
        self.display_thread = threading.Thread(target=self._display_loop, daemon=True)
        self.system_monitor_thread = threading.Thread(target=self._system_monitor_loop, daemon=True)
        self.display_thread.start()
        self.system_monitor_thread.start()
    
    def stop(self) -> None:
        """Stop the display and system monitoring threads."""
        self.running = False
        if self.display_thread and self.display_thread.is_alive():
            self.display_thread.join(timeout=5)
            if self.display_thread.is_alive():
                print("Warning: Display thread did not stop gracefully")
        
        if self.system_monitor_thread and self.system_monitor_thread.is_alive():
            self.system_monitor_thread.join(timeout=5)
            if self.system_monitor_thread.is_alive():
                print("Warning: System monitor thread did not stop gracefully")
    
    def update_case_status(self, data: UpdateData) -> None:
        """Update case status information using structured UpdateData."""
        with self.lock:
            if data.case_id not in self.cases:
                self.cases[data.case_id] = CaseDisplayInfo(
                    case_id=data.case_id,
                    status=data.status or "PROCESSING",
                    progress=data.progress,
                    stage=data.stage or "",
                    start_time=datetime.now() if data.status == "PROCESSING" else None,
                    gpu_allocation=data.gpu_allocation or [],
                    beam_info=data.beam_info or "",
                    transfer_info=data.transfer_info or "",
                    error_message=data.error_message or "",
                    current_task=data.current_task or "",
                    current_step=data.current_step,
                    total_steps=data.total_steps,
                    detailed_progress=self._format_detailed_progress(data),
                    detailed_status=self._format_detailed_status(data)
                )
            else:
                case_info = self.cases[data.case_id]
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
                if data.total_steps > 0:
                    case_info.total_steps = data.total_steps
                
                # Update formatted fields using centralized logic
                case_info.detailed_progress = self._format_detailed_progress(data)
                case_info.detailed_status = self._format_detailed_status(data)
                
                if data.gpu_allocation is not None:
                    case_info.gpu_allocation = data.gpu_allocation
                if data.beam_info is not None:
                    case_info.beam_info = data.beam_info
                if data.transfer_info is not None:
                    case_info.transfer_info = data.transfer_info
                if data.error_message is not None:
                    case_info.error_message = data.error_message
                
                if data.status == "PROCESSING" and case_info.start_time is None:
                    case_info.start_time = datetime.now()
    
    def _format_detailed_progress(self, data: UpdateData) -> str:
        """Format detailed progress from raw data."""
        if data.detailed_progress is not None:
            return data.detailed_progress
        
        if data.current_file_index is not None and data.total_files is not None:
            return f"{data.current_file_index}/{data.total_files}"
        
        # Fallback to parsing transfer_info for backward compatibility
        if data.transfer_info and "(" in data.transfer_info and ")" in data.transfer_info:
            return data.transfer_info.split("(")[1].split(")")[0]
        
        return ""
    
    def _format_detailed_status(self, data: UpdateData) -> str:
        """Format detailed status from raw data."""
        if data.detailed_status is not None:
            return data.detailed_status
        
        if data.error_message:
            return f"Error: {data.error_message[:30]}..."
        
        if data.transfer_action and data.transfer_speed is not None:
            return f"{data.transfer_action} - {data.transfer_speed:.1f} MB/s"
        
        if data.transfer_action:
            return data.transfer_action
        
        # Fallback to beam_info or transfer_info for backward compatibility
        if data.beam_info:
            return data.beam_info[:35]
        
        if data.transfer_info:
            # Extract status part from formatted transfer_info
            if "(" in data.transfer_info and ")" in data.transfer_info:
                before_paren = data.transfer_info.split("(")[0].strip()
                after_paren = data.transfer_info.split(")")[1].strip() if ")" in data.transfer_info else ""
                if after_paren.startswith("-"):
                    after_paren = after_paren[1:].strip()
                return f"{before_paren} {after_paren}".strip()[:35]
            else:
                return data.transfer_info[:35]
        
        return ""
    
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
                        print(f"Display error: {e}")
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
                    
    def _system_monitor_loop(self) -> None:
        """Background loop to continuously update system statistics."""
        while self.running:
            try:
                # Update system statistics
                self._update_system_stats()
                # Update every 2 seconds
                time.sleep(2)
            except KeyboardInterrupt:
                break
            except Exception as e:
                # Log error but continue monitoring
                print(f"System monitor error: {e}")
                time.sleep(2)
                
    def _update_system_stats(self) -> None:
        """Update system statistics including CPU, memory, and GPU info."""
        try:
            import psutil
            
            # Get CPU and memory stats
            cpu_percent = psutil.cpu_percent(interval=None)
            memory_info = psutil.virtual_memory()
            
            # Initialize system info with basic stats
            updated_info = {
                'cpu_percent': cpu_percent,
                'memory_percent': memory_info.percent,
                'memory_used_gb': memory_info.used / (1024**3),
                'memory_total_gb': memory_info.total / (1024**3)
            }
            
            # Try to get GPU information if available
            try:
                gpu_info = self._get_gpu_info()
                updated_info['gpu_status'] = gpu_info
            except Exception:
                # If GPU info not available, use placeholder
                updated_info['gpu_status'] = {
                    'available_gpus': 0,
                    'total_gpus': 0,
                    'gpu_details': []
                }
            
            # Update system info thread-safely
            with self.lock:
                self.system_info.update(updated_info)
                self.last_update = datetime.now()
                
        except ImportError:
            # psutil not available, provide placeholder info
            with self.lock:
                self.system_info.update({
                    'cpu_percent': 0.0,
                    'memory_percent': 0.0,
                    'gpu_status': {'available_gpus': 0, 'total_gpus': 0}
                })
        except Exception as e:
            # Other errors, continue with existing info
            pass
            
    def _get_gpu_info(self) -> Dict[str, Any]:
        """Get GPU information using gpu_manager."""
        try:
            from gpu_manager import GPUManager
            
            # Create a temporary GPU manager instance for monitoring
            gpu_manager = GPUManager()
            gpu_status = gpu_manager.get_gpu_status_summary()
            
            # Convert to expected format
            return {
                'available_gpus': len(gpu_status.get('available_gpus', [])),
                'total_gpus': gpu_status.get('total_gpus', 0),
                'gpu_details': gpu_status.get('gpu_details', []),
                'allocated_gpus': gpu_status.get('allocated_gpus', []),
                'reserved_gpus': gpu_status.get('reserved_gpus', [])
            }
        except ImportError:
            # gpu_manager not available
            return {
                'available_gpus': 0,
                'total_gpus': 0,
                'gpu_details': []
            }
        except Exception:
            # Other errors, return safe defaults
            return {
                'available_gpus': 0,
                'total_gpus': 0,
                'gpu_details': []
            }

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
                    
                    # Task Column: [Current Step]/[Total Steps] [Current Task Name]
                    if case_info.current_task and case_info.current_step > 0:
                        task_display = f"{case_info.progress_display} {case_info.current_task}"[:15]
                    else:
                        task_display = (case_info.current_task or case_info.stage or "Idle")[:15]
                    
                    # Progress Column: Use centrally formatted detailed_progress
                    progress_display = case_info.detailed_progress or "-"
                    
                    # Details Column: Use centrally formatted detailed_status
                    details = case_info.detailed_status
                    
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
                
                # Task Column: [Current Step]/[Total Steps] [Current Task Name]
                if case_info.current_task and case_info.current_step > 0:
                    task_display = f"{case_info.progress_display} {case_info.current_task}"
                else:
                    task_display = case_info.current_task or case_info.stage or "Idle"
                
                # Progress Column: Use centrally formatted detailed_progress
                progress_display = case_info.detailed_progress or "-"
                
                # Details Column: Use centrally formatted detailed_status with rich formatting
                details = case_info.detailed_status
                if case_info.error_message and details.startswith("Error:"):
                    details = f"[bold red]{details}[/bold red]"

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
    display.update_case_status(UpdateData(
        case_id=case_id,
        status="PROCESSING",
        progress=progress,
        stage=stage,
        beam_info=additional_info
    ))


def mark_case_complete(display: StatusDisplay, case_id: str) -> None:
    """Convenience function to mark case as complete."""
    display.update_case_status(UpdateData(
        case_id=case_id,
        status="COMPLETED"
    ))


def mark_case_failed(display: StatusDisplay, case_id: str, error_message: str) -> None:
    """Convenience function to mark case as failed."""
    display.update_case_status(UpdateData(
        case_id=case_id,
        status="FAILED",
        error_message=error_message
    ))
