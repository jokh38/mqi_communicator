"""
ResourceManager - Service for managing resource pools.

Merges functionality from gpu_manager.py and directory_manager.py
to provide unified resource management.
"""

from typing import List, Dict, Optional, Any, TYPE_CHECKING
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime

from resources.gpu import GPUResource
from resources.disk import DiskResource
from core.logging import Logger

if TYPE_CHECKING:
    from executors.remote import RemoteExecutor


@dataclass
class ResourceConfig:
    """Configuration for ResourceManager."""
    total_gpus: int = 8
    reserved_gpus: List[int] = None
    memory_threshold: int = 1024
    local_base: str = None
    remote_base: str = None
    output_base: str = None
    
    def __post_init__(self):
        if self.reserved_gpus is None:
            self.reserved_gpus = []


class ResourceManager:
    """Manages pools of resources (GPU, Disk) and coordinates their allocation."""
    
    def __init__(self, 
                 config: ResourceConfig,
                 remote_executor: Optional['RemoteExecutor'] = None,
                 sftp_manager=None,
                 logger: Optional[Logger] = None):
        
        self.config = config
        self.remote_executor = remote_executor
        self.sftp_manager = sftp_manager
        self.logger = logger
        
        self._initialize_gpu_resources()
        self._initialize_disk_resources()
        self._setup_directory_paths()
    
    def _initialize_gpu_resources(self) -> None:
        """Initialize GPU resource pool."""
        self.gpu_resources: Dict[int, GPUResource] = {}
        for gpu_id in range(self.config.total_gpus):
            if gpu_id in self.config.reserved_gpus:
                continue
            self.gpu_resources[gpu_id] = GPUResource(
                gpu_id=gpu_id,
                memory_threshold=self.config.memory_threshold,
                remote_executor=self.remote_executor,
                logger=self.logger
            )
    
    def _initialize_disk_resources(self) -> None:
        """Initialize disk resource pool."""
        self.disk_resources: Dict[str, DiskResource] = {}
        if self.config.local_base:
            self.disk_resources['local'] = DiskResource(
                resource_id='local',
                path=self.config.local_base,
                logger=self.logger
            )
        if self.config.output_base:
            self.disk_resources['output'] = DiskResource(
                resource_id='output', 
                path=self.config.output_base,
                logger=self.logger
            )
    
    def _setup_directory_paths(self) -> None:
        """Setup directory management paths."""
        self.local_base = Path(self.config.local_base) if self.config.local_base else None
        self.remote_base = self.config.remote_base
        self.output_base = Path(self.config.output_base) if self.config.output_base else None
    
    # GPU Resource Management
    def get_available_gpus(self) -> List[int]:
        """Get list of available GPU IDs."""
        available = []
        for gpu_id, gpu_resource in self.gpu_resources.items():
            if gpu_resource.check_availability():
                available.append(gpu_id)
        return sorted(available)
    
    def allocate_gpus(self, count: int) -> List[int]:
        """Allocate specified number of GPUs with atomic locking."""
        available_gpus = []
        allocated_gpus = []
        
        try:
            # Get available GPUs
            for gpu_id, gpu_resource in self.gpu_resources.items():
                if gpu_resource.check_availability():
                    available_gpus.append(gpu_id)
            
            # Try to allocate up to requested count
            for gpu_id in available_gpus[:count]:
                gpu_resource = self.gpu_resources[gpu_id]
                if gpu_resource.acquire():
                    allocated_gpus.append(gpu_id)
                else:
                    # If allocation fails, release already allocated GPUs
                    self.release_gpus(allocated_gpus)
                    return []
            
            if self.logger:
                self.logger.info(f"Allocated {len(allocated_gpus)} GPUs: {allocated_gpus}")
            
            return allocated_gpus
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error allocating GPUs: {e}")
            # Release any partially allocated GPUs
            self.release_gpus(allocated_gpus)
            return []
    
    def release_gpus(self, gpu_ids: List[int]) -> bool:
        """Release specified GPUs."""
        success = True
        for gpu_id in gpu_ids:
            if gpu_id in self.gpu_resources:
                if not self.gpu_resources[gpu_id].release():
                    success = False
        
        if self.logger:
            if success:
                self.logger.info(f"Released GPUs: {gpu_ids}")
            else:
                self.logger.warning(f"Some GPUs failed to release: {gpu_ids}")
        
        return success
    
    def get_gpu_status(self, gpu_id: int) -> Dict[str, Any]:
        """Get status of specific GPU."""
        if gpu_id not in self.gpu_resources:
            return {}
        
        return self.gpu_resources[gpu_id].get_status()
    
    def get_all_gpu_status(self) -> Dict[str, Any]:
        """Get comprehensive GPU status summary."""
        gpu_statuses = {}
        for gpu_id, gpu_resource in self.gpu_resources.items():
            gpu_statuses[gpu_id] = gpu_resource.get_status()
        
        return {
            'total_gpus': len(self.gpu_resources),
            'available_gpus': self.get_available_gpus(),
            'gpu_details': gpu_statuses
        }
    
    def find_idle_gpu(self) -> int:
        """Find an idle GPU based on utilization criteria."""
        for gpu_id, gpu_resource in self.gpu_resources.items():
            if gpu_resource.check_availability():
                # Additional idle check could be implemented here
                return gpu_id
        
        raise RuntimeError("No idle GPU available")
    
    def cleanup_stale_gpu_locks(self) -> List[int]:
        """Clean up stale GPU locks."""
        cleaned_gpus = []
        for _, gpu_resource in self.gpu_resources.items():
            # This would need to be implemented in GPUResource
            pass
        return cleaned_gpus
    
    # Disk Resource Management
    def get_disk_usage(self, location: str = 'local') -> Dict[str, Any]:
        """Get disk usage for specified location."""
        if location in self.disk_resources:
            return self.disk_resources[location].get_status()
        
        # Fallback for remote disk usage
        if location == 'remote' and self.remote_executor:
            return self._get_remote_disk_usage()
        
        return {}
    
    def _get_remote_disk_usage(self) -> Dict[str, Any]:
        """Get remote disk usage via remote executor."""
        try:
            if not self.remote_executor or not self.remote_base:
                return {"error": "No remote executor or remote base path available"}
            
            # Command to get disk usage of the remote base directory's filesystem
            command = "df -BG . | tail -n 1"
            result = self.remote_executor.execute(command, working_dir=self.remote_base)
            
            if not result.success:
                error_message = f"df command failed with exit code {result.exit_code}: {result.stderr}"
                if self.logger:
                    self.logger.error(error_message)
                return {"error": error_message}
            
            # Expected output format from 'df -BG .':
            # Filesystem     1G-blocks  Used Available Use% Mounted on
            # /dev/sda1           100G   50G       50G  50% /
            fields = result.stdout.split()
            if len(fields) < 6:
                error_message = f"Unexpected df output format: {result.stdout}"
                if self.logger:
                    self.logger.error(error_message)
                return {"error": error_message}

            try:
                # Parse values, removing 'G' suffix and converting to float
                total_gb = float(fields[1].rstrip('G'))
                used_gb = float(fields[2].rstrip('G'))
                available_gb = float(fields[3].rstrip('G'))

                # Ensure total_gb is not zero to avoid division by zero
                if total_gb == 0:
                    usage_percent = 0.0
                else:
                    usage_percent = round((used_gb / total_gb) * 100, 2)

                return {
                    "total_gb": total_gb,
                    "used_gb": used_gb,
                    "free_gb": available_gb,
                    "usage_percent": usage_percent,
                    "path": self.remote_base
                }
            except (ValueError, IndexError) as e:
                error_message = f"Failed to parse df output: {result.stdout}, error: {e}"
                if self.logger:
                    self.logger.error(error_message)
                return {"error": error_message}
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error getting remote disk usage: {e}")
            return {"error": str(e)}
    
    def check_disk_space(self, location: str, required_gb: float) -> bool:
        """Check if sufficient disk space is available."""
        usage = self.get_disk_usage(location)
        if 'error' in usage:
            return False
        
        return usage.get('free_gb', 0) >= required_gb
    
    # Directory Management (merged from DirectoryManager)
    def ensure_local_directory(self, directory_path: str) -> bool:
        """Ensure local directory exists."""
        try:
            path = Path(directory_path)
            if path.exists():
                return True
            
            path.mkdir(parents=True, exist_ok=True)
            if self.logger:
                self.logger.info(f"Created local directory: {directory_path}")
            return True
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to create local directory {directory_path}: {e}")
            return False
    
    def ensure_remote_directory(self, directory_path: str) -> bool:
        """Ensure remote directory exists."""
        try:
            if not self.remote_executor:
                if self.logger:
                    self.logger.error("Remote executor not available")
                return False
            
            if self.remote_executor.check_directory_exists(directory_path):
                return True
            
            if self.remote_executor.create_directory(directory_path):
                if self.logger:
                    self.logger.info(f"Created remote directory: {directory_path}")
                return True
            
            if self.logger:
                self.logger.error(f"Failed to create remote directory: {directory_path}")
            return False
                
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error ensuring remote directory {directory_path}: {e}")
            return False
    
    def create_case_workspace(self, case_id: str) -> bool:
        """Create workspace directories for a case."""
        try:
            # Create local directory
            if self.local_base:
                local_path = self.local_base / case_id
                if not self.ensure_local_directory(str(local_path)):
                    return False
            
            # Create remote directory
            if self.remote_base:
                remote_path = f"{self.remote_base}/{case_id}"
                if not self.ensure_remote_directory(remote_path):
                    return False
            
            if self.logger:
                self.logger.info(f"Created workspace for case: {case_id}")
            return True
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error creating workspace for case {case_id}: {e}")
            return False
    
    def get_case_local_path(self, case_id: str) -> Path:
        """Get local path for a case."""
        if not self.local_base:
            raise ValueError("Local base path not configured")
        return self.local_base / case_id
    
    def get_case_remote_path(self, case_id: str) -> str:
        """Get remote path for a case."""
        if not self.remote_base:
            raise ValueError("Remote base path not configured")
        return f"{self.remote_base}/{case_id}"
    
    def get_case_output_path(self, case_id: str, date: Optional[datetime] = None) -> Path:
        """Get output path for a case with monthly structure."""
        if not self.output_base:
            raise ValueError("Output base path not configured")
        
        if date is None:
            date = datetime.now()
        
        year = str(date.year)
        month = f"{date.month:02d}"
        
        return self.output_base / year / month / case_id
    
    def sync_directories(self, case_id: str, direction: str = "upload", status_display=None) -> bool:
        """Sync directories between local and remote."""
        try:
            if not self.sftp_manager:
                if self.logger:
                    self.logger.error("SFTP manager not available")
                return False
            
            if direction == "upload":
                if not self.create_case_workspace(case_id):
                    return False
                
                local_path = str(self.get_case_local_path(case_id))
                remote_path = self.get_case_remote_path(case_id)
                
                return self.sftp_manager.upload_directory(local_path, remote_path, status_display, case_id)
                
            if direction == "download":
                if self.output_base:
                    output_path = self.get_case_output_path(case_id)
                    if not self.ensure_local_directory(str(output_path)):
                        return False
                    
                    remote_path = self.get_case_remote_path(case_id)
                    
                    return self.sftp_manager.download_directory(remote_path, str(output_path), status_display, case_id)
                
            return False
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error syncing directories for case {case_id}: {e}")
            return False
    
    # Resource Monitoring and Health Checks
    def get_resource_health(self) -> Dict[str, Any]:
        """Get overall resource health status."""
        gpu_health = {
            'total_gpus': len(self.gpu_resources),
            'available_gpus': len(self.get_available_gpus()),
            'healthy': True
        }
        
        disk_health = {}
        for location, disk_resource in self.disk_resources.items():
            status = disk_resource.get_status()
            disk_health[location] = {
                'free_gb': status.get('free_gb', 0),
                'usage_percent': status.get('usage_percent', 100),
                'healthy': status.get('usage_percent', 100) < 90
            }
        
        return {
            'gpu_health': gpu_health,
            'disk_health': disk_health,
            'overall_healthy': gpu_health['healthy'] and all(d['healthy'] for d in disk_health.values())
        }
    
    def cleanup_resources(self) -> Dict[str, Any]:
        """Clean up stale resources and locks."""
        cleanup_results = {
            'gpu_locks_cleaned': 0,
            'disk_space_freed_gb': 0,
            'errors': []
        }
        
        try:
            # Clean up GPU locks
            cleaned_gpus = self.cleanup_stale_gpu_locks()
            cleanup_results['gpu_locks_cleaned'] = len(cleaned_gpus)
            
            # Additional cleanup logic can be added here
            
        except Exception as e:
            cleanup_results['errors'].append(str(e))
        
        return cleanup_results