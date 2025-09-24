# =====================================================================================
# Target File: src/handlers/remote_handler.py
# Source Reference: src/remote_handler.py
# =====================================================================================
"""Manages HPC communication, remote execution, and file transfers."""

import os
import time
from typing import Optional, Dict, Any
from pathlib import Path
import paramiko
from paramiko import SSHClient, SFTPClient

from src.infrastructure.logging_handler import StructuredLogger
from src.config.settings import Settings
from src.utils.retry_policy import RetryPolicy
from src.domain.errors import ProcessingError
from src.handlers.local_handler import ExecutionResult


class UploadResult:
    """Result from a file upload operation."""

    def __init__(self, success: bool, error: Optional[str] = None):
        self.success = success
        self.error = error


class JobSubmissionResult:
    """Result from an HPC job submission."""

    def __init__(self,
                 success: bool,
                 job_id: Optional[str] = None,
                 error: Optional[str] = None):
        self.success = success
        self.job_id = job_id
        self.error = error


class JobStatus:
    """Status of an HPC job."""

    def __init__(self,
                 job_id: str,
                 status: str,
                 failed: bool,
                 completed: bool,
                 error_message: Optional[str] = None):
        self.job_id = job_id
        self.status = status
        self.failed = failed
        self.completed = completed
        self.error_message = error_message


class DownloadResult:
    """Result from a file download operation."""

    def __init__(self, success: bool, error: Optional[str] = None):
        self.success = success
        self.error = error


