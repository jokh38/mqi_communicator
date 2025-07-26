"""Scheduler for MOQUI automation system.

Handles case scanning, queuing, scheduling logic, and case processing workflow.
"""

import threading
import time
import queue
import shlex
from datetime import datetime, timedelta
from typing import List


class Scheduler:
    """Manages case scanning, queuing, and scheduling logic."""
    
    def __init__(self, scan_queue, shared_state, shared_state_lock, scan_lock, 
                 case_service, job_service, status_display, logger, error_handler,
                 scan_interval, workflow_engine, resource_manager, transfer_manager,
                 remote_executor, state_manager, config, completed_queue):
        """Initialize scheduler with dependencies."""
        self.scan_queue = scan_queue
        self.shared_state = shared_state
        self.shared_state_lock = shared_state_lock
        self.scan_lock = scan_lock
        self.case_service = case_service
        self.job_service = job_service
        self.status_display = status_display
        self.logger = logger
        self.error_handler = error_handler
        self.scan_interval = scan_interval
        self.workflow_engine = workflow_engine
        self.resource_manager = resource_manager
        self.transfer_manager = transfer_manager
        self.remote_executor = remote_executor
        self.state_manager = state_manager
        self.config = config
        self.completed_queue = completed_queue
        self.running = True

    def scan_for_new_cases(self) -> None:
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
                
                # Scan for new cases using case service
                new_cases = self.case_service.scan_for_new_cases()
                
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

    def calculate_next_scan_time(self, current_time: datetime) -> datetime:
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

    def check_waiting_cases(self) -> List[str]:
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

    def add_to_waiting_list(self, case_id: str) -> None:
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

    def remove_from_waiting_list(self, case_id: str) -> None:
        """Remove case from waiting list (e.g., when successfully scheduled)."""
        with self.shared_state_lock:
            if case_id in self.shared_state['waiting_cases']:
                del self.shared_state['waiting_cases'][case_id]
                self.logger.debug(f"Removed case {case_id} from waiting list")

    def _calculate_retry_delay(self, retry_count: int, base_delay: int = 60) -> int:
        """Calculate retry delay using exponential backoff with jitter."""
        import random
        
        # Exponential backoff: base_delay * (2 ^ retry_count)
        delay = base_delay * (2 ** min(retry_count, 6))  # Cap at 6 to prevent extremely long delays
        
        # Add jitter (±25% randomization)
        jitter = delay * 0.25 * random.uniform(-1, 1)
        final_delay = max(base_delay, int(delay + jitter))
        
        return final_delay

    def process_case(self, case_id: str, start_task: str = None) -> bool:
        """Process a single case through the complete workflow using WorkflowEngine."""
        try:
            # Read current status from state manager to determine starting point
            case_data = self.state_manager.get_case_status(case_id) or {}
            
            # Determine starting task
            if start_task:
                current_task = start_task
            elif case_data.get("status") == "PROCESSING":
                current_task = case_data.get("current_task", "setup")
            else:
                current_task = "setup"
            
            # Update status to PROCESSING at start using state manager
            self.state_manager.set_case_processing(case_id, current_task=current_task)
            
            # Create processing context with new service layer
            context = self.workflow_engine.create_context(
                case_id=case_id,
                logger=self.logger,
                status_display=self.status_display,
                resource_manager=self.resource_manager,
                transfer_manager=self.transfer_manager,
                remote_executor=self.remote_executor,
                job_service=self.job_service,
                case_service=self.case_service,
                shared_state=self.shared_state
            )
            
            # Execute the workflow
            success = self.workflow_engine.execute_workflow(context)
            
            if success:
                # Update status to COMPLETED using state manager
                self.state_manager.set_case_completed(case_id)
                
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
            
            # Get current retry count from case status
            case_info = self.state_manager.get_case_status(case_id)
            current_retry_count = case_info.get("retry_count", 0) if case_info else 0
            
            # Check if we should retry (for ANY exception type) - get max_retries from config
            max_retries = self.config.get("error_handling", {}).get("max_retries", 3)
            if current_retry_count < max_retries:
                self.logger.warning(f"Error processing case {case_id} (retry {current_retry_count + 1}/{max_retries}): {e}")
                # Update case status with incremented retry count and reset to NEW for reprocessing
                self.state_manager.update_case_status(case_id, "NEW", retry_count=current_retry_count + 1)
                return False # Indicate failure, but allow for retry
            else:
                # Exceeded retry limit - mark as permanently failed
                self.logger.error(f"Case {case_id} failed after {current_retry_count} retries: {e}")
                self.state_manager.set_case_failed(case_id, str(e), retry_count=current_retry_count)
                self.logger.log_case_progress(case_id, "FAILED", 0.0, {"stage": "error", "error": str(e)})
                self.status_display.update_case_status(case_id, "FAILED", 0.0, "Failed", error_message=str(e))
                
                # Clean up remote workspace on permanent failure
                try:
                    remote_path = self.resource_manager.get_case_remote_path(case_id)
                    self.remote_executor.execute_command(f"rm -rf {shlex.quote(remote_path)}")
                    self.logger.info(f"Cleaned up remote workspace for failed case: {case_id}")
                except Exception as cleanup_error:
                    self.logger.warning(f"Failed to clean up remote workspace for case {case_id}: {cleanup_error}")
            
            with self.shared_state_lock:
                self.shared_state['active_cases'].discard(case_id)
            
            return False

    def background_case_processor(self) -> None:
        """Background worker for processing cases."""
        while self.running:
            try:
                # Re-queue waiting cases that are ready for retry
                ready_cases = self.check_waiting_cases()
                for case_id in ready_cases:
                    if case_id not in list(self.scan_queue.queue):
                        self.scan_queue.put(case_id)

                # Attempt to schedule one case from the scan_queue into the job_scheduler
                if not self.scan_queue.empty():
                    try:
                        case_id = self.scan_queue.get_nowait()
                        start_task = self.case_service.get_case_resumption_task(case_id)
                        
                        if self.job_service.schedule_case(case_id, start_task=start_task):
                            self.logger.info(f"Successfully queued job for case: {case_id}")
                            self.remove_from_waiting_list(case_id)
                        else:
                            self.logger.warning(f"Failed to schedule case {case_id}, adding to waiting list.")
                            self.add_to_waiting_list(case_id)
                    except queue.Empty:
                        pass

                # Fetch a job that is ready for processing (i.e., GPUs are allocated)
                job_to_process = self.job_service.get_next_job()

                if job_to_process:
                    case_id = job_to_process["case_id"]
                    start_task = job_to_process.get("start_task", "setup") # Ensure start_task exists
                    
                    # Process the case in a separate thread to not block the scheduler
                    processing_thread = threading.Thread(
                        target=self.process_case,
                        args=(case_id, start_task),
                        name=f"CaseProcessor-{case_id[:8]}"
                    )
                    processing_thread.daemon = True
                    processing_thread.start()
                else:
                    # Wait if no jobs are ready to be processed
                    time.sleep(2)

            except Exception as e:
                self.error_handler.handle_error(e, {"operation": "background_case_processing"})
                time.sleep(5)

    def stop(self) -> None:
        """Stop the scheduler."""
        self.running = False