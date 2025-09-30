# =====================================================================================
# Target File: src/infrastructure/ui_process_manager.py
# Source Reference: jsons/display_process_manager.json
# =====================================================================================
"""Manages the UI subprocess lifecycle, handling its creation, monitoring, and termination."""

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
        self.log_dir = self.project_root / self.config.get_logging_config()['log_dir']
    
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
            # Check if web mode is enabled
            ui_config = self.config.get_ui_config()
            web_config = ui_config.get("web", {})
            web_enabled = web_config.get("enabled", False)

            # Validate GoTTY availability and port if web mode
            if web_enabled:
                if not self._validate_gotty_available():
                    raise RuntimeError("GoTTY not available")

                if not self._check_port_available(web_config.get("port", 8080)):
                    raise RuntimeError(f"Port {web_config.get('port', 8080)} already in use")

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
                # Web mode: Run as background process
                popen_kwargs = {
                    "cwd": self.project_root,
                    "stdout": subprocess.PIPE,
                    "stderr": subprocess.PIPE,
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

            # Verify GoTTY startup if web mode
            if web_enabled:
                if not self._verify_gotty_startup():
                    if self.logger:
                        self.logger.warning("Could not confirm GoTTY startup, but process is running")

            # Check if process is still running
            poll_result = self._process.poll()
            if poll_result is None:
                self._is_running = True
                if self.logger:
                    self.logger.info("UI process started successfully", {
                        "pid": self._process.pid,
                        "web_mode": web_enabled
                    })
                return True
            else:
                # Process failed to start
                if self.logger:
                    self.logger.error("UI process failed to start.", {
                        "return_code": poll_result
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
        """Constructs the command to launch the UI process (optionally via GoTTY).

        Returns:
            list[str]: A list of command arguments.
        """
        # Base dashboard command
        base_command = [
            sys.executable,
            "-m", "src.ui.dashboard",
            self.database_path
        ]
        if self.config_path:
            base_command.extend(["--config", str(self.config_path)])

        # Check if web mode is enabled
        ui_config = self.config.get_ui_config()
        web_config = ui_config.get("web", {})

        if not web_config.get("enabled", False):
            return base_command

        # Build GoTTY wrapper command
        gotty_cmd = [
            web_config.get("gotty_path", "gotty"),
            "--port", str(web_config.get("port", 8080)),
            "--address", web_config.get("bind_address", "0.0.0.0"),
            "--width", str(web_config.get("terminal_width", 500)),
            "--height", str(web_config.get("terminal_height", 300)),
        ]

        # Optional GoTTY flags
        if web_config.get("permit_write", False):
            gotty_cmd.append("--permit-write")

        if web_config.get("reconnect", True):
            gotty_cmd.append("--reconnect")

        # Add title for browser tab
        gotty_cmd.extend([
            "--title-format", "MOQUI Communicator Dashboard"
        ])

        # Combine: gotty [options] -- python -m src.ui.dashboard [args]
        gotty_cmd.append("--")
        gotty_cmd.extend(base_command)

        return gotty_cmd

    def _verify_gotty_startup(self, timeout: int = 5) -> bool:
        """
        Verify that GoTTY server started successfully by checking stdout.

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
                    self.logger.error("GoTTY process terminated unexpectedly")
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
                        if 'HTTP server is listening' in line or 'listening at' in line.lower():
                            if self.logger:
                                self.logger.info(f"GoTTY server started: {line.strip()}")
                            return True
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"Error checking GoTTY startup: {e}")

            time.sleep(0.1)

        if self.logger:
            self.logger.warning("Could not confirm GoTTY startup within timeout")
        return False

    def _validate_gotty_available(self) -> bool:
        """Check if GoTTY executable is available."""
        import shutil

        ui_config = self.config.get_ui_config()
        web_config = ui_config.get("web", {})
        gotty_path = web_config.get("gotty_path", "gotty")

        if shutil.which(gotty_path) is None:
            if self.logger:
                self.logger.error(
                    f"GoTTY executable not found: {gotty_path}",
                    {
                        "message": "Please install GoTTY or update 'ui.web.gotty_path' in config",
                        "install_url": "https://github.com/yudai/gotty"
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
        except OSError:
            if self.logger:
                self.logger.error(f"Port {port} is already in use")
            return False

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