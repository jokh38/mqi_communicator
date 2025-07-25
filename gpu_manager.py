import subprocess
import re
import xml.etree.ElementTree as ET
import time
import filelock
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
                 memory_threshold: int = 1024, remote_executor: Optional['RemoteExecutor'] = None, logger=None):
        self.total_gpus = total_gpus
        self.reserved_gpus = reserved_gpus or []
        self.memory_threshold = memory_threshold  # MB
        self.allocated_gpus: List[int] = []
        self.remote_executor = remote_executor
        self.logger = logger
        self.lock_dir_base = "/var/lock"
        
        # Store active file locks
        self._gpu_locks: Dict[int, filelock.FileLock] = {}
        
        # XML cache for nvidia-smi optimization
        self._gpu_xml_cache = None
        self._cache_timestamp = 0
        self._cache_ttl = 5  # Cache for 5 seconds

    def _get_gpu_info_xml(self) -> Optional[ET.Element]:
        """
        Get GPU information in XML format from nvidia-smi with caching.
        This single call replaces multiple nvidia-smi invocations.
        """
        current_time = time.time()
        
        # Return cached data if still valid
        if (self._gpu_xml_cache is not None and 
            (current_time - self._cache_timestamp) < self._cache_ttl):
            return self._gpu_xml_cache
        
        try:
            cmd = ['nvidia-smi', '-q', '-x']
            
            if self.remote_executor:
                # Use remote executor for remote GPU server
                result = self.remote_executor.execute_command(' '.join(cmd))
                if result['exit_code'] != 0:
                    if self.logger:
                        self.logger.error(f"Remote nvidia-smi XML query failed: {result['stderr']}")
                    return None
                xml_output = result['stdout']
            else:
                # Use local subprocess for local execution
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                if result.returncode != 0:
                    if self.logger:
                        self.logger.error(f"Local nvidia-smi XML query failed: {result.stderr}")
                    return None
                xml_output = result.stdout
            
            # Parse XML
            root = ET.fromstring(xml_output)
            
            # Cache the result
            self._gpu_xml_cache = root
            self._cache_timestamp = current_time
            
            return root
            
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, 
                ET.ParseError, Exception) as e:
            if self.logger:
                self.logger.error(f"Failed to get GPU XML info: {e}")
            return None

    def get_gpu_info(self) -> List[Dict[str, Any]]:
        """Get detailed GPU information using cached XML data."""
        try:
            root = self._get_gpu_info_xml()
            if root is None:
                return []
            
            gpu_info = []
            
            # Parse GPU information from XML
            for gpu in root.findall('gpu'):
                gpu_id_elem = gpu.find('minor_number')
                if gpu_id_elem is None:
                    continue
                
                gpu_id = int(gpu_id_elem.text)
                
                # Get memory information
                memory = gpu.find('fb_memory_usage')
                if memory is not None:
                    total_elem = memory.find('total')
                    used_elem = memory.find('used')
                    
                    if total_elem is not None and used_elem is not None:
                        # Convert from MiB to MB for consistency
                        memory_total = int(float(total_elem.text.split()[0]))
                        memory_used = int(float(used_elem.text.split()[0]))
                        memory_free = memory_total - memory_used
                    else:
                        memory_total = memory_used = memory_free = 0
                else:
                    memory_total = memory_used = memory_free = 0
                
                # Get GPU name
                name_elem = gpu.find('product_name')
                gpu_name = name_elem.text if name_elem is not None else "Unknown"
                
                # Get processes from cached XML
                processes = self._get_gpu_processes_from_xml(gpu_id, gpu)
                
                gpu_info.append({
                    'gpu_id': gpu_id,
                    'memory_total': memory_total,
                    'memory_used': memory_used,
                    'memory_free': memory_free,
                    'gpu_name': gpu_name,
                    'processes': processes
                })
            
            return gpu_info
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error parsing GPU XML info: {e}")
            return []

    def _get_gpu_processes_from_xml(self, gpu_id: int, gpu_elem: ET.Element) -> List[Dict[str, Any]]:
        """Extract GPU processes from XML element."""
        try:
            processes = []
            processes_elem = gpu_elem.find('processes')
            
            if processes_elem is not None:
                for process in processes_elem.findall('process_info'):
                    pid_elem = process.find('pid')
                    name_elem = process.find('process_name')
                    memory_elem = process.find('used_memory')
                    
                    if pid_elem is not None and name_elem is not None:
                        memory_used = 0
                        if memory_elem is not None and memory_elem.text:
                            try:
                                # Parse memory usage (format: "X MiB")
                                memory_used = int(float(memory_elem.text.split()[0]))
                            except (ValueError, IndexError):
                                memory_used = 0
                        
                        processes.append({
                            'gpu_name': f"GPU {gpu_id}",
                            'pid': int(pid_elem.text),
                            'process_name': name_elem.text,
                            'used_memory': memory_used
                        })
            
            return processes
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error parsing GPU processes from XML: {e}")
            return []

    def _get_gpu_processes(self, gpu_id: int) -> List[Dict[str, Any]]:
        """Get processes running on specific GPU using cached XML data."""
        try:
            root = self._get_gpu_info_xml()
            if root is None:
                return []
            
            # Find the specific GPU in the XML
            for gpu in root.findall('gpu'):
                gpu_id_elem = gpu.find('minor_number')
                if gpu_id_elem is not None and int(gpu_id_elem.text) == gpu_id:
                    return self._get_gpu_processes_from_xml(gpu_id, gpu)
            
            return []
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error getting GPU processes for GPU {gpu_id}: {e}")
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
                            self.logger.info(f"Successfully killed zombie process {pid} on remote server")
                    except Exception as e:
                        self.logger.error(f"Failed to kill zombie process {pid}: {e}")
            
            except Exception as e:
                self.logger.error(f"Error checking process {process['pid']}: {e}")
                continue
        
        return zombie_processes

    def acquire_gpu_lock(self, gpu_id: int) -> bool:
        """Atomically acquire lock for a specific GPU using filelock."""
        try:
            # If already locked by this instance, return True
            if gpu_id in self._gpu_locks:
                return True
            
            lock_file_path = f"{self.lock_dir_base}/gpu_{gpu_id}.lock"
            lock = filelock.FileLock(lock_file_path, timeout=0.1)
            
            try:
                # Try to acquire lock with minimal timeout
                lock.acquire(timeout=0.1)
                
                # Store the lock object for later release
                self._gpu_locks[gpu_id] = lock
                
                if self.logger:
                    self.logger.info(f"Successfully acquired atomic lock for GPU {gpu_id}")
                return True
                
            except filelock.Timeout:
                if self.logger:
                    self.logger.debug(f"Failed to acquire lock for GPU {gpu_id} - already locked")
                return False
                
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error acquiring GPU lock for GPU {gpu_id}: {e}")
            return False

    def release_gpu_lock(self, gpu_id: int) -> bool:
        """Release lock for a specific GPU using filelock."""
        try:
            # If not locked by this instance, return True (nothing to release)
            if gpu_id not in self._gpu_locks:
                if self.logger:
                    self.logger.debug(f"No lock to release for GPU {gpu_id}")
                return True
            
            # Get the lock object and release it
            lock = self._gpu_locks[gpu_id]
            lock.release()
            
            # Remove from our tracking dict
            del self._gpu_locks[gpu_id]
            
            if self.logger:
                self.logger.info(f"Successfully released atomic lock for GPU {gpu_id}")
            return True
                
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error releasing GPU lock for GPU {gpu_id}: {e}")
            return False

    def is_gpu_locked(self, gpu_id: int) -> bool:
        """Check if a specific GPU is locked using filelock."""
        try:
            # If we have this GPU locked, return True
            if gpu_id in self._gpu_locks:
                return True
            
            # Try to acquire lock briefly to test availability
            lock_file_path = f"{self.lock_dir_base}/gpu_{gpu_id}.lock"
            lock = filelock.FileLock(lock_file_path, timeout=0.01)
            
            try:
                lock.acquire(timeout=0.01)
                # If we can acquire it, it's not locked - release immediately
                lock.release()
                return False
            except filelock.Timeout:
                # If we can't acquire it, it's locked
                return True
                
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error checking GPU lock for GPU {gpu_id}: {e}")
            return True  # Assume locked if we can't check

    def get_available_gpus_with_locking(self) -> List[int]:
        """Get list of available GPU IDs considering atomic locks."""
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
            
            # Skip locked GPUs
            if self.is_gpu_locked(gpu_id):
                continue
            
            # Check if GPU has free memory above threshold
            if gpu['memory_free'] >= self.memory_threshold:
                # Check if GPU has no or minimal processes
                if len(gpu['processes']) == 0:
                    available_gpus.append(gpu_id)
        
        return sorted(available_gpus)

    def allocate_gpu_with_lock(self, gpu_id: int) -> bool:
        """Atomically allocate a specific GPU with locking."""
        try:
            # First check if GPU is available (without lock check)
            if not self.is_gpu_available(gpu_id):
                self.logger.debug(f"GPU {gpu_id} not available for allocation")
                return False
            
            # Try to acquire lock atomically
            if not self.acquire_gpu_lock(gpu_id):
                self.logger.debug(f"Failed to acquire lock for GPU {gpu_id}")
                return False
            
            # Double-check availability after acquiring lock
            if not self.is_gpu_available(gpu_id):
                self.logger.warning(f"GPU {gpu_id} became unavailable after lock acquisition")
                self.release_gpu_lock(gpu_id)
                return False
            
            # Add to allocated list
            if gpu_id not in self.allocated_gpus:
                self.allocated_gpus.append(gpu_id)
            
            self.logger.info(f"Successfully allocated GPU {gpu_id} with lock")
            return True
            
        except Exception as e:
            self.logger.error(f"Error allocating GPU {gpu_id} with lock: {e}")
            # Try to release lock if something went wrong
            try:
                self.release_gpu_lock(gpu_id)
            except:
                pass
            return False

    def release_gpu_with_lock(self, gpu_id: int) -> bool:
        """Release a GPU and its lock."""
        try:
            # Remove from allocated list
            if gpu_id in self.allocated_gpus:
                self.allocated_gpus.remove(gpu_id)
            
            # Release the lock
            success = self.release_gpu_lock(gpu_id)
            
            if success:
                self.logger.info(f"Successfully released GPU {gpu_id} with lock")
            else:
                self.logger.warning(f"Failed to release lock for GPU {gpu_id}")
            
            return success
            
        except Exception as e:
            self.logger.error(f"Error releasing GPU {gpu_id} with lock: {e}")
            return False

    def cleanup_stale_locks(self) -> List[int]:
        """Clean up stale GPU locks using filelock (should only be called during recovery)."""
        cleaned_gpus = []
        
        try:
            import os
            import glob
            
            # Find all GPU lock files
            lock_pattern = f"{self.lock_dir_base}/gpu_*.lock"
            lock_files = glob.glob(lock_pattern)
            
            for lock_file in lock_files:
                try:
                    # Extract GPU ID from filename
                    filename = os.path.basename(lock_file)
                    gpu_id = int(filename.replace('gpu_', '').replace('.lock', ''))
                    
                    # Try to force release the lock by removing the file
                    if os.path.exists(lock_file):
                        try:
                            os.remove(lock_file)
                            cleaned_gpus.append(gpu_id)
                            if self.logger:
                                self.logger.info(f"Cleaned up stale lock file for GPU {gpu_id}")
                        except OSError as e:
                            if self.logger:
                                self.logger.warning(f"Failed to clean up stale lock for GPU {gpu_id}: {e}")
                    
                    # Also remove from our internal tracking if present
                    if gpu_id in self._gpu_locks:
                        del self._gpu_locks[gpu_id]
                            
                except (ValueError, IndexError) as e:
                    if self.logger:
                        self.logger.warning(f"Could not parse GPU ID from lock file {lock_file}: {e}")
                    continue
                        
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error cleaning up stale locks: {e}")
        
        return cleaned_gpus

    def find_idle_gpu(self) -> int:
        """Find an idle GPU based on utilization and memory usage criteria.
        
        Returns:
            int: GPU ID of the selected idle GPU
            
        Raises:
            RuntimeError: If no idle GPU is available
        """
        try:
            # Execute nvidia-smi command to get GPU utilization and memory info
            cmd = [
                'nvidia-smi',
                '--query-gpu=index,utilization.gpu,memory.used,memory.total',
                '--format=csv,noheader,nounits'
            ]
            
            if self.remote_executor:
                # Use remote executor for remote GPU server
                result = self.remote_executor.execute_command(' '.join(cmd))
                if result['exit_code'] != 0:
                    raise RuntimeError(f"Failed to execute nvidia-smi on remote server: {result['stderr']}")
                output = result['stdout']
            else:
                # Use local subprocess for local execution
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                if result.returncode != 0:
                    raise RuntimeError(f"Failed to execute nvidia-smi locally: {result.stderr}")
                output = result.stdout
            
            # Parse CSV output
            idle_gpus = []
            for line in output.strip().split('\n'):
                if line.strip():
                    parts = [part.strip() for part in line.split(',')]
                    if len(parts) >= 4:
                        try:
                            gpu_id = int(parts[0])
                            gpu_utilization = float(parts[1])
                            memory_used = int(parts[2])
                            memory_total = int(parts[3])
                            
                            # Skip reserved GPUs
                            if gpu_id in self.reserved_gpus:
                                continue
                            
                            # Check idle criteria: GPU utilization < 10% and memory usage < 20%
                            memory_usage_percent = (memory_used / memory_total) * 100
                            
                            if gpu_utilization < 10.0 and memory_usage_percent < 20.0:
                                idle_gpus.append({
                                    'gpu_id': gpu_id,
                                    'memory_used': memory_used,
                                    'gpu_utilization': gpu_utilization,
                                    'memory_usage_percent': memory_usage_percent
                                })
                        except (ValueError, IndexError):
                            continue
            
            if not idle_gpus:
                raise RuntimeError("No idle GPU available.")
            
            # Select GPU with minimum memory usage
            selected_gpu = min(idle_gpus, key=lambda x: x['memory_used'])
            
            if self.logger:
                self.logger.info(f"Selected idle GPU {selected_gpu['gpu_id']} (utilization: {selected_gpu['gpu_utilization']}%, memory usage: {selected_gpu['memory_usage_percent']:.1f}%)")
            
            return selected_gpu['gpu_id']
            
        except subprocess.TimeoutExpired:
            raise RuntimeError("nvidia-smi command timed out")
        except Exception as e:
            if "No idle GPU available" in str(e):
                raise
            raise RuntimeError(f"Error finding idle GPU: {e}")

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