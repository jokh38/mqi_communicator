import paramiko
import time
import logging
import socket
import shlex
from typing import Dict, List, Optional, Any
import threading
import signal
from base_ssh_connector import BaseSSHConnector
from config_manager import ConfigManager


class RemoteExecutor(BaseSSHConnector):
    def __init__(self, config: Dict[str, Any], logger=None, **kwargs):
        """
        Initialize RemoteExecutor.
        
        Args:
            config (Dict[str, Any]): Configuration dictionary.
            **kwargs: Additional keyword arguments (e.g., status_display).
        """
        # Extract connection details from config
        host = config.get("servers", {}).get("linux_gpu")
        username = config.get("credentials", {}).get("username")
        password = config.get("credentials", {}).get("password")
        port = config.get("servers", {}).get("ssh_port", 22)
        timeout = config.get("servers", {}).get("ssh_timeout", 30)

        if not all([host, username, password]):
            raise ValueError("Host, username, or password not found in configuration.")

        # Initialize parent class with connection details
        super().__init__(host, username, password, port, timeout)
        
        self.ssh: Optional[paramiko.SSHClient] = None
        self.config = config
        self.logger = logger
        self._connection_failures = 0
        
        # Extract paths from config
        self.paths = self.config.get("paths", {})
        self.remote_workspace = self.paths.get("remote_workspace")
        self.mqi_interpreter_path = self.paths.get("linux_mqi_interpreter")
        self.moqui_binary_path = self.paths.get("linux_moqui_binary")
        self.raw_to_dcm_path = self.paths.get("linux_raw_to_dcm")
        self.moqui_outputs_path = self.paths.get("linux_moqui_outputs_dir")
        self.venv_python_path = self.paths.get("linux_venv_python", "python3")  # Fallback to python3 if not configured

        if not all([self.remote_workspace, self.mqi_interpreter_path, self.moqui_binary_path, self.raw_to_dcm_path, self.moqui_outputs_path, self.venv_python_path]):
            raise ValueError("One or more required paths are missing in the configuration file.")

        # Load program working directories from config
        self.working_directories = self.config.get("working_directories", {})
        if not self.working_directories:
            raise ValueError("Working directories not found in configuration file.")

    def _post_connect_setup(self) -> None:
        """Create SSH client after connection is established."""
        self.ssh = paramiko.SSHClient()
        self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        # Use the existing transport to create the client
        self.ssh._transport = self.transport
        
        # Reset failure counter on successful connection
        self._connection_failures = 0
    
    def _pre_disconnect_cleanup(self) -> None:
        """Close SSH client before disconnection."""
        if self.ssh:
            try:
                self.ssh.close()
            except Exception:
                pass  # Ignore errors during cleanup
            self.ssh = None

    def _ensure_ssh_connected(self) -> bool:
        """Ensure SSH client is available, reconnect if necessary."""
        # First check if we have a valid connection
        if self.connected and self.ssh is not None:
            # Test if the connection is still alive with a simple command
            try:
                self.ssh.exec_command("echo test", timeout=5)
                return True
            except Exception as e:
                logging.warning(f"SSH connection test failed, reconnecting: {e}")
                self.connected = False
                self.ssh = None
        
        # If no valid connection, try to establish one
        return self._ensure_connected() and self.ssh is not None

    def execute_command(self, command: str, timeout: Optional[int] = None) -> Dict[str, Any]:
        """Execute remote command and return result."""
        if not self._ensure_ssh_connected():
            self._connection_failures += 1
            return {
                "stdout": "",
                "stderr": "Connection failed",
                "exit_code": -1
            }
        
        try:
            stdin, stdout, stderr = self.ssh.exec_command(command, timeout=timeout)
            
            stdout_data = stdout.read().decode('utf-8')
            stderr_data = stderr.read().decode('utf-8')
            exit_code = stdout.channel.recv_exit_status()
            
            return {
                "stdout": stdout_data,
                "stderr": stderr_data,
                "exit_code": exit_code
            }
            
        except socket.timeout:
            return {
                "stdout": "",
                "stderr": f"Command timed out after {timeout} seconds",
                "exit_code": -1
            }
        except Exception as e:
            if self.logger:
                self.logger.log_exception(e, {
                    "operation": "command_execution",
                    "command": command,
                    "host": self.host
                })
            else:
                logging.error(f"Command execution failed: {e}")
            return {
                "stdout": "",
                "stderr": f"Execution error: {str(e)}",
                "exit_code": -1
            }

    def execute_command_with_workdir(self, command: str, working_dir: str, timeout: Optional[int] = None) -> Dict[str, Any]:
        """Execute command in specified working directory."""
        full_command = f"cd {shlex.quote(working_dir)} && {command}"
        return self.execute_command(full_command, timeout)

    def execute_command_streaming(self, command: str, working_dir: str, case_id: str, 
                                 status_display=None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """Execute command with real-time output streaming for progress updates."""
        if not self._ensure_ssh_connected():
            self._connection_failures += 1
            return {
                "stdout": "",
                "stderr": "Connection failed",
                "exit_code": -1
            }
        
        try:
            full_command = f"cd {shlex.quote(working_dir)} && {command}"
            stdin, stdout, stderr = self.ssh.exec_command(full_command, timeout=timeout)
            
            stdout_lines = []
            stderr_lines = []
            
            # Stream stdout in real-time
            while True:
                line = stdout.readline()
                if not line:
                    break
                    
                line_str = line.decode('utf-8').rstrip()
                stdout_lines.append(line_str)
                
                # Update progress display based on interpreter output
                if status_display and line_str.strip():
                    self._update_interpreter_progress(case_id, line_str, status_display)
                
                # Log individual progress lines
                if self.logger and line_str.strip():
                    self.logger.log_structured("INFO", "MOQUI interpreter progress", {
                        "case_id": case_id,
                        "output_line": line_str
                    })
            
            # Read any remaining stderr
            stderr_data = stderr.read().decode('utf-8')
            if stderr_data:
                stderr_lines.extend(stderr_data.splitlines())
            
            exit_code = stdout.channel.recv_exit_status()
            
            return {
                "stdout": "\n".join(stdout_lines),
                "stderr": "\n".join(stderr_lines),
                "exit_code": exit_code
            }
            
        except socket.timeout:
            return {
                "stdout": "\n".join(stdout_lines) if 'stdout_lines' in locals() else "",
                "stderr": f"Command timed out after {timeout} seconds",
                "exit_code": -1
            }
        except Exception as e:
            if self.logger:
                self.logger.log_exception(e, {
                    "operation": "streaming_command_execution",
                    "command": command,
                    "host": self.host
                })
            else:
                logging.error(f"Streaming command execution failed: {e}")
            return {
                "stdout": "\n".join(stdout_lines) if 'stdout_lines' in locals() else "",
                "stderr": f"Execution error: {str(e)}",
                "exit_code": -1
            }

    def _update_interpreter_progress(self, case_id: str, output_line: str, status_display):
        """Update progress display based on interpreter output line."""
        line = output_line.strip().lower()
        
        # Parse different progress indicators
        if "parsing rt plan file" in line:
            status_display.update_case_status(
                case_id=case_id,
                status="PROCESSING",
                current_task="MOQUI Interpreter",
                detailed_status="Parsing RT Plan file..."
            )
        elif "skipping beam" in line:
            # Extract beam info for display
            if ":" in output_line:
                beam_info = output_line.split(":", 1)[1].strip()
                status_display.update_case_status(
                    case_id=case_id,
                    status="PROCESSING", 
                    current_task="MOQUI Interpreter",
                    detailed_status=f"Processing beams: {beam_info[:30]}..."
                )
        elif "processing beam" in line:
            status_display.update_case_status(
                case_id=case_id,
                status="PROCESSING",
                current_task="MOQUI Interpreter", 
                detailed_status="Processing beam data..."
            )
        elif "saved" in line and "output" in line:
            status_display.update_case_status(
                case_id=case_id,
                status="PROCESSING",
                current_task="MOQUI Interpreter",
                detailed_status="Saving output files..."
            )
        elif "completed" in line or "finished" in line:
            status_display.update_case_status(
                case_id=case_id,
                status="PROCESSING",
                current_task="MOQUI Interpreter",
                detailed_status="Interpreter completed successfully"
            )

    def run_python_script(self, script_path: str, args: List[str] = None) -> bool:
        """Execute Python script remotely using virtual environment."""
        args = args or []
        command = f"{self.venv_python_path} {script_path} {' '.join(args)}"
        
        result = self.execute_command(command)
        
        if result["exit_code"] == 0:
            logging.info(f"Python script executed successfully using {self.venv_python_path}: {script_path}")
            return True
        else:
            logging.error(f"Python script failed: {script_path}, Error: {result['stderr']}")
            return False


    def run_moqui_interpreter(self, case_id: str, log_dir: str = None, workspace_path: str = None, status_display=None) -> bool:
        """Run moqui interpreter for case parsing."""
        workspace_path = workspace_path or self.remote_workspace
        case_path = f"{workspace_path}/{case_id}"
        
        # The log directory should be the same as the case path where DCM files are located
        # This is where find_dcm_file_in_logdir will search for DCM files
        log_directory = log_dir or case_path
        
        # Ensure the output directory exists
        self.create_directory(self.moqui_outputs_path)
        
        # Enhanced command to capture all error output including Python tracebacks
        # Use working directory change to ensure config files are found
        command = (f"{self.venv_python_path} -u ./main_cli.py "
                   f"--logdir {shlex.quote(log_directory)} "
                   f"--outputdir {shlex.quote(self.moqui_outputs_path)} 2>&1")
        
        # Log MOQUI interpreter execution start
        if self.logger:
            self.logger.log_structured("INFO", "MOQUI interpreter started", {
                "case_id": case_id,
                "command": command,
                "log_dir": log_directory,
                "case_path": case_path,
                "output_dir": self.moqui_outputs_path
            })
        else:
            logging.info(f"Executing MOQUI interpreter for case {case_id} with command: {command}")

        # Update status display - starting interpreter
        if status_display:
            status_display.update_case_status(
                case_id=case_id,
                status="PROCESSING",
                current_task="MOQUI Interpreter",
                current_step=1,
                total_steps=4,
                detailed_status="Parsing RTPLAN and preparing inputs..."
            )
        
        # Use streaming execution for real-time progress updates
        result = self.execute_command_streaming(
            command, 
            self.working_directories["mqi_interpreter"], 
            case_id, 
            status_display, 
            timeout=1800  # 30 minute timeout for interpreter
        )
        
        # Enhanced error capture - since we used 2>&1, all output is in stdout
        stdout_output = result['stdout'].strip()
        stderr_output = result['stderr'].strip()
        
        # Detailed logging for diagnostics with comprehensive error capture
        log_message = (
            f"MOQUI interpreter execution for case {case_id} finished with exit code {result['exit_code']}.\n"
            f"  - COMBINED OUTPUT: {stdout_output}\n"
            f"  - STDERR (if any): {stderr_output}"
        )
        
        # Extract error information for better visibility
        has_python_error = any(keyword in stdout_output.lower() for keyword in 
                              ['traceback', 'error:', 'exception:', 'failed', 'errno'])
        has_stderr = len(stderr_output) > 0

        if result["exit_code"] == 0 and not has_python_error:
            if self.logger:
                self.logger.log_case_progress(case_id, "INTERPRETER_SUCCESS", 1.0, {
                    "stage": "moqui_interpreter",
                    "stdout": stdout_output,
                    "execution_time_s": "measured_in_caller" # Could be enhanced
                })
            else:
                logging.info(log_message)
        elif result["exit_code"] == 0 and has_python_error:
            # Exit code 0 but Python errors detected - treat as warning
            if self.logger:
                self.logger.warning(f"MOQUI interpreter completed with warnings for case {case_id}")
                self.logger.warning(f"Python warnings/errors detected: {stdout_output}")
            else:
                logging.warning(f"MOQUI interpreter completed with warnings for case {case_id}")
                logging.warning(f"Output contains errors: {log_message}")
            if status_display:
                status_display.update_case_status(
                    case_id=case_id,
                    status="PROCESSING",
                    current_task="MOQUI Interpreter",
                    current_step=2,
                    total_steps=4,
                    detailed_status="RTPLAN parsed successfully"
                )
            return True
        else:
            # Failure case - ensure ALL error information is captured and displayed
            full_error_output = stdout_output if stdout_output else stderr_output
            
            if self.logger:
                self.logger.error(f"MOQUI interpreter FAILED for case {case_id}")
                self.logger.error(f"Exit code: {result['exit_code']}")
                self.logger.error(f"Full error output from GPU server:")
                self.logger.error(f"{full_error_output}")
                
                self.logger.log_case_progress(case_id, "INTERPRETER_FAILED", 0.0, {
                    "stage": "moqui_interpreter",
                    "error": full_error_output,
                    "stderr": stderr_output,
                    "exit_code": result["exit_code"],
                    "command": command
                })
            else:
                logging.error(f"MOQUI interpreter FAILED for case {case_id}")
                logging.error(log_message)
                
            if status_display:
                # Show more error details in status display
                error_preview = full_error_output[:200] + "..." if len(full_error_output) > 200 else full_error_output
                status_display.update_case_status(
                    case_id=case_id,
                    status="PROCESSING",
                    stage="Interpreter Failed",
                    error_message=f"Exit code {result['exit_code']}: {error_preview}",
                    transfer_info=""
                )
            return False

    def run_moqui_beam(self, case_id: str, beam_id: int, gpu_id: int, 
                      workspace_path: str = None, status_display=None) -> bool:
        """Run moqui beam calculation on specific GPU."""
        workspace_path = workspace_path or self.remote_workspace
        case_path = f"{workspace_path}/{case_id}"
        # Use working directory change to ensure proper execution environment
        input_dir = f"{case_path}/moqui_inputs"
        output_dir = f"{case_path}/moqui_output"
        command = f"CUDA_VISIBLE_DEVICES={gpu_id} ./main --input_dir {shlex.quote(input_dir)} --output_dir {shlex.quote(output_dir)}"
        
        # Update status display - starting beam calculation
        if status_display:
            status_display.update_case_status(
                case_id=case_id,
                status="PROCESSING",
                current_task="Beam Calculation",
                current_step=3,
                total_steps=4,
                detailed_status=f"Processing beam {beam_id} on GPU {gpu_id}"
            )
        
        result = self.execute_command_with_workdir(command, self.working_directories["moqui_binary"], timeout=3600)  # 1 hour timeout
        
        if result["exit_code"] == 0:
            if self.logger:
                self.logger.log_case_progress(case_id, "BEAM_COMPLETED", 1.0, {
                    "stage": "moqui_beam_processing",
                    "beam_id": beam_id,
                    "gpu_id": gpu_id
                })
            else:
                logging.info(f"MOQUI beam {beam_id} completed for case: {case_id} on GPU {gpu_id}")
            if status_display:
                status_display.update_case_status(
                    case_id=case_id,
                    status="PROCESSING",
                    current_task="Beam Calculation",
                    current_step=3,
                    total_steps=4,
                    detailed_status=f"Beam {beam_id} completed on GPU {gpu_id}"
                )
            return True
        else:
            if self.logger:
                self.logger.log_case_progress(case_id, "BEAM_FAILED", 0.0, {
                    "stage": "moqui_beam_processing",
                    "beam_id": beam_id,
                    "gpu_id": gpu_id,
                    "error": result['stderr'].strip(),
                    "exit_code": result['exit_code']
                })
            else:
                logging.error(f"MOQUI beam {beam_id} failed for case: {case_id}, Error: {result['stderr']}")
            if status_display:
                status_display.update_case_status(
                    case_id=case_id,
                    status="PROCESSING",
                    stage="Beam Failed",
                    error_message=f"Beam {beam_id} failed on GPU {gpu_id}: {result['stderr'][:100]}...",
                    transfer_info=""
                )
            return False

    def run_raw_to_dicom_converter(self, case_id: str, workspace_path: str = None, status_display=None) -> bool:
        """Run raw to DICOM converter."""
        workspace_path = workspace_path or self.remote_workspace
        case_path = f"{workspace_path}/{case_id}"
        # Use working directory change to ensure proper module imports
        input_file = f"{case_path}/moqui_output/dose.raw"
        output_file = f"{case_path}/moqui_output/RTDOSE.dcm"
        command = f"{self.venv_python_path} ./moqui_raw2dicom.py --input {shlex.quote(input_file)} --output {shlex.quote(output_file)}"
        
        # Update status display - starting conversion
        if status_display:
            status_display.update_case_status(
                case_id=case_id,
                status="PROCESSING",
                current_task="DICOM Conversion",
                current_step=4,
                total_steps=4,
                detailed_status="Converting raw data to DICOM format..."
            )
        
        result = self.execute_command_with_workdir(command, self.working_directories["raw2dicom"])
        
        if result["exit_code"] == 0:
            if self.logger:
                self.logger.log_case_progress(case_id, "RAW_TO_DICOM_SUCCESS", 1.0, {
                    "stage": "raw_to_dicom",
                    "stdout": result['stdout'].strip()
                })
            else:
                logging.info(f"Raw to DICOM conversion completed for case: {case_id}")
            if status_display:
                status_display.update_case_status(
                    case_id=case_id,
                    status="COMPLETED",
                    current_task="DICOM Conversion",
                    current_step=4,
                    total_steps=4,
                    detailed_status="DICOM conversion completed successfully"
                )
            return True
        else:
            if self.logger:
                self.logger.log_case_progress(case_id, "RAW_TO_DICOM_FAILED", 0.0, {
                    "stage": "raw_to_dicom",
                    "error": result['stderr'].strip(),
                    "exit_code": result['exit_code']
                })
            else:
                logging.error(f"Raw to DICOM conversion failed for case: {case_id}, Error: {result['stderr']}")
            if status_display:
                status_display.update_case_status(
                    case_id=case_id,
                    status="PROCESSING",
                    stage="Conversion Failed",
                    error_message=f"DICOM conversion failed: {result['stderr'][:100]}...",
                    transfer_info=""
                )
            return False

    def check_process_status(self, process_name: str) -> List[Dict[str, str]]:
        """Check status of processes by name."""
        command = f"ps aux | grep {process_name} | grep -v grep"
        
        result = self.execute_command(command)
        
        processes = []
        if result["exit_code"] == 0 and result["stdout"]:
            for line in result["stdout"].strip().split('\n'):
                if line.strip():
                    parts = line.split()
                    if len(parts) >= 11:
                        processes.append({
                            "pid": parts[1],
                            "cpu": parts[2],
                            "memory": parts[3],
                            "command": ' '.join(parts[10:])
                        })
        
        return processes

    def kill_process(self, pid: int, signal_type: str = "TERM") -> bool:
        """Kill process by PID."""
        command = f"kill -{signal_type} {pid}"
        
        result = self.execute_command(command)
        
        if result["exit_code"] == 0:
            logging.info(f"Process {pid} killed successfully")
            return True
        else:
            logging.error(f"Failed to kill process {pid}: {result['stderr']}")
            return False

    def check_file_exists(self, file_path: str) -> bool:
        """Check if file exists on remote system."""
        command = f"test -f {file_path}"
        
        result = self.execute_command(command)
        return result["exit_code"] == 0

    def check_directory_exists(self, dir_path: str) -> bool:
        """Check if directory exists on remote system."""
        command = f"test -d {dir_path}"
        
        result = self.execute_command(command)
        return result["exit_code"] == 0

    def create_directory(self, dir_path: str) -> bool:
        """Create directory on remote system."""
        command = f"mkdir -p {dir_path}"
        
        result = self.execute_command(command)
        
        if result["exit_code"] == 0:
            logging.info(f"Directory created: {dir_path}")
            return True
        else:
            logging.error(f"Failed to create directory {dir_path}: {result['stderr']}")
            return False

    def get_system_info(self) -> Dict[str, str]:
        """Get system information."""
        commands = {
            "hostname": "hostname",
            "uptime": "uptime",
            "memory": "free -h",
            "disk": "df -h",
            "cpu": "nproc",
            "load": "cat /proc/loadavg"
        }
        
        system_info = {}
        for key, command in commands.items():
            result = self.execute_command(command)
            if result["exit_code"] == 0:
                system_info[key] = result["stdout"].strip()
            else:
                system_info[key] = "N/A"
        
        return system_info

    def monitor_log_file(self, log_path: str, lines: int = 10) -> str:
        """Monitor log file (tail)."""
        command = f"tail -n {lines} {log_path}"
        
        result = self.execute_command(command)
        
        if result["exit_code"] == 0:
            return result["stdout"]
        else:
            return f"Error reading log: {result['stderr']}"

    def cleanup_old_files(self, directory: str, days: int = 7) -> bool:
        """Clean up old files in directory."""
        command = f"find {directory} -type f -mtime +{days} -delete"
        
        result = self.execute_command(command)
        
        if result["exit_code"] == 0:
            logging.info(f"Cleanup completed in {directory}")
            return True
        else:
            logging.error(f"Cleanup failed in {directory}: {result['stderr']}")
            return False

    def get_disk_usage(self, path: str = "/") -> Dict[str, str]:
        """Get disk usage information."""
        command = f"df -h {path}"
        
        result = self.execute_command(command)
        
        if result["exit_code"] == 0:
            lines = result["stdout"].strip().split('\n')
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

    def get_connection_info(self) -> Dict[str, Any]:
        """Get connection information."""
        return {
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "connected": self.connected
        }

    def __enter__(self):
        """Context manager entry."""
        if not self.connect():
            raise ConnectionError("Failed to establish SSH connection")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.disconnect()

    

# Example usage
if __name__ == '__main__':
    import os
    from dotenv import load_dotenv

    # Load environment variables
    load_dotenv()

    # ConfigManager is now used inside RemoteExecutor, but we still need credentials and host
    config_manager = ConfigManager(config_path='config.json')
    config = config_manager.get_config()

    # Get credentials from environment variables or config
    username = os.getenv("LINUX_USERNAME", config.get("credentials", {}).get("username"))
    password = os.getenv("LINUX_PASSWORD", config.get("credentials", {}).get("password"))
    host = config.get("servers", {}).get("linux_gpu")

    if not all([host, username, password]):
        print("Error: Missing host, username, or password in config or environment variables.")
        exit(1)

    try:
        # RemoteExecutor now loads its own config.
        with RemoteExecutor(host, username, password) as executor:
            print(f"Successfully connected to {host}")

            # Example 1: Execute a simple command
            print("\n--- Testing simple command ---")
            result = executor.execute_command("ls -l")
            if result["exit_code"] == 0:
                print("ls -l successful:\n", result["stdout"])
            else:
                print("ls -l failed:\n", result["stderr"])

            # Example 2: Run a Python script (assuming a test script exists)
            print("\n--- Testing Python script execution ---")
            # Create a dummy script for testing
            executor.execute_command("echo 'print(\"Hello from remote Python!\")' > test_script.py")
            if executor.run_python_script("test_script.py"):
                print("Python script ran successfully.")
            else:
                print("Python script execution failed.")
            executor.execute_command("rm test_script.py")

            # Example 3: Check for a process
            print("\n--- Testing process check ---")
            processes = executor.check_process_status("sshd")
            if processes:
                print(f"Found {len(processes)} sshd processes.")
                for p in processes:
                    print(f"  PID: {p['pid']}, Command: {p['command']}")
            else:
                print("No sshd processes found.")

            # Example 4: Get system info
            print("\n--- Testing system info ---")
            info = executor.get_system_info()
            for key, value in info.items():
                print(f"{key.capitalize()}: {value}")

    except (ConnectionError, ValueError) as e:
        print(f"An error occurred: {e}")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")