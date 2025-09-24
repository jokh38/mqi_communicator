# =====================================================================================
# Target File: src/infrastructure/process_manager.py
# Source Reference: src/display_process_manager.py and process management from main.py
# =====================================================================================
"""Manages process pools and subprocess execution for the application."""

import subprocess
import multiprocessing
import threading
import time
from typing import Dict, List, Optional, Callable, Any
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, Future

from src.infrastructure.logging_handler import StructuredLogger
from src.config.settings import ProcessingConfig
from src.domain.errors import ProcessingError

class ProcessManager:
    """Manages process pools and subprocess execution for the application."""
    
    def __init__(self, config: ProcessingConfig, logger: StructuredLogger):
        """Initialize the process manager with configuration.

        Args:
            config (ProcessingConfig): The processing configuration settings.
            logger (StructuredLogger): The logger for recording operations.
        """
        self.config = config
        self.logger = logger
        self._executor: Optional[ProcessPoolExecutor] = None
        self._active_processes: Dict[str, Future] = {}
        self._shutdown = False
    
    def start(self) -> None:
        """Start the process pool executor."""
        if self._executor is None:
            self._executor = ProcessPoolExecutor(
                max_workers=self.config.max_workers
            )
            self.logger.info("Process manager started", {
                "max_workers": self.config.max_workers
            })
    
    def shutdown(self, wait: bool = True) -> None:
        """Shutdown the process manager and clean up resources.

        Args:
            wait (bool, optional): Whether to wait for active processes to complete. Defaults to True.
        """
        self._shutdown = True
        
        if self._executor:
            self.logger.info("Shutting down process manager", {
                "active_processes": len(self._active_processes),
                "wait": wait
            })
            
            self._executor.shutdown(wait=wait)
            self._executor = None
            
            # Clear active processes
            self._active_processes.clear()
    
    def submit_case_processing(self, worker_func: Callable, case_id: str, 
                             case_path: Path, **kwargs) -> str:
        """Submit a case for processing in the worker pool.

        Args:
            worker_func (Callable): The worker function to execute.
            case_id (str): The case identifier.
            case_path (Path): The path to the case directory.
            \*\*kwargs: Additional arguments for the worker function.

        Raises:
            RuntimeError: If the process manager is not started or is shutting down.

        Returns:
            str: A process ID for tracking.
        """
        if not self._executor:
            raise RuntimeError("Process manager not started")
        
        if self._shutdown:
            raise RuntimeError("Process manager is shutting down")
        
        self.logger.info("Submitting case for processing", {
            "case_id": case_id,
            "case_path": str(case_path)
        })
        
        # Submit to process pool
        future = self._executor.submit(
            worker_func, case_id, case_path, **kwargs
        )
        
        # Track the process
        process_id = f"case_{case_id}_{int(time.time())}"
        self._active_processes[process_id] = future
        
        # Add completion callback
        future.add_done_callback(
            lambda f: self._process_completed(process_id, f)
        )
        
        return process_id
    
    def _process_completed(self, process_id: str, future: Future) -> None:
        """Handle process completion and cleanup.

        Args:
            process_id (str): The ID of the completed process.
            future (Future): The Future object representing the completed process.
        """
        try:
            result = future.result()
            self.logger.info("Process completed successfully", {
                "process_id": process_id,
                "result": result
            })
        except Exception as e:
            self.logger.error("Process failed", {
                "process_id": process_id,
                "error": str(e)
            })
        finally:
            # Remove from active processes
            self._active_processes.pop(process_id, None)
    
    def get_active_process_count(self) -> int:
        """Get the count of currently active processes.

        Returns:
            int: The number of active processes.
        """
        return len(self._active_processes)
    
    def is_process_active(self, process_id: str) -> bool:
        """Check if a specific process is still active.

        Args:
            process_id (str): The ID of the process to check.

        Returns:
            bool: True if the process is active, False otherwise.
        """
        return process_id in self._active_processes
    
    def wait_for_process(self, process_id: str, timeout: Optional[float] = None) -> Any:
        """Wait for a specific process to complete.

        Args:
            process_id (str): The process identifier.
            timeout (Optional[float], optional): The maximum time to wait (None for indefinite). Defaults to None.

        Raises:
            ValueError: If the process ID is not found.

        Returns:
            Any: The result of the process.
        """
        if process_id not in self._active_processes:
            raise ValueError(f"Process {process_id} not found")
        
        future = self._active_processes[process_id]
        return future.result(timeout=timeout)

