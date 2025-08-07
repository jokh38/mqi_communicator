"""
RemoteExecutor - Remote command execution via SSH.

Provides remote command execution capabilities with connection management.
"""

import time
import socket
import traceback
from typing import Optional, Dict, Any, TYPE_CHECKING
from contextlib import contextmanager
import paramiko

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

    @contextmanager
    def _get_ssh_client_context(self):
        """Provides an SSH client within a context, ensuring it's closed."""
        ssh_client = None
        try:
            ssh_client = self.connection_manager.get_ssh_client()
            if not ssh_client:
                raise ConnectionError("Failed to get SSH client from ConnectionManager.")
            yield ssh_client
        finally:
            if ssh_client:
                # This closes the channel, not the transport, as the transport
                # was not created by this client instance.
                ssh_client.close()
    
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
        working_dir = kwargs.get('working_dir')
        final_command = f"cd '{working_dir}' && {command}" if working_dir else command
        
        try:
            with self._get_ssh_client_context() as ssh_client:
                if self.logger:
                    self.logger.debug(f"Executing remote command: {final_command}")

                stdin, stdout, stderr = ssh_client.exec_command(final_command, timeout=timeout)

                stdout_data = stdout.read().decode('utf-8', errors='ignore')
                stderr_data = stderr.read().decode('utf-8', errors='ignore')
                exit_code = stdout.channel.recv_exit_status()

                execution_time = time.time() - start_time
                
                execution_result = ExecutionResult(
                    stdout=stdout_data,
                    stderr=stderr_data,
                    exit_code=exit_code,
                    execution_time=execution_time,
                    command=final_command,
                    timeout_occurred=False
                )

                if self.logger:
                    if execution_result.success:
                        self.logger.debug(f"Remote command succeeded in {execution_time:.2f}s: {command}")
                        # Log stderr even on success if it's not empty (for debugging)
                        if stderr_data:
                            self.logger.debug(f"Command stderr (success): {stderr_data.strip()}")
                    else:
                        self.logger.warning(f"Remote command failed (exit code {exit_code}): {command}")
                        # Log both stdout and stderr content for failed commands (first 200 chars)
                        if stdout_data:
                            self.logger.warning(f"stdout: {stdout_data[:200]}")
                        if stderr_data:
                            self.logger.warning(f"stderr: {stderr_data[:200]}")

                self._connection_failures = 0
                return execution_result

        except socket.timeout:
            execution_time = time.time() - start_time
            if self.logger:
                self.logger.error(f"Remote command timed out after {timeout}s: {command}")
            return ExecutionResult(
                stdout="",
                stderr=f"Command timed out after {timeout} seconds",
                exit_code=-1,
                execution_time=execution_time,
                command=final_command,
                timeout_occurred=True
            )

        except (ConnectionError, socket.error, paramiko.SSHException, Exception) as e:
            execution_time = time.time() - start_time
            self._connection_failures += 1
            
            if self.logger:
                self.logger.error(f"Remote command execution exception: {command}")
                self.logger.error(f"Exception type: {type(e).__name__}")
                self.logger.error(f"Exception message: {str(e)}")
                self.logger.error(f"Traceback: {traceback.format_exc()}")
            
            return ExecutionResult(
                stdout="",
                stderr=f"Exception ({type(e).__name__}): {str(e)}",
                exit_code=-1,
                execution_time=execution_time,
                command=final_command,
                timeout_occurred=False
            )

    def check_availability(self) -> bool:
        """
        Check if remote executor is available.
        
        Returns:
            bool: True if SSH connection is available, False otherwise
        """
        try:
            # A short timeout is crucial here to avoid long waits on network issues
            result = self.execute("echo test", timeout=10)
            return result.success and "test" in result.stdout
        except Exception as e:
            if self.logger:
                self.logger.warning(f"Remote executor availability check failed: {e}")
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
        return self.execute(command, timeout=timeout, working_dir=working_dir)
    
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
        final_command = f"cd '{working_dir}' && {command}" if working_dir else command

        stdout_lines = []
        stderr_lines = []
        
        try:
            with self._get_ssh_client_context() as ssh_client:
                if self.logger:
                    self.logger.debug(f"Executing streaming remote command: {final_command}")

                stdin, stdout, stderr = ssh_client.exec_command(final_command, timeout=timeout)

                # Stream stdout in real-time
                for line in iter(stdout.readline, ""):
                    if not line:
                        break
                    line_str = line.rstrip()
                    stdout_lines.append(line_str)

                    if output_callback and line_str.strip():
                        try:
                            output_callback(line_str)
                        except Exception as cb_e:
                            if self.logger:
                                self.logger.error(f"Output callback failed: {cb_e}")

                    if self.logger and line_str.strip():
                        self.logger.debug(f"Remote output: {line_str}")

                stderr_data = stderr.read().decode('utf-8', errors='ignore')
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
                    timeout_occurred=False
                )
                
                if self.logger:
                    if execution_result.success:
                        self.logger.debug(f"Streaming remote command succeeded in {execution_time:.2f}s")
                        # Log stderr even on success if it's not empty (for debugging)
                        if stderr_data:
                            self.logger.debug(f"Streaming command stderr (success): {stderr_data.strip()}")
                    else:
                        self.logger.warning(f"Streaming remote command failed (exit code {exit_code})")
                        # Log both stdout and stderr content for failed commands (first 200 chars)
                        stdout_str = "\n".join(stdout_lines)
                        stderr_str = "\n".join(stderr_lines)
                        if stdout_str:
                            self.logger.warning(f"stdout: {stdout_str[:200]}")
                        if stderr_str:
                            self.logger.warning(f"stderr: {stderr_str[:200]}")

                self._connection_failures = 0
                return execution_result
                
        except socket.timeout:
            execution_time = time.time() - start_time
            if self.logger:
                self.logger.error(f"Streaming remote command timed out after {timeout}s")
            return ExecutionResult(
                stdout="\n".join(stdout_lines),
                stderr=f"Command timed out after {timeout} seconds",
                exit_code=-1,
                execution_time=execution_time,
                command=final_command,
                timeout_occurred=True
            )
            
        except (ConnectionError, socket.error, paramiko.SSHException, Exception) as e:
            execution_time = time.time() - start_time
            self._connection_failures += 1
            
            if self.logger:
                self.logger.error(f"Streaming remote command execution exception: {command}")
                self.logger.error(f"Exception type: {type(e).__name__}")
                self.logger.error(f"Exception message: {str(e)}")
                self.logger.error(f"Traceback: {traceback.format_exc()}")
            
            return ExecutionResult(
                stdout="\n".join(stdout_lines),
                stderr=f"Exception ({type(e).__name__}): {str(e)}",
                exit_code=-1,
                execution_time=execution_time,
                command=final_command,
                timeout_occurred=False
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

    def check_directory_exists(self, path: str) -> bool:
        """Check if a directory exists on the remote server."""
        # Use a more robust check to avoid issues with special characters in path
        command = f"if [ -d '{path}' ]; then exit 0; else exit 1; fi"
        result = self.execute(command)
        return result.success

    def create_directory(self, path: str) -> bool:
        """Create a directory on the remote server."""
        command = f"mkdir -p '{path}'"
        result = self.execute(command)
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