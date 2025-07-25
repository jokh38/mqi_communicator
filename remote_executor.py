import paramiko
import time
import socket
import shlex
from typing import Dict, List, Optional, Any
import threading
import signal
from datetime import datetime
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
        # self.moqui_binary_path = self.paths.get("linux_moqui_binary")  # Removed per revision plan
        self.raw_to_dcm_path = self.paths.get("linux_raw_to_dcm")
        # This will now fetch the path for Dose_raw: "/home/gpuadmin/MOQUI_SMC/Dose_raw"
        self.moqui_outputs_path = self.paths.get("linux_moqui_outputs_dir")
        # Add a new variable for the interpreter's output path: "/home/gpuadmin/MOQUI_SMC/Outputs_csv"
        self.moqui_interpreter_outputs_path = self.paths.get("linux_moqui_interpreter_outputs_dir")
        self.venv_python_path = self.paths.get("linux_venv_python", "python3")  # Fallback to python3 if not configured
        # Add path for MOQUI execution executable
        self.moqui_execution_path = self.paths.get("linux_moqui_execution")

        if not all([self.remote_workspace, self.mqi_interpreter_path, self.raw_to_dcm_path, self.moqui_outputs_path, self.moqui_interpreter_outputs_path, self.venv_python_path, self.moqui_execution_path]):
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
                self.logger.warning(f"SSH connection test failed, reconnecting: {e}")
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
                self.logger.error(f"Command execution failed: {e}")
            return {
                "stdout": "",
                "stderr": f"Execution error: {str(e)}",
                "exit_code": -1
            }

    def execute_remote_command(self, command: str, timeout: Optional[int] = None) -> tuple:
        """Execute remote command and return (stdout, stderr, exit_code) tuple for enhanced monitoring."""
        result = self.execute_command(command, timeout)
        return (result["stdout"], result["stderr"], result["exit_code"])

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
                    
                line_str = line.rstrip()
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
                self.logger.error(f"Streaming command execution failed: {e}")
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
            self.logger.info(f"Python script executed successfully using {self.venv_python_path}: {script_path}")
            return True
        else:
            self.logger.error(f"Python script failed: {script_path}, Error: {result['stderr']}")
            return False


    def update_moqui_tps_in(self, target_path: str, dynamic_params: Dict[str, Any]) -> bool:
        """Update moqui_tps.in configuration file using config-based template."""
        try:
            # Get template from ConfigManager
            config_manager = ConfigManager()
            template_dict = config_manager.get_moqui_tps_template()
            
            if not template_dict:
                self.logger.error(f"Failed to get moqui_tps template from configuration")
                return False
            
            # Merge template with dynamic parameters
            merged_params = template_dict.copy()
            merged_params.update(dynamic_params)
            
            # Convert dictionary to "Key Value" formatted string
            tps_content_lines = []
            for key, value in merged_params.items():
                # Convert boolean values to lowercase strings as expected by MOQUI
                if isinstance(value, bool):
                    value = str(value).lower()
                tps_content_lines.append(f"{key} {value}")
            
            tps_content = "\n".join(tps_content_lines)
            
            # Write content to target path using heredoc for stability
            # Escape single quotes in content for heredoc
            escaped_content = tps_content.replace("'", "'\"'\"'")
            write_command = f"cat <<'EOF' > {shlex.quote(target_path)}\n{escaped_content}\nEOF"
            
            write_result = self.execute_command(write_command)
            if write_result["exit_code"] != 0:
                self.logger.error(f"Failed to write moqui_tps.in to {target_path}: {write_result['stderr']}")
                return False
            
            self.logger.info(f"Successfully generated moqui_tps.in with {len(merged_params)} parameters")
            return True
            
        except Exception as e:
            self.logger.error(f"Error updating moqui_tps.in: {e}")
            return False

    def run_moqui_interpreter(self, case_id: str, log_dir: str = None, workspace_path: str = None, status_display=None) -> Dict[str, Any]:
        """Run moqui interpreter for case parsing and return PID information."""
        workspace_path = workspace_path or self.remote_workspace
        case_path = f"{workspace_path}/{case_id}"
        
        # The log directory should be the same as the case path where DCM files are located
        # This is where find_dcm_file_in_logdir will search for DCM files
        log_directory = log_dir or case_path
        
        # Ensure the output directory exists
        self.create_directory(self.moqui_interpreter_outputs_path)
        
        # Enhanced command to capture all error output including Python tracebacks
        # Use working directory change to ensure config files are found
        # Run in background to capture PID
        command = (f"nohup {self.venv_python_path} -u ./main_cli.py "
                   f"--logdir {shlex.quote(log_directory)} "
                   f"--outputdir {shlex.quote(self.moqui_interpreter_outputs_path)} > interpreter.log 2>&1 & echo $!")
        
        # Log MOQUI interpreter execution start
        if self.logger:
            self.logger.log_structured("INFO", "MOQUI interpreter started with PID tracking", {
                "case_id": case_id,
                "command": command,
                "log_dir": log_directory,
                "case_path": case_path,
                "output_dir": self.moqui_interpreter_outputs_path
            })
        else:
            self.logger.info(f"Executing MOQUI interpreter for case {case_id} with command: {command}")

        # Update status display - starting interpreter
        if status_display:
            status_display.update_case_status(
                case_id=case_id,
                status="PROCESSING",
                current_task="MOQUI Interpreter",
                detailed_status="Parsing RTPLAN and preparing inputs..."
            )
        
        # Get PID first
        pid_result = self.execute_command_with_workdir(command, self.working_directories["mqi_interpreter"], timeout=30)
        
        if pid_result["exit_code"] != 0:
            error_msg = f"Failed to start interpreter process: {pid_result['stderr']}"
            if self.logger:
                self.logger.error(error_msg)
            return {"success": False, "error": error_msg, "remote_pid": None}
        
        try:
            remote_pid = int(pid_result["stdout"].strip())
        except (ValueError, AttributeError):
            error_msg = f"Failed to parse PID from output: {pid_result['stdout']}"
            if self.logger:
                self.logger.error(error_msg)
            return {"success": False, "error": error_msg, "remote_pid": None}
        
        if self.logger:
            self.logger.info(f"Started MOQUI interpreter for case {case_id} with PID {remote_pid}")
        
        # Wait for process to complete (poll periodically)
        import time
        timeout_seconds = 1800  # 30 minute timeout for interpreter
        poll_interval = 10  # Check every 10 seconds
        elapsed_time = 0
        
        while elapsed_time < timeout_seconds:
            # Check if process is still running
            check_result = self.execute_command(f"ps -p {remote_pid}")
            if check_result["exit_code"] != 0:
                # Process has completed, check the log for results
                log_result = self.execute_command_with_workdir("cat interpreter.log", self.working_directories["mqi_interpreter"])
                stdout_output = log_result.get('stdout', '').strip()
                
                # Extract error information for better visibility
                has_python_error = any(keyword in stdout_output.lower() for keyword in 
                                      ['traceback', 'error:', 'exception:', 'failed', 'errno'])
                
                # Check if process completed successfully (no Python errors and expected outputs exist)
                if not has_python_error:
                    # Extract gantry information from interpreter output
                    gantry_info = self._extract_gantry_info_from_output(stdout_output)
                    
                    # Update case_status.json with gantry information
                    if gantry_info:
                        self._update_case_status_with_gantry(case_id, gantry_info)
                    
                    if self.logger:
                        self.logger.log_case_progress(case_id, "INTERPRETER_SUCCESS", 1.0, {
                            "stage": "moqui_interpreter",
                            "stdout": stdout_output,
                            "remote_pid": remote_pid,
                            "gantry_info": gantry_info
                        })
                    else:
                        self.logger.info(f"MOQUI interpreter completed successfully for case {case_id}")
                    if status_display:
                        status_display.update_case_status(
                            case_id=case_id,
                            status="PROCESSING",
                            current_task="MOQUI Interpreter",
                            detailed_status="Interpreter completed successfully"
                        )
                    return {"success": True, "remote_pid": remote_pid, "gantry_info": gantry_info}
                else:
                    # Failure case - ensure ALL error information is captured
                    if self.logger:
                        self.logger.error(f"MOQUI interpreter FAILED for case {case_id}")
                        self.logger.error(f"Full error output from GPU server:")
                        self.logger.error(f"{stdout_output}")
                        
                        self.logger.log_case_progress(case_id, "INTERPRETER_FAILED", 0.0, {
                            "stage": "moqui_interpreter",
                            "error": stdout_output,
                            "remote_pid": remote_pid
                        })
                    else:
                        self.logger.error(f"MOQUI interpreter FAILED for case {case_id}")
                        
                    if status_display:
                        # Show more error details in status display
                        error_preview = stdout_output[:200] + "..." if len(stdout_output) > 200 else stdout_output
                        status_display.update_case_status(
                            case_id=case_id,
                            status="PROCESSING",
                            stage="Interpreter Failed",
                            error_message=f"Interpreter failed: {error_preview}",
                            transfer_info=""
                        )
                    return {"success": False, "error": stdout_output, "remote_pid": remote_pid}
                    
            time.sleep(poll_interval)
            elapsed_time += poll_interval
        
        # Timeout reached
        error_msg = f"MOQUI interpreter timed out after {timeout_seconds} seconds"
        if self.logger:
            self.logger.error(error_msg)
        # Kill the timed-out process
        self.execute_command(f"kill {remote_pid}")
        return {"success": False, "error": error_msg, "remote_pid": remote_pid}

    def run_moqui_beam(self, case_id: str, beam_id: int, gpu_id: int, 
                      workspace_path: str = None, status_display=None) -> Dict[str, Any]:
        """Run moqui beam calculation on specific GPU and return PID information."""
        workspace_path = workspace_path or self.remote_workspace
        case_workspace_path = f"{workspace_path}/{case_id}"
        
        # Ensure the case-specific output directory exists
        case_output_dir = f"{self.moqui_outputs_path}/{case_id}"
        self.create_directory(case_output_dir)
        
        # Run from the tps_env directory as specified in revision requirements  
        # Use subshell to isolate directory change and prevent working directory side effects
        tps_env_path = self.working_directories["tps_env"]
        command = f"(cd {shlex.quote(tps_env_path)} && CUDA_VISIBLE_DEVICES={gpu_id} nohup {shlex.quote(self.moqui_execution_path)} > moqui.log 2>&1 & echo $!)"
        
        # Update status display - starting beam calculation
        if status_display:
            status_display.update_case_status(
                case_id=case_id,
                status="PROCESSING",
                current_task="Beam Calculation",
                detailed_status=f"Processing beam {beam_id} on GPU {gpu_id}"
            )
        
        # Get PID first
        pid_result = self.execute_command(command, timeout=30)
        
        if pid_result["exit_code"] != 0:
            error_msg = f"Failed to start beam process: {pid_result['stderr']}"
            if self.logger:
                self.logger.error(error_msg)
            return {"success": False, "error": error_msg, "remote_pid": None}
        
        try:
            remote_pid = int(pid_result["stdout"].strip())
        except (ValueError, AttributeError):
            error_msg = f"Failed to parse PID from output: {pid_result['stdout']}"
            if self.logger:
                self.logger.error(error_msg)
            return {"success": False, "error": error_msg, "remote_pid": None}
        
        if self.logger:
            self.logger.info(f"Started beam calculation for case {case_id} with PID {remote_pid}")
        
        # Wait for process to complete (poll periodically)
        import time
        timeout_seconds = 3600  # 1 hour timeout
        poll_interval = 30  # Check every 30 seconds
        elapsed_time = 0
        
        while elapsed_time < timeout_seconds:
            # Check if process is still running
            check_result = self.execute_command(f"ps -p {remote_pid}")
            if check_result["exit_code"] != 0:
                # Process has completed - capture stdout, stderr, and exit_code for enhanced monitoring
                log_result = self.execute_command(f"cat {tps_env_path}/moqui.log")
                stdout_output = log_result.get('stdout', '').strip() if log_result["exit_code"] == 0 else ''
                stderr_output = log_result.get('stderr', '').strip() if log_result["exit_code"] != 0 else ''
                
                # Get the actual exit code from the completed process (since we can't get it from nohup)
                # We'll determine success/failure based on output file existence and log content
                output_check_result = self.execute_command(f"test -f {self.moqui_outputs_path}/{case_id}/dose.raw")
                execution_exit_code = 0 if output_check_result["exit_code"] == 0 else 1
                
                # Analyze the results as specified in monitoring plan
                if execution_exit_code == 0:
                    # Success case - log detailed success information
                    if self.logger:
                        self.logger.info(f"Remote execution successful (Exit Code: {execution_exit_code})")
                        if stdout_output:
                            self.logger.info(f"MOQUI execution output: {stdout_output[:200]}...")  # Log key info
                        
                        
                        self.logger.log_case_progress(case_id, "BEAM_COMPLETED", 1.0, {
                            "stage": "moqui_beam_processing",
                            "beam_id": beam_id,
                            "gpu_id": gpu_id,
                            "remote_pid": remote_pid,
                            "exit_code": execution_exit_code,
                            "stdout_preview": stdout_output[:100] if stdout_output else ""
                        })
                    else:
                        self.logger.info(f"MOQUI beam {beam_id} completed for case: {case_id} on GPU {gpu_id}")
                    
                    if status_display:
                        status_display.update_case_status(
                            case_id=case_id,
                            status="PROCESSING",
                            current_task="Beam Calculation",
                            detailed_status=f"Beam {beam_id} completed on GPU {gpu_id}"
                        )
                    return {"success": True, "remote_pid": remote_pid, "exit_code": execution_exit_code, "stdout": stdout_output}
                else:
                    # Failure case - log detailed failure report for quick debugging
                    
                    if self.logger:
                        self.logger.error(f"Remote execution failed (Exit Code: {execution_exit_code})")
                        self.logger.error(f"Error Details: {stderr_output if stderr_output else 'No stderr output'}")
                        if stdout_output:
                            self.logger.error(f"Stdout Details: {stdout_output}")
                        
                        self.logger.log_case_progress(case_id, "BEAM_FAILED", 0.0, {
                            "stage": "moqui_beam_processing",
                            "beam_id": beam_id,
                            "gpu_id": gpu_id,
                            "error": stderr_output or "Process failed without error output",
                            "remote_pid": remote_pid,
                            "exit_code": execution_exit_code,
                            "stdout": stdout_output,
                            "stderr": stderr_output
                        })
                    else:
                        self.logger.error(f"MOQUI beam {beam_id} failed for case: {case_id}, Error: {stderr_output}")
                    
                    if status_display:
                        error_preview = (stderr_output or stdout_output or "Unknown error")[:100]
                        status_display.update_case_status(
                            case_id=case_id,
                            status="PROCESSING",
                            stage="Beam Failed",
                            error_message=f"Beam {beam_id} failed on GPU {gpu_id}: {error_preview}...",
                            transfer_info=""
                        )
                    return {"success": False, "error": stderr_output or stdout_output, "remote_pid": remote_pid, "exit_code": execution_exit_code}
                    
            time.sleep(poll_interval)
            elapsed_time += poll_interval
        
        # Timeout reached
        error_msg = f"Beam calculation timed out after {timeout_seconds} seconds"
        if self.logger:
            self.logger.error(error_msg)
        # Kill the timed-out process
        self.execute_command(f"kill {remote_pid}")
        return {"success": False, "error": error_msg, "remote_pid": remote_pid}

    def run_raw_to_dicom_converter(self, case_id: str, workspace_path: str = None, status_display=None) -> bool:
        """Run raw to DICOM converter."""
        workspace_path = workspace_path or self.remote_workspace
        case_path = f"{workspace_path}/{case_id}"
        # Use working directory change to ensure proper module imports
        input_file = f"{self.moqui_outputs_path}/{case_id}/dose.raw"
        output_file = f"{self.moqui_outputs_path}/{case_id}/RTDOSE.dcm"
        command = f"{self.venv_python_path} ./moqui_raw2dicom.py --input {shlex.quote(input_file)} --output {shlex.quote(output_file)}"
        
        # Update status display - starting conversion
        if status_display:
            status_display.update_case_status(
                case_id=case_id,
                status="PROCESSING",
                current_task="DICOM Conversion",
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
                self.logger.info(f"Raw to DICOM conversion completed for case: {case_id}")
            if status_display:
                status_display.update_case_status(
                    case_id=case_id,
                    status="COMPLETED",
                    current_task="DICOM Conversion",
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
                self.logger.error(f"Raw to DICOM conversion failed for case: {case_id}, Error: {result['stderr']}")
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
            self.logger.info(f"Process {pid} killed successfully")
            return True
        else:
            self.logger.error(f"Failed to kill process {pid}: {result['stderr']}")
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
            self.logger.info(f"Directory created: {dir_path}")
            return True
        else:
            self.logger.error(f"Failed to create directory {dir_path}: {result['stderr']}")
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
            self.logger.info(f"Cleanup completed in {directory}")
            return True
        else:
            self.logger.error(f"Cleanup failed in {directory}: {result['stderr']}")
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

    def _extract_gantry_info_from_output(self, output: str) -> Dict[str, Any]:
        """Extract gantry information from mqi_interpreter output."""
        import re
        
        gantry_info = {}
        
        # Look for gantry angle patterns in the output
        # Common patterns: "Gantry Angle: 180", "gantry_angle=180", "Processing beam with gantry 180"
        gantry_pattern = r'(?i)gantry[_\s]*(?:angle)?[_\s]*[=:]\s*(\d+(?:\.\d+)?)'
        matches = re.findall(gantry_pattern, output)
        
        if matches:
            # Extract all unique gantry angles
            gantry_angles = [float(angle) for angle in matches]
            gantry_info['gantry_angles'] = sorted(list(set(gantry_angles)))
            gantry_info['gantry_count'] = len(gantry_info['gantry_angles'])
            
            # Use the first gantry angle as primary
            if gantry_angles:
                gantry_info['primary_gantry'] = int(gantry_angles[0])
        
        # Look for beam information that might contain gantry data
        beam_pattern = r'(?i)(?:processing|beam)\s+(\d+).*?gantry[_\s]*(?:angle)?[_\s]*[=:]\s*(\d+(?:\.\d+)?)'
        beam_matches = re.findall(beam_pattern, output)
        
        if beam_matches:
            beam_gantry_map = {}
            for beam_id, gantry_angle in beam_matches:
                beam_gantry_map[int(beam_id)] = float(gantry_angle)
            gantry_info['beam_gantry_mapping'] = beam_gantry_map
        
        return gantry_info


    def _update_case_status_with_gantry(self, case_id: str, gantry_info: Dict[str, Any]):
        """Update case_status.json with gantry information."""
        import json
        import os
        from pathlib import Path
        
        try:
            # Path to case_status.json (in working directory)
            status_file_path = Path.cwd() / "case_status.json"
            
            # Read current status
            case_status = {}
            if status_file_path.exists():
                with open(status_file_path, 'r', encoding='utf-8') as f:
                    case_status = json.load(f)
            
            # Update with gantry information
            if case_id not in case_status:
                case_status[case_id] = {}
            
            case_status[case_id]['gantry_info'] = gantry_info
            case_status[case_id]['gantry_updated_time'] = str(datetime.now())
            
            # Write back to file atomically
            temp_file_path = status_file_path.with_suffix('.tmp')
            with open(temp_file_path, 'w', encoding='utf-8') as f:
                json.dump(case_status, f, indent=2, ensure_ascii=False)
            
            # Atomic rename
            temp_file_path.replace(status_file_path)
            
            if self.logger:
                self.logger.info(f"Updated case_status.json with gantry info for case {case_id}: {gantry_info}")
                
        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to update case_status.json with gantry info for case {case_id}: {e}")

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