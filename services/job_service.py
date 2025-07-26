"""
JobService - Service for managing job lifecycle.

Merges functionality from job_scheduler.py to provide unified job management.
"""

import uuid
import threading
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any, TYPE_CHECKING

from models.job import Job
from services.resource_manager import ResourceManager
from services.case_service import CaseService
from core.logging import Logger

if TYPE_CHECKING:
    from executors.remote import RemoteExecutor


class JobService:
    """Manages the lifecycle of Job models (creation, scheduling, execution)."""
    
    def __init__(self,
                 resource_manager: ResourceManager,
                 case_service: CaseService,
                 max_concurrent_jobs: int = 2,
                 remote_executor: Optional['RemoteExecutor'] = None,
                 status_display=None,
                 logger: Optional[Logger] = None):
        
        self.resource_manager = resource_manager
        self.case_service = case_service
        self.remote_executor = remote_executor
        self.max_concurrent_jobs = max_concurrent_jobs
        self.status_display = status_display
        self.logger = logger
        
        self.job_queue: List[Job] = []
        self.active_jobs: Dict[str, Job] = {}
        self.completed_jobs: List[Job] = []
        self.lock = threading.Lock()
        
        # Disk space thresholds (in GB)
        self.min_remote_space_gb = 10
        self.min_local_space_gb = 5
    
    def estimate_case_disk_usage(self, case_id: str) -> float:
        """Estimate disk space usage for a case in GB."""
        try:
            base_usage = 0.5  # Base processing overhead
            beam_factor = 0.2  # Additional space per beam
            
            beam_count = self.case_service.analyze_case_beam_count(case_id)
            estimated_gb = base_usage + (beam_count * beam_factor)
            
            return estimated_gb
            
        except Exception as e:
            if self.logger:
                self.logger.warning(f"Failed to estimate disk usage for case {case_id}: {e}")
            return 2.0  # Conservative default estimate
    
    def can_schedule_case(self, case_id: str, beam_count: int) -> bool:
        """Check if case can be scheduled (added to queue)."""
        try:
            # Check if we have reached max concurrent jobs
            if len(self.active_jobs) >= self.max_concurrent_jobs:
                if self.logger:
                    self.logger.info(f"Max concurrent jobs reached ({self.max_concurrent_jobs})")
                return False
            
            # Check remote disk space
            estimated_usage = self.estimate_case_disk_usage(case_id)
            if not self.resource_manager.check_disk_space('remote', estimated_usage):
                remote_usage = self.resource_manager.get_disk_usage('remote')
                if self.logger:
                    self.logger.warning(f"Insufficient remote disk space for case {case_id}: "
                                      f"need {estimated_usage}GB, have {remote_usage.get('free_gb', 0)}GB")
                return False
            
            # Check local disk space
            if not self.resource_manager.check_disk_space('local', self.min_local_space_gb):
                if self.logger:
                    self.logger.warning(f"Insufficient local disk space for case {case_id}")
                return False
            
            return True
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error checking schedulability for case {case_id}: {e}")
            return False
    
    def create_job(self, case_id: str, beam_count: int, priority: int = 0, start_task: str = None) -> Optional[Job]:
        """Create a new job for case processing."""
        try:
            job = Job(
                job_id=str(uuid.uuid4()),
                case_id=case_id,
                beam_count=beam_count,
                priority=priority,
                start_task=start_task or "setup"
            )
            
            if self.logger:
                self.logger.info(f"Created job for case {case_id} with {beam_count} beams")
            
            return job
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error creating job for case {case_id}: {e}")
            return None
    
    def schedule_case(self, case_id: str, priority: int = 0, start_task: str = None) -> bool:
        """Schedule a case for processing."""
        with self.lock:
            try:
                # Check if case is already scheduled or processing
                if case_id in self.active_jobs:
                    if self.logger:
                        self.logger.warning(f"Case {case_id} is already being processed")
                    return False
                
                # Check if case is already in queue
                if any(job.case_id == case_id for job in self.job_queue):
                    if self.logger:
                        self.logger.warning(f"Case {case_id} is already in queue")
                    return False
                
                # Determine starting task if not provided
                if start_task is None:
                    resumption_task = self.case_service.get_case_resumption_task(case_id)
                    if resumption_task:
                        start_task = resumption_task
                        if self.logger:
                            self.logger.info(f"Case {case_id} will resume from task: {start_task}")
                    else:
                        start_task = "setup"
                
                # Analyze case
                beam_count = self.case_service.analyze_case_beam_count(case_id)
                if beam_count == 0:
                    if self.logger:
                        self.logger.error(f"No beams found for case {case_id}")
                    return False
                
                # Check if case can be scheduled
                if not self.can_schedule_case(case_id, beam_count):
                    if self.logger:
                        self.logger.info(f"Case {case_id} cannot be scheduled now - will retry later")
                    return False
                
                # Create job
                job = self.create_job(case_id, beam_count, priority, start_task)
                if not job:
                    return False
                
                # Add to queue
                self.job_queue.append(job)
                
                # Sort queue by priority (higher priority first)
                self.job_queue.sort(key=lambda x: x.priority, reverse=True)
                
                if self.logger:
                    self.logger.info(f"Case {case_id} scheduled successfully (start_task: {start_task})")
                return True
                
            except Exception as e:
                if self.logger:
                    self.logger.error(f"Error scheduling case {case_id}: {e}")
                return False
    
    def get_next_job(self) -> Optional[Job]:
        """Get the next job from the queue and allocate resources."""
        with self.lock:
            try:
                if not self.job_queue:
                    return None
                
                # Get highest priority pending job
                for job in self.job_queue:
                    if job.status == "PENDING":
                        # Try to allocate GPUs for this job
                        gpu_allocation = self.resource_manager.allocate_gpus(job.beam_count)
                        
                        if not gpu_allocation:
                            if self.logger:
                                self.logger.warning(f"Cannot allocate {job.beam_count} GPUs for case {job.case_id} - keeping in queue")
                            continue
                        
                        # Schedule the job
                        job.schedule(gpu_allocation)
                        
                        # Move to active jobs
                        self.job_queue.remove(job)
                        self.active_jobs[job.case_id] = job
                        
                        if self.logger:
                            self.logger.info(f"Started job for case {job.case_id} with GPUs {gpu_allocation}")
                        
                        # Update status display
                        if self.status_display:
                            self.status_display.update_case_status(
                                case_id=job.case_id,
                                status="PROCESSING",
                                stage="Starting GPU Processing",
                                gpu_allocation=gpu_allocation,
                                beam_info=f"Allocated GPUs: {gpu_allocation}"
                            )
                        
                        return job
                
                return None
                
            except Exception as e:
                if self.logger:
                    self.logger.error(f"Error getting next job: {e}")
                return None
    
    def start_job_execution(self, job: Job) -> bool:
        """Start execution of a job."""
        try:
            job.start_execution()
            
            # Update case status to processing
            self.case_service.start_case_processing(job.case_id, job.start_task)
            
            if self.logger:
                self.logger.info(f"Started execution of job {job.job_id} for case {job.case_id}")
            
            return True
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error starting job execution for case {job.case_id}: {e}")
            return False
    
    def complete_job(self, case_id: str, success: bool = True, error_message: str = None) -> bool:
        """Mark job as completed and release resources."""
        with self.lock:
            try:
                if case_id not in self.active_jobs:
                    if self.logger:
                        self.logger.warning(f"Job for case {case_id} not found in active jobs")
                    return False
                
                job = self.active_jobs[case_id]
                
                if success:
                    job.complete()
                    if self.logger:
                        self.logger.info(f"Job for case {case_id} completed successfully")
                    
                    # Update case status
                    self.case_service.complete_case(case_id)
                    
                    # Update status display
                    if self.status_display:
                        self.status_display.update_case_status(
                            case_id=case_id,
                            status="COMPLETED",
                            stage="Processing Complete",
                            beam_info="All beams processed successfully"
                        )
                else:
                    job.fail(error_message or "Job processing failed")
                    if self.logger:
                        self.logger.error(f"Job for case {case_id} failed: {error_message}")
                    
                    # Update case status
                    self.case_service.fail_case(case_id, error_message or "Job processing failed")
                    
                    # Update status display
                    if self.status_display:
                        self.status_display.update_case_status(
                            case_id=case_id,
                            status="FAILED",
                            stage="Processing Failed",
                            error_message=f"Processing failed: {error_message}",
                            beam_info=""
                        )
                
                # Release GPUs
                if job.gpu_allocation:
                    self.resource_manager.release_gpus(job.gpu_allocation)
                
                # Move to completed jobs
                self.completed_jobs.append(job)
                del self.active_jobs[case_id]
                
                return True
                
            except Exception as e:
                if self.logger:
                    self.logger.error(f"Error completing job for case {case_id}: {e}")
                return False
    
    def cancel_job(self, case_id: str) -> bool:
        """Cancel a job and release resources."""
        with self.lock:
            try:
                # Check if job is in queue
                for job in self.job_queue:
                    if job.case_id == case_id:
                        job.cancel()
                        self.job_queue.remove(job)
                        
                        if self.logger:
                            self.logger.info(f"Canceled queued job for case {case_id}")
                        return True
                
                # Check if job is active
                if case_id in self.active_jobs:
                    job = self.active_jobs[case_id]
                    job.cancel()
                    
                    # Release GPUs
                    if job.gpu_allocation:
                        self.resource_manager.release_gpus(job.gpu_allocation)
                    
                    # Move to completed jobs
                    self.completed_jobs.append(job)
                    del self.active_jobs[case_id]
                    
                    if self.logger:
                        self.logger.info(f"Canceled active job for case {case_id}")
                    return True
                
                if self.logger:
                    self.logger.warning(f"Job for case {case_id} not found")
                return False
                
            except Exception as e:
                if self.logger:
                    self.logger.error(f"Error canceling job for case {case_id}: {e}")
                return False
    
    def get_job_status(self, case_id: str) -> Optional[str]:
        """Get status of a specific job."""
        with self.lock:
            # Check active jobs
            if case_id in self.active_jobs:
                return self.active_jobs[case_id].status
            
            # Check queue
            for job in self.job_queue:
                if job.case_id == case_id:
                    return job.status
            
            # Check completed jobs
            for job in self.completed_jobs:
                if job.case_id == case_id:
                    return job.status
            
            return None
    
    def get_queue_status(self) -> Dict[str, Any]:
        """Get current queue status."""
        with self.lock:
            pending_jobs = len([job for job in self.job_queue if job.status == "PENDING"])
            running_jobs = len(self.active_jobs)
            completed_jobs = len([job for job in self.completed_jobs if job.status == "COMPLETED"])
            failed_jobs = len([job for job in self.completed_jobs if job.status == "FAILED"])
            
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
            gpu_status = self.resource_manager.get_all_gpu_status()
            
            # Calculate allocated GPUs
            allocated_gpus = 0
            for job in self.active_jobs.values():
                allocated_gpus += len(job.gpu_allocation)
            
            total_gpus = gpu_status.get('total_gpus', 0)
            utilization_percent = (allocated_gpus / total_gpus) * 100 if total_gpus > 0 else 0
            
            return {
                "total_gpus": total_gpus,
                "available_gpus": len(gpu_status.get('available_gpus', [])),
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
                if job.completed_time and job.completed_time > cutoff_time
            ]
            
            cleaned_count = initial_count - len(self.completed_jobs)
            
            if cleaned_count > 0 and self.logger:
                self.logger.info(f"Cleaned up {cleaned_count} old completed jobs")
            
            return cleaned_count
    
    def retry_failed_jobs(self, max_retries: int = 3) -> int:
        """Retry failed jobs that haven't exceeded max retries."""
        with self.lock:
            retry_count = 0
            
            failed_jobs = [job for job in self.completed_jobs if job.status == "FAILED"]
            
            for job in failed_jobs:
                if job.retry_count < max_retries:
                    case_id = job.case_id
                    
                    # Remove from completed jobs
                    self.completed_jobs.remove(job)
                    
                    # Reschedule
                    if self.schedule_case(case_id, job.priority):
                        retry_count += 1
                        if self.logger:
                            self.logger.info(f"Retrying failed job for case {case_id}")
            
            return retry_count
    
    def get_job_history(self, case_id: str) -> List[Dict[str, Any]]:
        """Get job history for a specific case."""
        with self.lock:
            history = []
            
            # Check all job lists
            all_jobs = list(self.job_queue) + list(self.active_jobs.values()) + self.completed_jobs
            
            for job in all_jobs:
                if job.case_id == case_id:
                    history.append({
                        "job_id": job.job_id,
                        "status": job.status,
                        "created_time": job.created_time,
                        "started_time": job.started_time,
                        "completed_time": job.completed_time,
                        "gpu_allocation": job.gpu_allocation,
                        "retry_count": job.retry_count,
                        "error_message": job.error_message
                    })
            
            return sorted(history, key=lambda x: x["created_time"])
    
    def get_performance_metrics(self) -> Dict[str, Any]:
        """Get performance metrics for the job service."""
        with self.lock:
            total_jobs = len(self.completed_jobs)
            
            if total_jobs == 0:
                return {
                    "total_jobs": 0,
                    "success_rate": 0.0,
                    "average_processing_time": 0.0,
                    "gpu_efficiency": 0.0
                }
            
            successful_jobs = [job for job in self.completed_jobs if job.status == "COMPLETED"]
            success_rate = (len(successful_jobs) / total_jobs) * 100
            
            # Calculate average processing time
            processing_times = []
            for job in successful_jobs:
                if job.started_time and job.completed_time:
                    processing_time = (job.completed_time - job.started_time).total_seconds()
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