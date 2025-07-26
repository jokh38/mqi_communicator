from abc import ABC, abstractmethod
from typing import Dict, Any


class BaseResource(ABC):
    """Abstract base class for system resources."""
    
    def __init__(self, resource_id: str, resource_type: str):
        self.resource_id = resource_id
        self.resource_type = resource_type
        self.is_allocated = False
        self.allocated_to = None
    
    @abstractmethod
    def check_availability(self) -> bool:
        """Check if resource is available for allocation."""
        pass
    
    @abstractmethod
    def acquire(self, requester_id: str = None) -> bool:
        """Acquire/allocate the resource."""
        pass
    
    @abstractmethod
    def release(self) -> bool:
        """Release/deallocate the resource."""
        pass
    
    @abstractmethod
    def get_status(self) -> Dict[str, Any]:
        """Get detailed status information about the resource."""
        pass
    
    def get_id(self) -> str:
        """Get resource ID."""
        return self.resource_id
    
    def get_type(self) -> str:
        """Get resource type."""
        return self.resource_type
    
    def is_available(self) -> bool:
        """Check if resource is currently available."""
        return not self.is_allocated and self.check_availability()
    
    def get_allocated_to(self) -> str:
        """Get ID of current allocation holder."""
        return self.allocated_to