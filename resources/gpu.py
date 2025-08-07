import subprocess
import xml.etree.ElementTree as ET
import time
import filelock
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from .base import BaseResource


@dataclass
class GPUInfo:
    gpu_id: int
    memory_total: int
    memory_used: int
    memory_free: int
    gpu_name: str
    processes: List[Dict[str, Any]]


class GPUResource(BaseResource):
    """Represents a single GPU resource."""
    
    def __init__(self, gpu_id: int, memory_threshold: int = 1024, 
                 remote_executor=None, logger=None):
        super().__init__(f"gpu_{gpu_id}", "GPU")
        self.gpu_id = gpu_id
        self.memory_threshold = memory_threshold  # MB
        self.remote_executor = remote_executor
        self.logger = logger
        self.lock_dir_base = "/var/lock"
        self._file_lock: Optional[filelock.FileLock] = None
        
        # Cache for GPU info
        self._gpu_info_cache: Optional[GPUInfo] = None
        self._cache_timestamp = 0
        self._cache_ttl = 5  # Cache for 5 seconds
    
    def check_availability(self) -> bool:
        """Check if GPU is available based on memory usage and process count."""
        try:
            gpu_info = self._get_gpu_info()
            if not gpu_info:
                return False
            
            # Check memory threshold
            if gpu_info.memory_used > self.memory_threshold:
                return False
            
            # Check for heavy processes (exclude lightweight background processes)
            heavy_processes = self._filter_heavy_processes(gpu_info.processes)
            if len(heavy_processes) > 0:
                return False
            
            return True
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error checking GPU {self.gpu_id} availability: {e}")
            return False
    
    def acquire(self, requester_id: str = None) -> bool:
        """Acquire the GPU with file locking."""
        try:
            if self.is_allocated:
                return False
            
            # Create file lock
            lock_file = f"{self.lock_dir_base}/gpu_{self.gpu_id}.lock"
            self._file_lock = filelock.FileLock(lock_file, timeout=1)
            
            try:
                self._file_lock.acquire()
                self.is_allocated = True
                self.allocated_to = requester_id
                
                if self.logger:
                    self.logger.info(f"GPU {self.gpu_id} acquired by {requester_id}")
                return True
                
            except filelock.Timeout:
                if self.logger:
                    self.logger.warning(f"GPU {self.gpu_id} is locked by another process")
                return False
                
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error acquiring GPU {self.gpu_id}: {e}")
            return False
    
    def release(self) -> bool:
        """Release the GPU and file lock."""
        try:
            if self._file_lock and self._file_lock.is_locked:
                self._file_lock.release()
                self._file_lock = None
            
            self.is_allocated = False
            allocated_to = self.allocated_to
            self.allocated_to = None
            
            if self.logger:
                self.logger.info(f"GPU {self.gpu_id} released from {allocated_to}")
            return True
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error releasing GPU {self.gpu_id}: {e}")
            return False
    
    def get_status(self) -> Dict[str, Any]:
        """Get detailed GPU status information."""
        gpu_info = self._get_gpu_info()
        
        status = {
            "resource_id": self.resource_id,
            "resource_type": self.resource_type,
            "gpu_id": self.gpu_id,
            "is_allocated": self.is_allocated,
            "allocated_to": self.allocated_to,
            "is_available": self.is_available(),
            "memory_threshold": self.memory_threshold
        }
        
        if gpu_info:
            status.update({
                "memory_total": gpu_info.memory_total,
                "memory_used": gpu_info.memory_used,
                "memory_free": gpu_info.memory_free,
                "gpu_name": gpu_info.gpu_name,
                "process_count": len(gpu_info.processes),
                "heavy_process_count": len(self._filter_heavy_processes(gpu_info.processes))
            })
        
        return status
    
    def _get_gpu_info(self, timeout: int = 10) -> Optional[GPUInfo]:
        """Get GPU information with caching."""
        current_time = time.time()
        
        # Return cached data if still valid
        if (self._gpu_info_cache is not None and 
            (current_time - self._cache_timestamp) < self._cache_ttl):
            return self._gpu_info_cache
        
        try:
            if self.remote_executor:
                # Get GPU info from remote system
                xml_output = self.remote_executor.execute(
                    "nvidia-smi -q -x", capture_output=True, timeout=timeout
                )
                if xml_output and xml_output.exit_code == 0:
                    root = ET.fromstring(xml_output.stdout)
                    gpu_info = self._parse_gpu_xml(root)
                    if gpu_info:
                        self._gpu_info_cache = gpu_info
                        self._cache_timestamp = current_time
                        return gpu_info
                elif xml_output:
                    self.logger.error(f"nvidia-smi command failed with exit code {xml_output.exit_code}: {xml_output.stderr}")
            else:
                # Get GPU info from local system
                result = subprocess.run(
                    ["nvidia-smi", "-q", "-x"],
                    capture_output=True, text=True, timeout=10
                )
                if result.returncode == 0:
                    root = ET.fromstring(result.stdout)
                    gpu_info = self._parse_gpu_xml(root)
                    if gpu_info:
                        self._gpu_info_cache = gpu_info
                        self._cache_timestamp = current_time
                        return gpu_info
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error getting GPU {self.gpu_id} info: {e}")
        
        return None
    
    def _parse_gpu_xml(self, root: ET.Element) -> Optional[GPUInfo]:
        """Parse GPU information from nvidia-smi XML output."""
        try:
            gpus = root.findall('gpu')
            if self.gpu_id >= len(gpus):
                return None
            
            gpu = gpus[self.gpu_id]
            
            # Parse memory information
            memory = gpu.find('fb_memory_usage')
            if memory is None:
                return None
            
            memory_total = int(memory.find('total').text.split()[0])
            memory_used = int(memory.find('used').text.split()[0])
            memory_free = int(memory.find('free').text.split()[0])
            
            # Parse GPU name
            gpu_name = gpu.find('product_name').text if gpu.find('product_name') is not None else "Unknown"
            
            # Parse processes
            processes = []
            processes_elem = gpu.find('processes')
            if processes_elem is not None:
                for process in processes_elem.findall('process_info'):
                    pid = process.find('pid').text if process.find('pid') is not None else "0"
                    process_name = process.find('process_name').text if process.find('process_name') is not None else "Unknown"
                    used_memory = process.find('used_memory').text if process.find('used_memory') is not None else "0 MiB"
                    
                    processes.append({
                        'pid': int(pid),
                        'process_name': process_name,
                        'used_memory': int(used_memory.split()[0]) if used_memory != "N/A" else 0
                    })
            
            return GPUInfo(
                gpu_id=self.gpu_id,
                memory_total=memory_total,
                memory_used=memory_used,
                memory_free=memory_free,
                gpu_name=gpu_name,
                processes=processes
            )
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error parsing GPU XML for GPU {self.gpu_id}: {e}")
            return None
    
    def _filter_heavy_processes(self, processes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Filter out lightweight background processes."""
        # Define lightweight process patterns
        lightweight_patterns = [
            'Xorg', 'gnome-shell', 'compiz', 'nvidia-settings',
            'nvidia-persistenced', 'nvidia-modeset'
        ]
        
        heavy_processes = []
        for process in processes:
            process_name = process.get('process_name', '').lower()
            used_memory = process.get('used_memory', 0)
            
            # Skip if it's a known lightweight process with low memory usage
            is_lightweight = any(pattern.lower() in process_name for pattern in lightweight_patterns)
            if is_lightweight and used_memory < 100:  # Less than 100MB
                continue
            
            heavy_processes.append(process)
        
        return heavy_processes
    
    def get_gpu_id(self) -> int:
        """Get the GPU ID."""
        return self.gpu_id