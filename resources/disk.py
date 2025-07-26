import shutil
from pathlib import Path
from typing import Dict, Any
from .base import BaseResource


class DiskResource(BaseResource):
    """Represents disk space at a specific path."""
    
    def __init__(self, resource_id: str, path: str, min_free_space_mb: int = 1024, logger=None):
        super().__init__(resource_id, "DISK")
        self.path = Path(path)
        self.min_free_space_mb = min_free_space_mb
        self.logger = logger
        self.reserved_space_mb = 0
    
    def check_availability(self) -> bool:
        """Check if disk has sufficient free space."""
        try:
            if not self.path.exists():
                return False
            
            # Get disk usage
            total, used, free = shutil.disk_usage(self.path)
            free_mb = free / (1024 * 1024)
            
            # Check if free space minus reserved space meets minimum requirement
            available_space = free_mb - self.reserved_space_mb
            return available_space >= self.min_free_space_mb
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error checking disk availability for {self.path}: {e}")
            return False
    
    def acquire(self, requester_id: str = None) -> bool:
        """Reserve disk space (logical allocation)."""
        try:
            if not self.check_availability():
                return False
            
            self.is_allocated = True
            self.allocated_to = requester_id
            # Reserve the minimum required space
            self.reserved_space_mb += self.min_free_space_mb
            
            if self.logger:
                self.logger.info(f"Disk space reserved at {self.path} for {requester_id}")
            return True
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error acquiring disk space at {self.path}: {e}")
            return False
    
    def release(self) -> bool:
        """Release reserved disk space."""
        try:
            if self.is_allocated:
                # Release the reserved space
                self.reserved_space_mb = max(0, self.reserved_space_mb - self.min_free_space_mb)
                
                allocated_to = self.allocated_to
                self.is_allocated = False
                self.allocated_to = None
                
                if self.logger:
                    self.logger.info(f"Disk space released at {self.path} from {allocated_to}")
            
            return True
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error releasing disk space at {self.path}: {e}")
            return False
    
    def get_status(self) -> Dict[str, Any]:
        """Get detailed disk status information."""
        status = {
            "resource_id": self.resource_id,
            "resource_type": self.resource_type,
            "path": str(self.path),
            "is_allocated": self.is_allocated,
            "allocated_to": self.allocated_to,
            "is_available": self.is_available(),
            "min_free_space_mb": self.min_free_space_mb,
            "reserved_space_mb": self.reserved_space_mb
        }
        
        try:
            if self.path.exists():
                total, used, free = shutil.disk_usage(self.path)
                status.update({
                    "total_space_mb": total / (1024 * 1024),
                    "used_space_mb": used / (1024 * 1024),
                    "free_space_mb": free / (1024 * 1024),
                    "available_space_mb": (free / (1024 * 1024)) - self.reserved_space_mb,
                    "path_exists": True
                })
            else:
                status.update({
                    "total_space_mb": 0,
                    "used_space_mb": 0,
                    "free_space_mb": 0,
                    "available_space_mb": 0,
                    "path_exists": False
                })
                
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error getting disk status for {self.path}: {e}")
            status.update({
                "error": str(e),
                "path_exists": False
            })
        
        return status
    
    def get_path(self) -> Path:
        """Get the disk path."""
        return self.path
    
    def set_min_free_space(self, min_free_space_mb: int) -> None:
        """Update minimum free space requirement."""
        self.min_free_space_mb = min_free_space_mb