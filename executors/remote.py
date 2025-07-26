"""
RemoteExecutor - Remote command execution via SSH.

Provides remote command execution capabilities with connection management.
"""

import time
import socket
from typing import Optional, Dict, Any, TYPE_CHECKING

from .base import BaseExecutor, ExecutionResult

if TYPE_CHECKING:
    from remote.connection import ConnectionManager


class RemoteExecutor(BaseExecutor):
    """Executor for remote command execution via SSH."""
    
    def __init__(self, 
                 connection_manager: 'ConnectionManager',
                 logger=None):
        super().__init__(logger)
        self.connection_manager = connection_manager
        self._connection_failures = 0
    
    def execute(self, command: str, timeout: Optional[int] = None, **kwargs) -> ExecutionResult:
        """
        Execute a command remotely via SSH.
        
        Args:
            command: Command to execute
            timeout: Timeout in seconds
            **kwargs: Additional arguments (working_dir, etc.)
        
        Returns:
            ExecutionResult: Result of command execution
        """
        start_time = time.time()
        timeout_occurred = False
        
        try:
            ssh_client = self.connection_manager.get_ssh_client()
            if not ssh_client:
                self._connection_failures += 1
                execution_time = time.time() - start_time
                
                return ExecutionResult(
                    stdout="",
                    stderr="SSH connection failed",
                    exit_code=-1,
                    execution_time=execution_time,
                    command=command,
                    timeout_occurred=False
                )
            
            if self.logger:
                self.logger.debug(f"Executing remote command: {command}")
            
            # Handle working directory change if specified
            final_command = command
            if 'working_dir' in kwargs:
                working_dir = kwargs['working_dir']
                final_command = f"cd '{working_dir}' && {command}"
            
            # Execute command
            stdin, stdout, stderr = ssh_client.exec_command(final_command, timeout=timeout)
            
            stdout_data = stdout.read().decode('utf-8')
            stderr_data = stderr.read().decode('utf-8')
            exit_code = stdout.channel.recv_exit_status()
            
            execution_time = time.time() - start_time
            
            execution_result = ExecutionResult(
                stdout=stdout_data,
                stderr=stderr_data,
                exit_code=exit_code,
                execution_time=execution_time,
                command=final_command,
                timeout_occurred=timeout_occurred
            )
            
            if self.logger:
                if execution_result.success:
                    self.logger.debug(f"Remote command succeeded in {execution_time:.2f}s: {command}")
                else:
                    self.logger.warning(f"Remote command failed (exit code {exit_code}): {command}")
                    if stderr_data:
                        self.logger.warning(f"Command stderr: {stderr_data}")
            
            # Reset connection failure counter on success
            self._connection_failures = 0
            
            return execution_result
            
        except socket.timeout:
            execution_time = time.time() - start_time
            timeout_occurred = True
            
            if self.logger:
                self.logger.error(f"Remote command timed out after {timeout}s: {command}")
            
            return ExecutionResult(
                stdout="",
                stderr=f"Command timed out after {timeout} seconds",
                exit_code=-1,
                execution_time=execution_time,
                command=command,
                timeout_occurred=timeout_occurred
            )
            
        except (socket.error, Exception) as e:
            execution_time = time.time() - start_time
            self._connection_failures += 1
            
            if self.logger:
                self.logger.error(f"Remote command execution error: {command}, error: {e}")
            
            return ExecutionResult(
                stdout="",
                stderr=f"Connection/execution error: {str(e)}",
                exit_code=-1,
                execution_time=execution_time,
                command=command,
                timeout_occurred=timeout_occurred
            )
    
    def check_availability(self) -> bool:
        """
        Check if remote executor is available.
        
        Returns:
            bool: True if SSH connection is available, False otherwise
        """
        try:
            result = self.execute("echo test", timeout=5)
            return result.success and "test" in result.stdout
        except Exception:
            return False
    
    def execute_with_workdir(self, command: str, working_dir: str, timeout: Optional[int] = None) -> ExecutionResult:
        """
        Execute command in specified working directory.
        
        Args:
            command: Command to execute
            working_dir: Working directory
            timeout: Timeout in seconds
        
        Returns:
            ExecutionResult: Result of command execution
        """
        return self.execute(command, timeout, working_dir=working_dir)
    
    def execute_streaming(self, command: str, working_dir: str = None, 
                         timeout: Optional[int] = None, 
                         output_callback=None) -> ExecutionResult:
        """
        Execute command with real-time output streaming.
        
        Args:
            command: Command to execute
            working_dir: Working directory
            timeout: Timeout in seconds
            output_callback: Callback function for streaming output
        
        Returns:
            ExecutionResult: Result of command execution
        """
        start_time = time.time()
        timeout_occurred = False
        
        try:
            ssh_client = self.connection_manager.get_ssh_client()
            if not ssh_client:
                self._connection_failures += 1
                execution_time = time.time() - start_time
                
                return ExecutionResult(
                    stdout="",
                    stderr="SSH connection failed",
                    exit_code=-1,
                    execution_time=execution_time,
                    command=command,
                    timeout_occurred=False
                )
            
            # Handle working directory change if specified
            final_command = command
            if working_dir:
                final_command = f"cd '{working_dir}' && {command}"
            
            if self.logger:
                self.logger.debug(f"Executing streaming remote command: {final_command}")
            
            stdin, stdout, stderr = ssh_client.exec_command(final_command, timeout=timeout)
            
            stdout_lines = []
            stderr_lines = []
            
            # Stream stdout in real-time
            while True:
                line = stdout.readline()
                if not line:
                    break
                    
                line_str = line.rstrip()
                stdout_lines.append(line_str)
                
                # Call output callback if provided
                if output_callback and line_str.strip():
                    output_callback(line_str)
                
                # Log individual progress lines
                if self.logger and line_str.strip():
                    self.logger.debug(f"Remote output: {line_str}")
            
            # Read any remaining stderr
            stderr_data = stderr.read().decode('utf-8')
            if stderr_data:
                stderr_lines.extend(stderr_data.splitlines())
            
            exit_code = stdout.channel.recv_exit_status()
            execution_time = time.time() - start_time
            
            execution_result = ExecutionResult(
                stdout="\n".join(stdout_lines),
                stderr="\n".join(stderr_lines),
                exit_code=exit_code,
                execution_time=execution_time,
                command=final_command,
                timeout_occurred=timeout_occurred
            )
            
            if self.logger:
                if execution_result.success:
                    self.logger.debug(f"Streaming remote command succeeded in {execution_time:.2f}s")
                else:
                    self.logger.warning(f"Streaming remote command failed (exit code {exit_code})")
            
            # Reset connection failure counter on success
            self._connection_failures = 0
            
            return execution_result
            
        except socket.timeout:
            execution_time = time.time() - start_time
            timeout_occurred = True
            
            if self.logger:
                self.logger.error(f"Streaming remote command timed out after {timeout}s")
            
            return ExecutionResult(
                stdout="\n".join(stdout_lines) if 'stdout_lines' in locals() else "",
                stderr=f"Command timed out after {timeout} seconds",
                exit_code=-1,
                execution_time=execution_time,
                command=command,
                timeout_occurred=timeout_occurred
            )
            
        except Exception as e:
            execution_time = time.time() - start_time
            self._connection_failures += 1
            
            if self.logger:
                self.logger.error(f"Streaming remote command execution error: {e}")
            
            return ExecutionResult(
                stdout="\n".join(stdout_lines) if 'stdout_lines' in locals() else "",
                stderr=f"Connection/execution error: {str(e)}",
                exit_code=-1,
                execution_time=execution_time,
                command=command,
                timeout_occurred=timeout_occurred
            )
    
    def get_system_info(self) -> Dict[str, Any]:
        """
        Get remote system information.
        
        Returns:
            dict: System information
        """
        info = {}
        
        commands = {
            "hostname": "hostname",
            "uptime": "uptime",
            "memory": "free -h",
            "disk": "df -h",
            "cpu": "nproc",
            "load": "cat /proc/loadavg",
            "user": "whoami",
            "working_directory": "pwd"
        }
        
        for key, command in commands.items():
            result = self.execute(command)
            if result.success:
                info[key] = result.stdout.strip()
            else:
                info[key] = "N/A"
        
        return info
    
    def get_connection_failures(self) -> int:
        """Get number of connection failures."""
        return self._connection_failures
    
    def reset_connection_failures(self) -> None:
        """Reset connection failure counter."""
        self._connection_failures = 0
    
    def get_connection_info(self) -> Dict[str, Any]:
        """Get connection information from connection manager."""
        return self.connection_manager.get_connection_info()
    
    def kill_process(self, pid: int, signal_type: str = "TERM") -> bool:
        """
        Kill process by PID.
        
        Args:
            pid: Process ID
            signal_type: Signal type (TERM, KILL, etc.)
        
        Returns:
            bool: True if successful, False otherwise
        """
        result = self.execute(f"kill -{signal_type} {pid}")
        return result.success
    
    def check_process_running(self, pid: int) -> bool:
        """
        Check if process is running.
        
        Args:
            pid: Process ID
        
        Returns:
            bool: True if process is running, False otherwise
        """
        result = self.execute(f"ps -p {pid}")
        return result.success
    
    def get_disk_usage(self, path: str = "/") -> Dict[str, str]:
        """
        Get disk usage information.
        
        Args:
            path: Path to check
        
        Returns:
            dict: Disk usage information
        """
        result = self.execute(f"df -h {path}")
        
        if result.success:
            lines = result.stdout.strip().split('\n')
            if len(lines) >= 2:
                parts = lines[1].split()
                if len(parts) >= 6:
                    return {
                        "filesystem": parts[0],
                        "size": parts[1],
                        "used": parts[2],
                        "available": parts[3],
                        "use_percent": parts[4],
                        "mount_point": parts[5]
                    }
        
        return {}
    
    def monitor_log_file(self, log_path: str, lines: int = 10) -> str:
        """
        Monitor log file (tail).
        
        Args:
            log_path: Path to log file
            lines: Number of lines to show
        
        Returns:
            str: Log file content
        """
        result = self.execute(f"tail -n {lines} {log_path}")
        
        if result.success:
            return result.stdout
        else:
            return f"Error reading log: {result.stderr}"