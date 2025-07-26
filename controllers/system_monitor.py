"""System Monitor for MOQUI automation system.

Handles system health monitoring and status display updates.
"""

import time
import psutil
from typing import Dict, Any


class SystemMonitor:
    """Manages system health monitoring and status display updates."""
    
    def __init__(self, shared_state, shared_state_lock, resource_manager, 
                 state_manager, job_service, status_display, logger, 
                 error_handler, monitoring_interval):
        """Initialize system monitor with dependencies."""
        self.shared_state = shared_state
        self.shared_state_lock = shared_state_lock
        self.resource_manager = resource_manager
        self.state_manager = state_manager
        self.job_service = job_service
        self.status_display = status_display
        self.logger = logger
        self.error_handler = error_handler
        self.monitoring_interval = monitoring_interval
        self.running = True

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

    def background_system_monitor(self) -> None:
        """Background worker for system monitoring."""
        while self.running:
            try:
                self.monitor_system_health()
                self.update_status_display()  # Update display every monitoring cycle
                time.sleep(self.monitoring_interval)
            except Exception as e:
                self.error_handler.handle_error(e, {"operation": "background_system_monitoring"})
                time.sleep(self.monitoring_interval)

    def get_system_status(self) -> Dict[str, Any]:
        """Get current system status snapshot."""
        with self.shared_state_lock:
            return {
                "gpu_status": self.shared_state.get('gpu_status', {}),
                "system_health": self.shared_state.get('system_health', {}),
                "active_cases_count": len(self.shared_state.get('active_cases', set())),
                "waiting_cases_count": len(self.shared_state.get('waiting_cases', {}))
            }

    def stop(self) -> None:
        """Stop the system monitor."""
        self.running = False