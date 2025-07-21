import logging
import time
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any
import threading
from dataclasses import dataclass, field


@dataclass
class ProcessInfo:
    case_id: str
    beam_id: int
    process_type: str
    command: str
    status: str = "RUNNING"
    start_time: datetime = field(default_factory=datetime.now)
    end_time: Optional[datetime] = None
    timeout: int = 3600  # Default 1 hour timeout
    pid: Optional[int] = None
    error_message: Optional[str] = None
    retry_count: int = 0


class ProcessMonitor:
    def __init__(self, remote_executor, monitoring_interval: int = 10):
        self.remote_executor = remote_executor
        self.monitoring_interval = monitoring_interval
        self.tracked_processes: List[Dict[str, Any]] = []
        self.monitoring_active = False
        self.monitor_thread: Optional[threading.Thread] = None
        self.lock = threading.Lock()

    def start_monitoring_process(self, process_info: Dict[str, Any]) -> bool:
        """Start monitoring a new process."""
        try:
            with self.lock:
                tracked_process = {
                    "case_id": process_info["case_id"],
                    "beam_id": process_info["beam_id"],
                    "process_type": process_info["process_type"],
                    "command": process_info["command"],
                    "status": "RUNNING",
                    "start_time": datetime.now(),
                    "end_time": None,
                    "timeout": process_info.get("timeout", 3600),
                    "pid": process_info.get("pid"),
                    "error_message": None,
                    "retry_count": 0
                }
                
                self.tracked_processes.append(tracked_process)
                
                logging.info(f"Started monitoring process: {process_info['case_id']}-{process_info['beam_id']}")
                return True
                
        except Exception as e:
            logging.error(f"Error starting process monitoring: {e}")
            return False

    def stop_monitoring_process(self, case_id: str, beam_id: int) -> bool:
        """Stop monitoring a specific process."""
        try:
            with self.lock:
                for process in self.tracked_processes:
                    if process["case_id"] == case_id and process["beam_id"] == beam_id:
                        if process["status"] == "RUNNING":
                            process["status"] = "COMPLETED"
                            process["end_time"] = datetime.now()
                            
                            logging.info(f"Stopped monitoring process: {case_id}-{beam_id}")
                            return True
                
                return False
                
        except Exception as e:
            logging.error(f"Error stopping process monitoring: {e}")
            return False

    def check_process_status(self) -> List[Dict[str, Any]]:
        """Check status of all tracked processes."""
        status_updates = []
        
        try:
            with self.lock:
                running_processes = [p for p in self.tracked_processes if p["status"] == "RUNNING"]
                
                if not running_processes:
                    return status_updates
                
                # Get all running processes from remote system
                all_remote_processes = self.remote_executor.check_process_status(".*")
                
                for process in running_processes:
                    # Check if process is still running
                    process_found = False
                    
                    for remote_process in all_remote_processes:
                        if process["command"] in remote_process["command"]:
                            process_found = True
                            process["pid"] = int(remote_process["pid"])
                            break
                    
                    if not process_found:
                        # Process has completed or failed
                        process["status"] = "COMPLETED"
                        process["end_time"] = datetime.now()
                        
                        status_updates.append({
                            "case_id": process["case_id"],
                            "beam_id": process["beam_id"],
                            "status": "COMPLETED",
                            "runtime": self._calculate_runtime(process)
                        })
                        
                        logging.info(f"Process completed: {process['case_id']}-{process['beam_id']}")
                
                return status_updates
                
        except Exception as e:
            logging.error(f"Error checking process status: {e}")
            return status_updates

    def check_timeouts(self) -> List[Dict[str, Any]]:
        """Check for processes that have exceeded their timeout."""
        timed_out_processes = []
        
        try:
            with self.lock:
                current_time = datetime.now()
                
                for process in self.tracked_processes:
                    if process["status"] == "RUNNING":
                        runtime = (current_time - process["start_time"]).total_seconds()
                        
                        if runtime > process["timeout"]:
                            process["status"] = "TIMEOUT"
                            process["end_time"] = current_time
                            process["error_message"] = f"Process timed out after {process['timeout']} seconds"
                            
                            timed_out_processes.append({
                                "case_id": process["case_id"],
                                "beam_id": process["beam_id"],
                                "timeout_seconds": process["timeout"],
                                "actual_runtime": runtime
                            })
                            
                            logging.warning(f"Process timed out: {process['case_id']}-{process['beam_id']}")
                
                return timed_out_processes
                
        except Exception as e:
            logging.error(f"Error checking timeouts: {e}")
            return timed_out_processes

    def wait_for_beam_completion(self, case_id: str, timeout: int = 3600) -> bool:
        """Wait for all beams of a case to complete."""
        try:
            start_time = datetime.now()
            
            while True:
                # Check if timeout exceeded
                if (datetime.now() - start_time).total_seconds() > timeout:
                    logging.error(f"Timeout waiting for beam completion: {case_id}")
                    return False
                
                # Get all processes for this case
                case_processes = self.get_case_processes(case_id)
                
                if not case_processes:
                    # No processes found for this case
                    return True
                
                # Check if all processes are completed
                all_completed = True
                for process in case_processes:
                    if process["status"] == "RUNNING":
                        all_completed = False
                        break
                
                if all_completed:
                    logging.info(f"All beams completed for case: {case_id}")
                    return True
                
                # Update process status
                self.check_process_status()
                
                # Wait before next check
                time.sleep(self.monitoring_interval)
                
        except Exception as e:
            logging.error(f"Error waiting for beam completion: {e}")
            return False

    def kill_process(self, case_id: str, beam_id: int) -> bool:
        """Kill a specific process."""
        try:
            with self.lock:
                for process in self.tracked_processes:
                    if process["case_id"] == case_id and process["beam_id"] == beam_id:
                        if process["status"] == "RUNNING":
                            # Find process PID
                            pid = self._find_process_pid(process)
                            
                            if pid:
                                # Kill the process
                                if self.remote_executor.kill_process(pid):
                                    process["status"] = "KILLED"
                                    process["end_time"] = datetime.now()
                                    
                                    logging.info(f"Killed process: {case_id}-{beam_id}")
                                    return True
                                else:
                                    logging.error(f"Failed to kill process: {case_id}-{beam_id}")
                                    return False
                            else:
                                logging.error(f"Process PID not found: {case_id}-{beam_id}")
                                return False
                        else:
                            logging.warning(f"Process not running: {case_id}-{beam_id}")
                            return False
                
                logging.warning(f"Process not found: {case_id}-{beam_id}")
                return False
                
        except Exception as e:
            logging.error(f"Error killing process: {e}")
            return False

    def _find_process_pid(self, process: Dict[str, Any]) -> Optional[int]:
        """Find PID for a tracked process."""
        try:
            if process.get("pid"):
                return process["pid"]
            
            # Search for process by command
            all_processes = self.remote_executor.check_process_status(".*")
            
            for remote_process in all_processes:
                if process["command"] in remote_process["command"]:
                    return int(remote_process["pid"])
            
            return None
            
        except Exception as e:
            logging.error(f"Error finding process PID: {e}")
            return None

    def get_process_info(self, case_id: str, beam_id: int) -> Optional[Dict[str, Any]]:
        """Get detailed information about a specific process."""
        try:
            with self.lock:
                for process in self.tracked_processes:
                    if process["case_id"] == case_id and process["beam_id"] == beam_id:
                        # Return a deep copy to avoid reference issues
                        import copy
                        return copy.deepcopy(process)
                
                return None
                
        except Exception as e:
            logging.error(f"Error getting process info: {e}")
            return None

    def get_case_processes(self, case_id: str) -> List[Dict[str, Any]]:
        """Get all processes for a specific case."""
        try:
            with self.lock:
                case_processes = []
                
                for process in self.tracked_processes:
                    if process["case_id"] == case_id:
                        case_processes.append(process.copy())
                
                return case_processes
                
        except Exception as e:
            logging.error(f"Error getting case processes: {e}")
            return []

    def get_case_status(self, case_id: str) -> Dict[str, Any]:
        """Get overall status for a case."""
        try:
            case_processes = self.get_case_processes(case_id)
            
            if not case_processes:
                return {
                    "case_id": case_id,
                    "total_processes": 0,
                    "running_processes": 0,
                    "completed_processes": 0,
                    "failed_processes": 0,
                    "timeout_processes": 0,
                    "overall_status": "NOT_FOUND"
                }
            
            status_counts = {
                "RUNNING": 0,
                "COMPLETED": 0,
                "FAILED": 0,
                "TIMEOUT": 0,
                "KILLED": 0
            }
            
            for process in case_processes:
                status = process["status"]
                if status in status_counts:
                    status_counts[status] += 1
            
            # Determine overall status
            if status_counts["RUNNING"] > 0:
                overall_status = "RUNNING"
            elif status_counts["FAILED"] > 0 or status_counts["TIMEOUT"] > 0:
                overall_status = "FAILED"
            elif status_counts["COMPLETED"] == len(case_processes):
                overall_status = "COMPLETED"
            else:
                overall_status = "PARTIAL"
            
            return {
                "case_id": case_id,
                "total_processes": len(case_processes),
                "running_processes": status_counts["RUNNING"],
                "completed_processes": status_counts["COMPLETED"],
                "failed_processes": status_counts["FAILED"],
                "timeout_processes": status_counts["TIMEOUT"],
                "killed_processes": status_counts["KILLED"],
                "overall_status": overall_status
            }
            
        except Exception as e:
            logging.error(f"Error getting case status: {e}")
            return {"case_id": case_id, "overall_status": "ERROR"}

    def cleanup_completed_processes(self, max_age_hours: int = 24) -> int:
        """Clean up old completed processes."""
        try:
            with self.lock:
                cutoff_time = datetime.now() - timedelta(hours=max_age_hours)
                initial_count = len(self.tracked_processes)
                
                self.tracked_processes = [
                    process for process in self.tracked_processes
                    if not (process["status"] in ["COMPLETED", "FAILED", "TIMEOUT", "KILLED"] and
                            process.get("end_time") and
                            process["end_time"] < cutoff_time)
                ]
                
                cleaned_count = initial_count - len(self.tracked_processes)
                
                if cleaned_count > 0:
                    logging.info(f"Cleaned up {cleaned_count} old processes")
                
                return cleaned_count
                
        except Exception as e:
            logging.error(f"Error cleaning up processes: {e}")
            return 0

    def get_monitoring_stats(self) -> Dict[str, Any]:
        """Get monitoring statistics."""
        try:
            with self.lock:
                stats = {
                    "total_processes": len(self.tracked_processes),
                    "running_processes": 0,
                    "completed_processes": 0,
                    "failed_processes": 0,
                    "timeout_processes": 0,
                    "killed_processes": 0
                }
                
                for process in self.tracked_processes:
                    status = process["status"]
                    if status == "RUNNING":
                        stats["running_processes"] += 1
                    elif status == "COMPLETED":
                        stats["completed_processes"] += 1
                    elif status == "FAILED":
                        stats["failed_processes"] += 1
                    elif status == "TIMEOUT":
                        stats["timeout_processes"] += 1
                    elif status == "KILLED":
                        stats["killed_processes"] += 1
                
                return stats
                
        except Exception as e:
            logging.error(f"Error getting monitoring stats: {e}")
            return {"total_processes": 0}

    def monitor_log_file(self, case_id: str, log_path: str, lines: int = 10) -> str:
        """Monitor log file for a case."""
        try:
            return self.remote_executor.monitor_log_file(log_path, lines)
        except Exception as e:
            logging.error(f"Error monitoring log file: {e}")
            return f"Error reading log: {str(e)}"

    def set_monitoring_interval(self, interval: int) -> None:
        """Set the monitoring interval in seconds."""
        self.monitoring_interval = interval
        logging.info(f"Monitoring interval set to {interval} seconds")

    def get_process_runtime(self, case_id: str, beam_id: int) -> float:
        """Get runtime of a specific process in seconds."""
        try:
            process_info = self.get_process_info(case_id, beam_id)
            
            if not process_info:
                return 0.0
            
            return self._calculate_runtime(process_info)
            
        except Exception as e:
            logging.error(f"Error getting process runtime: {e}")
            return 0.0

    def _calculate_runtime(self, process: Dict[str, Any]) -> float:
        """Calculate runtime of a process."""
        try:
            start_time = process.get("start_time")
            if not start_time:
                return 0.0
            
            end_time = process.get("end_time")
            if not end_time:
                end_time = datetime.now()
            
            return (end_time - start_time).total_seconds()
            
        except Exception as e:
            logging.error(f"Error calculating runtime: {e}")
            return 0.0

    def start_background_monitoring(self) -> bool:
        """Start background monitoring thread."""
        try:
            if self.monitoring_active:
                logging.warning("Background monitoring already active")
                return False
            
            self.monitoring_active = True
            self.monitor_thread = threading.Thread(target=self._background_monitor_loop)
            self.monitor_thread.daemon = True
            self.monitor_thread.start()
            
            logging.info("Background monitoring started")
            return True
            
        except Exception as e:
            logging.error(f"Error starting background monitoring: {e}")
            return False

    def stop_background_monitoring(self) -> bool:
        """Stop background monitoring thread."""
        try:
            if not self.monitoring_active:
                logging.warning("Background monitoring not active")
                return False
            
            self.monitoring_active = False
            
            if self.monitor_thread and self.monitor_thread.is_alive():
                self.monitor_thread.join(timeout=10)
            
            logging.info("Background monitoring stopped")
            return True
            
        except Exception as e:
            logging.error(f"Error stopping background monitoring: {e}")
            return False

    def _background_monitor_loop(self) -> None:
        """Background monitoring loop."""
        try:
            while self.monitoring_active:
                # Check process status
                self.check_process_status()
                
                # Check timeouts
                self.check_timeouts()
                
                # Sleep for monitoring interval
                time.sleep(self.monitoring_interval)
                
        except Exception as e:
            logging.error(f"Error in background monitoring loop: {e}")
        finally:
            self.monitoring_active = False

    def get_active_cases(self) -> List[str]:
        """Get list of cases with active processes."""
        try:
            with self.lock:
                active_cases = set()
                
                for process in self.tracked_processes:
                    if process["status"] == "RUNNING":
                        active_cases.add(process["case_id"])
                
                return list(active_cases)
                
        except Exception as e:
            logging.error(f"Error getting active cases: {e}")
            return []

    def __enter__(self):
        """Context manager entry."""
        self.start_background_monitoring()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.stop_background_monitoring()

    def stop(self):
        """Alias for stop_background_monitoring for cleanup."""
        logging.info("ProcessMonitor stop method called, stopping background monitoring.")
        self.stop_background_monitoring()