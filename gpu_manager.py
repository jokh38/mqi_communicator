import subprocess
import re
import logging
from typing import List, Dict, Optional, Any, TYPE_CHECKING
from dataclasses import dataclass

if TYPE_CHECKING:
    from remote_executor import RemoteExecutor


@dataclass
class GPUInfo:
    gpu_id: int
    memory_total: int
    memory_used: int
    memory_free: int
    gpu_name: str
    processes: List[Dict[str, Any]]


class GPUManager:
    def __init__(self, total_gpus: int = 8, reserved_gpus: List[int] = None, 
                 memory_threshold: int = 1024, remote_executor: Optional['RemoteExecutor'] = None):
        self.total_gpus = total_gpus
        self.reserved_gpus = reserved_gpus or []
        self.memory_threshold = memory_threshold  # MB
        self.allocated_gpus: List[int] = []
        self.remote_executor = remote_executor

    def get_gpu_info(self) -> List[Dict[str, Any]]:
        """Get detailed GPU information using nvidia-smi."""
        try:
            cmd = [
                'nvidia-smi',
                '--query-gpu=index,memory.total,memory.used,name',
                '--format=csv,noheader,nounits'
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            
            if result.returncode != 0:
                return []
            
            gpu_info = []
            for line in result.stdout.strip().split('\n'):
                if line.strip():
                    parts = [part.strip() for part in line.split(',')]
                    if len(parts) >= 4:
                        gpu_info.append({
                            'gpu_id': int(parts[0]),
                            'memory_total': int(parts[1]),
                            'memory_used': int(parts[2]),
                            'memory_free': int(parts[1]) - int(parts[2]),
                            'gpu_name': parts[3],
                            'processes': self._get_gpu_processes(int(parts[0]))
                        })
            
            return gpu_info
            
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, FileNotFoundError):
            return []

    def _get_gpu_processes(self, gpu_id: int) -> List[Dict[str, Any]]:
        """Get processes running on specific GPU."""
        try:
            cmd = [
                'nvidia-smi',
                '--query-compute-apps=gpu_name,pid,process_name,used_memory',
                '--format=csv,noheader,nounits',
                f'--id={gpu_id}'
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            
            if result.returncode != 0:
                return []
            
            processes = []
            for line in result.stdout.strip().split('\n'):
                if line.strip() and 'No running processes found' not in line:
                    parts = [part.strip() for part in line.split(',')]
                    if len(parts) >= 4:
                        processes.append({
                            'gpu_name': parts[0],
                            'pid': int(parts[1]),
                            'process_name': parts[2],
                            'used_memory': int(parts[3])
                        })
            
            return processes
            
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, ValueError):
            return []

    def get_available_gpus(self) -> List[int]:
        """Get list of available GPU IDs (not reserved, not busy)."""
        gpu_info = self.get_gpu_info()
        available_gpus = []
        
        for gpu in gpu_info:
            gpu_id = gpu['gpu_id']
            
            # Skip reserved GPUs
            if gpu_id in self.reserved_gpus:
                continue
            
            # Skip already allocated GPUs
            if gpu_id in self.allocated_gpus:
                continue
            
            # Check if GPU has free memory above threshold
            if gpu['memory_free'] >= self.memory_threshold:
                # Check if GPU has no or minimal processes
                if len(gpu['processes']) == 0:
                    available_gpus.append(gpu_id)
        
        return sorted(available_gpus)

    def check_gpu_memory(self, gpu_id: int) -> Dict[str, Any]:
        """Check memory usage for specific GPU."""
        gpu_info = self.get_gpu_info()
        
        for gpu in gpu_info:
            if gpu['gpu_id'] == gpu_id:
                return {
                    'gpu_id': gpu_id,
                    'memory_total': gpu['memory_total'],
                    'memory_used': gpu['memory_used'],
                    'memory_free': gpu['memory_free'],
                    'memory_usage_percent': (gpu['memory_used'] / gpu['memory_total']) * 100
                }
        
        return {}

    def is_gpu_available(self, gpu_id: int) -> bool:
        """Check if specific GPU is available for use."""
        # Check if GPU is reserved
        if gpu_id in self.reserved_gpus:
            return False
        
        # Check if GPU is already allocated
        if gpu_id in self.allocated_gpus:
            return False
        
        # Check GPU memory and processes
        gpu_info = self.get_gpu_info()
        for gpu in gpu_info:
            if gpu['gpu_id'] == gpu_id:
                return (gpu['memory_free'] >= self.memory_threshold and 
                        len(gpu['processes']) == 0)
        
        return False

    def monitor_gpu_processes(self) -> List[Dict[str, Any]]:
        """Monitor all GPU processes across all GPUs."""
        all_processes = []
        gpu_info = self.get_gpu_info()
        
        for gpu in gpu_info:
            for process in gpu['processes']:
                process_info = process.copy()
                process_info['gpu_id'] = gpu['gpu_id']
                all_processes.append(process_info)
        
        return all_processes

    def get_gpu_utilization(self, gpu_id: int) -> float:
        """Get memory utilization percentage for specific GPU."""
        memory_info = self.check_gpu_memory(gpu_id)
        
        if memory_info:
            return memory_info['memory_usage_percent']
        
        return 0.0

    def allocate_gpus(self, count: int) -> List[int]:
        """Allocate specified number of GPUs."""
        available_gpus = self.get_available_gpus()
        
        # Allocate up to requested count or all available
        to_allocate = min(count, len(available_gpus))
        allocated = available_gpus[:to_allocate]
        
        # Mark as allocated
        self.allocated_gpus.extend(allocated)
        
        return allocated

    def release_gpus(self, gpu_ids: List[int]) -> None:
        """Release specified GPUs from allocation."""
        for gpu_id in gpu_ids:
            if gpu_id in self.allocated_gpus:
                self.allocated_gpus.remove(gpu_id)

    def cleanup_zombie_processes(self) -> List[Dict[str, Any]]:
        """Identify and potentially clean up zombie GPU processes on remote server."""
        processes = self.monitor_gpu_processes()
        zombie_processes = []
        
        if not self.remote_executor:
            # Fall back to local checking if no remote executor available
            for process in processes:
                try:
                    result = subprocess.run(['ps', '-p', str(process['pid'])], 
                                           capture_output=True, text=True)
                    if result.returncode != 0:
                        zombie_processes.append(process)
                except (subprocess.CalledProcessError, FileNotFoundError):
                    continue
            return zombie_processes
        
        # Check processes on remote server
        for process in processes:
            try:
                # Use remote executor to check if process exists on remote server
                pid = process['pid']
                result = self.remote_executor.execute_command(f"ps -p {pid}")
                
                if not result.success or result.exit_code != 0:
                    # Process not found on remote server, might be zombie
                    zombie_processes.append(process)
                    
                    # Optionally try to kill zombie process
                    try:
                        kill_result = self.remote_executor.execute_command(f"kill -9 {pid}")
                        if kill_result.success:
                            logging.info(f"Successfully killed zombie process {pid} on remote server")
                    except Exception as e:
                        logging.error(f"Failed to kill zombie process {pid}: {e}")
            
            except Exception as e:
                logging.error(f"Error checking process {process['pid']}: {e}")
                continue
        
        return zombie_processes

    def get_gpu_status_summary(self) -> Dict[str, Any]:
        """Get comprehensive GPU status summary."""
        gpu_info = self.get_gpu_info()
        
        return {
            'total_gpus': self.total_gpus,
            'reserved_gpus': self.reserved_gpus,
            'allocated_gpus': self.allocated_gpus,
            'available_gpus': self.get_available_gpus(),
            'gpu_details': gpu_info,
            'total_processes': len(self.monitor_gpu_processes())
        }

    def wait_for_gpu_availability(self, count: int, timeout: int = 300) -> List[int]:
        """Wait for specified number of GPUs to become available."""
        import time
        
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            available_gpus = self.get_available_gpus()
            
            if len(available_gpus) >= count:
                return self.allocate_gpus(count)
            
            time.sleep(self.monitoring_interval)
        
        # Return whatever is available
        return self.allocate_gpus(count)

    @property
    def monitoring_interval(self) -> int:
        """Default monitoring interval in seconds."""
        return 5