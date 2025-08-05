"""
Process Monitor Module - Monitors system processes and resource usage.

This module provides process monitoring functionality for the MOQUI automation system.
"""

import os
import time
import threading
import psutil
from typing import Dict, List, Any, Optional, Callable
from datetime import datetime


class ProcessInfo:
    """Information about a monitored process."""
    
    def __init__(self, pid: int, name: str = "", command: str = ""):
        """Initialize process information."""
        self.pid = pid
        self.name = name
        self.command = command
        self.start_time = datetime.now()
        self.last_check = datetime.now()
        self.cpu_percent = 0.0
        self.memory_mb = 0.0
        self.status = "unknown"
        self.is_alive = True


class ProcessMonitor:
    """Monitors system processes and resource usage."""
    
    def __init__(self, logger: Optional[Any] = None, config_manager: Optional[Any] = None, 
                 monitoring_interval: float = 5.0, 
                 process_cleanup_callback: Optional[Callable] = None):
        """Initialize process monitor."""
        self.logger = logger
        self.config_manager = config_manager
        self.monitoring_interval = monitoring_interval
        self.process_cleanup_callback = process_cleanup_callback
        
        self.monitored_processes: Dict[int, ProcessInfo] = {}
        self.running = False
        self.monitor_thread = None
        self.lock = threading.Lock()
    
    def start(self) -> None:
        """Start the process monitor."""
        if not self.running:
            self.running = True
            self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
            self.monitor_thread.start()
            if self.logger:
                self.logger.info("Process monitor started")
    
    def stop(self) -> None:
        """Stop the process monitor."""
        self.running = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=10)
            if self.logger:
                self.logger.info("Process monitor stopped")
    
    def add_process(self, pid: int, name: str = "", command: str = "") -> bool:
        """Add a process to monitor."""
        try:
            # Check if process exists
            if not psutil.pid_exists(pid):
                if self.logger:
                    self.logger.warning(f"Cannot monitor non-existent process: {pid}")
                return False
            
            with self.lock:
                process_info = ProcessInfo(pid, name, command)
                self.monitored_processes[pid] = process_info
            
            if self.logger:
                self.logger.info(f"Added process to monitor: PID {pid} ({name})")
            return True
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error adding process to monitor: {e}")
            return False
    
    def remove_process(self, pid: int) -> bool:
        """Remove a process from monitoring."""
        try:
            with self.lock:
                if pid in self.monitored_processes:
                    del self.monitored_processes[pid]
                    if self.logger:
                        self.logger.info(f"Removed process from monitor: PID {pid}")
                    return True
                return False
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error removing process from monitor: {e}")
            return False
    
    def is_process_alive(self, pid: int) -> bool:
        """Check if a monitored process is still alive."""
        try:
            return psutil.pid_exists(pid)
        except Exception:
            return False
    
    def get_process_info(self, pid: int) -> Optional[ProcessInfo]:
        """Get information about a monitored process."""
        with self.lock:
            return self.monitored_processes.get(pid)
    
    def get_all_processes(self) -> Dict[int, ProcessInfo]:
        """Get information about all monitored processes."""
        with self.lock:
            return self.monitored_processes.copy()
    
    def terminate_process(self, pid: int, force: bool = False) -> bool:
        """Terminate a monitored process."""
        try:
            if not psutil.pid_exists(pid):
                # Process already dead, remove from monitoring
                self.remove_process(pid)
                return True
            
            process = psutil.Process(pid)
            
            if force:
                process.kill()
                if self.logger:
                    self.logger.info(f"Force killed process: PID {pid}")
            else:
                process.terminate()
                if self.logger:
                    self.logger.info(f"Terminated process: PID {pid}")
            
            # Wait a bit and check if process is gone
            time.sleep(1)
            if not psutil.pid_exists(pid):
                self.remove_process(pid)
                return True
            
            return False
            
        except psutil.NoSuchProcess:
            # Process already gone
            self.remove_process(pid)
            return True
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error terminating process {pid}: {e}")
            return False
    
    def _monitor_loop(self) -> None:
        """Main monitoring loop."""
        while self.running:
            try:
                self._check_processes()
                time.sleep(self.monitoring_interval)
            except Exception as e:
                if self.logger:
                    self.logger.error(f"Error in monitor loop: {e}")
                time.sleep(self.monitoring_interval)
    
    def _check_processes(self) -> None:
        """Check status of all monitored processes."""
        dead_processes = []
        
        with self.lock:
            for pid, process_info in self.monitored_processes.items():
                try:
                    if psutil.pid_exists(pid):
                        # Update process information
                        proc = psutil.Process(pid)
                        process_info.cpu_percent = proc.cpu_percent()
                        process_info.memory_mb = proc.memory_info().rss / 1024 / 1024
                        process_info.status = proc.status()
                        process_info.is_alive = True
                        process_info.last_check = datetime.now()
                    else:
                        # Process is dead
                        process_info.is_alive = False
                        dead_processes.append(pid)
                        
                except psutil.NoSuchProcess:
                    # Process is dead
                    process_info.is_alive = False
                    dead_processes.append(pid)
                except Exception as e:
                    if self.logger:
                        self.logger.warning(f"Error checking process {pid}: {e}")
        
        # Clean up dead processes
        for pid in dead_processes:
            if self.logger:
                self.logger.info(f"Process {pid} has terminated")
            
            # Call cleanup callback if provided
            if self.process_cleanup_callback:
                try:
                    self.process_cleanup_callback(pid)
                except Exception as e:
                    if self.logger:
                        self.logger.error(f"Error in process cleanup callback for PID {pid}: {e}")
            
            # Remove from monitoring
            with self.lock:
                if pid in self.monitored_processes:
                    del self.monitored_processes[pid]
    
    def get_system_stats(self) -> Dict[str, Any]:
        """
        Get overall system statistics.

        .. deprecated:: 1.1.0
           This function is deprecated and will be removed in a future version.
           System statistics are now collected by the SystemMonitor.
        """
        if self.logger:
            self.logger.warning("The 'get_system_stats' method in ProcessMonitor is deprecated.")

        # Return empty dict to avoid breaking callers expecting a dictionary.
        return {}
    
    def cleanup_zombie_processes(self) -> int:
        """Clean up any zombie processes."""
        cleaned_count = 0
        
        try:
            # Get all zombie processes
            for proc in psutil.process_iter(['pid', 'status']):
                try:
                    if proc.info['status'] == psutil.STATUS_ZOMBIE:
                        pid = proc.info['pid']
                        if self.logger:
                            self.logger.warning(f"Found zombie process: PID {pid}")
                        
                        # Try to clean up
                        try:
                            os.waitpid(pid, os.WNOHANG)
                            cleaned_count += 1
                        except (OSError, ChildProcessError):
                            # Process might not be our child or already cleaned
                            pass
                            
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
                    
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error cleaning zombie processes: {e}")
        
        if cleaned_count > 0 and self.logger:
            self.logger.info(f"Cleaned up {cleaned_count} zombie processes")
        
        return cleaned_count