class RemoteHandler:
    """Manages HPC communication (SSH/SFTP), remote execution and file transfers.

    This class uses injected dependencies for retry policy and settings,
    and provides improved error handling and connection management.
    """

    def __init__(self,
                 settings: Settings,
                 logger: StructuredLogger,
                 retry_policy: RetryPolicy):
        """Initialize RemoteHandler with injected dependencies.

        Args:
            settings (Settings): Application settings containing HPC configuration.
            logger (StructuredLogger): Logger for recording operations.
            retry_policy (RetryPolicy): Retry policy for failed operations.
        """
        self.settings = settings
        self.logger = logger
        self.retry_policy = retry_policy
        self._ssh_client: Optional[SSHClient] = None
        self._sftp_client: Optional[SFTPClient] = None
        self._connected = False

    def connect(self) -> None:
        """Establish SSH/SFTP connections to the remote HPC system.

        Raises:
            ProcessingError: If HPC connection settings are not configured or
            connection fails.
        """
        self.logger.info(
            "Establishing HPC connection", {
                "host":
                (self.settings.hpc.hostname
                 if hasattr(self.settings, "hpc") else "configured")
            },
        )
        try:
            hpc_config = self.settings.get_hpc_connection()
            if not hpc_config:
                raise ProcessingError(
                    "HPC connection settings not configured.")
            self._ssh_client = paramiko.SSHClient()
            self._ssh_client.set_missing_host_key_policy(
                paramiko.AutoAddPolicy())
            self._ssh_client.connect(
                hostname=hpc_config.get("host"),
                username=hpc_config.get("user"),
                key_filename=hpc_config.get("ssh_key_path"),
                timeout=hpc_config.get("connection_timeout_seconds", 30),
            )
            self._sftp_client = self._ssh_client.open_sftp()
            self._connected = True
            self.logger.info("HPC connection established successfully")
        except Exception as e:
            self.logger.error("Failed to establish HPC connection",
                              {"error": str(e)})
            self._cleanup_connections()
            raise ProcessingError(f"Failed to connect to HPC system: {e}")

    def disconnect(self) -> None:
        """Close SSH/SFTP connections."""
        self.logger.debug("Closing HPC connections")
        self._cleanup_connections()

    def _cleanup_connections(self) -> None:
        """Clean up connection resources."""
        if self._sftp_client:
            try:
                self._sftp_client.close()
            except Exception as e:
                self.logger.warning("Error closing SFTP connection",
                                    {"error": str(e)})
            self._sftp_client = None
        if self._ssh_client:
            try:
                self._ssh_client.close()
            except Exception as e:
                self.logger.warning("Error closing SSH connection",
                                    {"error": str(e)})
            self._ssh_client = None
        self._connected = False

    def _ensure_connected(self) -> None:
        """Ensure a connection is established before performing operations."""
        if not self._connected or not self._ssh_client:
            self.connect()

    def execute_remote_command(
        self,
        context_id: str,
        command: str,
        remote_cwd: Optional[str] = None
    ) -> ExecutionResult:
        """Execute a command on the remote HPC system.

        Args:
            context_id (str): An identifier for the operation for logging purposes
            (e.g., beam_id, 'gpu_monitoring').
            command (str): The command to execute.
            remote_cwd (Optional[str]): The remote working directory (optional).

        Returns:
            ExecutionResult: An ExecutionResult containing the outcome of the execution.
        """
        self.logger.info(
            "Executing remote command", {
                "context_id": context_id,
                "command": command,
                "remote_cwd": remote_cwd
            },
        )

        def execute_attempt():
            self._ensure_connected()
            if not self._ssh_client:
                raise ProcessingError("SSH client not available")
            full_command = command
            if remote_cwd:
                full_command = f"cd {remote_cwd} && {command}"
            stdin, stdout, stderr = self._ssh_client.exec_command(
                full_command)
            exit_code = stdout.channel.recv_exit_status()
            output = stdout.read().decode("utf-8")
            error = stderr.read().decode("utf-8")
            return ExecutionResult(
                success=(exit_code == 0),
                output=output,
                error=error,
                return_code=exit_code,
            )

        try:
            result = self.retry_policy.execute(
                execute_attempt,
                operation_name="remote_command",
                context={
                    "context_id": context_id,
                    "command": command
                },
            )
            if result.success:
                self.logger.info(
                    "Remote command completed successfully",
                    {
                        "context_id": context_id,
                        "command": command,
                        "output_length": len(result.output),
                    },
                )
            else:
                self.logger.error(
                    "Remote command failed",
                    {
                        "context_id": context_id,
                        "command": command,
                        "return_code": result.return_code,
                        "error": result.error,
                    },
                )
            return result
        except Exception as e:
            self.logger.error(
                "Remote command execution failed after retries", {
                    "context_id": context_id,
                    "command": command,
                    "error": str(e)
                },
            )
            return ExecutionResult(success=False,
                                   output="",
                                   error=str(e),
                                   return_code=-1)

    def check_job_status(self, job_id: str) -> Dict[str, Any]:
        """Check the status of a submitted job on the HPC system.

        Args:
            job_id (str): The HPC job identifier.

        Returns:
            Dict[str, Any]: A dictionary containing job status information.
        """
        self.logger.debug("Checking HPC job status", {"job_id": job_id})
        status_command = f"squeue -j {job_id} --noheader -o %T"
        try:
            result = self.execute_remote_command("job_status_check",
                                                 status_command)
            status = "UNKNOWN"
            error_message = None
            if result.success and result.output.strip():
                status = result.output.strip().upper()
            elif not result.success:
                error_message = result.error
            return {
                "job_id": job_id,
                "status": status,
                "queue_time":
                None,
                "start_time": None,
                "completion_time": None,
                "error_message": error_message,
            }
        except Exception as e:
            self.logger.error("Failed to check job status",
                              {"job_id": job_id, "error": str(e)})
            return {
                "job_id": job_id,
                "status": "UNKNOWN",
                "error_message": str(e),
            }

    def upload_file(self, local_file: Path, remote_dir: str) -> UploadResult:
        """Upload a single file to the HPC system.

        Args:
            local_file (Path): Path to the local file to upload.
            remote_dir (str): The remote directory to upload to.

        Returns:
            UploadResult: An UploadResult indicating success or failure.
        """
        self.logger.debug(
            "Uploading file to HPC", {
                "local_file": str(local_file),
                "remote_dir": remote_dir
            },
        )
        try:
            self._ensure_connected()
            if not self._sftp_client:
                return UploadResult(success=False,
                                    error="SFTP client not available")
            if not local_file.exists():
                return UploadResult(
                    success=False,
                    error=f"Local file does not exist: {local_file}",
                )
            self._mkdir_p(self._sftp_client, remote_dir)
            remote_file_path = f"{remote_dir}/{local_file.name}".replace(
                "\\", "/")
            self._sftp_client.put(str(local_file), remote_file_path)
            self.logger.debug(
                "File uploaded successfully", {
                    "local_file": str(local_file),
                    "remote_file": remote_file_path
                },
            )
            return UploadResult(success=True)
        except Exception as e:
            error_msg = f"Failed to upload file: {e}"
            self.logger.error(error_msg, {
                "local_file": str(local_file),
                "remote_dir": remote_dir
            })
            return UploadResult(success=False, error=error_msg)

    def submit_simulation_job(
        self, beam_id: str, remote_beam_dir: str, gpu_uuid: str
    ) -> JobSubmissionResult:
        """Submit a MOQUI simulation job to the HPC system for a single beam.

        Args:
            beam_id (str): The beam identifier.
            remote_beam_dir (str): The remote directory for job execution,
            containing all necessary files.
            gpu_uuid (str): The GPU UUID to use for the simulation.

        Returns:
            JobSubmissionResult: A JobSubmissionResult with the job ID if successful.
        """
        self.logger.info(
            "Submitting HPC simulation job for beam",
            {
                "beam_id": beam_id,
                "remote_beam_dir": remote_beam_dir,
                "gpu_uuid": gpu_uuid,
            },
        )
        try:
            job_script = f"""#!/bin/bash
#SBATCH --job-name=moqui_{beam_id}
#SBATCH --output={remote_beam_dir}/simulation.log
#SBATCH --error={remote_beam_dir}/simulation.err
#SBATCH --gres=gpu:1
#SBATCH --time=01:00:00

cd {remote_beam_dir}
export CUDA_VISIBLE_DEVICES={gpu_uuid}
/usr/local/bin/moqui_simulator --input . --output output.raw
"""
            job_script_path = f"{remote_beam_dir}/submit_job.sh"
            self._ensure_connected()
            if not self._sftp_client:
                return JobSubmissionResult(
                    success=False, error="SFTP client not available")
            with self._sftp_client.open(job_script_path, "w") as f:
                f.write(job_script)
            submit_command = f"sbatch {job_script_path}"
            result = self.execute_remote_command(beam_id, submit_command)
            if not result.success:
                return JobSubmissionResult(
                    success=False,
                    error=f"Job submission failed: {result.error}",
                )
            output_lines = result.output.strip().split("\n")
            job_id = None
            for line in output_lines:
                if "Submitted batch job" in line:
                    job_id = line.split()[-1]
                    break
            if not job_id:
                return JobSubmissionResult(
                    success=False,
                    error="Could not extract job ID from sbatch output",
                )
            self.logger.info("HPC job submitted successfully for beam",
                             {"beam_id": beam_id, "job_id": job_id})
            return JobSubmissionResult(success=True, job_id=job_id)
        except Exception as e:
            error_msg = f"Job submission error: {e}"
            self.logger.error(error_msg, {"beam_id": beam_id})
            return JobSubmissionResult(success=False, error=error_msg)

    def wait_for_job_completion(self,
                                job_id: str,
                                timeout_seconds: int = 3600) -> JobStatus:
        """Wait for an HPC job to complete, polling at regular intervals.

        Args:
            job_id (str): The HPC job identifier.
            timeout_seconds (int): The maximum time to wait for completion.

        Returns:
            JobStatus: A JobStatus indicating the final job status.
        """
        self.logger.info(
            "Waiting for HPC job completion",
            {"job_id": job_id, "timeout_seconds": timeout_seconds},
        )
        start_time = time.time()
        poll_interval = 30
        while time.time() - start_time < timeout_seconds:
            try:
                status_command = f"squeue -j {job_id} --noheader --format='%T'"
                result = self.execute_remote_command("job_polling",
                                                     status_command)
                if result.success and result.output.strip():
                    status = result.output.strip().upper()
                    if status in ["COMPLETED", "COMPLETING"]:
                        self.logger.info("HPC job completed successfully",
                                         {"job_id": job_id})
                        return JobStatus(job_id=job_id,
                                         status=status,
                                         failed=False,
                                         completed=True)
                    elif status in [
                            "FAILED", "CANCELLED", "TIMEOUT", "NODE_FAIL"
                    ]:
                        self.logger.error("HPC job failed", {
                            "job_id": job_id,
                            "status": status
                        })
                        return JobStatus(
                            job_id=job_id,
                            status=status,
                            failed=True,
                            completed=True,
                            error_message=f"Job failed with status: {status}",
                        )
                    else:
                        self.logger.debug(
                            "HPC job still running",
                            {"job_id": job_id, "status": status},
                        )
                else:
                    history_command = (
                        f"sacct -j {job_id} --noheader --format='State' | head -1"
                    )
                    history_result = self.execute_remote_command(
                        "job_history", history_command)
                    if history_result.success and history_result.output.strip(
                    ):
                        status = history_result.output.strip().upper()
                        if "COMPLETED" in status:
                            return JobStatus(job_id=job_id,
                                             status=status,
                                             failed=False,
                                             completed=True)
                        else:
                            return JobStatus(
                                job_id=job_id,
                                status=status,
                                failed=True,
                                completed=True,
                                error_message=f"Job finished with status: {status}",
                            )
                time.sleep(poll_interval)
            except Exception as e:
                self.logger.warning("Error checking job status",
                                    {"job_id": job_id, "error": str(e)})
                time.sleep(poll_interval)
        self.logger.error(
            "Timeout waiting for job completion",
            {"job_id": job_id, "timeout_seconds": timeout_seconds},
        )
        return JobStatus(
            job_id=job_id,
            status="TIMEOUT",
            failed=True,
            completed=False,
            error_message=f"Timeout after {timeout_seconds} seconds",
        )

    def download_file(self, remote_file_path: str,
                      local_dir: Path) -> DownloadResult:
        """Download a single file from the HPC system.

        Args:
            remote_file_path (str): The path to the remote file.
            local_dir (Path): The local directory to download to.

        Returns:
            DownloadResult: A DownloadResult indicating success or failure.
        """
        self.logger.debug(
            "Downloading file from HPC", {
                "remote_file": remote_file_path,
                "local_dir": str(local_dir)
            },
        )
        try:
            self._ensure_connected()
            if not self._sftp_client:
                return DownloadResult(success=False,
                                      error="SFTP client not available")
            local_dir.mkdir(parents=True, exist_ok=True)
            remote_filename = os.path.basename(remote_file_path)
            local_file_path = local_dir / remote_filename
            self._sftp_client.get(remote_file_path, str(local_file_path))
            self.logger.debug(
                "File downloaded successfully", {
                    "remote_file": remote_file_path,
                    "local_file": str(local_file_path)
                },
            )
            return DownloadResult(success=True)
        except Exception as e:
            error_msg = f"Failed to download file: {e}"
            self.logger.error(
                error_msg, {
                    "remote_file": remote_file_path,
                    "local_dir": str(local_dir)
                },
            )
            return DownloadResult(success=False, error=error_msg)

    def cleanup_remote_directory(self, remote_dir: str) -> bool:
        """Clean up a remote directory and its contents.

        Args:
            remote_dir (str): The remote directory to clean up.

        Returns:
            bool: True if cleanup was successful, False otherwise.
        """
        self.logger.debug("Cleaning up remote directory",
                          {"remote_dir": remote_dir})
        try:
            cleanup_command = f"rm -rf {remote_dir}"
            result = self.execute_remote_command("cleanup", cleanup_command)
            if result.success:
                self.logger.info("Remote directory cleaned up successfully",
                                 {"remote_dir": remote_dir})
                return True
            else:
                self.logger.warning("Remote directory cleanup failed", {
                    "remote_dir": remote_dir,
                    "error": result.error
                })
                return False
        except Exception as e:
            self.logger.error("Error during remote cleanup", {
                "remote_dir": remote_dir,
                "error": str(e)
            })
            return False

    def __enter__(self):
        """Context manager entry."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit with cleanup."""
        self.disconnect()

    def _mkdir_p(self, sftp: SFTPClient, remote_directory: str):
        """Creates a directory and all its parents recursively on the remote server.
        Args:
            sftp (SFTPClient): The SFTP client.
            remote_directory (str): The remote directory to create.
        """
        if remote_directory == "/":
            sftp.chdir("/")
            return
        if remote_directory == "":
            return
        try:
            sftp.chdir(remote_directory)
        except IOError:
            dirname, basename = os.path.split(remote_directory.rstrip("/"))
            self._mkdir_p(sftp, dirname)
            sftp.mkdir(basename)
            sftp.chdir(basename)
            return True
