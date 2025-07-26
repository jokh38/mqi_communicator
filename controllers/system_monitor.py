"""
System Monitor Controller - Handles system health monitoring and status display updates.

This class manages system health monitoring, status display updates, and runs
background monitoring tasks.
"""

import os
import time
import threading
import psutil
from datetime import datetime
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
    total_steps: Optional[int] = None
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
    total_steps: int = 0        # 전체 단계 수
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


class SystemMonitor:
    """Manages system health monitoring and status display updates."""
    
    def __init__(self, logger, config, monitoring_interval, resource_manager, 
                 shared_state, shared_state_lock, state_manager, 
                 job_service, error_handler, update_interval: int = 2, log_queue: Optional[Queue] = None):
        """Initialize system monitor with dependencies."""
        self.logger = logger
        self.config = config
        self.monitoring_interval = monitoring_interval
        self.resource_manager = resource_manager
        self.shared_state = shared_state
        self.shared_state_lock = shared_state_lock
        self.state_manager = state_manager
        self.job_service = job_service
        self.error_handler = error_handler
        
        # Status display functionality
        self.update_interval = update_interval
        self.display_thread: Optional[threading.Thread] = None
        self.lock = threading.Lock()
        
        # Display data
        self.cases: Dict[str, CaseDisplayInfo] = {}
        self.system_info: Dict[str, Any] = {}
        self.log_queue = log_queue
        self.log_messages = deque(maxlen=10)
        self.last_update = datetime.now()
        
        # Display configuration
        self.use_rich = self._check_rich_available()
        if self.use_rich:
            from rich.live import Live
            self.live: Optional[Live] = None
        self.terminal_width = self._get_terminal_width()
        
        # Thread control
        self.running = False
        self.worker_thread = None

    def start(self) -> None:
        """Start the background worker and display threads."""
        if self.running:
            return
            
        try:
            self.running = True
            self.worker_thread = threading.Thread(
                target=self._background_system_monitor,
                name="SystemMonitor",
                daemon=True
            )
            self.display_thread = threading.Thread(
                target=self._display_loop, 
                name="DisplayLoop",
                daemon=True
            )
            self.worker_thread.start()
            self.display_thread.start()
            self.logger.info("System monitor started successfully")
        except Exception as e:
            self.logger.error(f"Failed to start system monitor: {e}")
            raise

    def stop(self) -> None:
        """Stop the system monitor and display threads."""
        self.logger.info("Stopping system monitor")
        self.running = False
        if self.worker_thread and self.worker_thread.is_alive():
            self.worker_thread.join(timeout=5)
        if self.display_thread and self.display_thread.is_alive():
            self.display_thread.join(timeout=5)
            if self.display_thread.is_alive():
                print("Warning: Display thread did not stop gracefully")

    def monitor_system_health(self) -> None:
        """Monitor system health and log metrics."""
        try:
            # Get GPU status from resource manager
            gpu_status = self.resource_manager.get_gpu_status_summary()
            
            # Get system resources
            cpu_percent = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            
            system_resources = {
                "cpu_percent": cpu_percent,
                "memory_percent": memory.percent,
                "memory_available_gb": memory.available / (1024**3),
                "disk_percent": disk.percent,
                "disk_free_gb": disk.free / (1024**3),
                "gpu_usage": gpu_status.get("memory_usage", {}),
                "available_gpus": len(gpu_status.get("available_gpus", [])),
                "total_gpus": gpu_status.get("total_gpus", 0)
            }
            
            with self.shared_state_lock:
                self.shared_state['gpu_status'] = gpu_status
                self.shared_state['system_health'] = system_resources
            
            # self.logger.log_system_resources(system_resources)  # Disabled to reduce log noise
            
            # Check for critical conditions
            if cpu_percent > 90:
                self.logger.warning(f"High CPU usage: {cpu_percent}%")
            if memory.percent > 90:
                self.logger.warning(f"High memory usage: {memory.percent}%")
            if disk.percent > 90:
                self.logger.warning(f"High disk usage: {disk.percent}%")
            
        except Exception as e:
            self.error_handler.handle_error(e, {"operation": "system_health_monitoring"})

    def update_status_display(self) -> None:
        """Update the status display with current system and case information."""
        try:
            # Create a consistent snapshot of shared state under lock
            with self.shared_state_lock:
                gpu_status = self.shared_state.get('gpu_status', {})
                active_cases_count = len(self.shared_state.get('active_cases', set()))
                waiting_cases_count = len(self.shared_state.get('waiting_cases', {}))
                active_cases_list = list(self.shared_state.get('active_cases', set()))

            # Update system information
            system_info = {
                'gpu_status': gpu_status,
                'cpu_percent': psutil.cpu_percent(),
                'memory_percent': psutil.virtual_memory().percent,
                'active_cases_count': active_cases_count,
                'waiting_cases_count': waiting_cases_count
            }
            self.status_display.update_system_info(system_info)
            
            # Update case information
            for case_id in active_cases_list:
                case_status = self.state_manager.get_case_status(case_id)
                if case_status:
                    job_status = self.job_service.get_job_status(case_id)
                    active_job = self.job_service.active_jobs.get(case_id)
                    
                    gpu_allocation = []
                    if active_job:
                        gpu_allocation = active_job.get('gpu_allocation', [])
                    
                    self.status_display.update_case_status(
                        case_id=case_id,
                        status=case_status.get('status', 'UNKNOWN'),
                        gpu_allocation=gpu_allocation
                    )
            
        except Exception as e:
            self.logger.error(f"Error updating status display: {e}")

    def get_system_status(self) -> Dict[str, Any]:
        """Get current system status snapshot."""
        try:
            with self.shared_state_lock:
                return {
                    "gpu_status": self.shared_state.get('gpu_status', {}),
                    "system_health": self.shared_state.get('system_health', {}),
                    "active_cases_count": len(self.shared_state.get('active_cases', set())),
                    "waiting_cases_count": len(self.shared_state.get('waiting_cases', {}))
                }
        except Exception as e:
            self.logger.error(f"Error getting system status: {e}")
            return {"error": "Failed to get system status"}

    def _background_system_monitor(self) -> None:
        """Background worker for system monitoring."""
        while self.running:
            try:
                self.monitor_system_health()
                self.update_status_display()  # Update display every monitoring cycle
                time.sleep(self.monitoring_interval)
            except Exception as e:
                self.error_handler.handle_error(e, {"operation": "background_system_monitoring"})
                time.sleep(self.monitoring_interval)

    # Status Display Methods
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

    def update_case_status(self, data=None, case_id: str = None, status: str = "", progress: float = 0.0, 
                          stage: str = "", gpu_allocation: List[int] = None, 
                          beam_info: str = "", transfer_info: str = "", 
                          error_message: str = "",
                          # New parameters
                          current_task: str = "", current_step: int = 0,
                          total_steps: Optional[int] = None, detailed_progress: str = None,
                          detailed_status: str = None, **kwargs) -> None:
        """Update case status information using structured UpdateData or legacy parameters."""
        # Handle backward compatibility
        if data is None:
            if case_id is None:
                raise ValueError("Either 'data' (UpdateData) or 'case_id' must be provided")
            
            # Convert legacy parameters to UpdateData
            data = UpdateData(
                case_id=case_id,
                status=status,
                progress=progress,
                stage=stage,
                gpu_allocation=gpu_allocation,
                beam_info=beam_info,
                transfer_info=transfer_info,
                error_message=error_message,
                current_task=current_task,
                current_step=current_step,
                total_steps=total_steps,
                detailed_progress=detailed_progress,
                detailed_status=detailed_status
            )
        elif not isinstance(data, UpdateData):
            raise TypeError("First argument must be an UpdateData instance")
        
        # Use the structured UpdateData from here on
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
                
                if data.total_steps is not None and data.total_steps > 0:
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
                print("[System Status]:")
                print(f"   GPUs: {gpu_info.get('available_gpus', 0)} available / {gpu_info.get('total_gpus', 0)} total")
                print(f"   CPU: {self.system_info.get('cpu_percent', 0):.1f}%")
                print(f"   Memory: {self.system_info.get('memory_percent', 0):.1f}%")
                print()
            
            # Cases
            if self.cases:
                print("[Active Cases]:")
                print("-" * self.terminal_width)
                
                # Header
                print(f"{'Case ID':<15} {'Task':<15} {'Progress':<10} {'Details':<35} {'GPUs':<8} {'Runtime':<10}")
                print("-" * self.terminal_width)
                
                # Cases
                for case_info in sorted(self.cases.values(), key=lambda x: x.case_id):
                    runtime = ""
                    if case_info.start_time:
                        runtime = str(datetime.now() - case_info.start_time).split('.')[0]
                    
                    gpu_str = ",".join(map(str, case_info.gpu_allocation)) if case_info.gpu_allocation else ""
                    
                    # Task Column
                    if case_info.current_task and case_info.current_step > 0:
                        task_display = f"{case_info.progress_display} {case_info.current_task}"[:15]
                    else:
                        task_display = (case_info.current_task or case_info.stage or "Idle")[:15]
                    
                    # Progress Column
                    progress_display = case_info.detailed_progress or "-"
                    
                    # Details Column
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
                
                # Task Column
                if case_info.current_task and case_info.current_step > 0:
                    task_display = f"{case_info.progress_display} {case_info.current_task}"
                else:
                    task_display = case_info.current_task or case_info.stage or "Idle"
                
                # Progress Column
                progress_display = case_info.detailed_progress or "-"
                
                # Details Column
                details = case_info.detailed_status
                if case_info.error_message and details.startswith("Error:"):
                    details = f"[bold red]{details}[/bold red]"

                # Task styling
                task_style = "bold magenta" if case_info.status == "PROCESSING" else "dim"
                
                table.add_row(
                    case_info.case_id[:15],
                    Text(task_display[:25], style=task_style),
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
def create_status_display(update_interval: int = 2, log_queue: Optional[Queue] = None):
    """Create and return a SystemMonitor instance for backward compatibility."""
    return SystemMonitor(
        logger=None, config={}, monitoring_interval=30, resource_manager=None,
        shared_state={}, shared_state_lock=None, state_manager=None,
        job_service=None, error_handler=None, update_interval=update_interval,
        log_queue=log_queue
    )


def update_case_progress(display, case_id: str, stage: str, 
                        progress: float, additional_info: str = "") -> None:
    """Convenience function to update case progress."""
    display.update_case_status(UpdateData(
        case_id=case_id,
        status="PROCESSING",
        progress=progress,
        stage=stage,
        beam_info=additional_info
    ))


def mark_case_complete(display, case_id: str) -> None:
    """Convenience function to mark case as complete."""
    display.update_case_status(UpdateData(
        case_id=case_id,
        status="COMPLETED"
    ))


def mark_case_failed(display, case_id: str, error_message: str) -> None:
    """Convenience function to mark case as failed."""
    display.update_case_status(UpdateData(
        case_id=case_id,
        status="FAILED",
        error_message=error_message
    ))