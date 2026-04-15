"""
Provides a handler for executing commands and transferring files locally.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, NamedTuple, Optional

from src.config.settings import Settings
from src.infrastructure.logging_handler import LoggerFactory


class ExecutionResult(NamedTuple):
    """A structured result from a command execution."""
    success: bool
    output: str
    error: str
    return_code: int


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
    A handler that executes commands locally and manages file operations.
    """

    def __init__(self, settings: Optional[Settings] = None):
        """
        Initializes the ExecutionHandler.
        """
        self.settings = settings or Settings()
        try:
            self.logger = LoggerFactory.get_logger("ExecutionHandler")
        except RuntimeError:
            from src.infrastructure.logging_handler import LoggerFactory as LF
            LF.configure(self.settings)
            self.logger = LF.get_logger("ExecutionHandler")

    class JobWaitResult(NamedTuple):
        failed: bool
        error: Optional[str] = None

    def start_local_process(self, command: Any, cwd: Optional[Path] = None) -> subprocess.Popen:
        """Starts a local subprocess without blocking.

        Args:
            command: Command string or list to execute.
            cwd: Optional working directory.

        Returns:
            subprocess.Popen: The running process handle.
        """
        use_shell = isinstance(command, str)
        return subprocess.Popen(
            command,
            shell=use_shell if not isinstance(command, list) else False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd,
        )

    def wait_for_job_completion(self, job_id: Optional[str] = None, timeout: Optional[int] = None,
                                poll_interval: Optional[int] = None, log_file_path: Optional[str] = None,
                                beam_id: Optional[str] = None, case_repo: Optional[Any] = None,
                                process: Optional[subprocess.Popen] = None) -> "ExecutionHandler.JobWaitResult":
        """
        Wait for job completion by monitoring a log file for completion markers and progress.
        """
        import time
        import re
        from pathlib import Path

        # Get timeout and poll interval from settings if not provided
        if timeout is None:
            timeout = self.settings.get_processing_config().get("hpc_job_timeout_seconds", 3600)
        if poll_interval is None:
            poll_interval = self.settings.get_processing_config().get("hpc_poll_interval_seconds", 30)

        if log_file_path:
            # Monitor log file for completion patterns and progress
            completion_markers = self.settings.get_completion_patterns()
            success_pattern = completion_markers.get("success_pattern", "Simulation completed successfully")
            failure_patterns = completion_markers.get("failure_patterns", ["FATAL ERROR", "ERROR:", "Segmentation fault"])

            # Progress tracking patterns
            total_batches_pattern = re.compile(r"with (\d+) batches")
            current_batch_pattern = re.compile(r"Generating particles for \((\d+) of (\d+) batches\)")

            start_time = time.time()
            log_path = Path(log_file_path)

            total_batches = None
            last_progress = 0.0
            file_position = 0

            while time.time() - start_time < timeout:
                if log_path.exists():
                    try:
                        with open(log_path, 'r') as f:
                            # Read from last position
                            f.seek(file_position)
                            new_content = f.read()
                            file_position = f.tell()

                            if new_content:
                                # Check for failure patterns first
                                for pattern in failure_patterns:
                                    if pattern in new_content:
                                        if process:
                                            process.kill()
                                            process.wait()
                                        return ExecutionHandler.JobWaitResult(
                                            failed=True,
                                            error=f"Simulation failed: found pattern '{pattern}' in log"
                                        )

                                # Check for success pattern
                                if success_pattern in new_content:
                                    # Update to 100% before returning
                                    if case_repo and beam_id:
                                        try:
                                            case_repo.update_beam_progress(beam_id, 100.0)
                                        except Exception:
                                            pass
                                    if process:
                                        process.wait()
                                    return ExecutionHandler.JobWaitResult(failed=False)

                                # Extract total batches if not found yet
                                if total_batches is None:
                                    match = total_batches_pattern.search(new_content)
                                    if match:
                                        total_batches = int(match.group(1))
                                        self.logger.info(f"Detected total batches: {total_batches}")

                                # Extract current batch and update progress
                                if total_batches is not None:
                                    for match in current_batch_pattern.finditer(new_content):
                                        current_batch = int(match.group(1))
                                        # Calculate simulation progress (30% to 90% range for HPC phase)
                                        sim_progress = (current_batch / total_batches) * 60 + 30

                                        # Only update if progress increased by at least 1%
                                        if case_repo and beam_id and (sim_progress - last_progress >= 1.0):
                                            try:
                                                case_repo.update_beam_progress(beam_id, sim_progress)
                                                last_progress = sim_progress
                                            except Exception as e:
                                                self.logger.warning(f"Failed to update progress: {e}")

                    except Exception as e:
                        self.logger.warning(f"Error reading log file: {e}")

                # Check if the process exited without a success/failure pattern in the log
                if process and process.poll() is not None:
                    # Process terminated — do one final read of the log
                    if log_path.exists():
                        try:
                            with open(log_path, 'r') as f:
                                f.seek(file_position)
                                remaining = f.read()
                                if remaining:
                                    if success_pattern in remaining:
                                        if case_repo and beam_id:
                                            try:
                                                case_repo.update_beam_progress(beam_id, 100.0)
                                            except Exception:
                                                pass
                                        return ExecutionHandler.JobWaitResult(failed=False)
                                    for pattern in failure_patterns:
                                        if pattern in remaining:
                                            return ExecutionHandler.JobWaitResult(
                                                failed=True,
                                                error=f"Simulation failed: found pattern '{pattern}' in log"
                                            )
                        except Exception:
                            pass

                    rc = process.returncode
                    if rc == 0:
                        if case_repo and beam_id:
                            try:
                                case_repo.update_beam_progress(beam_id, 100.0)
                            except Exception:
                                pass
                        return ExecutionHandler.JobWaitResult(failed=False)
                    else:
                        stderr_output = ""
                        try:
                            stderr_output = process.stderr.read() if process.stderr else ""
                        except Exception:
                            pass
                        return ExecutionHandler.JobWaitResult(
                            failed=True,
                            error=f"Simulation process exited with code {rc}: {stderr_output}"
                        )

                time.sleep(poll_interval)

            # Timeout reached
            if process:
                process.kill()
                process.wait()
            return ExecutionHandler.JobWaitResult(
                failed=True,
                error=f"Simulation timeout after {timeout} seconds"
            )
        else:
            # No monitoring needed
            return ExecutionHandler.JobWaitResult(failed=False)


    def execute_command(self,
                        command: Any,
                        cwd: Optional[Path] = None,
                        timeout: int = 30) -> ExecutionResult:
        """
        Executes a command locally.
        """
        try:
            use_shell = isinstance(command, str)
            result = subprocess.run(command,
                                    shell=use_shell if not isinstance(command, list) else False,
                                    check=True,
                                    capture_output=True,
                                    text=True,
                                    cwd=cwd,
                                    timeout=timeout)
            return ExecutionResult(success=True,
                                   output=result.stdout,
                                   error=result.stderr,
                                   return_code=result.returncode)
        except subprocess.TimeoutExpired:
            return ExecutionResult(success=False,
                                   output="",
                                   error=f"Command timed out after {timeout}s",
                                   return_code=-1)
        except subprocess.CalledProcessError as e:
            return ExecutionResult(success=False,
                                   output=e.stdout,
                                   error=e.stderr,
                                   return_code=e.returncode)

    def post_process(self, handler_name: str, **context: Any) -> ExecutionResult:
        """
        Runs a post-processing command obtained from Settings.
        """
        command = self.settings.get_command('post_process',
                                            handler_name=handler_name,
                                            **context)
        cwd = context.get('cwd')
        return self.execute_command(command, cwd=cwd)

    def upload_file(self, local_path: str, dest_path: str) -> UploadResult:
        """
        Copies a file to the target path (local operation).
        """
        try:
            target_dir = os.path.dirname(dest_path)
            if target_dir:
                os.makedirs(target_dir, exist_ok=True)
            shutil.copy(local_path, dest_path)
            return UploadResult(success=True)
        except Exception as e:
            self.logger.error("File copy failed", context={"error": str(e)})
            return UploadResult(success=False, error=str(e))

    def download_file(self, src_path: str, local_path: str) -> DownloadResult:
        """
        Copies a file from source to destination (local operation).
        """
        try:
            local_dir = os.path.dirname(local_path)
            if local_dir:
                os.makedirs(local_dir, exist_ok=True)
            shutil.copy(src_path, local_path)
            return DownloadResult(success=True)
        except Exception as e:
            self.logger.error("File copy failed", context={"error": str(e)})
            return DownloadResult(success=False, error=str(e))

    def download_directory(self, src_dir: str, local_dir: str) -> DownloadResult:
        """Copies a directory tree (local operation)."""
        try:
            local_dir_path = Path(local_dir)
            local_dir_path.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src_dir, local_dir, dirs_exist_ok=True)
            return DownloadResult(success=True)
        except Exception as e:
            self.logger.error("Directory copy failed", context={"error": str(e)})
            return DownloadResult(success=False, error=str(e))

    def cleanup(self, handler_name: str, **context: Any) -> ExecutionResult:
        """
        Runs a cleanup command (e.g., 'rm -rf') from Settings.
        """
        command = self.settings.get_command("cleanup",
                                            handler_name=handler_name,
                                            **context)
        return self.execute_command(command)

    def upload_to_pc_localdata(self, local_path: Path | str, case_id: str, settings: Optional[Settings] = None,
                                handler_name: Optional[str] = None, **context: Any) -> UploadResult:
        """
        Upload results to PC_localdata by copying to ./localdata_uploads/{case_id}.
        """
        try:
            base_dir = Path("./localdata_uploads") / case_id
            base_dir.mkdir(parents=True, exist_ok=True)
            src = Path(local_path)
            if src.is_dir():
                shutil.copytree(src, base_dir / src.name, dirs_exist_ok=True)
            else:
                shutil.copy(str(src), str(base_dir / src.name))
            import logging
            logging.getLogger(__name__).info(
                f"Simulating upload by copying to local directory for case {case_id}")
            return UploadResult(success=True)
        except Exception as e:
            return UploadResult(success=False, error=str(e))


    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass
