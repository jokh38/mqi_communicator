"""
Lifecycle Manager Controller - Manages application lifecycle: startup, shutdown, and crash recovery.

This class handles application locking, startup recovery checks, cleanup of stale resources,
and shutdown procedures.
"""

import os
import psutil
import atexit
from pathlib import Path


class LifecycleManager:
    """Manages the application's lifecycle: startup, shutdown, and crash recovery."""
    
    def __init__(self, logger, state_manager, resource_manager, remote_executor, case_service):
        """Initialize lifecycle manager with dependencies."""
        self.logger = logger
        self.state_manager = state_manager
        self.resource_manager = resource_manager
        self.remote_executor = remote_executor
        self.case_service = case_service
        
        self.lock_file = Path("mqi_communicator.pid")
        
        # Register cleanup function
        atexit.register(self._cleanup_lock_file)

    def acquire_lock(self) -> bool:
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

    def perform_startup_checks(self) -> None:
        """Perform recovery checks on startup."""
        try:
            self.logger.info("Performing startup recovery checks")
            
            # Recover ALL cases left in PROCESSING state (regardless of duration)
            recovery_result = self.case_service.recover_stale_jobs(max_processing_hours=0)
            
            if recovery_result["recovered_count"] > 0:
                self.logger.warning(f"Recovered {recovery_result['recovered_count']} stale cases: {recovery_result['recovered_cases']}")
                
                # Clean up resources for recovered cases
                for case_id in recovery_result['recovered_cases']:
                    self._cleanup_stale_case_resources(case_id)
            else:
                self.logger.info("No stale cases found during startup")
            
            # Clean up any zombie GPU processes and stale locks
            if self.resource_manager:
                zombie_processes = self.resource_manager.cleanup_zombie_processes()
                if zombie_processes:
                    self.logger.warning(f"Cleaned up {len(zombie_processes)} zombie GPU processes")
                else:
                    self.logger.info("No zombie GPU processes found")
                
                # Clean up stale GPU locks
                cleaned_locks = self.resource_manager.cleanup_stale_locks()
                if cleaned_locks:
                    self.logger.warning(f"Cleaned up stale locks for GPUs: {cleaned_locks}")
                else:
                    self.logger.info("No stale GPU locks found")
            
        except Exception as e:
            self.logger.error(f"Error during startup recovery checks: {e}")

    def _cleanup_stale_case_resources(self, case_id: str) -> None:
        """Clean up resources for a stale case."""
        try:
            case_status = self.state_manager.get_case_status(case_id)
            if not case_status:
                return
            
            # Clean up remote processes
            remote_pid = case_status.get('remote_pid')
            if remote_pid and self.remote_executor:
                try:
                    result = self.remote_executor.execute_command(f"kill {remote_pid}")
                    if result['exit_code'] == 0:
                        self.logger.info(f"Killed remote process {remote_pid} for stale case {case_id}")
                    else:
                        self.logger.warning(f"Failed to kill remote process {remote_pid} for case {case_id}: {result['stderr']}")
                except Exception as e:
                    self.logger.warning(f"Error killing remote process {remote_pid} for case {case_id}: {e}")
            
            # Release GPU locks through resource manager
            locked_gpus = case_status.get('locked_gpus', [])
            if locked_gpus and self.resource_manager:
                for gpu_id in locked_gpus:
                    try:
                        self.resource_manager.release_gpu_resource(gpu_id)
                        self.logger.info(f"Released GPU lock for GPU {gpu_id} (case {case_id})")
                    except Exception as e:
                        self.logger.warning(f"Error releasing GPU lock for GPU {gpu_id} (case {case_id}): {e}")
            
        except Exception as e:
            self.logger.error(f"Error cleaning up resources for stale case {case_id}: {e}")

    def perform_shutdown(self) -> None:
        """Perform shutdown cleanup procedures."""
        try:
            self.logger.info("Performing shutdown cleanup")
            
            # Clean up remote processes for currently processing cases
            self._cleanup_active_remote_processes()
            
            # Cleanup resources
            self._cleanup_resources()
            
            # Release application lock
            self._cleanup_lock_file()
            
            self.logger.info("Shutdown cleanup complete")
            
        except Exception as e:
            self.logger.error(f"Error during shutdown: {e}")

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

    def _cleanup_resources(self) -> None:
        """Clean up system resources."""
        try:
            # Additional resource cleanup can be added here
            # For now, the main cleanup is handled by the calling Application class
            pass
        except Exception as e:
            self.logger.error(f"Error during resource cleanup: {e}")