class CommandExecutor:
    """Handles subprocess command execution with proper error handling and logging."""
    
    def __init__(self, logger: StructuredLogger, default_timeout: int = 300):
        """Initialize the command executor.

        Args:
            logger (StructuredLogger): The logger for recording operations.
            default_timeout (int, optional): The default timeout for commands in seconds. Defaults to 300.
        """
        self.logger = logger
        self.default_timeout = default_timeout
    
    def execute_command(self, command: List[str], cwd: Optional[Path] = None, 
                       timeout: Optional[int] = None, capture_output: bool = True,
                       env: Optional[Dict[str, str]] = None) -> subprocess.CompletedProcess:
        """Execute a command with proper error handling and logging.

        Args:
            command (List[str]): The command and arguments as a list.
            cwd (Optional[Path], optional): The working directory for the command. Defaults to None.
            timeout (Optional[int], optional): The command timeout (uses default if None). Defaults to None.
            capture_output (bool, optional): Whether to capture stdout/stderr. Defaults to True.
            env (Optional[Dict[str, str]], optional): Environment variables. Defaults to None.

        Raises:
            ProcessingError: If the command fails or times out.

        Returns:
            subprocess.CompletedProcess: A subprocess.CompletedProcess instance.
        """
        timeout = timeout or self.default_timeout
        
        self.logger.debug("Executing command", {
            "command": ' '.join(command),
            "cwd": str(cwd) if cwd else None,
            "timeout": timeout
        })
        
        try:
            result = subprocess.run(
                command,
                cwd=cwd,
                timeout=timeout,
                capture_output=capture_output,
                text=True,
                env=env,
                check=True
            )
            
            self.logger.debug("Command completed successfully", {
                "command": command[0],
                "return_code": result.returncode,
                "stdout_length": len(result.stdout) if result.stdout else 0,
                "stderr_length": len(result.stderr) if result.stderr else 0
            })
            
            return result
            
        except subprocess.TimeoutExpired as e:
            self.logger.error("Command timed out", {
                "command": ' '.join(command),
                "timeout": timeout,
                "cwd": str(cwd) if cwd else None
            })
            raise ProcessingError(f"Command timed out after {timeout}s: {' '.join(command)}")
            
        except subprocess.CalledProcessError as e:
            self.logger.error("Command failed", {
                "command": ' '.join(command),
                "return_code": e.returncode,
                "stdout": e.stdout,
                "stderr": e.stderr,
                "cwd": str(cwd) if cwd else None
            })
            raise ProcessingError(f"Command failed with code {e.returncode}: {' '.join(command)}")
    
    def execute_command_async(self, command: List[str], cwd: Optional[Path] = None,
                            timeout: Optional[int] = None, 
                            env: Optional[Dict[str, str]] = None) -> subprocess.Popen:
        """Execute a command asynchronously and return a Popen object.

        Args:
            command (List[str]): The command and arguments as a list.
            cwd (Optional[Path], optional): The working directory for the command. Defaults to None.
            timeout (Optional[int], optional): The command timeout (for documentation only). Defaults to None.
            env (Optional[Dict[str, str]], optional): Environment variables. Defaults to None.

        Returns:
            subprocess.Popen: A subprocess.Popen instance for the running process.
        """
        self.logger.debug("Starting async command", {
            "command": ' '.join(command),
            "cwd": str(cwd) if cwd else None
        })
        
        return subprocess.Popen(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env
        )