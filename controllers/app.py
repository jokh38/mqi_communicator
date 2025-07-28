"""
Application Controller - Main orchestrator for MOQUI automation system.

This class replaces the MainController and acts as the central orchestrator,
initializing and holding all services and controllers.
"""

import threading
import time
import queue
from datetime import datetime

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

# Other components
from status_display import StatusDisplay
from processing_steps import WorkflowEngine
from process_monitor import ProcessMonitor

# Controllers
from .scheduler import Scheduler
from .system_monitor import SystemMonitor
from .lifecycle_manager import LifecycleManager
from .backup_manager import BackupManager


class Application:
    """Main application class that orchestrates all system components."""
    
    def __init__(self, config_file: str = "config.json"):
        """Initialize application with all components."""
        self.config_file = config_file
        self.running = False
        self.threads = []
        
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
        
        # Initialize log queue for UI
        self.log_queue = queue.Queue()
        
        # Initialize workflow engine
        self.workflow_engine = WorkflowEngine()
        
        # Initialize status display with default settings (will be updated after config is loaded)
        self.status_display = StatusDisplay(update_interval=2, log_queue=self.log_queue)
        
        # Initialize all components
        self._initialize_components()

        # Update status display with configurable refresh interval (after config is loaded)
        display_refresh_interval = self.config.get("display", {}).get("refresh_interval_seconds", 2)
        self.status_display.update_interval = display_refresh_interval
        
        # Initialize controllers
        self._initialize_controllers()

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
                logger=self.logger
            )

            self.job_service = JobService(
                resource_manager=self.resource_manager,
                case_service=self.case_service,
                max_concurrent_jobs=self.max_concurrent_cases,
                remote_executor=self.remote_executor,
                status_display=self.status_display,
                logger=self.logger
            )

            # Initialize process monitor
            self.process_monitor = ProcessMonitor(
                logger=self.logger,
                monitoring_interval=self.monitoring_interval
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
                    raise RuntimeError(f"Auto GPU allocation failed: {e}") from e
                    
            elif isinstance(gpu_id, (int, str)) and str(gpu_id).isdigit():
                # GPU ID is already a number, no action needed
                self.logger.info(f"Using configured GPU ID: {gpu_id}")
            else:
                # Invalid GPU ID configuration
                self.logger.warning(f"Invalid GPUID configuration: {gpu_id}. Using default behavior.")
                
        except Exception as e:
            self.logger.error(f"Error during auto GPU allocation resolution: {e}")
            raise

    def _initialize_controllers(self) -> None:
        """Initialize all controller instances."""
        try:
            # Initialize LifecycleManager
            self.lifecycle_manager = LifecycleManager(
                logger=self.logger,
                state_manager=self.state_manager,
                resource_manager=self.resource_manager,
                remote_executor=self.remote_executor,
                case_service=self.case_service
            )
            
            # Initialize Scheduler
            self.scheduler = Scheduler(
                logger=self.logger,
                config=self.config,
                case_service=self.case_service,
                job_service=self.job_service,
                scan_queue=self.scan_queue,
                completed_queue=self.completed_queue,
                shared_state=self.shared_state,
                shared_state_lock=self.shared_state_lock,
                scan_lock=self.scan_lock,
                status_display=self.status_display,
                error_handler=self.error_handler,
                resource_manager=self.resource_manager,
                transfer_manager=self.transfer_manager,
                remote_executor=self.remote_executor,
                state_manager=self.state_manager
            )
            
            # Initialize SystemMonitor
            self.system_monitor = SystemMonitor(
                logger=self.logger,
                config=self.config,
                monitoring_interval=self.monitoring_interval,
                resource_manager=self.resource_manager,
                shared_state=self.shared_state,
                shared_state_lock=self.shared_state_lock,
                state_manager=self.state_manager,
                job_service=self.job_service,
                error_handler=self.error_handler
            )
            
            # Initialize BackupManager
            self.backup_manager = BackupManager(
                logger=self.logger,
                config=self.config,
                backup_months_to_keep=self.backup_months_to_keep
            )
            
            self.logger.info("All controllers initialized successfully")
            
        except Exception as e:
            self.logger.error(f"Failed to initialize controllers: {e}")
            raise

    def run(self) -> None:
        """Main application run method."""
        try:
            if not self._initialize_application():
                return
            
            self._start_services()
            self._run_main_loop()
            
        except KeyboardInterrupt:
            self.logger.info("Received keyboard interrupt, shutting down...")
        except Exception as e:
            self.error_handler.handle_error(e, {"operation": "main_execution"})
        finally:
            self.shutdown()
    
    def _initialize_application(self) -> bool:
        """Initialize application and acquire lock."""
        if not self.lifecycle_manager.acquire_lock():
            if hasattr(self, 'logger'):
                self.logger.critical("Failed to acquire application lock. Another instance may be running.")
            else:
                print("CRITICAL: Failed to acquire application lock. Another instance may be running.")
            return False
        
        self.logger.info("Starting MOQUI automation system")
        self.running = True
        return True
    
    def _start_services(self) -> None:
        """Start all background services and perform initial operations."""
        self.status_display.start()
        self.lifecycle_manager.perform_startup_checks()
        
        self.scheduler.start()
        self.system_monitor.start()
        
        self._perform_initial_operations()
    
    def _perform_initial_operations(self) -> None:
        """Perform initial system health check and case scan."""
        self.logger.info("Performing initial system health check and display update.")
        self.system_monitor.monitor_system_health()
        self.system_monitor.update_status_display()

        self.logger.info("Performing initial scan for new cases.")
        self.scheduler.scan_for_new_cases()
    
    def _run_main_loop(self) -> None:
        """Run the main scheduling loop."""
        while self.running:
            try:
                self._execute_scheduling_cycle()
            except KeyboardInterrupt:
                self.logger.info("Received keyboard interrupt, shutting down...")
                break
            except Exception as e:
                self.error_handler.handle_error(e, {"operation": "main_loop"})
                time.sleep(60)  # Wait before retrying
    
    def _execute_scheduling_cycle(self) -> None:
        """Execute one complete scheduling cycle."""
        current_time = datetime.now()
        next_scan_time = self.scheduler.calculate_next_scan_time(current_time)
        
        self._wait_for_next_scan(next_scan_time)
        
        if self.running:
            self.scheduler.scan_for_new_cases()
    
    def _wait_for_next_scan(self, next_scan_time) -> None:
        """Wait until next scan time while performing maintenance tasks."""
        while datetime.now() < next_scan_time and self.running:
            time.sleep(10)  # Check every 10 seconds for more responsive display
            
            self.system_monitor.update_status_display()
            self._perform_periodic_maintenance()
    
    def _perform_periodic_maintenance(self) -> None:
        """Perform periodic maintenance tasks while waiting."""
        current_time = datetime.now()
        
        # Perform health checks every 5 minutes
        if current_time.minute % 5 == 0:
            self.system_monitor.monitor_system_health()
        
        self._check_daily_archive(current_time)
        self._check_monthly_backup(current_time)
    
    def _check_daily_archive(self, current_time) -> None:
        """Check for daily archive if conditions are met."""
        if current_time.hour < 7:
            return
            
        with self.shared_state_lock:
            last_check_date = self.shared_state.get('last_archive_check_date')
        
        if last_check_date != current_time.date():
            self.logger.info("Performing daily check for old cases to archive.")
            self.case_service.archive_old_cases()
            with self.shared_state_lock:
                self.shared_state['last_archive_check_date'] = current_time.date()
    
    def _check_monthly_backup(self, current_time) -> None:
        """Check for monthly backup at scheduled time."""
        if current_time.hour == 2 and current_time.minute == 0:
            self.backup_manager.run_backup_cycle()

    def shutdown(self) -> None:
        """Shutdown the application and cleanup resources."""
        try:
            self.logger.info("Shutting down MOQUI automation system")
            self.running = False
            
            # Stop controllers
            if hasattr(self, 'scheduler'):
                self.scheduler.stop()
            if hasattr(self, 'system_monitor'):
                self.system_monitor.stop()
            
            # Perform shutdown cleanup
            if hasattr(self, 'lifecycle_manager'):
                self.lifecycle_manager.perform_shutdown()
            
            # Stop status display
            self.status_display.stop()
            
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

    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.shutdown()
        return False