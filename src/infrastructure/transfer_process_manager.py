"""Manage the lifecycle of the MQI Transfer receiver subprocess."""

import platform
import signal
import socket
import subprocess
import time
from pathlib import Path
from typing import Optional, IO

from src.infrastructure.logging_handler import StructuredLogger


class TransferProcessManager:
    """Launches and supervises the Linux-side transfer receiver."""

    def __init__(self, project_root: Path, python_executable: str, logger: StructuredLogger):
        self.project_root = project_root
        self.python_executable = python_executable
        self.logger = logger
        self.transfer_dir = self.project_root / "mqi_transfer" / "Linux"
        self.script_path = self.transfer_dir / "mqi_transfer.py"
        self.log_path = self.project_root / "mqi_transfer" / "mqi_server.log"
        self._process: Optional[subprocess.Popen] = None
        self._log_file: Optional[IO[str]] = None

    def start(self, timeout: float = 10.0) -> bool:
        """Start the transfer receiver and wait until port 80 is reachable."""
        if self.is_running():
            if self.logger:
                self.logger.warning("MQI Transfer receiver is already running")
            return True

        if not self.script_path.exists():
            if self.logger:
                self.logger.error(
                    "MQI Transfer receiver script is missing",
                    {"script_path": str(self.script_path)},
                )
            return False

        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_file = open(self.log_path, "a", encoding="utf-8")

        command = [self.python_executable, "mqi_transfer.py"]
        popen_kwargs = {
            "cwd": self.transfer_dir,
            "stdout": self._log_file,
            "stderr": self._log_file,
        }
        if platform.system() == "Windows":
            popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        else:
            popen_kwargs["start_new_session"] = True

        try:
            self._process = subprocess.Popen(command, **popen_kwargs)
        except Exception as exc:
            if self.logger:
                self.logger.error(
                    "Failed to launch MQI Transfer receiver",
                    {"error": str(exc), "command": " ".join(command)},
                )
            self._close_log_file()
            self._process = None
            return False

        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._process.poll() is not None:
                if self.logger:
                    self.logger.error(
                        "MQI Transfer receiver exited during startup",
                        {"return_code": self._process.returncode},
                    )
                self.stop()
                return False
            if self._port_is_listening(80):
                if self.logger:
                    self.logger.info(
                        "MQI Transfer receiver started successfully",
                        {"pid": self._process.pid, "port": 80},
                    )
                return True
            time.sleep(0.25)

        if self.logger:
            self.logger.error(
                "MQI Transfer receiver did not become ready on port 80",
                {"pid": self._process.pid if self._process else None},
            )
        self.stop()
        return False

    def stop(self, timeout: float = 10.0) -> bool:
        """Stop the transfer receiver if it is running."""
        if not self._process:
            self._close_log_file()
            return True

        proc = self._process
        self._process = None

        if proc.poll() is None:
            try:
                if platform.system() == "Windows":
                    proc.terminate()
                else:
                    proc.send_signal(signal.SIGTERM)
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                    proc.wait(timeout=timeout)
                except Exception:
                    pass
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

        self._close_log_file()
        return True

    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _close_log_file(self) -> None:
        if self._log_file is not None:
            try:
                self._log_file.close()
            except Exception:
                pass
        self._log_file = None

    def _port_is_listening(self, port: int) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            return False
