"""
Scheduler Controller - Manages case scanning, queuing, and processing logic.

This class handles all case-related scheduling operations including scanning for new cases,
managing the case queue, processing workflow, and retry logic.
"""

import json
import os
import random
import shlex
import threading
import time
import queue
from datetime import datetime, timedelta
from pathlib import Path
from typing import List

from core.config import ConfigManager


class Scheduler:
    """Manages case scanning, queuing, and processing logic."""
    
    def __init__(self, logger, config, case_service, job_service, scan_queue, completed_queue,
                 shared_state, shared_state_lock, scan_lock, status_display,
                 error_handler, resource_manager, transfer_manager, remote_executor, state_manager):
        """Initialize scheduler with dependencies."""
        self.logger = logger
        self.config = config
        self.case_service = case_service
        self.job_service = job_service
        self.scan_queue = scan_queue
        self.completed_queue = completed_queue
        self.shared_state = shared_state
        self.shared_state_lock = shared_state_lock
        self.scan_lock = scan_lock
        self.status_display = status_display
        self.error_handler = error_handler
        self.resource_manager = resource_manager
        self.transfer_manager = transfer_manager
        self.remote_executor = remote_executor
        self.state_manager = state_manager
        
        # Extract scan interval from config
        self.scan_interval = config.get("scanning", {}).get("interval_minutes", 30)
        
        # Thread control
        self.running = False
        self.worker_thread = None

    def start(self) -> None:
        """Start the background worker thread."""
        try:
            self.running = True
            self.worker_thread = threading.Thread(
                target=self._background_case_processor,
                name="CaseProcessor",
                daemon=True
            )
            self.worker_thread.start()
            self.logger.info("Scheduler started successfully")
        except Exception as e:
            self.logger.error(f"Failed to start scheduler: {e}")
            raise

    def stop(self) -> None:
        """Stop the scheduler."""
        self.logger.info("Stopping scheduler")
        self.running = False
        if self.worker_thread and self.worker_thread.is_alive():
            self.worker_thread.join(timeout=5)

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

    def _remove_from_waiting_list(self, case_id: str) -> None:
        """Remove case from waiting list (e.g., when successfully scheduled)."""
        with self.shared_state_lock:
            if case_id in self.shared_state['waiting_cases']:
                del self.shared_state['waiting_cases'][case_id]
                self.logger.debug(f"Removed case {case_id} from waiting list")

    def _calculate_retry_delay(self, retry_count: int, base_delay: int = 60) -> int:
        """Calculate retry delay using exponential backoff with jitter."""
        
        # Exponential backoff: base_delay * (2 ^ retry_count)
        delay = base_delay * (2 ** min(retry_count, 6))  # Cap at 6 to prevent extremely long delays
        
        # Add jitter (±25% randomization)
        jitter = delay * 0.25 * random.uniform(-1, 1)
        final_delay = max(base_delay, int(delay + jitter))
        
        return final_delay

    def _process_case(self, case_id: str, start_task: str = None) -> bool:
        """Process a single case through the complete workflow with integrated steps."""
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
            
            # Define workflow steps
            workflow_steps = [
                "create_workspace",
                "upload_data", 
                "run_interpreter",
                "execute_beam_calculations",
                "run_converter",
                "create_output_directory",
                "check_disk_space",
                "download_results",
                "complete_processing"
            ]
            
            # Find starting step index
            start_index = 0
            last_completed_step = case_data.get('last_completed_step', '')
            if last_completed_step:
                try:
                    step_index = workflow_steps.index(last_completed_step)
                    start_index = step_index + 1
                    self.logger.info(f"Resuming workflow for case {case_id} from step {start_index + 1}")
                except ValueError:
                    self.logger.warning(f"Last completed step '{last_completed_step}' not found, starting from beginning")
                    start_index = 0
            
            # Execute workflow steps
            for i in range(start_index, len(workflow_steps)):
                step_name = workflow_steps[i]
                self.logger.info(f"Executing step {i + 1}/{len(workflow_steps)}: {step_name} for case {case_id}")
                
                # Update status display
                self.status_display.update_case_status(
                    case_id=case_id,
                    status="PROCESSING",
                    current_task=step_name,
                    current_step=i + 1,
                    total_steps=len(workflow_steps)
                )
                
                # Execute the step
                success = self._execute_step(case_id, step_name)
                
                if not success:
                    self.logger.error(f"Step {step_name} failed for case {case_id}")
                    return False
                
                # Mark step as completed
                self.state_manager.update_case_status(
                    case_id, "PROCESSING", last_completed_step=step_name
                )
                self.logger.info(f"Completed step: {step_name} for case {case_id}")
            
            # Update status to COMPLETED using state manager
            self.state_manager.set_case_completed(case_id)
            
            # Move to completed queue
            self.completed_queue.put(case_id)
            
            # Remove from display after a short delay
            time.sleep(3)
            self.status_display.remove_case(case_id)
            
            self.logger.info(f"Successfully processed case: {case_id}")
            return True
            
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
                
                # Ensure GPU resources are released on permanent failure
                self.job_service.complete_job(case_id, success=False, error_message=str(e))
                
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

    def _execute_step(self, case_id: str, step_name: str) -> bool:
        """Execute a specific workflow step."""
        step_methods = {
            "create_workspace": self._step_create_workspace,
            "upload_data": self._step_upload_data,
            "run_interpreter": self._step_run_interpreter,
            "execute_beam_calculations": self._step_execute_beam_calculations,
            "run_converter": self._step_run_converter,
            "create_output_directory": self._step_create_output_directory,
            "check_disk_space": self._step_check_disk_space,
            "download_results": self._step_download_results,
            "complete_processing": self._step_complete_processing
        }
        
        if step_name not in step_methods:
            self.logger.error(f"Unknown step: {step_name}")
            return False
        
        try:
            return step_methods[step_name](case_id)
        except Exception as e:
            self.logger.error(f"Error executing step {step_name} for case {case_id}: {e}")
            return False

    def _step_create_workspace(self, case_id: str) -> bool:
        """Create workspace directories (idempotent)."""
        try:
            if not self.resource_manager.create_case_workspace(case_id):
                self.logger.error(f"Failed to create workspace for case: {case_id}")
                return False
            
            self.logger.info(f"Workspace created/verified for case: {case_id}")
            return True
        except Exception as e:
            self.logger.error(f"Error creating workspace for case {case_id}: {e}")
            return False

    def _step_upload_data(self, case_id: str) -> bool:
        """Upload log data to remote server (idempotent)."""
        try:
            local_path = str(self.resource_manager.get_case_local_path(case_id))
            remote_path = self.resource_manager.get_case_remote_path(case_id)
            
            if not self.transfer_manager.upload_directory(
                local_path=local_path, 
                remote_path=remote_path,
                status_display=self.status_display,
                case_id=case_id
            ):
                self.logger.error(f"Failed to upload data for case: {case_id}")
                return False
            
            # Save the remote path to the case status
            self.case_service.update_case_status(
                case_id=case_id,
                status="PROCESSING",
                remote_path=remote_path
            )
            
            self.logger.info(f"Data uploaded/verified for case: {case_id}")
            return True
        except Exception as e:
            self.logger.error(f"Error uploading data for case {case_id}: {e}")
            return False

    def _step_run_interpreter(self, case_id: str) -> bool:
        """Run Python interpreter (idempotent with output check)."""
        try:
            # Check if interpreter output already exists
            workspace_path = self.resource_manager.get_case_remote_path(case_id)
            inputs_check = self.remote_executor.execute_command(f"test -d {workspace_path}/moqui_inputs")
            
            if inputs_check["exit_code"] == 0:
                self.logger.info(f"Interpreter output already exists for case {case_id}, skipping")
                return True
            
            result = self.remote_executor.run_moqui_interpreter(
                case_id, 
                status_display=self.status_display
            )
            
            if not result.get("success", False):
                self.logger.error(f"Failed to run interpreter for case: {case_id}")
                return False
            
            # Store remote PID in case status for cleanup purposes
            remote_pid = result.get("remote_pid")
            if remote_pid:
                self.case_service.update_case_status(
                    case_id=case_id,
                    status="PROCESSING",
                    remote_pid=remote_pid
                )
            
            # Store gantry info in shared state for beam calculations
            gantry_info = result.get("gantry_info")
            if gantry_info:
                self.shared_state[f"{case_id}_gantry_info"] = gantry_info
            
            # Mark interpreter step as completed
            self.case_service.update_case_status(
                case_id=case_id,
                status="PROCESSING",
                interpreter_completed=True,
                ready_for_beams=True
            )
            
            return True
        except Exception as e:
            self.logger.error(f"Error running interpreter for case {case_id}: {e}")
            return False

    def _step_execute_beam_calculations(self, case_id: str) -> bool:
        """Execute beam calculations."""
        try:
            # Check if case is ready for beam calculations
            case_status = self.case_service.get_case_status(case_id)
            if not case_status or not case_status.get('ready_for_beams', False):
                self.logger.error(f"Case {case_id} is not ready for beam calculations")
                return False
            
            # Get or create job for this specific case
            job = self.job_service.get_active_job(case_id)
            if not job:
                if not self.job_service.schedule_case(case_id, start_task="beams"):
                    self.logger.error(f"Failed to schedule beam calculations for case: {case_id}")
                    return False
                job = self.job_service.get_next_job()
                if not job:
                    self.logger.error(f"Failed to get scheduled job for case: {case_id}")
                    return False
            
            # Update context with GPU allocation
            gpu_allocation = job.get('gpu_allocation', [])
            self.status_display.update_case_status(
                case_id=case_id,
                status="PROCESSING",
                stage="Calculating beams",
                gpu_allocation=gpu_allocation,
                beam_info=f"Using GPUs: {gpu_allocation}"
            )
            
            # Read gantry information from case_status.json
            gantry_info = self._read_gantry_info_from_status(case_id)
            
            # Execute beams using available GPUs
            beam_results = []
            total_beams = len(gpu_allocation)

            for i, gpu_id in enumerate(gpu_allocation):
                beam_id = i + 1
                
                self.status_display.update_case_status(
                    case_id=case_id,
                    detailed_progress=f"{beam_id}/{total_beams}",
                    detailed_status=f"Calculating beam {beam_id} on GPU {gpu_id}"
                )
                
                self.logger.info(f"Starting beam {beam_id} calculation for case {case_id} on GPU {gpu_id}")
                
                # Prepare dynamic parameters
                dynamic_params = self._prepare_dynamic_params(case_id, gantry_info, gpu_id, beam_id)
                
                # Create the config file
                config_manager = ConfigManager()
                tps_env_path = self.remote_executor.working_directories["tps_env"]
                target_path = f"{tps_env_path}/moqui_tps.in"
                
                if not self.remote_executor.update_moqui_tps_in(target_path, dynamic_params):
                    self.logger.error(f"Failed to update moqui_tps.in for case: {case_id}")
                    self.job_service.complete_job(case_id, False)
                    return False
            
                self.logger.info(f"Successfully generated moqui_tps.in file for beam {beam_id} case: {case_id}")
                
                # Archive the generated parameters
                template_params = config_manager.get_moqui_tps_template()
                merged_params = {**template_params, **dynamic_params}
                self._archive_tps_parameters(case_id, merged_params, beam_id)
                
                # Run beam calculation
                beam_result = self.remote_executor.run_moqui_beam(
                    case_id=case_id,
                    beam_id=beam_id,
                    gpu_id=gpu_id,
                    status_display=self.status_display
                )
                
                beam_results.append(beam_result)
                
                if not beam_result.get("success", False):
                    self.logger.error(f"Beam {beam_id} calculation failed for case: {case_id}")
                    self.job_service.complete_job(case_id, False)
                    return False
                
                self.logger.info(f"Beam {beam_id} calculation completed successfully for case: {case_id}")
            
            # Check for moqui_inputs directory
            workspace_path = self.remote_executor.remote_workspace
            case_path = f"{workspace_path}/{case_id}"
            
            inputs_check = self.remote_executor.execute_command(f"ls {case_path}/moqui_inputs/")
            if inputs_check["exit_code"] != 0:
                self.logger.error(f"No moqui_inputs found for case: {case_id}")
                self.job_service.complete_job(case_id, False)
                return False
            
            # Mark job as complete
            success = all(result.get("success", False) for result in beam_results)
            self.job_service.complete_job(case_id, success)
            
            if not success:
                self.logger.error(f"One or more beam calculations failed for case: {case_id}")
                return False
            
            self.logger.info(f"All beam calculations completed successfully for case: {case_id}")
            return True
            
        except Exception as e:
            self.logger.error(f"Error executing beam calculations for case {case_id}: {e}")
            try:
                self.job_service.complete_job(case_id, False)
            except Exception:
                pass
            return False

    def _step_run_converter(self, case_id: str) -> bool:
        """Run raw to DICOM converter (idempotent with output check)."""
        try:
            # Check if converter output already exists
            dicom_check = self.remote_executor.execute_command(
                f"test -f {self.remote_executor.moqui_outputs_path}/{case_id}/RTDOSE.dcm"
            )
            
            if dicom_check["exit_code"] == 0:
                self.logger.info(f"DICOM output already exists for case {case_id}, skipping")
                return True
            
            if not self.remote_executor.run_raw_to_dicom_converter(case_id, status_display=self.status_display):
                self.logger.error(f"Failed to run converter for case: {case_id}")
                return False
            
            return True
        except Exception as e:
            self.logger.error(f"Error running converter for case {case_id}: {e}")
            return False

    def _step_create_output_directory(self, case_id: str) -> bool:
        """Create output directory (idempotent)."""
        try:
            if not self.resource_manager.create_output_directory(case_id):
                self.logger.error(f"Failed to create output directory for case: {case_id}")
                return False
            
            self.logger.info(f"Output directory created/verified for case: {case_id}")
            return True
        except Exception as e:
            self.logger.error(f"Error creating output directory for case {case_id}: {e}")
            return False

    def _step_check_disk_space(self, case_id: str) -> bool:
        """Check local disk space before download."""
        estimated_download_size = self.job_service.estimate_case_disk_usage(case_id)
        local_space = self.job_service.check_local_disk_space(estimated_download_size)
        
        if not local_space["sufficient"]:
            error_msg = (f"Insufficient local disk space for case {case_id}: "
                        f"need {estimated_download_size}GB, have {local_space['free_gb']}GB")
            self.logger.error(error_msg)
            raise Exception(error_msg)
        
        return True

    def _step_download_results(self, case_id: str) -> bool:
        """Download results from remote server (idempotent)."""
        try:
            output_path = str(self.resource_manager.get_case_output_path(case_id))
            remote_path = self.resource_manager.get_case_remote_path(case_id)
            
            # Check if results already downloaded
            output_exists = os.path.exists(output_path) and os.listdir(output_path)
            
            if output_exists:
                self.logger.info(f"Results already downloaded for case {case_id}, skipping")
                return True
            
            if not self.transfer_manager.download_directory(
                remote_path=remote_path, 
                local_path=output_path,
                status_display=self.status_display,
                case_id=case_id
            ):
                self.logger.error(f"Failed to download results for case: {case_id}")
                return False
            
            self.logger.info(f"Results downloaded for case: {case_id}")
            return True
        except Exception as e:
            self.logger.error(f"Error downloading results for case {case_id}: {e}")
            return False

    def _step_complete_processing(self, case_id: str) -> bool:
        """Complete processing and update status."""
        self.case_service.update_case_status(case_id, "COMPLETED")
        self.status_display.update_case_status(case_id, "COMPLETED", 1.0, "Finished successfully")
        
        # Remove from active cases
        with self.shared_state_lock:
            self.shared_state['active_cases'].discard(case_id)
        
        return True

    def _read_gantry_info_from_status(self, case_id: str) -> dict:
        """Read gantry information from case_status.json."""
        
        try:
            status_file_path = Path.cwd() / "case_status.json"
            
            if not status_file_path.exists():
                return {}
            
            with open(status_file_path, 'r', encoding='utf-8') as f:
                case_status = json.load(f)
            
            case_info = case_status.get(case_id, {})
            return case_info.get('gantry_info', {})
            
        except Exception:
            return {}

    def _prepare_dynamic_params(self, case_id: str, gantry_info: dict, gpu_id: int, beam_id: int) -> dict:
        """Prepare dynamic parameters for moqui_tps.in with absolute paths."""
        
        dynamic_params = {}
        
        config = self.remote_executor.config
        
        # Construct absolute DicomDir path
        remote_dicom_base = config['paths'].get('remote_workspace', '/home/gpuadmin/MOQUI_SMC/tps')
        dicom_dir_path = os.path.join(remote_dicom_base, case_id)
        dynamic_params["DicomDir"] = dicom_dir_path
        
        # Construct absolute logFilePath
        remote_output_csv_base = config['paths'].get('linux_moqui_interpreter_outputs_dir', '/home/gpuadmin/MOQUI_SMC/Outputs_csv')
        log_file_path = os.path.join(remote_output_csv_base, case_id)
        dynamic_params["logFilePath"] = log_file_path
        
        # Update paths
        dynamic_params["ParentDir"] = f"{self.remote_executor.moqui_interpreter_outputs_path}/{case_id}"
        dynamic_params["OutputDir"] = f"{self.remote_executor.moqui_outputs_path}/{case_id}"
        
        # Set GPU and beam parameters
        dynamic_params["GPUID"] = gpu_id
        dynamic_params["BeamNumbers"] = beam_id
        
        # Gantry information
        if gantry_info:
            if 'primary_gantry' in gantry_info:
                dynamic_params["GantryNum"] = gantry_info['primary_gantry']
            elif 'gantry_angles' in gantry_info and gantry_info['gantry_angles']:
                dynamic_params["GantryNum"] = int(gantry_info['gantry_angles'][0])
            else:
                dynamic_params["GantryNum"] = 0
        else:
            dynamic_params["GantryNum"] = 0
        
        return dynamic_params

    def _archive_tps_parameters(self, case_id: str, merged_params: dict, beam_id: int = None) -> None:
        """Archive the generated moqui_tps.in parameters locally for monitoring."""
        try:
            
            # Create archive directory
            archive_dir = Path("logs/archive")
            archive_dir.mkdir(parents=True, exist_ok=True)
            
            # Generate filename
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            beam_suffix = f"_beam{beam_id}" if beam_id is not None else ""
            archived_filename = f"moqui_tps_{case_id}{beam_suffix}_{timestamp}.in"
            archived_path = archive_dir / archived_filename
            
            # Create content
            content_lines = []
            for key, value in merged_params.items():
                content_lines.append(f"{key} {value}")
            
            # Write to file
            with open(archived_path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(content_lines) + '\n')
            
            self.logger.info(f"Input file archived locally as {archived_filename}")
            
        except Exception as e:
            self.logger.error(f"Failed to archive moqui_tps.in parameters: {e}")

    def _background_case_processor(self) -> None:
        """Background worker for processing cases."""
        while self.running:
            try:
                # Re-queue waiting cases that are ready for retry
                ready_cases = self._check_waiting_cases()
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
                            self._remove_from_waiting_list(case_id)
                        else:
                            self.logger.warning(f"Failed to schedule case {case_id}, adding to waiting list.")
                            self._add_to_waiting_list(case_id)
                    except queue.Empty:
                        pass

                # Fetch a job that is ready for processing (i.e., GPUs are allocated)
                job_to_process = self.job_service.get_next_job()

                if job_to_process:
                    case_id = job_to_process["case_id"]
                    start_task = job_to_process.get("start_task", "setup") # Ensure start_task exists
                    
                    # Process the case in a separate thread to not block the scheduler
                    processing_thread = threading.Thread(
                        target=self._process_case,
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