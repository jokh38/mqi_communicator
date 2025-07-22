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
    def __init__(self, host: str, username: str, password: str, port: int = 22, timeout: int = 30):
        super().__init__(host, username, password, port, timeout)
        self.ssh: Optional[paramiko.SSHClient] = None
        
        # Load configuration
        config_manager = ConfigManager()
        self.config = config_manager.get_config()
        
        self._connection_failures = 0  # Track consecutive connection failures
        
        # Extract paths from config
        self.paths = self.config.get("paths", {})
        self.remote_workspace = self.paths.get("remote_workspace")
        self.mqi_interpreter_path = self.paths.get("linux_mqi_interpreter")
        self.moqui_binary_path = self.paths.get("linux_moqui_binary")
        self.raw_to_dcm_path = self.paths.get("linux_raw_to_dcm")

        if not all([self.remote_workspace, self.mqi_interpreter_path, self.moqui_binary_path, self.raw_to_dcm_path]):
            raise ValueError("One or more required paths are missing in the configuration file.")

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
            logging.error(f"Command execution failed: {e}")
            return {
                "stdout": "",
                "stderr": f"Execution error: {str(e)}",
                "exit_code": -1
            }

    def run_python_script(self, script_path: str, args: List[str] = None) -> bool:
        """Execute Python script remotely."""
        args = args or []
        command = f"python3 {script_path} {' '.join(args)}"
        
        result = self.execute_command(command)
        
        if result["exit_code"] == 0:
            logging.info(f"Python3 script executed successfully: {script_path}")
            return True
        else:
            logging.error(f"Python3 script failed: {script_path}, Error: {result['stderr']}")
            return False


    def run_moqui_interpreter(self, case_id: str, workspace_path: str = None, status_display=None) -> bool:
        """Run moqui interpreter for case parsing."""
        workspace_path = workspace_path or self.remote_workspace
        case_path = f"{workspace_path}/{case_id}"
        command = f"cd {shlex.quote(case_path)} && python3 {shlex.quote(self.mqi_interpreter_path)} --logdir logs --outputdir moqui_inputs"
        
        # Update status display - starting interpreter
        if status_display:
            status_display.update_case_status(
                case_id=case_id,
                status="PROCESSING",
                stage="Running Interpreter",
                transfer_info="Parsing RTPLAN and preparing inputs..."
            )
        
        result = self.execute_command(command)
        
        if result["exit_code"] == 0:
            logging.info(f"MOQUI interpreter completed for case: {case_id}")
            if status_display:
                status_display.update_case_status(
                    case_id=case_id,
                    status="PROCESSING",
                    stage="Interpreter Complete",
                    transfer_info="RTPLAN parsed successfully"
                )
            return True
        else:
            logging.error(f"MOQUI interpreter failed for case: {case_id}, Error: {result['stderr']}")
            if status_display:
                status_display.update_case_status(
                    case_id=case_id,
                    status="PROCESSING",
                    stage="Interpreter Failed",
                    error_message=f"Interpreter failed: {result['stderr'][:100]}...",
                    transfer_info=""
                )
            return False

    def run_moqui_beam(self, case_id: str, beam_id: int, gpu_id: int, 
                      workspace_path: str = None, status_display=None) -> bool:
        """Run moqui beam calculation on specific GPU."""
        workspace_path = workspace_path or self.remote_workspace
        case_path = f"{workspace_path}/{case_id}"
        command = f"cd {shlex.quote(case_path)} && CUDA_VISIBLE_DEVICES={gpu_id} {shlex.quote(self.moqui_binary_path)} --input_dir moqui_inputs --output_dir moqui_output"
        
        # Update status display - starting beam calculation
        if status_display:
            status_display.update_case_status(
                case_id=case_id,
                status="PROCESSING",
                stage="Calculating Beam",
                transfer_info=f"Processing beam {beam_id} on GPU {gpu_id}..."
            )
        
        result = self.execute_command(command, timeout=3600)  # 1 hour timeout
        
        if result["exit_code"] == 0:
            logging.info(f"MOQUI beam {beam_id} completed for case: {case_id} on GPU {gpu_id}")
            if status_display:
                status_display.update_case_status(
                    case_id=case_id,
                    status="PROCESSING",
                    stage="Beam Complete",
                    transfer_info=f"Beam {beam_id} calculated successfully on GPU {gpu_id}"
                )
            return True
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
        command = f"cd {shlex.quote(case_path)} && python3 {shlex.quote(self.raw_to_dcm_path)} --input moqui_output/dose.raw --output moqui_output/RTDOSE.dcm"
        
        # Update status display - starting conversion
        if status_display:
            status_display.update_case_status(
                case_id=case_id,
                status="PROCESSING",
                stage="Converting to DICOM",
                transfer_info="Converting raw data to DICOM format..."
            )
        
        result = self.execute_command(command)
        
        if result["exit_code"] == 0:
            logging.info(f"Raw to DICOM conversion completed for case: {case_id}")
            if status_display:
                status_display.update_case_status(
                    case_id=case_id,
                    status="PROCESSING",
                    stage="Conversion Complete",
                    transfer_info="DICOM conversion completed successfully"
                )
            return True
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

    def __del__(self):
        """Destructor - ensure connection is closed."""
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