"""
LocalExecutor - Local command execution using subprocess.

Provides local command execution capabilities with timeout support.
"""

import subprocess
import time
from typing import Optional, Any

from .base import BaseExecutor, ExecutionResult


class LocalExecutor(BaseExecutor):
    """Executor for local command execution using subprocess."""
    
    def __init__(self, working_directory: Optional[str] = None, logger: Optional[Any] = None):
        super().__init__(logger)
        self.working_directory = working_directory
    
    def execute(self, command: str, timeout: Optional[int] = None, **kwargs) -> ExecutionResult:
        """
        Execute a command locally using subprocess.
        
        Args:
            command: Command to execute
            timeout: Timeout in seconds
            **kwargs: Additional subprocess arguments
        
        Returns:
            ExecutionResult: Result of command execution
        """
        start_time = time.time()
        timeout_occurred = False
        
        try:
            if self.logger:
                self.logger.debug(f"Executing local command: {command}")
            
            # Prepare subprocess arguments
            subprocess_kwargs = {
                'shell': True,
                'capture_output': True,
                'text': True,
                'timeout': timeout,
                'cwd': self.working_directory
            }
            
            # Override with any provided kwargs
            subprocess_kwargs.update(kwargs)
            
            # Execute command
            result = subprocess.run(command, **subprocess_kwargs)
            
            execution_time = time.time() - start_time
            
            execution_result = ExecutionResult(
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.returncode,
                execution_time=execution_time,
                command=command,
                timeout_occurred=timeout_occurred
            )
            
            if self.logger:
                if execution_result.success:
                    self.logger.debug(f"Local command succeeded in {execution_time:.2f}s: {command}")
                else:
                    self.logger.warning(f"Local command failed (exit code {result.returncode}): {command}")
                    if result.stderr:
                        self.logger.warning(f"Command stderr: {result.stderr}")
            
            return execution_result
            
        except subprocess.TimeoutExpired as e:
            execution_time = time.time() - start_time
            timeout_occurred = True
            
            if self.logger:
                self.logger.error(f"Local command timed out after {timeout}s: {command}")
            
            return ExecutionResult(
                stdout=e.stdout or "",
                stderr=e.stderr or f"Command timed out after {timeout} seconds",
                exit_code=-1,
                execution_time=execution_time,
                command=command,
                timeout_occurred=timeout_occurred
            )
            
        except subprocess.CalledProcessError as e:
            execution_time = time.time() - start_time
            
            if self.logger:
                self.logger.error(f"Local command failed with exit code {e.returncode}: {command}")
            
            return ExecutionResult(
                stdout=e.stdout or "",
                stderr=e.stderr or f"Command failed with exit code {e.returncode}",
                exit_code=e.returncode,
                execution_time=execution_time,
                command=command,
                timeout_occurred=timeout_occurred
            )
            
        except Exception as e:
            execution_time = time.time() - start_time
            
            if self.logger:
                self.logger.error(f"Unexpected error executing local command: {command}, error: {e}")
            
            return ExecutionResult(
                stdout="",
                stderr=f"Unexpected error: {str(e)}",
                exit_code=-1,
                execution_time=execution_time,
                command=command,
                timeout_occurred=timeout_occurred
            )
    
    def check_availability(self) -> bool:
        """
        Check if local executor is available.
        
        Returns:
            bool: Always True for local executor
        """
        try:
            # Test with a simple command
            result = self.execute("echo test", timeout=5)
            return result.success and "test" in result.stdout
        except Exception:
            return False
    
    def get_system_info(self) -> dict:
        """
        Get basic system information.
        
        Returns:
            dict: System information
        """
        info = {}
        
        # Get OS information
        os_result = self.execute("uname -a")
        if os_result.success:
            info["os"] = os_result.stdout.strip()
        
        # Get current user
        user_result = self.execute("whoami")
        if user_result.success:
            info["user"] = user_result.stdout.strip()
        
        # Get current working directory
        pwd_result = self.execute("pwd")
        if pwd_result.success:
            info["working_directory"] = pwd_result.stdout.strip()
        
        # Get available disk space
        df_result = self.execute("df -h .")
        if df_result.success:
            info["disk_usage"] = df_result.stdout.strip()
        
        return info
    
    def test_command(self, command: str) -> bool:
        """
        Test if a command is available.
        
        Args:
            command: Command to test
        
        Returns:
            bool: True if command is available
        """
        result = self.execute(f"command -v {command}")
        return result.success