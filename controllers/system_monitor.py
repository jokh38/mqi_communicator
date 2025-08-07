"""
System Monitor Controller - Handles background system health monitoring.
"""

import time
import threading
import psutil
from typing import Dict, Any

from core.display import StatusDisplay, UpdateData


class SystemMonitor:
    """Monitors system health and provides data to the status display."""

    def __init__(self, logger, config, monitoring_interval, resource_manager,
                 shared_state, shared_state_lock, state_manager,
                 job_service, error_handler, status_display: StatusDisplay):
        """Initialize system monitor."""
        self.logger = logger
        self.config = config
        self.monitoring_interval = monitoring_interval
        self.resource_manager = resource_manager
        self.shared_state = shared_state
        self.shared_state_lock = shared_state_lock
        self.state_manager = state_manager
        self.job_service = job_service
        self.error_handler = error_handler
        self.status_display = status_display

        self.running = False
        self.worker_thread: threading.Thread = None

    def start(self) -> None:
        """Start the background monitoring worker."""
        if not self.running:
            self.running = True
            self.worker_thread = threading.Thread(
                target=self._background_monitor_loop,
                name="SystemMonitor",
                daemon=True
            )
            self.worker_thread.start()
            self.logger.info("System monitor started")

    def stop(self) -> None:
        """Stop the background monitoring worker."""
        self.logger.info("Stopping system monitor")
        self.running = False
        if self.worker_thread and self.worker_thread.is_alive():
            self.worker_thread.join(timeout=5)

    def monitor_system_health(self) -> None:
        """Monitor system health, update shared state, and log critical conditions."""
        try:
            gpu_status = self.resource_manager.get_all_gpu_status()
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

            if cpu_percent > 90:
                self.logger.warning(f"High CPU usage: {cpu_percent}%")
            if memory.percent > 90:
                self.logger.warning(f"High memory usage: {memory.percent}%")
            if disk.percent > 90:
                self.logger.warning(f"High disk usage: {disk.percent}%")

        except Exception as e:
            self.error_handler.handle_error(e, {"operation": "system_health_monitoring"})

    def update_display_data(self) -> None:
        """Update the status display with the latest data from the shared state."""
        try:
            with self.shared_state_lock:
                gpu_status = self.shared_state.get('gpu_status', {})
                active_cases_count = len(self.shared_state.get('active_cases', set()))
                waiting_cases_count = len(self.shared_state.get('waiting_cases', {}))
                active_cases_list = list(self.shared_state.get('active_cases', set()))

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
                    active_job = self.job_service.active_jobs.get(case_id)
                    gpu_allocation = active_job.get('gpu_allocation', []) if active_job else []
                    
                    update_data = UpdateData(
                        case_id=case_id,
                        status=case_status.get('status', 'UNKNOWN'),
                        gpu_allocation=gpu_allocation
                    )
                    self.status_display.update_case_status(update_data)

        except Exception as e:
            self.logger.error(f"Error updating status display data: {e}")

    def get_system_status(self) -> Dict[str, Any]:
        """Get a snapshot of the current system status from shared state."""
        with self.shared_state_lock:
            return {
                "gpu_status": self.shared_state.get('gpu_status', {}),
                "system_health": self.shared_state.get('system_health', {}),
                "active_cases_count": len(self.shared_state.get('active_cases', set())),
                "waiting_cases_count": len(self.shared_state.get('waiting_cases', {}))
            }

    def _background_monitor_loop(self) -> None:
        """The main background loop for the system monitor."""
        while self.running:
            try:
                self.monitor_system_health()
                self.update_display_data()
                time.sleep(self.monitoring_interval)
            except Exception as e:
                self.error_handler.handle_error(e, {"operation": "background_system_monitoring"})
                time.sleep(self.monitoring_interval)