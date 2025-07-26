import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Dict, Any, Optional
from abc import ABC, abstractmethod


class StatefulObject(ABC):
    """Abstract base class for stateful objects."""
    
    def __init__(self, obj_id: str):
        self.obj_id = obj_id
        self.status = "NEW"
        self.last_updated = None
    
    @abstractmethod
    def to_dict(self) -> Dict[str, Any]:
        """Convert object to dictionary for storage."""
        pass
    
    @classmethod
    @abstractmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'StatefulObject':
        """Create object from dictionary."""
        pass
    
    def _transition_to(self, new_status: str) -> None:
        """Transition object to new status."""
        from datetime import datetime
        self.status = new_status
        self.last_updated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class StateManager:
    """Simple repository for managing StatefulObject instances."""
    
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
    
    def save(self, obj: StatefulObject) -> None:
        """Save a StatefulObject instance."""
        with self._lock:
            self._state[obj.obj_id] = obj.to_dict()
            self._save_state()
    
    def get(self, obj_id: str) -> Optional[Dict[str, Any]]:
        """Get object data by ID."""
        with self._lock:
            return self._state.get(obj_id)
    
    def delete(self, obj_id: str) -> bool:
        """Delete object by ID."""
        with self._lock:
            if obj_id in self._state:
                del self._state[obj_id]
                self._save_state()
                return True
            return False
    
    def exists(self, obj_id: str) -> bool:
        """Check if object exists."""
        with self._lock:
            return obj_id in self._state
    
    def get_all(self) -> Dict[str, Any]:
        """Get all objects (thread-safe copy)."""
        with self._lock:
            return self._state.copy()
    
    def get_by_status(self, status: str) -> list:
        """Get list of object IDs with specified status."""
        with self._lock:
            return [obj_id for obj_id, info in self._state.items() 
                    if info.get("status") == status]
    
    def cleanup_old_objects(self, cutoff_timestamp: float) -> list:
        """Remove old object entries and return list of removed object IDs."""
        with self._lock:
            objects_to_remove = []
            
            for obj_id, info in self._state.items():
                try:
                    if info.get("end_time"):
                        from datetime import datetime
                        end_time = datetime.strptime(info["end_time"], "%Y-%m-%d %H:%M:%S")
                        if end_time.timestamp() < cutoff_timestamp:
                            objects_to_remove.append(obj_id)
                except (ValueError, TypeError):
                    continue
            
            for obj_id in objects_to_remove:
                del self._state[obj_id]
            
            if objects_to_remove:
                self._save_state()
            
            return objects_to_remove