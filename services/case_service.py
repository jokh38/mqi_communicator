"""
CaseService - Service for managing case lifecycle.

Merges functionality from case_scanner.py to provide unified case management.
"""

import json
import hashlib
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any

from models.case import Case
from core.state import StateManager
from core.logging import Logger


class CaseService:
    """Manages the lifecycle of Case models (scanning, creation, archiving)."""
    
    def __init__(self, 
                 base_path: str,
                 state_manager: StateManager,
                 stability_period_seconds: int = 10,
                 max_retries: int = 3,
                 logger: Optional[Logger] = None):
        
        self.base_path = Path(base_path)
        self.state_manager = state_manager
        self.stability_period_seconds = stability_period_seconds
        self.max_retries = max_retries
        self.logger = logger
    
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
    
    def _is_case_stalled(self, case: Case, stall_threshold_hours: int = 3) -> bool:
        """Check if a case appears to be stalled based on last_updated timestamp."""
        try:
            if case.status != "PROCESSING":
                return False
            
            if not case.last_updated:
                return True  # No timestamp, consider stalled
            
            current_time = datetime.now()
            time_diff = current_time - case.last_updated
            
            is_stalled = time_diff.total_seconds() > (stall_threshold_hours * 3600)
            if is_stalled and self.logger:
                self.logger.warning(f"Case {case.case_id} appears stalled (last updated: {case.last_updated})")
            
            return is_stalled
                
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error checking stalled case {case.case_id}: {e}")
            return False
    
    def _migrate_individual_status_file(self, case_id: str) -> Optional[Dict[str, Any]]:
        """Migrate individual status file to StateManager."""
        try:
            case_dir = self.base_path / case_id
            individual_status_file = case_dir / "case_status.json"
            
            if not individual_status_file.exists():
                return None
            
            with open(individual_status_file, 'r') as f:
                individual_data = json.load(f)
            
            # Create Case model from individual file data
            case = Case(
                case_id=case_id,
                status=individual_data.get("status", "NEW"),
                current_task=individual_data.get("current_task"),
                last_updated=datetime.now()  # Will be updated by the model
            )
            
            # Save to StateManager
            self.state_manager.save(case)
            
            if self.logger:
                self.logger.info(f"Migrated individual status file for case {case_id} to StateManager")
            
            return {
                "status": case.status,
                "current_task": case.current_task,
                "last_updated": case.last_updated
            }
            
        except Exception as e:
            if self.logger:
                self.logger.warning(f"Failed to migrate individual status file for {case_id}: {e}")
            return None
    
    def scan_for_new_cases(self) -> List[str]:
        """Scan base directory for cases that need processing."""
        cases_to_process = []
        
        if self.logger:
            self.logger.info(f"Scanning directory: {self.base_path}")
        
        if not self.base_path.exists():
            if self.logger:
                self.logger.warning(f"Base path does not exist: {self.base_path}")
            return cases_to_process
        
        try:
            directories_found = list(self.base_path.iterdir())
            if self.logger:
                self.logger.info(f"Found {len(directories_found)} items in base path")
            
            for case_dir in directories_found:
                if not case_dir.is_dir():
                    continue
                
                case_id = case_dir.name
                
                if not self._validate_case_directory(case_id):
                    if self.logger:
                        self.logger.warning(f"Case directory validation failed: {case_id}")
                    continue
                
                # Check if case exists in StateManager
                case = self.get_case(case_id)
                
                if case is None:
                    # New case - check stability
                    last_mod_time = self._get_last_modified_time(case_dir)
                    is_stable = not (last_mod_time and (datetime.now().timestamp() - last_mod_time) < self.stability_period_seconds)
                    
                    # Try migration from individual status file
                    migrated_data = self._migrate_individual_status_file(case_id)
                    
                    if not is_stable and not migrated_data:
                        if self.logger:
                            self.logger.info(f"Case '{case_id}' is unstable and has no status file. Skipping for now.")
                        continue
                    
                    if self.logger:
                        self.logger.info(f"New case detected: {case_id}")
                    cases_to_process.append(case_id)
                    
                    # Create new case if not migrated
                    if not migrated_data:
                        current_hash = self._calculate_folder_hash(case_dir)
                        new_case = Case(case_id=case_id, folder_hash=current_hash)
                        self.state_manager.save(new_case)
                
                else:
                    # Existing case - check status
                    if case.status == "PROCESSING":
                        if self._is_case_stalled(case):
                            if self.logger:
                                self.logger.warning(f"Stalled case detected for resumption: {case_id} (task: {case.current_task})")
                            cases_to_process.append(case_id)
                    elif case.status == "FAILED":
                        if case.retry_count < self.max_retries:
                            if self.logger:
                                self.logger.info(f"Failed case {case_id} being queued for retry (attempt {case.retry_count + 1}/{self.max_retries})")
                            cases_to_process.append(case_id)
        
        except (OSError, IOError) as e:
            if self.logger:
                self.logger.error(f"Error scanning directory: {e}")
        
        if self.logger:
            self.logger.info(f"Case scan completed. Found {len(cases_to_process)} cases to process")
        return cases_to_process
    
    def get_case(self, case_id: str) -> Optional[Case]:
        """Get a case by ID."""
        return self.state_manager.get(case_id)
    
    def create_case(self, case_id: str, **kwargs) -> Case:
        """Create a new case."""
        case = Case(case_id=case_id, **kwargs)
        self.state_manager.save(case)
        return case
    
    def update_case(self, case_id: str, **kwargs) -> bool:
        """Update an existing case."""
        case = self.get_case(case_id)
        if not case:
            return False
        
        # Update case attributes
        for key, value in kwargs.items():
            if hasattr(case, key):
                setattr(case, key, value)
        
        # Update last_updated timestamp
        case.last_updated = datetime.now()
        
        # Save updated case
        self.state_manager.save(case)
        return True
    
    def start_case_processing(self, case_id: str, current_task: str) -> bool:
        """Start processing a case."""
        case = self.get_case(case_id)
        if not case:
            return False
        
        case.start_processing(current_task)
        self.state_manager.save(case)
        return True
    
    def complete_case(self, case_id: str) -> bool:
        """Mark a case as completed."""
        case = self.get_case(case_id)
        if not case:
            return False
        
        case.complete()
        self.state_manager.save(case)
        return True
    
    def fail_case(self, case_id: str, error_message: str) -> bool:
        """Mark a case as failed."""
        case = self.get_case(case_id)
        if not case:
            return False
        
        case.fail(error_message)
        self.state_manager.save(case)
        return True
    
    def reset_case(self, case_id: str) -> bool:
        """Reset a case to allow reprocessing."""
        case = self.get_case(case_id)
        if not case:
            return False
        
        case.reset()
        self.state_manager.save(case)
        return True
    
    def get_case_resumption_task(self, case_id: str) -> Optional[str]:
        """Get the task that a PROCESSING case should resume from."""
        case = self.get_case(case_id)
        if case and case.status == "PROCESSING":
            return case.current_task
        return None
    
    def get_cases_by_status(self, status: str) -> List[Case]:
        """Get all cases with specified status."""
        # This would need to be implemented in StateManager
        # For now, return empty list
        return []
    
    def get_processing_cases(self) -> List[Case]:
        """Get list of currently processing cases."""
        return self.get_cases_by_status("PROCESSING")
    
    def get_failed_cases(self) -> List[Case]:
        """Get list of failed cases that can be retried."""
        return self.get_cases_by_status("FAILED")
    
    def analyze_case_beam_count(self, case_id: str) -> int:
        """Analyze case directory to determine number of beams."""
        try:
            case_path = self.base_path / case_id
            
            if not case_path.exists():
                if self.logger:
                    self.logger.warning(f"Case directory not found: {case_id}")
                return 0
            
            # Count subdirectories within the case directory
            subdirectories = [item for item in case_path.iterdir() if item.is_dir()]
            beam_count = len(subdirectories)
            
            if self.logger:
                self.logger.info(f"Found {beam_count} beams (subdirectories) for case {case_id}")
            
            return beam_count
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error analyzing case {case_id}: {e}")
            return 0
    
    def cleanup_old_cases(self, days: int = 30) -> None:
        """Remove old case entries from StateManager."""
        cutoff_time = datetime.now().timestamp() - (days * 24 * 3600)
        # This would need to be implemented in StateManager
        pass
    
    def archive_old_cases(self, days: int = 30) -> None:
        """Archive old completed or failed cases."""
        # Get all cases - this would need to be implemented in StateManager
        # For now, skip implementation
        pass
    
    def recover_stale_cases(self, max_processing_hours: int = 1) -> Dict[str, Any]:
        """Recover cases that have been stuck in PROCESSING state for too long."""
        # This would need to be implemented with proper StateManager integration
        recovered_cases = []
        
        # Get all processing cases - would need StateManager method
        # For now, return empty result
        
        return {
            "recovered_cases": recovered_cases,
            "recovered_count": len(recovered_cases),
            "remaining_processing_cases": [],
            "remaining_processing_count": 0,
            "recovery_timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
    
    def get_case_info(self, case_id: str) -> Dict[str, Any]:
        """Get comprehensive information about a case."""
        case = self.get_case(case_id)
        if not case:
            return {"case_id": case_id, "exists": False}
        
        case_path = self.base_path / case_id
        
        info = case.to_dict()
        info.update({
            "case_path": str(case_path),
            "directory_exists": case_path.exists(),
            "beam_count": self.analyze_case_beam_count(case_id)
        })
        
        return info
    
    def validate_case(self, case_id: str) -> Dict[str, Any]:
        """Validate case integrity."""
        validation_result = {
            "case_id": case_id,
            "valid": False,
            "errors": [],
            "warnings": []
        }
        
        # Check if case exists in StateManager
        case = self.get_case(case_id)
        if not case:
            validation_result["errors"].append("Case not found in StateManager")
        
        # Check if directory exists
        if not self._validate_case_directory(case_id):
            validation_result["errors"].append("Case directory not found or inaccessible")
        
        # Check beam count
        beam_count = self.analyze_case_beam_count(case_id)
        if beam_count == 0:
            validation_result["warnings"].append("No beams found in case directory")
        
        validation_result["valid"] = len(validation_result["errors"]) == 0
        return validation_result