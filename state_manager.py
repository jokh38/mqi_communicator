import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime


class StateManager:
    """Centralized state management with thread-safe operations."""
    
    def __init__(self, status_file: str = "case_status.json", logger=None):
        """Initialize StateManager with thread-safe lock."""
        self.status_file = Path(status_file) if Path(status_file).is_absolute() else Path.cwd() / status_file
        self.logger = logger
        self._lock = threading.Lock()
        self._state = {}
        
        # Load initial state
        self._load_state()
    
    def _load_state(self) -> None:
        """Load state from JSON file."""
        if not self.status_file.exists():
            # Create empty status file
            with open(self.status_file, 'w', encoding='utf-8') as f:
                json.dump({}, f, indent=2)
            self._state = {}
            return
        
        try:
            with open(self.status_file, 'r', encoding='utf-8') as f:
                self._state = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            self._state = {}
    
    def _save_state(self) -> None:
        """Save state to JSON file using atomic write operation."""
        try:
            # Create temporary file in the same directory as the target file
            temp_dir = self.status_file.parent
            with tempfile.NamedTemporaryFile(
                mode='w', 
                encoding='utf-8', 
                dir=temp_dir, 
                suffix='.tmp', 
                delete=False
            ) as temp_file:
                # Write data to temporary file
                json.dump(self._state, temp_file, indent=2, ensure_ascii=False)
                temp_file.flush()
                os.fsync(temp_file.fileno())  # Force write to disk
                temp_name = temp_file.name
            
            # Atomically replace the original file
            os.replace(temp_name, str(self.status_file))
            
        except Exception as e:
            # Clean up temporary file if it exists
            try:
                if 'temp_name' in locals() and os.path.exists(temp_name):
                    os.unlink(temp_name)
            except OSError:
                pass
            raise e
    
    def get_case_status(self, case_id: str) -> Optional[Dict[str, Any]]:
        """Get status information for a specific case."""
        with self._lock:
            return self._state.get(case_id)
    
    def update_case_status(self, case_id: str, status: str, **kwargs) -> None:
        """Update case status with thread-safe operation."""
        with self._lock:
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            if case_id not in self._state:
                self._state[case_id] = {
                    "status": status,
                    "start_time": current_time,
                    "end_time": "",
                    "hash": kwargs.get("folder_hash", ""),
                    "gpu_allocation": kwargs.get("gpu_allocation", []),
                    "retry_count": kwargs.get("retry_count", 0),
                    "remote_path": kwargs.get("remote_path", ""),
                    "remote_pid": kwargs.get("remote_pid"),
                    "locked_gpus": kwargs.get("locked_gpus", []),
                    "last_completed_step": kwargs.get("last_completed_step", ""),
                    "current_task": kwargs.get("current_task"),
                    "last_updated": current_time
                }
            else:
                # Update existing case
                self._state[case_id]["status"] = status
                self._state[case_id]["last_updated"] = current_time
                
                # Update specific fields if provided
                for key, value in kwargs.items():
                    if key == "folder_hash":
                        self._state[case_id]["hash"] = value
                    elif key in ["gpu_allocation", "retry_count", "remote_path", "remote_pid", 
                               "locked_gpus", "last_completed_step"]:
                        if value is not None:
                            self._state[case_id][key] = value
                    elif key == "current_task":
                        if status == "PROCESSING" and value is not None:
                            self._state[case_id]["current_task"] = value
                        elif status in ["COMPLETED", "FAILED"]:
                            self._state[case_id]["current_task"] = None
                
                if status in ["COMPLETED", "FAILED"]:
                    self._state[case_id]["end_time"] = current_time
            
            self._save_state()
    
    def get_cases_by_status(self, status: str) -> list:
        """Get list of case IDs with specified status."""
        with self._lock:
            return [case_id for case_id, info in self._state.items() 
                    if info.get("status") == status]
    
    def get_all_cases(self) -> Dict[str, Any]:
        """Get complete state dictionary (thread-safe copy)."""
        with self._lock:
            return self._state.copy()
    
    def delete_case(self, case_id: str) -> bool:
        """Delete a case from state."""
        with self._lock:
            if case_id in self._state:
                del self._state[case_id]
                self._save_state()
                return True
            return False
    
    def case_exists(self, case_id: str) -> bool:
        """Check if case exists in state."""
        with self._lock:
            return case_id in self._state
    
    def reset_case_status(self, case_id: str) -> bool:
        """Reset case status to allow reprocessing."""
        with self._lock:
            if case_id in self._state:
                self._state[case_id]["status"] = "NEW"
                self._state[case_id]["retry_count"] = 0
                self._state[case_id]["end_time"] = ""
                self._state[case_id]["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self._save_state()
                return True
            return False
    
    def cleanup_old_cases(self, cutoff_timestamp: float) -> list:
        """Remove old case entries and return list of removed case IDs."""
        with self._lock:
            cases_to_remove = []
            
            for case_id, info in self._state.items():
                try:
                    if info.get("end_time"):
                        end_time = datetime.strptime(info["end_time"], "%Y-%m-%d %H:%M:%S")
                        if end_time.timestamp() < cutoff_timestamp:
                            cases_to_remove.append(case_id)
                except (ValueError, TypeError):
                    continue
            
            for case_id in cases_to_remove:
                del self._state[case_id]
            
            if cases_to_remove:
                self._save_state()
            
            return cases_to_remove