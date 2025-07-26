from abc import ABC, abstractmethod
from datetime import datetime
from typing import Dict, Any


class StatefulObject(ABC):
    """Abstract base class for stateful objects that manage their own state."""
    
    def __init__(self, obj_id: str):
        self.obj_id = obj_id
        self.status = "NEW"
        self.last_updated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    def _transition_to(self, new_status: str) -> None:
        """Transition object to new status with timestamp update."""
        self.status = new_status
        self.last_updated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    @abstractmethod
    def to_dict(self) -> Dict[str, Any]:
        """Convert object to dictionary for storage."""
        pass
    
    @classmethod
    @abstractmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'StatefulObject':
        """Create object from dictionary."""
        pass
    
    def get_status(self) -> str:
        """Get current status."""
        return self.status
    
    def get_id(self) -> str:
        """Get object ID."""
        return self.obj_id