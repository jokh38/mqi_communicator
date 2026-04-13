# =====================================================================================
# Target File: src/infrastructure/ui_process_manager.py
# Source Reference: jsons/display_process_manager.json
# =====================================================================================
"""Manages the UI subprocess lifecycle, handling its creation, monitoring, and termination."""

import os
import errno
import shutil
import signal
import subprocess
import sys
import platform
import time
from typing import Optional, Dict, Any, IO
from pathlib import Path

from src.infrastructure.logging_handler import StructuredLogger
from src.config.settings import Settings


class UIProcessManager:
    """A professional manager for the UI subprocess lifecycle, handling its creation,
    monitoring, and clean termination with separate console window support.

    This class is responsible for launching and managing the UI as a separate process
    with proper console window handling on Windows systems.
    """
    
    def __init__(self, 
                 database_path: str, 
                 config_path: Optional[Path], 
                 config: Settings, 
                 logger: StructuredLogger):
        """Initializes the UIProcessManager.

        Args:
            database_path (str): The resolved path to the SQLite database file.
            config (Settings): The application configuration object.
            logger (StructuredLogger): A logger instance for status messages.
        """
        self.database_path = database_path
        self.config_path = config_path
        self.config = config
        self.logger = logger
        self.project_root = Path(__file__).parent.parent.parent
        self._process: Optional[subprocess.Popen] = None
        self._stdout_log_file: Optional[IO[str]] = None
        self._is_running = False
        self.web_port: Optional[int] = None
        self.log_dir = self.project_root / self.config.get_logging_config()['log_dir']
        self._pid_file = self.project_root / ".runtime" / "ui_process.pid"
    
    def _reclaim_stale_ui_process(self) -> None:
        """Kill any orphaned UI process from a previous run using the saved PID file."""
        if not self._pid_file.exists():
            return
        try:
            stale_pid = int(self._pid_file.read_text().strip())
        except (ValueError, OSError):
            self._pid_file.unlink(missing_ok=True)
            return

        if not self._pid_exists(stale_pid):
            self._pid_file.unlink(missing_ok=True)
            return

        if self.logger:
            self.logger.info("Reclaiming stale UI process from previous run", {"pid": stale_pid})
        self._terminate_process_tree(stale_pid)
        self._pid_file.unlink(missing_ok=True)

    def _save_ui_pid(self, pid: int) -> None:
        """Persist the UI subprocess PID so it can be reclaimed after an unclean shutdown."""
        self._pid_file.parent.mkdir(parents=True, exist_ok=True)
        self._pid_file.write_text(str(pid))

    def _remove_ui_pid(self) -> None:
        """Remove the UI PID file during clean shutdown."""
        self._pid_file.unlink(missing_ok=True)

    def start(self) -> bool:
        """Starts the UI as an independent process.

        Creates a new console window on Windows systems in terminal mode,
        or runs as a background process in web mode.

        Returns:
            bool: True if the process started successfully, False otherwise.
        """
        if self._is_running:
            if self.logger:
                self.logger.warning("UI process is already running")
            return False

        try:
            # Kill any orphaned UI process from a previous unclean shutdown
            self._reclaim_stale_ui_process()

            # Check if web mode is enabled
            ui_config = self.config.get_ui_config()
            mode = ui_config.get("mode", "web")
            web_enabled = (mode == "web")
            self.web_port = None

            if web_enabled:
                web_config = ui_config.get("web", {})
                port = web_config.get("port", 8080)
                if self._ensure_web_port_ready(port):
                    self.web_port = port
                else:
                    fallback_port = self._find_next_available_web_port(port + 1)
                    if fallback_port is None:
                        raise RuntimeError(f"Port {port} already in use")
                    self.web_port = fallback_port
                    if self.logger:
                        self.logger.warning(
                            "Using fallback UI port after reclaim failed",
                            {"requested_port": port, "fallback_port": fallback_port},
                        )

            command = self._get_ui_command()

            if self.logger:
                self.logger.info("Starting UI process", {
                    "command": ' '.join(command),
                    "database_path": self.database_path,
                    "platform": platform.system(),
                    "web_mode": web_enabled
                })

            # Determine process configuration based on mode
            if web_enabled:
                # Web mode: Run as background process, setting an env var for the db path
                env = os.environ.copy()
                env["DB_PATH"] = str(self.database_path)
                
                popen_kwargs = {
                    "cwd": self.project_root,
                    "stdout": subprocess.PIPE,
                    "stderr": subprocess.PIPE,
                    "env": env,
                }

                if platform.system() == "Windows":
                    popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
                else:
                    popen_kwargs["start_new_session"] = True

                self._process = subprocess.Popen(command, **popen_kwargs)
            else:
                # Terminal mode: Create new console window
                popen_kwargs = {
                    "cwd": self.project_root,
                    "stdout": None,  # Let stdout go to the console window
                    "stderr": None   # Let stderr go to the console window too
                }

                if platform.system() == "Windows":
                    creation_flags = subprocess.CREATE_NEW_CONSOLE
                    popen_kwargs["creationflags"] = creation_flags
                    if self.logger:
                        self.logger.info("About to start UI subprocess", {
                            "command": ' '.join(command),
                            "cwd": str(self.project_root),
                            "creation_flags": creation_flags
                        })

                self._process = subprocess.Popen(command, **popen_kwargs)

            # Give the process a moment to start
            wait_time = 3.0 if web_enabled else 2.0
            time.sleep(wait_time)

            # Check if process is still running
            poll_result = self._process.poll()
            if poll_result is None:
                self._is_running = True
                self._save_ui_pid(self._process.pid)
                if self.logger:
                    self.logger.info("UI process started successfully", {
                        "pid": self._process.pid,
                        "web_mode": web_enabled
                    })
                return True
            else:
                # Process failed to start
                if self.logger:
                    # Capture stdout and stderr for web mode debugging
                    stdout_str, stderr_str = "", ""
                    if web_enabled and self._process.stdout and self._process.stderr:
                        stdout_str = self._process.stdout.read().decode('utf-8', errors='ignore')
                        stderr_str = self._process.stderr.read().decode('utf-8', errors='ignore')
                    
                    self.logger.error("UI process failed to start.", {
                        "return_code": poll_result,
                        "stdout": stdout_str,
                        "stderr": stderr_str
                    })
                self._process = None
                return False

        except Exception as e:
            if self.logger:
                self.logger.error("Failed to start UI process", {"error": str(e)})
            self._process = None
            return False
    
    def stop(self, timeout: float = 10.0) -> bool:
        """Stops the UI process gracefully, with a specified timeout.

        Args:
            timeout (float, optional): The maximum time to wait for graceful shutdown. Defaults to 10.0.

        Returns:
            bool: True if the process stopped successfully, False otherwise.
        """
        # No file handles to close since we removed stderr logging

        if not self._is_running or not self._process:
            return True
        
        try:
            if self.logger:
                self.logger.info("Stopping UI process", {"pid": self._process.pid})
            
            # Try graceful termination first
            self._process.terminate()
            
            try:
                self._process.wait(timeout=timeout)
                if self.logger:
                    self.logger.info("UI process terminated gracefully")
            except subprocess.TimeoutExpired:
                # Force kill if graceful termination fails
                if self.logger:
                    self.logger.warning("UI process did not terminate gracefully, forcing kill")
                self._process.kill()
                self._process.wait()
            
            self._is_running = False
            self._process = None
            self._remove_ui_pid()
            return True

        except Exception as e:
            if self.logger:
                self.logger.error("Failed to stop UI process", {"error": str(e)})
            return False
    
    def is_running(self) -> bool:
        """Checks if the UI process is currently running.

        Returns:
            bool: True if the process is running, False otherwise.
        """
        if not self._process or not self._is_running:
            return False
        
        # Check if process is still alive
        if self._process.poll() is not None:
            # Process has terminated
            self._is_running = False
            if self.logger:
                self.logger.info("UI process has terminated", {
                    "return_code": self._process.returncode
                })
            return False
        
        return True
    
    def get_process_info(self) -> Dict[str, Any]:
        """Returns a dictionary with information about the managed process.

        Returns:
            Dict[str, Any]: A dictionary containing process information.
        """
        if not self._process:
            return {
                "status": "not_started",
                "pid": None,
                "is_running": False
            }
        
        return {
            "status": "running" if self.is_running() else "terminated",
            "pid": self._process.pid,
            "is_running": self.is_running(),
            "return_code": self._process.returncode
        }

    def get_web_port(self) -> Optional[int]:
        """Return the active web port if the UI is running in web mode."""
        if self.web_port is not None:
            return self.web_port
        ui_config = self.config.get_ui_config()
        web_config = ui_config.get("web", {})
        return web_config.get("port", 8080)
    
    def restart(self) -> bool:
        """Restarts the UI process.

        Returns:
            bool: True if the restart was successful, False otherwise.
        """
        if self.logger:
            self.logger.info("Restarting UI process")
        
        # Stop current process
        if not self.stop():
            if self.logger:
                self.logger.error("Failed to stop UI process for restart")
            return False
        
        # Wait a moment before restarting
        time.sleep(1.0)
        
        # Start new process
        return self.start()
    
    def _get_ui_command(self) -> list[str]:
        """Constructs the command to launch the UI process based on mode.

        Returns:
            list[str]: A list of command arguments.
        """
        ui_config = self.config.get_ui_config()
        mode = ui_config.get("mode", "web")

        if mode == "web":
            web_config = ui_config.get("web", {})
            host = web_config.get("host", "0.0.0.0")
            port = self.web_port if self.web_port is not None else web_config.get("port", 8080)
            
            cmd = [
                sys.executable,
                "-m", "uvicorn",
                "src.web.app:app",
                "--host", str(host),
                "--port", str(port)
            ]
            return cmd
        
        # Terminal mode
        base_command = [
            sys.executable,
            "-m", "src.ui.dashboard",
            self.database_path
        ]
        if self.config_path:
            base_command.extend(["--config", str(self.config_path)])

        return base_command

    def _verify_ttyd_startup(self, timeout: int = 5) -> bool:
        """
        Verify that ttyd server started successfully by checking stdout.

        Args:
            timeout: Seconds to wait for startup confirmation

        Returns:
            True if startup confirmed, False otherwise
        """
        import select

        if not self._process or not self._process.stdout:
            return False

        start_time = time.time()

        while time.time() - start_time < timeout:
            # Check if process crashed
            if self._process.poll() is not None:
                if self.logger:
                    self.logger.error("ttyd process terminated unexpectedly")
                return False

            # Try to read startup output (non-blocking)
            try:
                if platform.system() == 'Windows':
                    # Windows: simplified check
                    time.sleep(0.5)
                    if self._process.poll() is None:
                        return True
                else:
                    # Unix: use select
                    ready, _, _ = select.select([self._process.stdout], [], [], 0.1)
                    if ready:
                        line = self._process.stdout.readline().decode('utf-8', errors='ignore')
                        if 'Listening on port' in line or 'listening' in line.lower():
                            if self.logger:
                                self.logger.info(f"ttyd server started: {line.strip()}")
                            return True
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"Error checking ttyd startup: {e}")

            time.sleep(0.1)

        if self.logger:
            self.logger.warning("Could not confirm ttyd startup within timeout")
        return False

    def _validate_ttyd_available(self) -> bool:
        """Check if ttyd executable is available."""
        import shutil

        ui_config = self.config.get_ui_config()
        web_config = ui_config.get("web", {})
        ttyd_path = web_config.get("ttyd_path", "ttyd")

        if shutil.which(ttyd_path) is None:
            if self.logger:
                self.logger.error(
                    f"ttyd executable not found: {ttyd_path}",
                    {
                        "message": "Please install ttyd or update 'ui.web.ttyd_path' in config",
                        "install_url": "https://github.com/tsl0922/ttyd"
                    }
                )
            return False
        return True

    def _check_port_available(self, port: int) -> bool:
        """Check if port is available for binding."""
        import socket

        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('', port))
                return True
        except OSError as exc:
            if getattr(exc, "errno", None) == errno.EADDRINUSE:
                if self.logger:
                    self.logger.error(f"Port {port} is already in use")
                return False

            if self.logger:
                self.logger.warning(
                    "Could not probe UI port availability; continuing without a bind pre-check",
                    {"port": port, "error": str(exc), "errno": getattr(exc, "errno", None)},
                )
            return True

    def _ensure_web_port_ready(self, port: int) -> bool:
        """Ensure the configured UI port is free, killing any occupying process."""
        if self._check_port_available(port):
            return True

        owner_info = self._find_port_owner_info(port)
        if owner_info:
            if self.logger:
                self.logger.warning("UI port is occupied. Killing the occupying process to reclaim port.", {
                    "port": port,
                    "pid": owner_info.get("pid"),
                    "command": owner_info.get("command"),
                })

            if not self._terminate_process_tree(owner_info["pid"]):
                if self.logger:
                    self.logger.error("Failed to terminate process occupying UI port", {
                        "port": port,
                        "pid": owner_info.get("pid"),
                    })
                return False
        else:
            if self.logger:
                self.logger.warning(
                    "UI port is occupied but the owning process could not be identified; "
                    "attempting direct port reclamation.",
                    {"port": port},
                )
            reclaimed = self._reclaim_port_without_owner(port)
            if not reclaimed:
                # Some platforms briefly report EADDRINUSE while the previous listener
                # is still shutting down, even when no owner can be resolved anymore.
                if self.logger:
                    self.logger.warning(
                        "Could not identify a process to kill for the occupied UI port; "
                        "waiting for the port to clear before falling back.",
                        {"port": port},
                    )

        if not self._wait_for_port_available(port, timeout=15.0):
            if self.logger:
                self.logger.error("UI port did not clear after terminating occupying process", {
                    "port": port,
                    "pid": owner_info.get("pid") if owner_info else None,
                })
            return False

        if self.logger:
            self.logger.info("Successfully reclaimed UI port", {
                "port": port,
                "pid": owner_info.get("pid") if owner_info else None,
            })
        return True

    def _find_next_available_web_port(self, start_port: int, max_attempts: int = 50) -> Optional[int]:
        """Find the next available port at or above start_port."""
        for candidate in range(start_port, start_port + max_attempts):
            if self._check_port_available(candidate):
                return candidate
        return None

    def _reclaim_port_without_owner(self, port: int) -> bool:
        """Forcefully reclaim a port when the owning process cannot be identified."""
        commands = []
        if shutil.which("fuser"):
            commands.append(["fuser", "-k", "-KILL", "-n", "tcp", str(port)])
            commands.append(["fuser", "-k", "-n", "tcp", str(port)])
        if shutil.which("lsof"):
            commands.append(["bash", "-lc", f"lsof -tiTCP:{port} -sTCP:LISTEN | xargs -r kill -KILL"])
        commands.append([
            "pkill",
            "-9",
            "-f",
            f"uvicorn.*src.web.app:app.*--port {port}",
        ])
        commands.append([
            "pkill",
            "-9",
            "-f",
            f"src.web.app:app.*--port {port}",
        ])

        if not commands:
            return False

        for command in commands:
            try:
                result = subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    text=True,
                )
            except Exception:
                continue

            if result.returncode == 0:
                return True

        return False

    def _find_port_owner_info(self, port: int) -> Optional[Dict[str, Any]]:
        """Return PID and command for the process listening on a TCP port."""
        pid = self._find_port_owner_pid(port)
        if pid is None:
            return None
        return {
            "pid": pid,
            "command": self._get_process_command(pid),
        }

    def _find_port_owner_pid(self, port: int) -> Optional[int]:
        """Resolve the PID of the process listening on the target TCP port."""
        commands = []
        if shutil.which("lsof"):
            commands.append(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"])
        if shutil.which("fuser"):
            commands.append(["fuser", "-n", "tcp", str(port)])
        if shutil.which("ss"):
            commands.append([
                "bash",
                "-lc",
                f"ss -ltnp '( sport = :{port} )' | grep -o 'pid=[0-9]\\+' | head -n 1",
            ])

        for command in commands:
            try:
                result = subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    text=True,
                )
            except Exception:
                continue

            output = " ".join(
                part.strip()
                for part in [result.stdout, result.stderr]
                if part and part.strip()
            )
            for token in output.split():
                if token.isdigit():
                    return int(token)
        return None

    def _get_process_command(self, pid: int) -> str:
        """Return a readable command line for a PID when available."""
        try:
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "command="],
                check=False,
                capture_output=True,
                text=True,
            )
            command = result.stdout.strip()
            if command:
                return command
        except Exception:
            pass
        return ""

    def _wait_for_port_available(self, port: int, timeout: float = 5.0) -> bool:
        """Wait for a TCP port to become available."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._check_port_available(port):
                return True
            time.sleep(0.1)
        return False

    def _terminate_process_tree(self, pid: int, timeout: float = 5.0) -> bool:
        """Terminate a process and its descendants."""
        if platform.system() == "Windows":
            return self._terminate_process_tree_windows(pid)
        return self._terminate_process_tree_unix(pid, timeout=timeout)

    def _terminate_process_tree_windows(self, pid: int) -> bool:
        """Terminate a process tree on Windows."""
        try:
            result = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                check=False,
                capture_output=True,
                text=True,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _terminate_process_tree_unix(self, pid: int, timeout: float = 5.0) -> bool:
        """Terminate a process tree on Unix-like platforms."""
        pids = self._collect_descendant_pids(pid)
        pids.append(pid)

        for target_pid in reversed(pids):
            try:
                os.kill(target_pid, signal.SIGTERM)
            except ProcessLookupError:
                continue
            except Exception:
                return False

        deadline = time.time() + timeout
        while time.time() < deadline:
            alive = [target_pid for target_pid in pids if self._pid_exists(target_pid)]
            if not alive:
                return True
            time.sleep(0.1)

        for target_pid in reversed(pids):
            try:
                os.kill(target_pid, signal.SIGKILL)
            except ProcessLookupError:
                continue
            except Exception:
                return False

        time.sleep(0.1)
        return not any(self._pid_exists(target_pid) for target_pid in pids)

    def _collect_descendant_pids(self, pid: int) -> list[int]:
        """Collect descendant PIDs for a root process using ps."""
        descendants: list[int] = []
        queue = [pid]

        while queue:
            parent_pid = queue.pop()
            try:
                result = subprocess.run(
                    ["ps", "-o", "pid=", "--ppid", str(parent_pid)],
                    check=False,
                    capture_output=True,
                    text=True,
                )
            except Exception:
                continue

            child_pids = []
            for line in result.stdout.splitlines():
                line = line.strip()
                if line.isdigit():
                    child_pid = int(line)
                    child_pids.append(child_pid)
                    descendants.append(child_pid)

            queue.extend(child_pids)

        return descendants

    def _pid_exists(self, pid: int) -> bool:
        """Check whether a PID still exists."""
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    def _get_process_creation_flags(self) -> int:
        """Gets the appropriate process creation flags based on the platform.

        Returns:
            int: The process creation flags.
        """
        if platform.system() == "Windows":
            # CREATE_NEW_CONSOLE flag to open a new console window
            return subprocess.CREATE_NEW_CONSOLE
        else:
            # No special flags needed for Unix-like systems
            return 0
