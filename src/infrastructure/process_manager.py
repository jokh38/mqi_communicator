"""Manages process pools and subprocess execution for the application."""

import subprocess
from concurrent.futures import Future, ProcessPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src.config.settings import Settings  # Updated import
from src.domain.errors import ProcessingError
from src.infrastructure.logging_handler import StructuredLogger


class ProcessManager:
    """Manages a ProcessPoolExecutor for running concurrent tasks."""

    def __init__(self, settings: Settings, logger: StructuredLogger):
        """
        Initialize the process manager with a settings object.

        Args:
            settings (Settings): The application's settings object.
            logger (StructuredLogger): The logger for recording operations.
        """
        self.settings = settings
        self.logger = logger
        self.proc_config = self.settings.get_processing_config()
        self.max_workers = self.proc_config.get("max_workers", 4)

        self._executor: Optional[ProcessPoolExecutor] = None
        self._active_processes: Dict[str, Future] = {}
        self._shutdown = False

    def start(self) -> None:
        """Start the process pool executor."""
        if self._executor is None:
            self._executor = ProcessPoolExecutor(max_workers=self.max_workers)
            self.logger.info("Process manager started",
                             {"max_workers": self.max_workers})

    def shutdown(self, wait: bool = True) -> None:
        """
        Shutdown the process manager and clean up resources.

        Args:
            wait (bool): Whether to wait for active processes to complete.
        """
        self._shutdown = True
        if self._executor:
            self.logger.info("Shutting down process manager", {
                "active_processes": len(self._active_processes),
                "wait": wait
            })
            self._executor.shutdown(wait=wait)
            self._executor = None
            self._active_processes.clear()

    def submit_task(self, task_id: str, worker_func: Callable,
                    *args: Any) -> None:
        """
        Submit a task to the worker pool.

        Args:
            task_id (str): A unique identifier for the task.
            worker_func (Callable): The worker function to execute.
            *args (Any): Arguments for the worker function.

        Raises:
            RuntimeError: If the process manager is not started or is shutting down.
        """
        if not self._executor:
            raise RuntimeError("Process manager not started")
        if self._shutdown:
            raise RuntimeError("Process manager is shutting down")

        self.logger.info("Submitting task", {"task_id": task_id})
        future = self._executor.submit(worker_func, *args)
        self._active_processes[task_id] = future
        future.add_done_callback(
            lambda f: self._process_completed(task_id, f))

    def _process_completed(self, task_id: str, future: Future) -> None:
        """Handle process completion and cleanup."""
        try:
            result = future.result()
            self.logger.info("Process completed successfully", {
                "task_id": task_id,
                "result": result
            })
        except Exception as e:
            self.logger.error("Process failed",
                              {"task_id": task_id, "error": str(e)})
        finally:
            self._active_processes.pop(task_id, None)

    def get_active_process_count(self) -> int:
        """Get the count of currently active processes."""
        return len(self._active_processes)


class CommandExecutor:
    """Handles subprocess command execution with proper error handling and logging."""

    def __init__(self, logger: StructuredLogger, default_timeout: int = 300):
        """
        Initialize the command executor.

        Args:
            logger (StructuredLogger): The logger for recording operations.
            default_timeout (int): The default timeout for commands in seconds.
        """
        self.logger = logger
        self.default_timeout = default_timeout

    def execute_command(self,
                        command: List[str],
                        cwd: Optional[Path] = None,
                        timeout: Optional[int] = None,
                        capture_output: bool = True,
                        env: Optional[Dict[str, str]] = None
                        ) -> subprocess.CompletedProcess:
        """
        Execute a command with proper error handling and logging.

        Args:
            command (List[str]): The command and arguments as a list.
            cwd (Optional[Path]): The working directory for the command.
            timeout (Optional[int]): The command timeout.
            capture_output (bool): Whether to capture stdout/stderr.
            env (Optional[Dict[str, str]]): Environment variables.

        Raises:
            ProcessingError: If the command fails or times out.

        Returns:
            subprocess.CompletedProcess: The result of the execution.
        """
        timeout = timeout or self.default_timeout
        self.logger.debug("Executing command", {
            "command": ' '.join(command),
            "cwd": str(cwd) if cwd else None,
            "timeout": timeout
        })
        try:
            result = subprocess.run(command,
                                    cwd=cwd,
                                    timeout=timeout,
                                    capture_output=capture_output,
                                    text=True,
                                    env=env,
                                    check=True)
            self.logger.debug("Command completed successfully", {
                "command": command[0],
                "return_code": result.returncode
            })
            return result
        except subprocess.TimeoutExpired:
            self.logger.error("Command timed out", {
                "command": ' '.join(command),
                "timeout": timeout
            })
            raise ProcessingError(
                f"Command timed out after {timeout}s: {' '.join(command)}")
        except subprocess.CalledProcessError as e:
            self.logger.error(
                "Command failed", {
                    "command": ' '.join(command),
                    "return_code": e.returncode,
                    "stdout": e.stdout,
                    "stderr": e.stderr
                })
            raise ProcessingError(
                f"Command failed with code {e.returncode}: {' '.join(command)}"
            )