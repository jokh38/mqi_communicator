# =====================================================================================
# Target File: src/ui/display.py
# Source Reference: src/display_handler.py
# =====================================================================================
"""Handles rendering the UI with the `rich` library."""

from typing import Any, Optional
import threading
import time
import signal
from datetime import datetime, timezone, timedelta

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

from src.ui.provider import DashboardDataProvider
from src.infrastructure.logging_handler import StructuredLogger
import src.ui.formatter as formatter


class DisplayManager:
    """Exclusively handles rendering the UI with the `rich` library, using data
    received from the provider.

    This class is responsible for pure UI rendering without any data fetching or business logic.
    """

    def __init__(self, provider: DashboardDataProvider, logger: StructuredLogger, settings: Any, timezone_hours: int = 9):
        """Initializes the display manager with an injected data provider.

        Args:
            provider (DashboardDataProvider): The data provider for the dashboard.
            logger (StructuredLogger): The logger for recording operations.
            settings (Any): The application settings object.
            timezone_hours (int, optional): The timezone hours offset from UTC (Seoul = 9). Defaults to 9.
        """
        self.provider = provider
        self.logger = logger
        # Force Rich to treat this as a terminal and auto-detect size
        self.console = Console(force_terminal=True)
        self.layout = self._create_layout()
        self.live: Optional[Live] = None
        self.running = False
        self._update_thread: Optional[threading.Thread] = None

        ui_config = settings.get_ui_config()
        self._refresh_rate = ui_config.get("refresh_interval", 2)

        self._local_tz = timezone(timedelta(hours=timezone_hours))
        self._resize_needed = False

    def _create_layout(self) -> Layout:
        """Creates and returns the main layout structure for the dashboard.

        Returns:
            Layout: The main layout structure.
        """
        layout = Layout(name="root")
        layout.split(
            Layout(name="header", size=3),
            Layout(ratio=1, name="main"),
            Layout(size=1, name="footer"),
        )
        layout["main"].split_row(Layout(name="left"), Layout(name="right", ratio=2))
        layout["left"].split(Layout(name="system_stats"), Layout(name="gpu_resources"))
        return layout

    def start(self) -> None:
        """Starts the display update loop in a separate thread."""
        if self.running:
            self.logger.warning("Display manager is already running.")
            return

        self.running = True
        
        # Set up signal handler for terminal resize (SIGWINCH)
        try:
            signal.signal(signal.SIGWINCH, self._handle_resize)
        except (AttributeError, ValueError):
            # SIGWINCH may not be available on all platforms (e.g., Windows)
            pass
        
        try:
            # Try with screen=True first (alternate screen mode)
            self.live = Live(self.layout, console=self.console, screen=True, auto_refresh=False)
            self.live.start()
        except Exception as e:
            try:
                # Fallback: try without alternate screen mode
                self.live = Live(self.layout, console=self.console, screen=False, auto_refresh=False)
                self.live.start()
            except Exception as e2:
                self.logger.error("Failed to start Live display", {"error": str(e2)})
                # As a last resort, let's try a simple print-based approach
                self.live = None

        self._update_thread = threading.Thread(target=self._update_loop, daemon=True)
        self._update_thread.start()
        self.logger.info("Display manager started.")

    def _handle_resize(self, signum, frame):
        """Handle terminal resize signal."""
        self._resize_needed = True

    def stop(self) -> None:
        """Stops the display update loop and cleans up resources."""
        if not self.running:
            return

        self.running = False
        if self._update_thread:
            self._update_thread.join()

        if self.live:
            self.live.stop()
        self.logger.info("Display manager stopped.")

    def _update_loop(self) -> None:
        """The main update loop that runs in a separate thread."""
        while self.running:
            try:
                # Check if resize is needed and recreate Live object if so
                if self._resize_needed and self.live:
                    self._resize_needed = False
                    try:
                        # Stop current Live object
                        self.live.stop()
                        # Create new Console and Live with updated terminal size
                        self.console = Console(force_terminal=True)
                        self.live = Live(self.layout, console=self.console, screen=True, auto_refresh=False)
                        self.live.start()
                    except Exception as e:
                        self.logger.error("Error handling resize", {"error": str(e)})
                
                self.provider.refresh_all_data()
                self.update_display()
                time.sleep(self._refresh_rate)
            except Exception as e:
                self.logger.error("Error in display update loop", {"error": str(e)})
                time.sleep(5) # Wait longer after an error

    def update_display(self) -> None:
        """Updates the display with fresh data from the provider."""
        try:
            # Header
            current_time = datetime.now(self._local_tz).strftime('%Y-%m-%d %H:%M:%S KST')
            header_text = Text(f"MQI Communicator Dashboard - Last Updated: {current_time}", justify="center")
            self.layout["header"].update(Panel(header_text, style="bold blue"))

            # System Stats
            stats_data = self.provider.get_system_stats()
            self.layout["system_stats"].update(self._create_system_stats_panel(stats_data))

            # GPU Resources
            gpu_data = self.provider.get_gpu_data()
            self.layout["gpu_resources"].update(self._create_gpu_panel(gpu_data))

            # Active Cases
            cases_data = self.provider.get_active_cases_data()
            self.layout["right"].update(self._create_cases_panel(cases_data))
            
            # Footer
            footer_text = Text(f"Watching for new cases... | Total Active Cases: {stats_data.get('total_cases', 0)} | Available GPUs: {stats_data.get('available_gpus', 0)}/{stats_data.get('total_gpus', 0)}", justify="left")
            self.layout["footer"].update(Panel(footer_text, style="white"))
            
            if self.live:
                self.live.refresh()
            else:
                # Fallback: simple print-based display
                print("\n" + "="*60)
                current_time = datetime.now(self._local_tz).strftime('%Y-%m-%d %H:%M:%S KST')
                print(f"MQI DASHBOARD - {current_time}")
                print("="*60)
                print(f"Total Cases: {stats_data.get('total_cases', 0)}")
                print(f"Pending: {stats_data.get('pending', 0)}, Processing: {stats_data.get('processing', 0)}")
                print(f"GPUs: {len(gpu_data)} available")
                print(f"Active Cases: {len(cases_data)}")
                if cases_data:
                    print("\nActive Cases:")
                    for case in cases_data:
                        print(f"  - {case['case_id']}: {case['status']} ({case['progress']:.1f}%)")
                print("="*60)
        except Exception as e:
            self.logger.error("Error updating display", {"error": str(e)})
            raise

    def _create_system_stats_panel(self, stats_data: dict) -> Panel:
        """Creates a panel for system statistics.

        Args:
            stats_data (dict): A dictionary of system statistics.

        Returns:
            Panel: A `rich` Panel object.
        """
        table = Table(show_header=False, expand=True)
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="white")
        
        stats = {
            "Total Active": stats_data.get('total_cases', 0),
            "Pending": stats_data.get('pending', 0),
            "CSV Interpreting": stats_data.get('csv_interpreting', 0),
            "Processing": stats_data.get('processing', 0),
            "Postprocessing": stats_data.get('postprocessing', 0),
            "Failed": stats_data.get('failed', 0),
        }
        for key, value in stats.items():
            table.add_row(key, str(value))

        return Panel(table, title="[bold]System Status[/bold]", border_style="green")

    def _create_gpu_panel(self, gpu_data: list) -> Panel:
        """Creates a panel for GPU resources.

        Args:
            gpu_data (list): A list of GPU resource data.

        Returns:
            Panel: A `rich` Panel object.
        """
        table = Table(expand=True)
        table.add_column("ID", style="dim", width=5)
        table.add_column("Name", style="cyan")
        table.add_column("Status", style="white")
        table.add_column("Mem (Used/Total)", style="white")
        table.add_column("Util", style="white")
        table.add_column("Temp", style="white")

        for gpu in gpu_data:
            table.add_row(
                str(gpu['gpu_index']),
                gpu['name'],
                formatter.get_gpu_status_text(gpu['status']),
                formatter.format_memory_usage(gpu['memory_used'], gpu['memory_total']),
                formatter.format_utilization(gpu['utilization']),
                formatter.format_temperature(gpu['temperature'])
            )
        return Panel(table, title="[bold]GPU Resources[/bold]", border_style="green")

    def _create_cases_panel(self, cases_data: list) -> Panel:
        """Creates a panel for active cases with expandable beam information.

        Args:
            cases_data (list): A list of active case data (backward compatible).

        Returns:
            Panel: A `rich` Panel object.
        """
        # Get detailed case+beam data
        cases_with_beams = self.provider.get_cases_with_beams_data()

        # Create a mapping from GPU UUID to index for display
        gpu_data = self.provider.get_gpu_data()
        uuid_to_index = {gpu['uuid']: gpu['gpu_index'] for gpu in gpu_data}

        table = Table(expand=True, show_header=True)
        table.add_column("Case / Beam ID", style="cyan", no_wrap=True)
        table.add_column("Status", style="white")
        table.add_column("Progress", style="white", width=28)
        table.add_column("GPU/Job", style="dim")
        table.add_column("Elapsed", style="dim")
        table.add_column("Error", style="red", overflow="fold")

        for item in cases_with_beams:
            case_display = item["case_display"]
            beams = item["beams"]

            # Map GPU UUID to index for display
            assigned_gpu_display = "N/A"
            if case_display['assigned_gpu']:
                gpu_index = uuid_to_index.get(case_display['assigned_gpu'])
                if gpu_index is not None:
                    assigned_gpu_display = str(gpu_index)
                else:
                    assigned_gpu_display = case_display['assigned_gpu'][-4:]

            # Add case row with interpreter completion indicator
            case_id_display = f"[bold]{case_display['case_id']}[/bold]"
            if case_display.get('interpreter_done', False):
                case_id_display += " [green]✓[/green]"
            case_id_display += f" ({case_display['beam_count']} beams)"

            table.add_row(
                case_id_display,
                formatter.get_case_status_text(case_display['status']),
                formatter.format_progress_bar(case_display['progress']),
                assigned_gpu_display,
                formatter.format_elapsed_time(case_display['elapsed_time']),
                formatter.format_error_message(case_display.get('error_message', ''), max_length=40),
                style="bold"
            )

            # Add beam rows (indented)
            for beam in beams:
                # Extract beam name (last part after underscore)
                beam_name = beam["beam_id"].split("_")[-1] if "_" in beam["beam_id"] else beam["beam_id"]

                hpc_display = beam["hpc_job_id"][:8] if beam["hpc_job_id"] else "N/A"

                table.add_row(
                    f"  ├─ {beam_name}",
                    formatter.get_beam_status_text(beam['status']),
                    "",  # No progress bar for individual beams
                    hpc_display,
                    formatter.format_elapsed_time(beam['elapsed_time']),
                    formatter.format_error_message(beam.get('error_message', ''), max_length=40),
                    style="dim"
                )

        return Panel(table, title="[bold]Active Cases & Beams[/bold]", border_style="magenta")