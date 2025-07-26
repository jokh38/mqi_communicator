"""
BaseExecutor - Abstract base class for command execution.

Provides a common interface for executing commands locally or remotely.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Dict, Any


@dataclass
class ExecutionResult:
    """Result of command execution."""
    stdout: str
    stderr: str
    exit_code: int
    execution_time: float = 0.0
    command: str = ""
    timeout_occurred: bool = False
    
    @property
    def success(self) -> bool:
        """Check if command executed successfully."""
        return self.exit_code == 0 and not self.timeout_occurred
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "execution_time": self.execution_time,
            "command": self.command,
            "timeout_occurred": self.timeout_occurred,
            "success": self.success
        }


class BaseExecutor(ABC):
    """Abstract base class for command executors."""
    
    def __init__(self, logger=None):
        self.logger = logger
    
    @abstractmethod
    def execute(self, command: str, timeout: Optional[int] = None, **kwargs) -> ExecutionResult:
        """
        Execute a command and return the result.
        
        Args:
            command: Command to execute
            timeout: Timeout in seconds (None for no timeout)
            **kwargs: Additional executor-specific arguments
        
        Returns:
            ExecutionResult: Result of command execution
        """
        pass
    
    @abstractmethod
    def check_availability(self) -> bool:
        """
        Check if the executor is available and ready to execute commands.
        
        Returns:
            bool: True if executor is available, False otherwise
        """
        pass
    
    def execute_command(self, command: str, timeout: Optional[int] = None, **kwargs) -> Dict[str, Any]:
        """
        Execute command and return result as dictionary (for backward compatibility).
        
        Args:
            command: Command to execute
            timeout: Timeout in seconds
            **kwargs: Additional arguments
        
        Returns:
            Dict containing stdout, stderr, exit_code
        """
        result = self.execute(command, timeout, **kwargs)
        return result.to_dict()
    
    def check_directory_exists(self, path: str) -> bool:
        """
        Check if a directory exists.
        
        Args:
            path: Directory path to check
        
        Returns:
            bool: True if directory exists, False otherwise
        """
        result = self.execute(f"test -d '{path}'")
        return result.success
    
    def create_directory(self, path: str) -> bool:
        """
        Create a directory.
        
        Args:
            path: Directory path to create
        
        Returns:
            bool: True if successful, False otherwise
        """
        result = self.execute(f"mkdir -p '{path}'")
        return result.success
    
    def get_file_size(self, path: str) -> Optional[int]:
        """
        Get file size in bytes.
        
        Args:
            path: File path
        
        Returns:
            Optional[int]: File size in bytes, None if file doesn't exist
        """
        result = self.execute(f"stat -c%s '{path}' 2>/dev/null")
        if result.success and result.stdout.strip().isdigit():
            return int(result.stdout.strip())
        return None
    
    def file_exists(self, path: str) -> bool:
        """
        Check if a file exists.
        
        Args:
            path: File path to check
        
        Returns:
            bool: True if file exists, False otherwise
        """
        result = self.execute(f"test -f '{path}'")
        return result.success
    
    def remove_file(self, path: str) -> bool:
        """
        Remove a file.
        
        Args:
            path: File path to remove
        
        Returns:
            bool: True if successful, False otherwise
        """
        result = self.execute(f"rm -f '{path}'")
        return result.success
    
    def get_environment_variable(self, var_name: str) -> Optional[str]:
        """
        Get environment variable value.
        
        Args:
            var_name: Environment variable name
        
        Returns:
            Optional[str]: Variable value, None if not set
        """
        result = self.execute(f"echo ${var_name}")
        if result.success and result.stdout.strip():
            return result.stdout.strip()
        return None