import json
import hashlib
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any


class CaseScanner:
    def __init__(self, base_path: str, state_manager, stability_period_seconds: int = 10, logger=None, config=None):
        self.base_path = Path(base_path)
        self.state_manager = state_manager
        self.stability_period_seconds = stability_period_seconds
        self.logger = logger
        self.config = config
        
        # Extract max_retries from config, defaulting to 3 if not specified
        if config and config.get("error_handling"):
            self.max_retries = config["error_handling"].get("max_retries", 3)
        else:
            self.max_retries = 3


    def _get_last_modified_time(self, folder_path: Path) -> Optional[float]:
        """Get the last modification time of any file in the folder."""
        if not folder_path.exists():
            return None
        
        last_modified = 0.0
        try:
            for file_path in folder_path.rglob("*"):
                if file_path.is_file():
                    mod_time = file_path.stat().st_mtime
                    if mod_time > last_modified:
                        last_modified = mod_time
            return last_modified if last_modified > 0.0 else None
        except (OSError, IOError):
            return None

    def _calculate_folder_hash(self, folder_path: Path) -> str:
        """Calculate hash of folder contents for change detection."""
        if not folder_path.exists():
            return ""
        
        hash_md5 = hashlib.md5()
        
        # Include all files with their sizes and modification times
        for file_path in sorted(folder_path.rglob("*")):
            if file_path.is_file():
                try:
                    stat = file_path.stat()
                    hash_md5.update(f"{file_path.name}:{stat.st_size}:{stat.st_mtime}".encode())
                except (OSError, IOError):
                    continue
        
        return hash_md5.hexdigest()

    def _validate_case_directory(self, case_id: str) -> bool:
        """Validate if case directory exists and is accessible."""
        case_path = self.base_path / case_id
        return case_path.exists() and case_path.is_dir()

    def _is_case_new(self, case_id: str, current_hash: str) -> bool:
        """Check if case is new or has been modified."""
        if case_id not in self.case_status:
            return True
        
        stored_hash = self.case_status[case_id].get("hash", "")
        return stored_hash != current_hash

    def _read_case_status_file(self, case_id: str) -> Dict[str, Any]:
        """Read case status from StateManager."""
        try:
            # Get case status from StateManager
            case_data = self.state_manager.get_case_status(case_id)
            if case_data:
                # Return data for compatibility with MainController expectations
                return {
                    "status": case_data.get("status", "NEW"),
                    "current_task": case_data.get("current_task"),
                    "last_updated": case_data.get("last_updated")
                }
            
            # For compatibility, check if case directory has individual status file
            # This allows graceful migration from old approach
            case_dir = self.base_path / case_id
            individual_status_file = case_dir / "case_status.json"
            
            if individual_status_file.exists():
                # Migrate individual status file to StateManager
                try:
                    with open(individual_status_file, 'r') as f:
                        individual_data = json.load(f)
                    
                    # Update StateManager with individual file data
                    status = individual_data.get("status", "NEW")
                    current_task = individual_data.get("current_task")
                    last_updated = individual_data.get("last_updated")
                    
                    # Add to StateManager using appropriate method based on status
                    if status == "PROCESSING":
                        self.state_manager.set_case_processing(case_id, current_task=current_task or "Starting processing")
                    elif status == "COMPLETED":
                        self.state_manager.set_case_completed(case_id)
                    elif status == "FAILED":
                        self.state_manager.set_case_failed(case_id, "Migrated from individual status file")
                    else:
                        # For NEW or other statuses, use the general method
                        self.state_manager.update_case_status(
                            case_id, status, 
                            current_task=current_task,
                            folder_hash="",
                            gpu_allocation=[],
                            retry_count=0,
                            remote_path="",
                            remote_pid=None,
                            locked_gpus=[],
                            last_completed_step=""
                        )
                    
                    self.logger.info(f"Migrated individual status file for case {case_id} to StateManager")
                    
                    # Return the migrated data
                    return {
                        "status": status,
                        "current_task": current_task,
                        "last_updated": last_updated
                    }
                    
                except Exception as migration_error:
                    self.logger.warning(f"Failed to migrate individual status file for {case_id}: {migration_error}")
            
            # Default return for new cases
            return {"status": "NEW"}
            
        except Exception as e:
            self.logger.error(f"Failed to read case status for {case_id}: {e}")
            return {"status": "NEW"}

    def _is_case_stalled(self, case_id: str, stall_threshold_hours: int = 3) -> bool:
        """Check if a case appears to be stalled based on last_updated timestamp."""
        try:
            # Get case data from StateManager
            case_data = self.state_manager.get_case_status(case_id) or {}
            
            if case_data.get("status") != "PROCESSING":
                return False
            
            last_updated_str = case_data.get("last_updated")
            if not last_updated_str:
                return True  # No timestamp, consider stalled
            
            try:
                # Handle both ISO format and simple datetime format
                if 'Z' in last_updated_str or '+' in last_updated_str:
                    last_updated = datetime.fromisoformat(last_updated_str.replace('Z', '+00:00'))
                    current_time = datetime.utcnow().replace(tzinfo=last_updated.tzinfo)
                else:
                    # Handle simple format like "2024-07-24 10:30:45"
                    last_updated = datetime.strptime(last_updated_str, "%Y-%m-%d %H:%M:%S")
                    current_time = datetime.now()
                
                time_diff = current_time - last_updated
                
                is_stalled = time_diff.total_seconds() > (stall_threshold_hours * 3600)
                if is_stalled:
                    self.logger.warning(f"Case {case_id} appears stalled (last updated: {last_updated_str})")
                
                return is_stalled
                
            except ValueError as e:
                self.logger.warning(f"Invalid timestamp format in case status for {case_id}: {e}")
                return True  # Invalid timestamp, consider stalled
                
        except Exception as e:
            self.logger.error(f"Error checking stalled case {case_id}: {e}")
            return False

    def scan_for_new_cases(self) -> List[str]:
        """Scan base directory for cases that need processing based on centralized status management."""
        cases_to_process = []
        
        self.logger.info(f"Scanning directory: {self.base_path}")
        
        if not self.base_path.exists():
            self.logger.warning(f"Base path does not exist: {self.base_path}")
            return cases_to_process
        
        try:
            directories_found = list(self.base_path.iterdir())
            self.logger.info(f"Found {len(directories_found)} items in base path")
            
            # Step 1: List all case directories from local_logdata path
            for case_dir in directories_found:
                if not case_dir.is_dir():
                    self.logger.debug(f"Skipping non-directory: {case_dir}")
                    continue
                
                case_id = case_dir.name
                self.logger.debug(f"Processing case directory: {case_id}")
                
                # Validate case directory
                if not self._validate_case_directory(case_id):
                    self.logger.warning(f"Case directory validation failed: {case_id}")
                    continue
                
                # Step 2: Check if case_id exists in StateManager
                if not self.state_manager.case_exists(case_id):
                    # Step 3: New Case - treat as new case
                    # Check for directory stability (only for truly new cases)
                    last_mod_time = self._get_last_modified_time(case_dir)
                    is_stable = not (last_mod_time and (datetime.now().timestamp() - last_mod_time) < self.stability_period_seconds)
                    
                    # Also check for individual status file for migration purposes
                    individual_status_file = case_dir / "case_status.json"
                    if not is_stable and not individual_status_file.exists():
                        self.logger.info(f"Case '{case_id}' is unstable and has no status file. Skipping for now.")
                        continue
                    
                    self.logger.info(f"New case detected: {case_id}")
                    cases_to_process.append(case_id)
                    
                    # Add case to StateManager with NEW status
                    current_hash = self._calculate_folder_hash(case_dir)
                    self.update_case_status(case_id, "NEW", folder_hash=current_hash)
                    
                else:
                    # Step 4: Existing Case - check status value in StateManager
                    case_data = self.state_manager.get_case_status(case_id)
                    status = case_data.get("status", "NEW")
                    
                    self.logger.debug(f"Case '{case_id}' has status: {status}")
                    
                    if status == "PROCESSING":
                        # Check if case is stalled
                        if self._is_case_stalled(case_id):
                            current_task = case_data.get("current_task")
                            self.logger.warning(f"Stalled case detected for resumption: {case_id} (task: {current_task})")
                            cases_to_process.append(case_id)
                        else:
                            current_task = case_data.get("current_task")
                            self.logger.debug(f"Case still processing: {case_id} (task: {current_task})")
                    elif status == "COMPLETED":
                        # Ignore completed cases
                        self.logger.debug(f"Case already completed: {case_id}")
                    elif status == "FAILED":
                        # Check retry count for failed cases
                        retry_count = case_data.get("retry_count", 0)
                        if retry_count < self.max_retries:
                            self.logger.info(f"Failed case {case_id} being queued for retry (attempt {retry_count + 1}/{self.max_retries})")
                            cases_to_process.append(case_id)
                        else:
                            self.logger.debug(f"Case {case_id} has exceeded retry limit ({retry_count}/{self.max_retries})")
                    else:
                        self.logger.warning(f"Unknown status '{status}' for case {case_id}")
        
        except (OSError, IOError) as e:
            self.logger.error(f"Error scanning directory: {e}")
        
        self.logger.info(f"Case scan completed. Found {len(cases_to_process)} cases to process")
        return cases_to_process

    def get_case_resumption_task(self, case_id: str) -> Optional[str]:
        """Get the task that a PROCESSING case should resume from."""
        try:
            # Get case data from StateManager
            case_data = self.state_manager.get_case_status(case_id) or {}
            
            if case_data.get("status") == "PROCESSING":
                return case_data.get("current_task")
            
            return None
            
        except Exception as e:
            self.logger.error(f"Error getting resumption task for case {case_id}: {e}")
            return None

    def update_case_status(self, case_id: str, status: str, folder_hash: str = "", 
                          gpu_allocation: Optional[List[int]] = None, 
                          retry_count: int = 0,
                          remote_path: Optional[str] = None,
                          remote_pid: Optional[int] = None,
                          locked_gpus: Optional[List[int]] = None,
                          last_completed_step: Optional[str] = None,
                          current_task: Optional[str] = None) -> None:
        """Update case status by delegating to StateManager."""
        self.state_manager.update_case_status(
            case_id=case_id,
            status=status,
            folder_hash=folder_hash,
            gpu_allocation=gpu_allocation,
            retry_count=retry_count,
            remote_path=remote_path,
            remote_pid=remote_pid,
            locked_gpus=locked_gpus,
            last_completed_step=last_completed_step,
            current_task=current_task
        )

    def get_case_status(self, case_id: str) -> Optional[Dict[str, Any]]:
        """Get status information for a specific case."""
        return self.state_manager.get_case_status(case_id)

    def get_cases_by_status(self, status: str) -> List[str]:
        """Get list of case IDs with specified status."""
        return self.state_manager.get_cases_by_status(status)

    def get_processing_cases(self) -> List[str]:
        """Get list of currently processing cases."""
        return self.get_cases_by_status("PROCESSING")

    def get_failed_cases(self) -> List[str]:
        """Get list of failed cases that can be retried."""
        return self.get_cases_by_status("FAILED")

    def cleanup_old_cases(self, days: int = 30) -> None:
        """Remove old case entries from StateManager."""
        cutoff_time = datetime.now().timestamp() - (days * 24 * 3600)
        self.state_manager.cleanup_old_cases(cutoff_time)

    def archive_old_cases(self, days: int = 30) -> None:
        """Archive old completed or failed cases to a separate file."""
        all_cases = self.state_manager.get_all_cases()
        if not all_cases:
            return

        cutoff_date = datetime.now()
        cutoff_timestamp = (cutoff_date - timedelta(days=days)).timestamp()
        
        cases_to_archive = {}
        
        for case_id, info in all_cases.items():
            status = info.get("status", "")
            end_time_str = info.get("end_time", "")
            
            if status in ["COMPLETED", "FAILED"] and end_time_str:
                try:
                    end_time = datetime.strptime(end_time_str, "%Y-%m-%d %H:%M:%S")
                    if end_time.timestamp() < cutoff_timestamp:
                        cases_to_archive[case_id] = info
                except (ValueError, TypeError):
                    continue
        
        if not cases_to_archive:
            return
            
        archive_filename = f"case_closed_{cutoff_date.strftime('%Y%m')}.json"
        archive_filepath = Path.cwd() / archive_filename
        
        archived_data = {}
        if archive_filepath.exists():
            try:
                with open(archive_filepath, 'r', encoding='utf-8') as f:
                    archived_data = json.load(f)
            except (json.JSONDecodeError, FileNotFoundError):
                self.logger.warning(f"Could not read existing archive file: {archive_filepath}")

        archived_data.update(cases_to_archive)
        
        try:
            with open(archive_filepath, 'w', encoding='utf-8') as f:
                json.dump(archived_data, f, indent=2, ensure_ascii=False)
            self.logger.info(f"Archived {len(cases_to_archive)} cases to {archive_filepath}")
        except IOError as e:
            self.logger.error(f"Failed to write to archive file: {e}")
            return

        # Remove archived cases from StateManager
        for case_id in cases_to_archive:
            self.state_manager.delete_case(case_id)
        
        self.logger.info("Cleaned up archived cases from StateManager.")

    def reset_case_status(self, case_id: str) -> bool:
        """Reset case status to allow reprocessing."""
        return self.state_manager.reset_case_status(case_id)

    def get_all_case_status(self) -> Dict[str, Any]:
        """Get complete case status dictionary."""
        return self.state_manager.get_all_cases()

    def recover_stale_jobs(self, max_processing_hours: int = 1) -> Dict[str, Any]:
        """Recover cases that have been stuck in PROCESSING state for too long."""
        all_cases = self.state_manager.get_all_cases()
        if not all_cases:
            return {
                "recovered_cases": [],
                "recovered_count": 0,
                "remaining_processing_cases": [],
                "remaining_processing_count": 0,
                "recovery_timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
        
        current_time = datetime.now()
        cutoff_time = current_time.timestamp() - (max_processing_hours * 3600)
        recovered_cases = []
        
        for case_id, info in all_cases.items():
            if info.get("status") != "PROCESSING":
                continue
            
            try:
                start_time_str = info.get("start_time", "")
                if not start_time_str:
                    continue
                
                start_time = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M:%S")
                
                # Check if case has been processing for too long
                if start_time.timestamp() < cutoff_time:
                    # Reset case to NEW status for reprocessing using StateManager
                    retry_count = info.get("retry_count", 0) + 1
                    self.state_manager.update_case_status(
                        case_id, "NEW", 
                        retry_count=retry_count,
                        remote_pid=None,
                        locked_gpus=[]
                    )
                    
                    recovered_cases.append(case_id)
                    self.logger.warning(f"Recovered stale case {case_id} (processing for {max_processing_hours}+ hours)")
            
            except (ValueError, TypeError) as e:
                self.logger.error(f"Error parsing start_time for case {case_id}: {e}")
                continue
        
        # Get remaining processing cases after recovery
        processing_cases = self.get_cases_by_status("PROCESSING")
        
        return {
            "recovered_cases": recovered_cases,
            "recovered_count": len(recovered_cases),
            "remaining_processing_cases": processing_cases,
            "remaining_processing_count": len(processing_cases),
            "recovery_timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

    def check_and_recover_stale_cases(self, max_processing_hours: int = 1) -> Dict[str, Any]:
        """Check for and recover stale cases, returning recovery statistics."""
        recovered_cases = self.recover_stale_cases(max_processing_hours)
        
        # Get current processing cases after recovery
        processing_cases = self.get_cases_by_status("PROCESSING")
        
        return {
            "recovered_cases": recovered_cases,
            "recovered_count": len(recovered_cases),
            "remaining_processing_cases": processing_cases,
            "remaining_processing_count": len(processing_cases),
            "recovery_timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }