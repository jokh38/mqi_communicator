import subprocess
import shutil
import paramiko
import os
import time
from typing import Optional, Dict, Any, NamedTuple
from pathlib import Path

class ExecutionResult(NamedTuple):
    """A structured result from a command execution."""
    success: bool
    output: str
    error: str
    return_code: int

class JobSubmissionResult(NamedTuple):
    """Result from a job submission."""
    success: bool
    job_id: Optional[str] = None
    error: Optional[str] = None

class JobStatus(NamedTuple):
    """Status of an HPC job."""
    failed: bool
    completed: bool
    error_message: Optional[str] = None

class UploadResult(NamedTuple):
    """Result from a file upload operation."""
    success: bool
    error: Optional[str] = None

class DownloadResult(NamedTuple):
    """Result from a file download operation."""
    success: bool
    error: Optional[str] = None

class ExecutionHandler:
    """
    A unified handler for executing commands and transferring files
    either locally or on a remote machine.
    """

    def __init__(self, mode: str, ssh_client: Optional[paramiko.SSHClient] = None):
        """
        Initializes the ExecutionHandler.
        Args:
            mode (str): The execution mode, either "local" or "remote".
            ssh_client (Optional[paramiko.SSHClient]): An active SSH client,
                                                     required for "remote" mode.
        """
        if mode not in ["local", "remote"]:
            raise ValueError("Mode must be either 'local' or 'remote'")

        if mode == "remote" and not ssh_client:
            raise ValueError("ssh_client is required for 'remote' mode")

        self.mode = mode
        self._ssh_client = ssh_client
        self._sftp_client = None

    def execute_command(self, command: str, cwd: Optional[Path] = None) -> ExecutionResult:
        """Executes a command."""
        if self.mode == "local":
            try:
                result = subprocess.run(
                    command, shell=True, check=True, capture_output=True, text=True, cwd=cwd
                )
                return ExecutionResult(
                    success=True,
                    output=result.stdout,
                    error=result.stderr,
                    return_code=result.returncode,
                )
            except subprocess.CalledProcessError as e:
                return ExecutionResult(
                    success=False,
                    output=e.stdout,
                    error=e.stderr,
                    return_code=e.returncode,
                )
        else:  # remote
            if not self._ssh_client:
                raise ConnectionError("SSH client not available for remote execution.")

            full_command = f"cd {cwd} && {command}" if cwd else command
            stdin, stdout, stderr = self._ssh_client.exec_command(full_command)
            exit_code = stdout.channel.recv_exit_status()
            return ExecutionResult(
                success=exit_code == 0,
                output=stdout.read().decode("utf-8"),
                error=stderr.read().decode("utf-8"),
                return_code=exit_code,
            )

    def upload_file(self, local_path: str, remote_path: str) -> UploadResult:
        """Uploads a file. In local mode, this is a copy."""
        try:
            if self.mode == "local":
                shutil.copy(local_path, remote_path)
            else:  # remote
                if not self._ssh_client:
                    raise ConnectionError("SSH client not available for remote upload.")
                if not self._sftp_client:
                    self._sftp_client = self._ssh_client.open_sftp()

                self._mkdir_p(self._sftp_client, os.path.dirname(remote_path))
                self._sftp_client.put(local_path, remote_path)
            return UploadResult(success=True)
        except Exception as e:
            return UploadResult(success=False, error=str(e))

    def run_raw_to_dcm(self, input_file: Path, output_dir: Path, case_path: Path) -> ExecutionResult:
        """Runs the raw_to_dcm conversion. Local-only for now."""
        if self.mode == 'local':
            # This logic would be more sophisticated, with template commands etc.
            command = f"raw_to_dcm --input {input_file} --output {output_dir}"
            return self.execute_command(command, cwd=case_path)
        else:
            raise NotImplementedError("run_raw_to_dcm is not supported in remote mode.")

    def submit_simulation_job( self, beam_id: str, remote_beam_dir: str, gpu_uuid: str ) -> JobSubmissionResult:
        """Submit a MOQUI simulation job to the HPC system for a single beam."""
        if self.mode == 'remote':
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
                if not self._sftp_client:
                    self._sftp_client = self._ssh_client.open_sftp()
                with self._sftp_client.open(job_script_path, "w") as f:
                    f.write(job_script)
                submit_command = f"sbatch {job_script_path}"
                result = self.execute_command(submit_command)
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
                return JobSubmissionResult(success=True, job_id=job_id)
            except Exception as e:
                return JobSubmissionResult(success=False, error=str(e))
        else:
            raise NotImplementedError("submit_simulation_job is not supported in local mode.")

    def wait_for_job_completion(self, job_id: str, timeout_seconds: int = 3600) -> JobStatus:
        """Wait for an HPC job to complete, polling at regular intervals."""
        if self.mode == 'remote':
            start_time = time.time()
            poll_interval = 30
            while time.time() - start_time < timeout_seconds:
                status_command = f"squeue -j {job_id} --noheader --format='%T'"
                result = self.execute_command(status_command)
                if result.success and result.output.strip():
                    status = result.output.strip().upper()
                    if status in ["COMPLETED", "COMPLETING"]:
                        return JobStatus(failed=False, completed=True)
                    elif status in ["FAILED", "CANCELLED", "TIMEOUT", "NODE_FAIL"]:
                        return JobStatus(failed=True, completed=True, error_message=f"Job failed with status: {status}")
                else: # If squeue fails, check sacct
                    history_command = f"sacct -j {job_id} --noheader --format='State' | head -1"
                    history_result = self.execute_command(history_command)
                    if history_result.success and "COMPLETED" in history_result.output.strip().upper():
                        return JobStatus(failed=False, completed=True)
                time.sleep(poll_interval)
            return JobStatus(failed=True, completed=False, error_message=f"Timeout after {timeout_seconds} seconds")
        else:
            raise NotImplementedError("wait_for_job_completion is not supported in local mode.")

    def download_file(self, remote_file_path: str, local_dir: Path) -> DownloadResult:
        """Download a single file from the HPC system."""
        if self.mode == 'remote':
            try:
                if not self._sftp_client:
                    self._sftp_client = self._ssh_client.open_sftp()
                local_dir.mkdir(parents=True, exist_ok=True)
                remote_filename = os.path.basename(remote_file_path)
                local_file_path = local_dir / remote_filename
                self._sftp_client.get(remote_file_path, str(local_file_path))
                return DownloadResult(success=True)
            except Exception as e:
                return DownloadResult(success=False, error=str(e))
        else:
            raise NotImplementedError("download_file is not supported in local mode.")

    def cleanup_remote_directory(self, remote_dir: str) -> ExecutionResult:
        """Clean up a remote directory and its contents."""
        if self.mode == 'remote':
            cleanup_command = f"rm -rf {remote_dir}"
            return self.execute_command(cleanup_command)
        else:
            raise NotImplementedError("cleanup_remote_directory is not supported in local mode.")

    def upload_to_pc_localdata(self, local_path: Path, case_id: str, settings: Any) -> UploadResult:
        """Uploads a file or an entire directory to the PC_localdata server."""
        if self.mode == 'remote':
            if not local_path.exists():
                return UploadResult(success=False, error=f"Local path does not exist: {local_path}")

            config = settings.get_pc_localdata_connection()
            if not config:
                return UploadResult(success=False, error="PC_localdata connection not configured.")

            host = config.get("host")
            user = config.get("user")
            key_path = config.get("ssh_key_path")
            remote_base_dir = config.get("remote_base_dir", "/")

            if not all([host, user, key_path]):
                return UploadResult(success=False, error="PC_localdata connection is missing required fields.")

            try:
                with paramiko.SSHClient() as ssh_client:
                    ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                    ssh_client.connect(hostname=host, username=user, key_filename=key_path, timeout=30)

                    with ssh_client.open_sftp() as sftp_client:
                        if local_path.is_dir():
                            remote_case_dir = f"{remote_base_dir}/{case_id}".replace("\\\\", "/")
                            self._mkdir_p(sftp_client, remote_case_dir)

                            for root, _, files in os.walk(str(local_path)):
                                relative_path = Path(root).relative_to(local_path)
                                remote_dir = (Path(remote_case_dir) / relative_path).as_posix()
                                if str(relative_path) != '.':
                                    self._mkdir_p(sftp_client, remote_dir)
                                for file in files:
                                    local_file = Path(root) / file
                                    remote_file = (Path(remote_dir) / file).as_posix()
                                    sftp_client.put(str(local_file), remote_file)
                        else:
                            remote_target_dir = f"{remote_base_dir}/{case_id}".replace("\\\\", "/")
                            remote_file_path = f"{remote_target_dir}/{local_path.name}"
                            self._mkdir_p(sftp_client, remote_target_dir)
                            sftp_client.put(str(local_path), remote_file_path)
                return UploadResult(success=True)
            except Exception as e:
                return UploadResult(success=False, error=str(e))
        else: # local mode
            # For local mode, we can simulate a copy to a 'localdata' directory
            local_dest = Path(f"./localdata_uploads/{case_id}")
            local_dest.mkdir(parents=True, exist_ok=True)
            if local_path.is_dir():
                shutil.copytree(local_path, local_dest / local_path.name)
            else:
                shutil.copy(local_path, local_dest / local_path.name)
            return UploadResult(success=True)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._sftp_client:
            self._sftp_client.close()
        # The management of the ssh_client connection itself is left
        # to the caller, as it might be shared.

    def _mkdir_p(self, sftp: paramiko.SFTPClient, remote_directory: str):
        """Creates a directory and all its parents recursively on the remote server."""
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