"""Lifecycle Manager for MOQUI automation system.

Handles application startup, shutdown, process locking, and main execution loop.
"""

import threading
import time
import queue
import psutil
import os
import atexit
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Any

# Core components
from core.config import ConfigManager
from core.logging import Logger
from core.error_handler import ErrorHandler
from core.state import StateManager

# Services
from services.resource_manager import ResourceManager
from services.case_service import CaseService
from services.job_service import JobService

# Remote communication
from remote.connection import ConnectionManager
from remote.transfer import TransferManager

# Executors
from executors.remote import RemoteExecutor
from executors.local import LocalExecutor

# Other components
from status_display import StatusDisplay
from processing_steps import WorkflowEngine
from process_monitor import ProcessMonitor


class LifecycleManager:
    """Manages application lifecycle including startup, shutdown, and main execution loop."""
    
    def __init__(self, config_file: str = "config.json"):
        """Initialize lifecycle manager with all components."""
        self.config_file = config_file
        self.running = False
        self.threads = []
        self.lock_file = Path("mqi_communicator.pid")
        
        # Initialize queues
        self.scan_queue = queue.Queue()
        self.completed_queue = queue.Queue()
        
        # Core services (will be properly initialized after config is loaded)
        self.state_manager = None
        self.resource_manager = None
        self.case_service = None
        self.job_service = None
        self.connection_manager = None
        self.transfer_manager = None
        self.remote_executor = None
        
        # Additional shared state (non-case related)
        self.shared_state = {
            'gpu_status': {},
            'system_health': {},
            'active_cases': set(),
            'last_scan_time': None,
            'waiting_cases': {},  # {case_id: {'retry_count': int, 'next_retry_time': datetime}}
            'last_archive_check_date': None
        }
        
        # Thread control
        self.scan_lock = threading.Lock()
        self.shared_state_lock = threading.Lock()
        
        # Register cleanup function
        atexit.register(self._cleanup_lock_file)
        
        # Initialize log queue for UI
        self.log_queue = queue.Queue()
        
        # Initialize workflow engine
        self.workflow_engine = WorkflowEngine()
        
        # Initialize status display with default settings (will be updated after config is loaded)
        self.status_display = StatusDisplay(update_interval=2, log_queue=self.log_queue)
        
        # Initialize controller components
        self.scheduler = None
        self.system_monitor = None
        self.recovery_manager = None
        
        # Initialize all components
        self._initialize_components()

        # Update status display with configurable refresh interval (after config is loaded)
        display_refresh_interval = self.config.get("display", {}).get("refresh_interval_seconds", 2)
        self.status_display.update_interval = display_refresh_interval

    def _acquire_lock(self) -> bool:
        """Acquire application lock to prevent multiple instances."""
        try:
            if self.lock_file.exists():
                # Check if lock file contains a valid PID
                try:
                    with open(self.lock_file, 'r') as f:
                        pid = int(f.read().strip())
                    
                    # Check if process is still running
                    if psutil.pid_exists(pid):
                        self.logger.warning(f"Another instance is already running (PID: {pid})")
                        return False
                    else:
                        # Process no longer exists, remove stale lock file
                        self.lock_file.unlink()
                        self.logger.info("Removed stale lock file")
                except (ValueError, FileNotFoundError):
                    # Invalid lock file, remove it
                    self.lock_file.unlink()
                    self.logger.info("Removed invalid lock file")
            
            # Create lock file with current PID
            with open(self.lock_file, 'w') as f:
                f.write(str(os.getpid()))
            
            self.logger.info(f"Acquired application lock (PID: {os.getpid()})")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to acquire lock: {e}")
            return False

    def _cleanup_lock_file(self) -> None:
        """Clean up lock file on exit."""
        try:
            if self.lock_file.exists():
                self.lock_file.unlink()
                self.logger.info("Released application lock")
        except Exception as e:
            self.logger.error(f"Error cleaning up lock file: {e}")

    def _resolve_auto_gpu_allocation(self) -> None:
        """Resolve auto GPU allocation by finding an idle GPU and updating configuration."""
        try:
            # Check if moqui_tps_template has GPUID set to "auto"
            moqui_template = self.config.get("moqui_tps_template", {})
            gpu_id = moqui_template.get("GPUID")
            
            if gpu_id == "auto":
                self.logger.info("Auto GPU allocation detected, finding idle GPU...")
                
                try:
                    # Find idle GPU using resource manager
                    selected_gpu_id = self.resource_manager.find_idle_gpu()
                    
                    # Update the configuration with the resolved GPU ID
                    self.config["moqui_tps_template"]["GPUID"] = selected_gpu_id
                    
                    # Update the config manager as well to persist the change during this session
                    self.config_manager.config["moqui_tps_template"]["GPUID"] = selected_gpu_id
                    
                    self.logger.info(f"Auto GPU allocation resolved: Selected GPU {selected_gpu_id}")
                    
                except RuntimeError as e:
                    # Log critical error and exit the application as specified
                    self.logger.critical(f"Failed to find idle GPU for auto allocation: {e}")
                    raise RuntimeError(f"Auto GPU allocation failed: {e}")
                    
            elif isinstance(gpu_id, (int, str)) and str(gpu_id).isdigit():
                # GPU ID is already a number, no action needed
                self.logger.info(f"Using configured GPU ID: {gpu_id}")
            else:
                # Invalid GPU ID configuration
                self.logger.warning(f"Invalid GPUID configuration: {gpu_id}. Using default behavior.")
                
        except Exception as e:
            self.logger.error(f"Error during auto GPU allocation resolution: {e}")
            raise

    def _initialize_components(self) -> None:
        """Initialize all system components using new service layer architecture."""
        try:
            # Initialize core components first
            self.logger = Logger(log_directory="logs", log_queue=self.log_queue)
            
            # Initialize configuration manager
            self.config_manager = ConfigManager(config_path=self.config_file, logger=self.logger)
            self.config = self.config_manager.get_config()

            # Update logger with configuration
            self.logger.config = self.config.get("logging", {})
            
            # Initialize StateManager
            status_file = self.config.get("paths", {}).get("status_file", "case_status.json")
            self.state_manager = StateManager(status_file=status_file, logger=self.logger)

            # Extract configuration values
            self.scan_interval = self.config_manager.get_scanning_interval()
            self.max_concurrent_cases = self.config_manager.get_max_concurrent_cases()
            
            scanning_config = self.config.get("scanning", {})
            self.stale_case_recovery_hours = scanning_config.get("stale_case_recovery_hours", 1)
            
            self.monitoring_interval = self.config_manager.get_monitoring_interval()
            
            error_config = self.config.get("error_handling", {})
            self.max_network_retries = error_config.get("max_network_retries", 3)
            
            backup_config = self.config.get("backup", {})
            self.backup_months_to_keep = backup_config.get("months_to_keep", 12)
            
            # Log session start
            self.logger.info("=" * 80)
            self.logger.info(f"{'=' * 20} START NEW SESSION {'=' * 20}")
            self.logger.info(f"Session started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            self.logger.info(f"Process ID: {os.getpid()}")
            self.logger.info("=" * 80)

            # Initialize core services
            self.error_handler = ErrorHandler(logger=self.logger)

            # Initialize connection and communication layer
            self.connection_manager = ConnectionManager(
                config_manager=self.config_manager,
                logger=self.logger
            )
            
            self.transfer_manager = TransferManager(
                connection_manager=self.connection_manager,
                logger=self.logger
            )
            
            self.remote_executor = RemoteExecutor(
                connection_manager=self.connection_manager,
                config_manager=self.config_manager,
                logger=self.logger
            )
            
            # Update status display with connection status
            if hasattr(self, 'status_display'):
                self.status_display.update_system_info({
                    "connection_manager": "INITIALIZED",
                    "remote_executor": "INITIALIZED",
                    "gpu_server_auth": "READY"
                })

            # Initialize resource manager
            self.resource_manager = ResourceManager(
                total_gpus=self.config_manager.get_total_gpus(),
                reserved_gpus=self.config_manager.get_reserved_gpus(),
                memory_threshold=self.config_manager.get_memory_threshold(),
                remote_executor=self.remote_executor,
                local_base=self.config_manager.get_local_logdata_path(),
                remote_base=self.config_manager.get_remote_workspace_path(),
                output_base=self.config_manager.get_local_output_path(),
                logger=self.logger
            )

            # Resolve auto GPU allocation if configured
            self._resolve_auto_gpu_allocation()

            # Initialize service layer
            self.case_service = CaseService(
                base_path=self.config_manager.get_local_logdata_path(),
                state_manager=self.state_manager,
                logger=self.logger,
                config=self.config
            )

            self.job_service = JobService(
                resource_manager=self.resource_manager,
                case_service=self.case_service,
                max_concurrent_jobs=self.max_concurrent_cases,
                remote_executor=self.remote_executor,
                config=self.config,
                status_display=self.status_display,
                logger=self.logger
            )

            # Initialize process monitor
            self.process_monitor = ProcessMonitor(
                remote_executor=self.remote_executor,
                monitoring_interval=self.monitoring_interval,
                status_display=self.status_display,
                logger=self.logger
            )

            # Initialize controller components
            from .scheduler import Scheduler
            from .system_monitor import SystemMonitor
            from .recovery_manager import RecoveryManager
            
            self.scheduler = Scheduler(
                scan_queue=self.scan_queue,
                shared_state=self.shared_state,
                shared_state_lock=self.shared_state_lock,
                scan_lock=self.scan_lock,
                case_service=self.case_service,
                job_service=self.job_service,
                status_display=self.status_display,
                logger=self.logger,
                error_handler=self.error_handler,
                scan_interval=self.scan_interval,
                workflow_engine=self.workflow_engine,
                resource_manager=self.resource_manager,
                transfer_manager=self.transfer_manager,
                remote_executor=self.remote_executor,
                state_manager=self.state_manager,
                config=self.config,
                completed_queue=self.completed_queue
            )
            
            self.system_monitor = SystemMonitor(
                shared_state=self.shared_state,
                shared_state_lock=self.shared_state_lock,
                resource_manager=self.resource_manager,
                state_manager=self.state_manager,
                job_service=self.job_service,
                status_display=self.status_display,
                logger=self.logger,
                error_handler=self.error_handler,
                monitoring_interval=self.monitoring_interval
            )
            
            self.recovery_manager = RecoveryManager(
                case_service=self.case_service,
                resource_manager=self.resource_manager,
                remote_executor=self.remote_executor,
                state_manager=self.state_manager,
                logger=self.logger,
                backup_months_to_keep=self.backup_months_to_keep
            )

            self.logger.info("All service components initialized successfully")
            
            # Update status display with successful initialization
            if hasattr(self, 'status_display'):
                self.status_display.update_system_info({
                    "initialization_status": "COMPLETED",
                    "gpu_server_connection": "ESTABLISHED",
                    "system_status": "READY"
                })

        except Exception as e:
            if hasattr(self, 'logger'):
                self.logger.critical(f"Failed to initialize components: {e}")
            else:
                # Fallback to console when logger is not available
                print(f"ERROR: Failed to initialize components: {e}")
            
            # Also update status display if available
            if hasattr(self, 'status_display'):
                self.status_display.update_system_info({
                    "initialization_status": f"FAILED: {str(e)}"
                })
            raise

    def _start_background_workers(self) -> None:
        """Start background worker threads."""
        try:
            # Start case processor thread
            processor_thread = threading.Thread(
                target=self.scheduler.background_case_processor,
                name="CaseProcessor",
                daemon=True
            )
            processor_thread.start()
            self.threads.append(processor_thread)
            
            # Start system monitor thread
            monitor_thread = threading.Thread(
                target=self.system_monitor.background_system_monitor,
                name="SystemMonitor",
                daemon=True
            )
            monitor_thread.start()
            self.threads.append(monitor_thread)
            
            self.logger.info("Background workers started successfully")
            
        except Exception as e:
            self.error_handler.handle_error(e, {"operation": "background_worker_startup"})

    def run(self) -> None:
        """Main execution loop."""
        try:
            # Acquire application lock to prevent multiple instances
            if not self._acquire_lock():
                if hasattr(self, 'logger'):
                    self.logger.critical("Failed to acquire application lock. Another instance may be running.")
                else:
                    print("CRITICAL: Failed to acquire application lock. Another instance may be running.")
                return
            
            self.logger.info("Starting MOQUI automation system")
            self.running = True
            
            # Start status display
            self.status_display.start()
            
            # Perform startup recovery checks
            self.recovery_manager.recovery_startup_checks()
            
            # Start background workers
            self._start_background_workers()
            
            # Perform an initial update to show info immediately
            self.logger.info("Performing initial system health check and display update.")
            self.system_monitor.monitor_system_health()
            self.system_monitor.update_status_display()

            # Perform an initial scan for cases
            self.logger.info("Performing initial scan for new cases.")
            self.scheduler.scan_for_new_cases()
            
            # Main scheduling loop
            while self.running:
                try:
                    # Calculate next scan time
                    current_time = datetime.now()
                    next_scan_time = self.scheduler.calculate_next_scan_time(current_time)
                    
                    # Wait until next scan time
                    while datetime.now() < next_scan_time and self.running:
                        time.sleep(10)  # Check every 10 seconds for more responsive display
                        
                        # Update status display frequently for real-time progress updates
                        self.system_monitor.update_status_display()
                        
                        # Perform health checks while waiting
                        if datetime.now().minute % 5 == 0:  # Every 5 minutes
                            self.system_monitor.monitor_system_health()
                            
                        # Check for daily archive (once per day after 07:00)
                        current_time = datetime.now()
                        if current_time.hour >= 7:
                            with self.shared_state_lock:
                                last_check_date = self.shared_state.get('last_archive_check_date')
                            
                            if last_check_date != current_time.date():
                                self.logger.info("Performing daily check for old cases to archive.")
                                self.case_service.archive_old_cases()
                                with self.shared_state_lock:
                                    self.shared_state['last_archive_check_date'] = current_time.date()

                        # Check for monthly backup (once per day at 02:00)
                        if current_time.hour == 2 and current_time.minute == 0:
                            self.recovery_manager.check_and_perform_monthly_backup()
                    
                    # Execute scheduled scan
                    if self.running:
                        self.scheduler.scan_for_new_cases()
                    
                except KeyboardInterrupt:
                    self.logger.info("Received keyboard interrupt, shutting down...")
                    break
                except Exception as e:
                    self.error_handler.handle_error(e, {"operation": "main_loop"})
                    time.sleep(60)  # Wait before retrying
            
        except KeyboardInterrupt:
            self.logger.info("Received keyboard interrupt, shutting down...")
        except Exception as e:
            self.error_handler.handle_error(e, {"operation": "main_execution"})
        finally:
            self.shutdown()

    def stop(self) -> None:
        """Stop the main controller."""
        self.logger.info("Stopping MOQUI automation system")
        self.running = False

    def shutdown(self) -> None:
        """Shutdown the controller and cleanup resources."""
        try:
            self.logger.info("Shutting down MOQUI automation system")
            self.running = False
            
            # Clean up remote processes for currently processing cases
            self._cleanup_active_remote_processes()
            
            # Wait for background threads to finish
            for thread in self.threads:
                if thread.is_alive():
                    thread.join(timeout=5)
            
            # Cleanup resources
            self.cleanup_resources()
            
            # Stop status display
            self.status_display.stop()
            
            # Release application lock
            self._cleanup_lock_file()
            
            self.logger.info("MOQUI automation system shutdown complete")
            
        except Exception as e:
            if hasattr(self, 'logger'):
                self.logger.error(f"Error during shutdown: {e}")
            else:
                print(f"ERROR: Error during shutdown: {e}")
            
            # Update status display if available
            if hasattr(self, 'status_display'):
                self.status_display.update_system_info({
                    "shutdown_status": f"ERROR: {str(e)}"
                })

    def _cleanup_active_remote_processes(self) -> None:
        """Clean up remote processes for active cases during shutdown."""
        try:
            if not hasattr(self, 'case_service') or not self.remote_executor:
                return
                
            processing_cases = self.state_manager.get_cases_by_status("PROCESSING")
            if not processing_cases:
                return
                
            self.logger.info(f"Cleaning up remote processes for {len(processing_cases)} active cases")
            
            for case_id in processing_cases:
                case_status = self.state_manager.get_case_status(case_id)
                if not case_status:
                    continue
                    
                # Kill remote process
                remote_pid = case_status.get('remote_pid')
                if remote_pid:
                    try:
                        result = self.remote_executor.execute_command(f"kill {remote_pid}")
                        if result['exit_code'] == 0:
                            self.logger.info(f"Killed remote process {remote_pid} for case {case_id}")
                        else:
                            self.logger.warning(f"Failed to kill remote process {remote_pid}: {result['stderr']}")
                    except Exception as e:
                        self.logger.warning(f"Error killing remote process {remote_pid} for case {case_id}: {e}")
                
                # Release GPU locks through resource manager
                locked_gpus = case_status.get('locked_gpus', [])
                for gpu_id in locked_gpus:
                    try:
                        self.resource_manager.release_gpu_resource(gpu_id)
                        self.logger.info(f"Released GPU lock for GPU {gpu_id} (case {case_id})")
                    except Exception as e:
                        self.logger.warning(f"Error releasing GPU lock for GPU {gpu_id}: {e}")
                        
        except Exception as e:
            self.logger.error(f"Error cleaning up active remote processes: {e}")

    def cleanup_resources(self) -> None:
        """Clean up all resources."""
        try:
            # Close connection manager (which manages both SFTP and SSH connections)
            if hasattr(self, 'connection_manager'):
                self.connection_manager.disconnect()
            
            # Stop process monitor
            if hasattr(self, 'process_monitor'):
                self.process_monitor.stop()
            
            # Cleanup error handler resources
            if hasattr(self, 'error_handler'):
                self.error_handler.cleanup_resources()
            
        except Exception as e:
            if hasattr(self, 'logger'):
                self.logger.error(f"Error during resource cleanup: {e}")
            else:
                print(f"ERROR: Error during resource cleanup: {e}")
            
            # Update status display if available
            if hasattr(self, 'status_display'):
                self.status_display.update_system_info({
                    "cleanup_status": f"ERROR: {str(e)}"
                })

    def get_system_status(self) -> Dict[str, Any]:
        """Get current system status."""
        try:
            with self.shared_state_lock:
                # Create a snapshot of the state to avoid holding the lock for too long
                shared_state_snapshot = {
                    "gpu_status": self.shared_state.get('gpu_status', {}),
                    "last_scan_time": self.shared_state.get('last_scan_time'),
                    "active_cases_count": len(self.shared_state.get('active_cases', set())),
                    "system_health": self.shared_state.get('system_health', {})
                }

            return {
                "running": self.running,
                "gpu_status": shared_state_snapshot["gpu_status"],
                "scan_status": {
                    "last_scan_time": shared_state_snapshot["last_scan_time"],
                    "scan_interval_minutes": self.scan_interval
                },
                "job_status": self.job_service.get_queue_status(),
                "process_status": {
                    "background_threads": len(self.threads),
                    "threads_alive": sum(1 for t in self.threads if t.is_alive())
                },
                "queue_status": {
                    "scan_queue_size": self.scan_queue.qsize(),
                    "completed_queue_size": self.completed_queue.qsize()
                },
                "system_health": shared_state_snapshot["system_health"]
            }
        except Exception as e:
            self.error_handler.handle_error(e, {"operation": "get_system_status"})
            return {"error": "Failed to get system status"}

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.shutdown()
        return False