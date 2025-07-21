import logging
import shutil
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any
from dataclasses import dataclass, field
import uuid
import threading


@dataclass
class Job:
    job_id: str
    case_id: str
    beam_count: int
    status: str = "PENDING"
    gpu_allocation: List[int] = field(default_factory=list)
    created_time: datetime = field(default_factory=datetime.now)
    started_time: Optional[datetime] = None
    completed_time: Optional[datetime] = None
    error_message: Optional[str] = None
    retry_count: int = 0
    priority: int = 0


class JobScheduler:
    def __init__(self, gpu_manager, case_scanner, max_concurrent_jobs: int = 2, remote_executor=None, config: Optional[Dict[str, Any]] = None, directory_manager=None):
        self.gpu_manager = gpu_manager
        self.case_scanner = case_scanner
        self.remote_executor = remote_executor
        self.max_concurrent_jobs = max_concurrent_jobs
        self.config = config or {}
        self.directory_manager = directory_manager
        
        self.job_queue: List[Dict[str, Any]] = []
        self.active_jobs: Dict[str, Dict[str, Any]] = {}
        self.completed_jobs: List[Dict[str, Any]] = []
        self.lock = threading.Lock()
        
        # Get workspace path from config (fallback if no directory_manager)
        self.workspace_path = self.config.get("paths", {}).get("remote_workspace", "MOQUI_SMC/tps")
        
        # Disk space thresholds (in GB)
        self.min_remote_space_gb = 10
        self.min_local_space_gb = 5

    def check_local_disk_space(self, required_gb: float = None) -> Dict[str, Any]:
        """Check local disk space availability."""
        try:
            # Check current working directory disk space
            total, used, free = shutil.disk_usage(".")
            
            # Convert to GB
            total_gb = total / (1024**3)
            used_gb = used / (1024**3)
            free_gb = free / (1024**3)
            
            required_gb = required_gb or self.min_local_space_gb
            has_sufficient_space = free_gb >= required_gb
            
            return {
                "total_gb": round(total_gb, 2),
                "used_gb": round(used_gb, 2),
                "free_gb": round(free_gb, 2),
                "required_gb": required_gb,
                "sufficient": has_sufficient_space,
                "usage_percent": round((used_gb / total_gb) * 100, 2)
            }
            
        except Exception as e:
            logging.error(f"Failed to check local disk space: {e}")
            return {
                "total_gb": 0,
                "used_gb": 0,
                "free_gb": 0,
                "required_gb": required_gb or self.min_local_space_gb,
                "sufficient": False,
                "usage_percent": 100,
                "error": str(e)
            }

    def check_remote_disk_space(self, required_gb: float = None) -> Dict[str, Any]:
        """Check remote server disk space availability."""
        try:
            if not self.remote_executor:
                return {
                    "total_gb": 0,
                    "used_gb": 0,
                    "free_gb": 0,
                    "required_gb": required_gb or self.min_remote_space_gb,
                    "sufficient": False,
                    "usage_percent": 100,
                    "error": "No remote executor available"
                }
            
            # Use df command to check disk space
            result = self.remote_executor.execute_command("df -BG . | tail -1")
            
            if result["exit_code"] != 0:
                raise Exception(f"df command failed: {result['stderr']}")
            
            # Parse df output: Filesystem 1G-blocks Used Available Use% Mounted on
            fields = result["stdout"].split()
            if len(fields) < 4:
                raise Exception("Unexpected df output format")
            
            # Extract values (remove 'G' suffix)
            total_gb = float(fields[1].rstrip('G'))
            used_gb = float(fields[2].rstrip('G'))
            available_gb = float(fields[3].rstrip('G'))
            
            required_gb = required_gb or self.min_remote_space_gb
            has_sufficient_space = available_gb >= required_gb
            
            return {
                "total_gb": total_gb,
                "used_gb": used_gb,
                "free_gb": available_gb,
                "required_gb": required_gb,
                "sufficient": has_sufficient_space,
                "usage_percent": round((used_gb / total_gb) * 100, 2)
            }
            
        except Exception as e:
            logging.error(f"Failed to check remote disk space: {e}")
            return {
                "total_gb": 0,
                "used_gb": 0,
                "free_gb": 0,
                "required_gb": required_gb or self.min_remote_space_gb,
                "sufficient": False,
                "usage_percent": 100,
                "error": str(e)
            }

    def estimate_case_disk_usage(self, case_id: str) -> float:
        """Estimate disk space usage for a case in GB."""
        try:
            # This is a simplified estimation
            # In practice, you might analyze the case data to get a better estimate
            base_usage = 0.5  # Base processing overhead
            beam_factor = 0.2  # Additional space per beam
            
            beam_count = self.analyze_case_beam_count(case_id)
            estimated_gb = base_usage + (beam_count * beam_factor)
            
            return estimated_gb
            
        except Exception as e:
            logging.warning(f"Failed to estimate disk usage for case {case_id}: {e}")
            return 2.0  # Conservative default estimate

    def analyze_case_beam_count(self, case_id: str) -> int:
        """Analyze case directory to determine number of beams by counting subdirectories."""
        try:
            # Use directory_manager if available, otherwise fall back to workspace_path
            if self.directory_manager:
                case_path = self.directory_manager.get_case_local_path(case_id)
            else:
                case_path = Path(self.workspace_path) / case_id
            
            if not case_path.exists():
                logging.warning(f"Case directory not found: {case_id}")
                return 0
            
            # Count subdirectories within the case directory
            subdirectories = [item for item in case_path.iterdir() if item.is_dir()]
            beam_count = len(subdirectories)
            
            logging.info(f"Found {beam_count} beams (subdirectories) for case {case_id}")
            
            return beam_count
            
        except Exception as e:
            logging.error(f"Error analyzing case {case_id}: {e}")
            return 0

    def can_schedule_case(self, case_id: str, beam_count: int) -> bool:
        """Check if case can be scheduled (added to queue). Includes disk space checks."""
        try:
            # Check if we have reached max concurrent jobs
            if len(self.active_jobs) >= self.max_concurrent_jobs:
                logging.info(f"Max concurrent jobs reached ({self.max_concurrent_jobs})")
                return False
            
            # Check remote disk space
            estimated_usage = self.estimate_case_disk_usage(case_id)
            remote_space = self.check_remote_disk_space(estimated_usage)
            
            if not remote_space["sufficient"]:
                logging.warning(f"Insufficient remote disk space for case {case_id}: "
                              f"need {estimated_usage}GB, have {remote_space['free_gb']}GB")
                return False
            
            # Note: We don't check GPU availability here anymore
            # GPUs will be allocated when the job is actually started
            # This allows jobs to be queued even when GPUs are currently busy
            
            return True
            
        except Exception as e:
            logging.error(f"Error checking schedulability for case {case_id}: {e}")
            return False

    def optimize_gpu_allocation(self, beam_count: int) -> List[int]:
        """Optimize GPU allocation for beam processing."""
        available_gpus = self.gpu_manager.get_available_gpus()
        
        if len(available_gpus) < beam_count:
            logging.warning(f"Insufficient GPUs available: need {beam_count}, have {len(available_gpus)}")
            return available_gpus
        
        # Prefer consecutive GPU IDs for better performance
        allocated_gpus = []
        
        # Try to find consecutive GPUs
        for i in range(len(available_gpus) - beam_count + 1):
            consecutive_gpus = available_gpus[i:i + beam_count]
            
            # Check if GPUs are consecutive
            if all(consecutive_gpus[j] == consecutive_gpus[0] + j for j in range(len(consecutive_gpus))):
                allocated_gpus = consecutive_gpus
                break
        
        # If no consecutive GPUs found, use first available
        if not allocated_gpus:
            allocated_gpus = available_gpus[:beam_count]
        
        return allocated_gpus

    def create_job(self, case_id: str, beam_count: int, priority: int = 0) -> Optional[Dict[str, Any]]:
        """Create a new job for case processing."""
        try:
            job = {
                "job_id": str(uuid.uuid4()),
                "case_id": case_id,
                "beam_count": beam_count,
                "status": "PENDING",
                "gpu_allocation": [],  # GPUs will be allocated when job starts
                "created_time": datetime.now(),
                "started_time": None,
                "completed_time": None,
                "error_message": None,
                "retry_count": 0,
                "priority": priority
            }
            
            logging.info(f"Created job for case {case_id} with {beam_count} beams (GPUs will be allocated at execution)")
            return job
            
        except Exception as e:
            logging.error(f"Error creating job for case {case_id}: {e}")
            return None

    def schedule_case(self, case_id: str, priority: int = 0) -> bool:
        """Schedule a case for processing."""
        with self.lock:
            try:
                # Check if case is already scheduled or processing
                if case_id in self.active_jobs:
                    logging.warning(f"Case {case_id} is already being processed")
                    return False
                
                # Check if case is already in queue
                if any(job["case_id"] == case_id for job in self.job_queue):
                    logging.warning(f"Case {case_id} is already in queue")
                    return False
                
                # Analyze case
                beam_count = self.analyze_case_beam_count(case_id)
                
                if beam_count == 0:
                    logging.error(f"No beams found for case {case_id}")
                    return False
                
                # Check if case can be scheduled
                if not self.can_schedule_case(case_id, beam_count):
                    logging.info(f"Case {case_id} cannot be scheduled now - will retry later")
                    return False
                
                # Create job
                job = self.create_job(case_id, beam_count, priority)
                
                if not job:
                    return False
                
                # Add to queue
                self.job_queue.append(job)
                
                # Sort queue by priority (higher priority first)
                self.job_queue.sort(key=lambda x: x["priority"], reverse=True)
                
                logging.info(f"Case {case_id} scheduled successfully")
                return True
                
            except Exception as e:
                logging.error(f"Error scheduling case {case_id}: {e}")
                return False

    def get_next_job(self) -> Optional[Dict[str, Any]]:
        """Get the next job from the queue and allocate GPUs just before execution."""
        with self.lock:
            try:
                if not self.job_queue:
                    return None
                
                # Get highest priority pending job
                for job in self.job_queue:
                    if job["status"] == "PENDING":
                        # Try to allocate GPUs for this job
                        beam_count = job["beam_count"]
                        gpu_allocation = self.gpu_manager.allocate_gpus(beam_count)
                        
                        if not gpu_allocation:
                            logging.warning(f"Cannot allocate {beam_count} GPUs for case {job['case_id']} - keeping in queue")
                            continue  # Try next job in queue
                        
                        # Move to active jobs with GPU allocation
                        self.job_queue.remove(job)
                        job["status"] = "RUNNING"
                        job["started_time"] = datetime.now()
                        job["gpu_allocation"] = gpu_allocation
                        self.active_jobs[job["case_id"]] = job
                        
                        logging.info(f"Started job for case {job['case_id']} with GPUs {gpu_allocation}")
                        return job
                
                return None
                
            except Exception as e:
                logging.error(f"Error getting next job: {e}")
                # If allocation failed, make sure to release any partially allocated GPUs
                if 'gpu_allocation' in locals() and gpu_allocation:
                    self.gpu_manager.release_gpus(gpu_allocation)
                return None

    def complete_job(self, case_id: str, success: bool = True, error_message: str = None) -> bool:
        """Mark job as completed and release resources."""
        with self.lock:
            try:
                if case_id not in self.active_jobs:
                    logging.warning(f"Job for case {case_id} not found in active jobs")
                    return False
                
                job = self.active_jobs[case_id]
                job["completed_time"] = datetime.now()
                
                if success:
                    job["status"] = "COMPLETED"
                    logging.info(f"Job for case {case_id} completed successfully")
                else:
                    job["status"] = "FAILED"
                    job["error_message"] = error_message
                    logging.error(f"Job for case {case_id} failed: {error_message}")
                
                # Release GPUs
                if job["gpu_allocation"]:
                    self.gpu_manager.release_gpus(job["gpu_allocation"])
                
                # Move to completed jobs
                self.completed_jobs.append(job)
                del self.active_jobs[case_id]
                
                # Update case status
                status = "COMPLETED" if success else "FAILED"
                self.case_scanner.update_case_status(case_id, status)
                
                return True
                
            except Exception as e:
                logging.error(f"Error completing job for case {case_id}: {e}")
                return False

    def cancel_job(self, case_id: str) -> bool:
        """Cancel a job and release resources."""
        with self.lock:
            try:
                # Check if job is in queue
                for job in self.job_queue:
                    if job["case_id"] == case_id:
                        self.job_queue.remove(job)
                        
                        # Release GPUs if allocated (though they shouldn't be for queued jobs now)
                        if job.get("gpu_allocation"):
                            self.gpu_manager.release_gpus(job["gpu_allocation"])
                        
                        logging.info(f"Canceled queued job for case {case_id}")
                        return True
                
                # Check if job is active
                if case_id in self.active_jobs:
                    job = self.active_jobs[case_id]
                    job["status"] = "CANCELLED"
                    job["completed_time"] = datetime.now()
                    
                    # Release GPUs (should always be allocated for active jobs)
                    if job.get("gpu_allocation"):
                        self.gpu_manager.release_gpus(job["gpu_allocation"])
                    
                    # Move to completed jobs
                    self.completed_jobs.append(job)
                    del self.active_jobs[case_id]
                    
                    logging.info(f"Canceled active job for case {case_id}")
                    return True
                
                logging.warning(f"Job for case {case_id} not found")
                return False
                
            except Exception as e:
                logging.error(f"Error canceling job for case {case_id}: {e}")
                return False

    def get_job_status(self, case_id: str) -> Optional[str]:
        """Get status of a specific job."""
        with self.lock:
            # Check active jobs
            if case_id in self.active_jobs:
                return self.active_jobs[case_id]["status"]
            
            # Check queue
            for job in self.job_queue:
                if job["case_id"] == case_id:
                    return job["status"]
            
            # Check completed jobs
            for job in self.completed_jobs:
                if job["case_id"] == case_id:
                    return job["status"]
            
            return None

    def get_queue_status(self) -> Dict[str, Any]:
        """Get current queue status."""
        with self.lock:
            pending_jobs = len([job for job in self.job_queue if job["status"] == "PENDING"])
            running_jobs = len(self.active_jobs)
            completed_jobs = len([job for job in self.completed_jobs if job["status"] == "COMPLETED"])
            failed_jobs = len([job for job in self.completed_jobs if job["status"] == "FAILED"])
            
            return {
                "pending_jobs": pending_jobs,
                "running_jobs": running_jobs,
                "completed_jobs": completed_jobs,
                "failed_jobs": failed_jobs,
                "total_jobs": pending_jobs + running_jobs + completed_jobs + failed_jobs
            }

    def get_resource_utilization(self) -> Dict[str, Any]:
        """Get current resource utilization."""
        with self.lock:
            total_gpus = getattr(self.gpu_manager, 'total_gpus', 8)
            available_gpus = len(self.gpu_manager.get_available_gpus())
            
            # Calculate allocated GPUs
            allocated_gpus = 0
            for job in self.active_jobs.values():
                allocated_gpus += len(job["gpu_allocation"])
            
            utilization_percent = (allocated_gpus / total_gpus) * 100 if total_gpus > 0 else 0
            
            return {
                "total_gpus": total_gpus,
                "available_gpus": available_gpus,
                "allocated_gpus": allocated_gpus,
                "utilization_percent": utilization_percent
            }

    def cleanup_completed_jobs(self, max_age_hours: int = 24) -> int:
        """Clean up old completed jobs."""
        with self.lock:
            cutoff_time = datetime.now() - timedelta(hours=max_age_hours)
            
            initial_count = len(self.completed_jobs)
            
            self.completed_jobs = [
                job for job in self.completed_jobs
                if job.get("completed_time", datetime.now()) > cutoff_time
            ]
            
            cleaned_count = initial_count - len(self.completed_jobs)
            
            if cleaned_count > 0:
                logging.info(f"Cleaned up {cleaned_count} old completed jobs")
            
            return cleaned_count

    def retry_failed_jobs(self, max_retries: int = 3) -> int:
        """Retry failed jobs that haven't exceeded max retries."""
        with self.lock:
            retry_count = 0
            
            failed_jobs = [job for job in self.completed_jobs if job["status"] == "FAILED"]
            
            for job in failed_jobs:
                if job["retry_count"] < max_retries:
                    case_id = job["case_id"]
                    
                    # Remove from completed jobs
                    self.completed_jobs.remove(job)
                    
                    # Reschedule
                    if self.schedule_case(case_id, job["priority"]):
                        retry_count += 1
                        logging.info(f"Retrying failed job for case {case_id}")
            
            return retry_count

    def get_job_history(self, case_id: str) -> List[Dict[str, Any]]:
        """Get job history for a specific case."""
        with self.lock:
            history = []
            
            # Check all job lists
            all_jobs = list(self.job_queue) + list(self.active_jobs.values()) + self.completed_jobs
            
            for job in all_jobs:
                if job["case_id"] == case_id:
                    history.append({
                        "job_id": job["job_id"],
                        "status": job["status"],
                        "created_time": job["created_time"],
                        "started_time": job.get("started_time"),
                        "completed_time": job.get("completed_time"),
                        "gpu_allocation": job["gpu_allocation"],
                        "retry_count": job["retry_count"],
                        "error_message": job.get("error_message")
                    })
            
            return sorted(history, key=lambda x: x["created_time"])

    def get_performance_metrics(self) -> Dict[str, Any]:
        """Get performance metrics for the scheduler."""
        with self.lock:
            total_jobs = len(self.completed_jobs)
            
            if total_jobs == 0:
                return {
                    "total_jobs": 0,
                    "success_rate": 0.0,
                    "average_processing_time": 0.0,
                    "gpu_efficiency": 0.0
                }
            
            successful_jobs = [job for job in self.completed_jobs if job["status"] == "COMPLETED"]
            success_rate = (len(successful_jobs) / total_jobs) * 100
            
            # Calculate average processing time
            processing_times = []
            for job in successful_jobs:
                if job.get("started_time") and job.get("completed_time"):
                    processing_time = (job["completed_time"] - job["started_time"]).total_seconds()
                    processing_times.append(processing_time)
            
            average_processing_time = sum(processing_times) / len(processing_times) if processing_times else 0.0
            
            # Calculate GPU efficiency (placeholder)
            gpu_efficiency = 85.0  # This would be calculated based on actual GPU usage
            
            return {
                "total_jobs": total_jobs,
                "success_rate": success_rate,
                "average_processing_time": average_processing_time,
                "gpu_efficiency": gpu_efficiency
            }