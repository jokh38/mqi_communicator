import threading
import time
import queue
import psutil
import os
import atexit
import shutil
import shlex
import zipfile
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

from config_manager import ConfigManager
from case_scanner import CaseScanner
from gpu_manager import GPUManager
from sftp_manager import SFTPManager
from remote_executor import RemoteExecutor
from job_scheduler import JobScheduler
from process_monitor import ProcessMonitor
from directory_manager import DirectoryManager
from error_handler import ErrorHandler
from logger import Logger
from status_display import StatusDisplay
from processing_steps import WorkflowEngine, ProcessingContext


class MainController:
    """Main controller for MOQUI automation system."""
    
    def __init__(self, config_file: str = "config.json"):
        """Initialize main controller with all components."""
        self.config_file = config_file
        self.running = False
        self.threads = []
        self.lock_file = Path(".app.lock")
        
        # Initialize queues
        self.scan_queue = queue.Queue()
        self.processing_queue = queue.Queue()
        self.completed_queue = queue.Queue()
        
        # Shared state
        self.shared_state = {
            'gpu_status': {},
            'case_status': {},
            'system_health': {},
            'active_cases': set(),
            'last_scan_time': None,
            'waiting_cases': {},  # {case_id: {'retry_count': int, 'next_retry_time': datetime}}
            'last_archive_check_date': None
        }
        
        # Thread control
        self.scan_lock = threading.Lock()
        self.processing_lock = threading.Lock()
        self.shared_state_lock = threading.Lock()
        
        # Register cleanup function
        atexit.register(self._cleanup_lock_file)
        
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

    def _recovery_startup_checks(self) -> None:
        """Perform recovery checks on startup."""
        try:
            self.logger.info("Performing startup recovery checks")
            
            # Recover stale cases
            recovery_result = self.case_scanner.check_and_recover_stale_cases(max_processing_hours=self.stale_case_recovery_hours)
            
            if recovery_result["recovered_count"] > 0:
                self.logger.warning(f"Recovered {recovery_result['recovered_count']} stale cases: {recovery_result['recovered_cases']}")
            else:
                self.logger.info("No stale cases found during startup")
            
            # Clean up any zombie GPU processes
            if hasattr(self.gpu_manager, 'remote_executor') and self.gpu_manager.remote_executor:
                zombie_processes = self.gpu_manager.cleanup_zombie_processes()
                if zombie_processes:
                    self.logger.warning(f"Cleaned up {len(zombie_processes)} zombie GPU processes")
                else:
                    self.logger.info("No zombie GPU processes found")
            
        except Exception as e:
            self.logger.error(f"Error during startup recovery checks: {e}")

    def _is_network_error(self, exception: Exception) -> bool:
        """Check if the exception is a network-related error."""
        import socket
        import paramiko
        
        network_error_types = (
            socket.error,
            paramiko.SSHException,
            paramiko.AuthenticationException,
            paramiko.ChannelException,
            OSError,
            TimeoutError,
            ConnectionError
        )
        return isinstance(exception, network_error_types)

    def _calculate_retry_delay(self, retry_count: int, base_delay: int = 60) -> int:
        """Calculate retry delay using exponential backoff with jitter."""
        import random
        import math
        
        # Exponential backoff: base_delay * (2 ^ retry_count)
        delay = base_delay * (2 ** min(retry_count, 6))  # Cap at 6 to prevent extremely long delays
        
        # Add jitter (±25% randomization)
        jitter = delay * 0.25 * random.uniform(-1, 1)
        final_delay = max(base_delay, int(delay + jitter))
        
        return final_delay

    def _add_to_waiting_list(self, case_id: str) -> None:
        """Add case to waiting list with exponential backoff."""
        with self.shared_state_lock:
            current_time = datetime.now()
            
            if case_id in self.shared_state['waiting_cases']:
                # Increment retry count
                waiting_info = self.shared_state['waiting_cases'][case_id]
                waiting_info['retry_count'] += 1
            else:
                # First time adding to waiting list
                waiting_info = {'retry_count': 1}
                self.shared_state['waiting_cases'][case_id] = waiting_info
            
            # Calculate next retry time
            retry_delay = self._calculate_retry_delay(waiting_info['retry_count'])
            waiting_info['next_retry_time'] = current_time + timedelta(seconds=retry_delay)
            
            self.logger.info(f"Added case {case_id} to waiting list (retry #{waiting_info['retry_count']}, "
                            f"next retry in {retry_delay} seconds)")

    def _check_waiting_cases(self) -> List[str]:
        """Check waiting cases and return those ready for retry."""
        with self.shared_state_lock:
            current_time = datetime.now()
            ready_cases = []
            
            # Iterate over a copy of items to allow modification
            cases_to_check = list(self.shared_state['waiting_cases'].items())
            
            for case_id, waiting_info in cases_to_check:
                if current_time >= waiting_info['next_retry_time']:
                    ready_cases.append(case_id)
                    del self.shared_state['waiting_cases'][case_id]
                    self.logger.info(f"Case {case_id} is ready for retry after waiting")
            
            return ready_cases

    def _remove_from_waiting_list(self, case_id: str) -> None:
        """Remove case from waiting list (e.g., when successfully scheduled)."""
        with self.shared_state_lock:
            if case_id in self.shared_state['waiting_cases']:
                del self.shared_state['waiting_cases'][case_id]
                self.logger.debug(f"Removed case {case_id} from waiting list")
    
    def _initialize_components(self) -> None:
        """Initialize all system components."""
        try:
            # Initialize configuration manager
            self.config_manager = ConfigManager(config_path=self.config_file)
            self.config = self.config_manager.get_config()

            # Extract configuration values
            scanning_config = self.config.get("scanning", {})
            gpu_config = self.config.get("gpu_management", {})
            error_config = self.config.get("error_handling", {})
            backup_config = self.config.get("backup", {})

            self.scan_interval = scanning_config.get("interval_minutes", 30)
            self.max_concurrent_cases = scanning_config.get("max_concurrent_cases", 2)
            self.stale_case_recovery_hours = scanning_config.get("stale_case_recovery_hours", 1)
            
            self.monitoring_interval = gpu_config.get("monitoring_interval_sec", 5)
            
            self.max_network_retries = error_config.get("max_network_retries", 3)
            
            self.backup_months_to_keep = backup_config.get("months_to_keep", 12)

            # Initialize logger first
            self.logger = Logger(log_directory="logs", config=self.config.get("logging", {}), log_queue=self.log_queue)
            
            # Log session start with spacer
            self.logger.info("=" * 80)
            self.logger.info(f"{'=' * 20} START NEW SESSION {'=' * 20}")
            self.logger.info(f"Session started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            self.logger.info(f"Process ID: {os.getpid()}")
            self.logger.info("=" * 80)

            # Initialize error handler
            self.error_handler = ErrorHandler(logger=self.logger)

            # Initialize directory manager
            self.directory_manager = DirectoryManager(
                local_base=self.config["paths"]["local_logdata"],
                remote_base=self.config["paths"]["remote_workspace"],
                output_base=self.config["paths"]["local_output"],
                logger=self.logger
            )

            # Initialize SFTP manager
            self.sftp_manager = SFTPManager(
                host=self.config["servers"]["linux_gpu"],
                username=self.config["credentials"]["username"],
                password=self.config["credentials"]["password"],
                logger=self.logger
            )
            
            # Update status display with SFTP connection
            if hasattr(self, 'status_display'):
                self.status_display.update_system_info({
                    "sftp_connection": "INITIALIZED"
                })

            # Initialize remote executor
            self.remote_executor = RemoteExecutor(config=self.config, logger=self.logger)
            
            # Update status display with remote executor
            if hasattr(self, 'status_display'):
                self.status_display.update_system_info({
                    "remote_executor": "INITIALIZED",
                    "gpu_server_auth": "READY"
                })

            # Initialize GPU manager with remote executor
            self.gpu_manager = GPUManager(
                total_gpus=self.config["gpu_management"]["total_gpus"],
                reserved_gpus=self.config["gpu_management"]["reserved_gpus"],
                memory_threshold=self.config["gpu_management"]["memory_threshold_mb"],
                remote_executor=self.remote_executor,
                logger=self.logger
            )

            # Initialize case scanner
            self.case_scanner = CaseScanner(
                base_path=self.config["paths"]["local_logdata"],
                logger=self.logger
            )

            # Initialize job scheduler
            self.job_scheduler = JobScheduler(
                gpu_manager=self.gpu_manager,
                case_scanner=self.case_scanner,
                max_concurrent_jobs=self.max_concurrent_cases,
                remote_executor=self.remote_executor,
                config=self.config,
                directory_manager=self.directory_manager,
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

            # Connect directory manager to other components
            self.directory_manager.sftp_manager = self.sftp_manager
            self.directory_manager.remote_executor = self.remote_executor

            self.logger.info("All components initialized successfully")
            
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
    
    def _calculate_next_scan_time(self, current_time: datetime) -> datetime:
        """Calculate the next scan time based on interval."""
        try:
            # Calculate minutes until next scan interval
            minutes_past_hour = current_time.minute
            next_scan_minute = ((minutes_past_hour // self.scan_interval) + 1) * self.scan_interval
            
            if next_scan_minute >= 60:
                # Next scan is in the next hour
                next_scan_time = current_time.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            else:
                # Next scan is in the current hour
                next_scan_time = current_time.replace(minute=next_scan_minute, second=0, microsecond=0)
            
            return next_scan_time
            
        except Exception as e:
            self.logger.error(f"Error calculating next scan time: {e}")
            # Default to next 30 minutes
            return current_time + timedelta(minutes=self.scan_interval)
    
    def _scan_for_new_cases(self) -> None:
        """Scan for new cases and add them to the queue."""
        try:
            with self.scan_lock:
                self.logger.info("Starting case scan")
                
                # Update status display with scan start
                if hasattr(self, 'status_display'):
                    self.status_display.update_system_info({
                        "scan_status": "SCANNING",
                        "last_scan": datetime.now().strftime("%H:%M:%S")
                    })
                
                # Scan for new cases
                new_cases = self.case_scanner.scan_for_new_cases()
                
                # Add new cases to queue under lock
                with self.shared_state_lock:
                    for case_id in new_cases:
                        if case_id not in self.shared_state['active_cases']:
                            self.scan_queue.put(case_id)
                            self.shared_state['active_cases'].add(case_id)
                            self.logger.info(f"Added new case to queue: {case_id}")
                    
                    self.shared_state['last_scan_time'] = datetime.now()
                
                self.logger.info(f"Case scan completed. Found {len(new_cases)} new cases")
                
                # Update status display with scan results
                if hasattr(self, 'status_display'):
                    self.status_display.update_system_info({
                        "scan_status": "COMPLETED",
                        "new_cases_found": len(new_cases),
                        "last_scan_result": f"Found {len(new_cases)} new cases"
                    })
                
        except Exception as e:
            self.error_handler.handle_error(e, {"operation": "case_scanning"})
    
    def _process_case_queue(self) -> None:
        """Process cases from the queue with exponential backoff for failures."""
        try:
            with self.processing_lock:
                # Check if we have reached max concurrent cases under lock
                with self.shared_state_lock:
                    active_case_count = len(self.shared_state['active_cases'])
                
                if active_case_count >= self.max_concurrent_cases:
                    return
                
                # First, check if any waiting cases are ready for retry
                ready_cases = self._check_waiting_cases()
                for case_id in ready_cases:
                    self.scan_queue.put(case_id)
                
                # Get available GPUs
                available_gpus = self.gpu_manager.get_available_gpus()
                if not available_gpus:
                    self.logger.debug("No GPUs available for processing")
                    return
                
                # Process cases from queue
                cases_to_process = []
                while (not self.scan_queue.empty() and 
                       len(cases_to_process) < self.max_concurrent_cases and
                       len(cases_to_process) < len(available_gpus)):
                    
                    try:
                        case_id = self.scan_queue.get_nowait()
                        cases_to_process.append(case_id)
                    except queue.Empty:
                        break
                
                # Schedule cases for processing
                for case_id in cases_to_process:
                    if self.job_scheduler.schedule_case(case_id):
                        self.processing_queue.put(case_id)
                        self._remove_from_waiting_list(case_id)  # This is now thread-safe
                        self.logger.info(f"Scheduled case for processing: {case_id}")
                    else:
                        # Add to waiting list with exponential backoff instead of immediate retry
                        self._add_to_waiting_list(case_id) # This is now thread-safe
                        self.logger.warning(f"Failed to schedule case {case_id} - added to waiting list")
                
        except Exception as e:
            self.error_handler.handle_error(e, {"operation": "case_queue_processing"})
    
    def _process_case(self, case_id: str) -> bool:
        """Process a single case through the complete workflow using WorkflowEngine."""
        try:
            # Create processing context
            context = self.workflow_engine.create_context(
                case_id=case_id,
                logger=self.logger,
                status_display=self.status_display,
                directory_manager=self.directory_manager,
                sftp_manager=self.sftp_manager,
                remote_executor=self.remote_executor,
                job_scheduler=self.job_scheduler,
                case_scanner=self.case_scanner,
                shared_state=self.shared_state
            )
            
            # Execute the workflow
            success = self.workflow_engine.execute_workflow(context)
            
            if success:
                # Move to completed queue
                self.completed_queue.put(case_id)
                
                # Remove from display after a short delay
                time.sleep(3)
                self.status_display.remove_case(case_id)
                
                self.logger.info(f"Successfully processed case: {case_id}")
            
            return success
            
        except Exception as e:
            # Always handle the error and update status
            self.error_handler.handle_error(e, {"operation": "case_processing", "case_id": case_id})
            self.case_scanner.update_case_status(case_id, "FAILED")
            self.logger.log_case_progress(case_id, "FAILED", 0.0, {"stage": "error", "error": str(e)})
            self.status_display.update_case_status(case_id, "FAILED", 0.0, "Failed", error_message=str(e))
            with self.shared_state_lock:
                self.shared_state['active_cases'].discard(case_id)

            # Check if this is a network error that should be retried
            if self._is_network_error(e):
                # Get current retry count from case status
                case_info = self.case_scanner.get_case_status(case_id)
                current_retry_count = case_info.get("retry_count", 0) if case_info else 0
                
                if current_retry_count < self.max_network_retries:  # Max retries for network errors
                    self.logger.warning(f"Network error processing case {case_id} (retry {current_retry_count + 1}/{self.max_network_retries}): {e}")
                    # Update case status with incremented retry count and reset to NEW for reprocessing
                    self.case_scanner.update_case_status(case_id, "NEW", retry_count=current_retry_count + 1)
                    return False # Indicate failure, but allow for retry
                else:
                    self.logger.error(f"Case {case_id} failed after {current_retry_count} network retries: {e}")
            
            # Clean up remote workspace on non-retryable failure
            try:
                remote_path = self.directory_manager.get_case_remote_path(case_id)
                self.remote_executor.execute_command(f"rm -rf {shlex.quote(remote_path)}")
                self.logger.info(f"Cleaned up remote workspace for failed case: {case_id}")
            except Exception as cleanup_error:
                self.logger.warning(f"Failed to clean up remote workspace for case {case_id}: {cleanup_error}")
            
            return False
    
    def _monitor_system_health(self) -> None:
        """Monitor system health and log metrics."""
        try:
            # Get GPU status
            gpu_status = self.gpu_manager.get_gpu_status_summary()
            
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

    def _update_status_display(self) -> None:
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
                case_status = self.case_scanner.get_case_status(case_id)
                if case_status:
                    job_status = self.job_scheduler.get_job_status(case_id)
                    active_job = self.job_scheduler.active_jobs.get(case_id)
                    
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

    def _check_and_perform_monthly_backup(self) -> None:
        """Check if monthly backup is needed and perform it."""
        try:
            backup_info_file = Path("last_backup.txt")
            current_month = datetime.now().strftime("%Y-%m")
            
            # Check last backup month
            last_backup_month = None
            if backup_info_file.exists():
                try:
                    with open(backup_info_file, 'r') as f:
                        last_backup_month = f.read().strip()
                except Exception as e:
                    self.logger.warning(f"Failed to read last backup info: {e}")
            
            # Perform backup if needed
            if last_backup_month != current_month:
                self.logger.info(f"Starting monthly backup for {current_month}")
                success = self._perform_monthly_backup()
                
                if success:
                    # Update last backup month
                    with open(backup_info_file, 'w') as f:
                        f.write(current_month)
                    self.logger.info(f"Monthly backup completed successfully for {current_month}")
                else:
                    self.logger.error(f"Monthly backup failed for {current_month}")
            
        except Exception as e:
            self.logger.error(f"Error during monthly backup check: {e}")

    def _perform_monthly_backup(self) -> bool:
        """Perform monthly backup of case status and logs."""
        try:
            backup_dir = Path("backups")
            backup_dir.mkdir(exist_ok=True)
            
            current_month = datetime.now().strftime("%Y-%m")
            success = True
            
            # Backup case_status.json
            try:
                case_status_file = Path("case_status.json")
                if case_status_file.exists():
                    backup_status_file = backup_dir / f"case_status_{current_month}.json"
                    shutil.copy2(case_status_file, backup_status_file)
                    self.logger.info(f"Backed up case_status.json to {backup_status_file}")
                else:
                    self.logger.warning("case_status.json not found for backup")
            except Exception as e:
                self.logger.error(f"Failed to backup case_status.json: {e}")
                success = False
            
            # Backup logs directory
            try:
                logs_dir = Path("logs")
                if logs_dir.exists() and logs_dir.is_dir():
                    backup_logs_file = backup_dir / f"logs_{current_month}.zip"
                    
                    with zipfile.ZipFile(backup_logs_file, 'w', zipfile.ZIP_DEFLATED) as zipf:
                        for log_file in logs_dir.rglob("*"):
                            if log_file.is_file():
                                # Add file to zip with relative path
                                arcname = log_file.relative_to(logs_dir)
                                zipf.write(log_file, arcname)
                    
                    self.logger.info(f"Backed up logs directory to {backup_logs_file}")
                else:
                    self.logger.warning("logs directory not found for backup")
            except Exception as e:
                self.logger.error(f"Failed to backup logs directory: {e}")
                success = False
            
            # Backup configuration files
            try:
                config_files = ["config.json"]
                for config_file in config_files:
                    config_path = Path(config_file)
                    if config_path.exists():
                        backup_config_file = backup_dir / f"{config_path.stem}_{current_month}{config_path.suffix}"
                        shutil.copy2(config_path, backup_config_file)
                        self.logger.debug(f"Backed up {config_file}")
            except Exception as e:
                self.logger.error(f"Failed to backup configuration files: {e}")
                # Don't fail the entire backup for config files
            
            # Clean up old backups (keep last configured months)
            try:
                self._cleanup_old_backups(backup_dir, months_to_keep=self.backup_months_to_keep)
            except Exception as e:
                self.logger.warning(f"Failed to cleanup old backups: {e}")
            
            return success
            
        except Exception as e:
            self.logger.error(f"Error during monthly backup: {e}")
            return False

    def _cleanup_old_backups(self, backup_dir: Path, months_to_keep: int = 12) -> None:
        """Clean up old backup files, keeping only the specified number of months."""
        try:
            if not backup_dir.exists():
                return
            
            # Calculate cutoff date
            cutoff_date = datetime.now() - timedelta(days=months_to_keep * 30)
            cutoff_month = cutoff_date.strftime("%Y-%m")
            
            # Find old backup files
            old_files = []
            for backup_file in backup_dir.iterdir():
                if backup_file.is_file():
                    # Extract month from filename (format: name_YYYY-MM.ext)
                    try:
                        parts = backup_file.stem.split('_')
                        if len(parts) >= 2:
                            file_month = parts[-1]  # Last part should be YYYY-MM
                            if len(file_month) == 7 and file_month < cutoff_month:
                                old_files.append(backup_file)
                    except Exception:
                        continue
            
            # Remove old files
            for old_file in old_files:
                try:
                    old_file.unlink()
                    self.logger.info(f"Removed old backup file: {old_file}")
                except Exception as e:
                    self.logger.warning(f"Failed to remove old backup file {old_file}: {e}")
            
            if old_files:
                self.logger.info(f"Cleaned up {len(old_files)} old backup files")
            
        except Exception as e:
            self.logger.error(f"Error during backup cleanup: {e}")
    
    def _background_case_processor(self) -> None:
        """Background worker for processing cases."""
        while self.running:
            try:
                # Process case queue
                self._process_case_queue()
                
                # Process individual cases
                try:
                    case_id = self.processing_queue.get(timeout=5)
                    self._process_case(case_id)
                except queue.Empty:
                    continue
                
            except Exception as e:
                self.error_handler.handle_error(e, {"operation": "background_case_processing"})
                time.sleep(1)
    
    def _background_system_monitor(self) -> None:
        """Background worker for system monitoring."""
        while self.running:
            try:
                self._monitor_system_health()
                time.sleep(self.monitoring_interval)
            except Exception as e:
                self.error_handler.handle_error(e, {"operation": "background_system_monitoring"})
                time.sleep(self.monitoring_interval)
    
    def _start_background_workers(self) -> None:
        """Start background worker threads."""
        try:
            # Start case processor thread
            processor_thread = threading.Thread(
                target=self._background_case_processor,
                name="CaseProcessor",
                daemon=True
            )
            processor_thread.start()
            self.threads.append(processor_thread)
            
            # Start system monitor thread
            monitor_thread = threading.Thread(
                target=self._background_system_monitor,
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
            self._recovery_startup_checks()
            
            # Start background workers
            self._start_background_workers()
            
            # Perform an initial update to show info immediately
            self.logger.info("Performing initial system health check and display update.")
            self._monitor_system_health()
            self._update_status_display()

            # Perform an initial scan for cases
            self.logger.info("Performing initial scan for new cases.")
            self._scan_for_new_cases()
            
            # Main scheduling loop
            while self.running:
                try:
                    # Calculate next scan time
                    current_time = datetime.now()
                    next_scan_time = self._calculate_next_scan_time(current_time)
                    
                    # Wait until next scan time
                    while datetime.now() < next_scan_time and self.running:
                        time.sleep(60)  # Check every minute
                        
                        # Perform health checks while waiting
                        if datetime.now().minute % 5 == 0:  # Every 5 minutes
                            self._monitor_system_health()
                            self._update_status_display()
                            
                        # Check for daily archive (once per day after 07:00)
                        current_time = datetime.now()
                        if current_time.hour >= 7:
                            with self.shared_state_lock:
                                last_check_date = self.shared_state.get('last_archive_check_date')
                            
                            if last_check_date != current_time.date():
                                self.logger.info("Performing daily check for old cases to archive.")
                                self.case_scanner.archive_old_cases()
                                with self.shared_state_lock:
                                    self.shared_state['last_archive_check_date'] = current_time.date()

                        # Check for monthly backup (once per day at 02:00)
                        if current_time.hour == 2 and current_time.minute == 0:
                            self._check_and_perform_monthly_backup()
                    
                    # Execute scheduled scan
                    if self.running:
                        self._scan_for_new_cases()
                    
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
    
    def cleanup_resources(self) -> None:
        """Clean up all resources."""
        try:
            # Close SFTP connections
            if hasattr(self, 'sftp_manager'):
                self.sftp_manager.disconnect()
            
            # Close remote executor connections
            if hasattr(self, 'remote_executor'):
                self.remote_executor.disconnect()
            
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
                "job_status": {
                    "active_cases": shared_state_snapshot["active_cases_count"],
                    "max_concurrent_cases": self.max_concurrent_cases
                },
                "process_status": {
                    "background_threads": len(self.threads),
                    "threads_alive": sum(1 for t in self.threads if t.is_alive())
                },
                "queue_status": {
                    "scan_queue_size": self.scan_queue.qsize(),
                    "processing_queue_size": self.processing_queue.qsize(),
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


if __name__ == "__main__":
    # Example usage
    try:
        with MainController() as controller:
            controller.run()
    except KeyboardInterrupt:
        print("Shutting down...")
    except Exception as e:
        print(f"CRITICAL: Error: {e}")
        import traceback
        traceback.print_exc()