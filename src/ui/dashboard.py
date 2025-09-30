#!/usr/bin/env python3
# =====================================================================================
# Target File: src/ui/dashboard.py
# Purpose: UI Process Entry Point - Serves as the entry point for the UI process
# =====================================================================================

"""UI Process Entry Point for the MQI Communicator Dashboard.

This script initializes all necessary components and starts the display
manager in its own process space.
"""

import sys
import signal
import os
import time
import json
from pathlib import Path
from typing import NoReturn, Optional, Dict, Any
import argparse
from datetime import datetime, timezone, timedelta
from logging.handlers import RotatingFileHandler
from src.config.settings import Settings
from src.infrastructure.logging_handler import StructuredLogger
from src.database.connection import DatabaseConnection
from src.repositories.case_repo import CaseRepository
from src.repositories.gpu_repo import GpuRepository
from src.ui.provider import DashboardDataProvider
from src.ui.display import DisplayManager


class DashboardLogger(StructuredLogger):
    """A logger that only writes to files, not console, for dashboard UI."""

    def __init__(self, name: str, config: Dict[str, Any]):
        """Initialize dashboard logger with file-only output.

        Args:
            name (str): The logger name.
            config (Dict[str, Any]): The logging configuration settings.
        """
        # Initialize parent StructuredLogger but override handler setup
        self.config = config
        import logging
        self.logger = logging.getLogger(name)
        log_level = self.config.get("log_level", "INFO").upper()
        self.logger.setLevel(getattr(logging, log_level))

        if not self.logger.handlers:
            self._setup_file_handler_only()

    def _setup_file_handler_only(self) -> None:
        """Setup only file handler, no console output."""
        import logging
        log_dir = Path(self.config['log_dir'])
        # Ensure log directory exists
        log_dir.mkdir(parents=True, exist_ok=True)

        # File handler with rotation - NO console handler
        log_file = log_dir / f"{self.logger.name}.log"
        max_bytes = self.config.get("max_file_size_mb", 10) * 1024 * 1024
        backup_count = self.config.get("backup_count", 5)
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=max_bytes,
            backupCount=backup_count
        )

        # Set formatter
        if self.config.get("structured_logging", True):
            formatter = self._create_json_formatter()
        else:
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )

        file_handler.setFormatter(formatter)
        self.logger.addHandler(file_handler)


class DashboardProcess:
    """Main dashboard process controller.

    This class is responsible for initializing and managing the UI components
    in a separate process.
    """
    
    def __init__(self, database_path: str, config_path: Optional[str] = None):
        """Initialize the dashboard process.

        Args:
            database_path (str): The path to the SQLite database file.
            config_path (Optional[str]): Path to the YAML configuration file.
        """
        self.database_path = database_path
        self.config_path = Path(config_path) if config_path else None
        self.settings = Settings(self.config_path)
        self.logger: StructuredLogger = None
        self.display_manager: DisplayManager = None
        self.running = False
        
    def initialize_logging(self) -> None:
        """Initialize logging for the UI process."""
        try:
            # settings.logging is already a dictionary, so copy it.
            dashboard_logging_config = self.settings.get_logging_config()
            
            self.logger = DashboardLogger(
                name="ui_dashboard",
                config=dashboard_logging_config
            )
            self.logger.info("Dashboard process starting", {
                "database_path": self.database_path
            })
        except Exception as e:
            print(f"Failed to initialize logging: {e}")
            sys.exit(1)
    
    def setup_database_components(self) -> tuple[CaseRepository, GpuRepository]:
        """Setup the database connection and repositories.

        Returns:
            tuple[CaseRepository, GpuRepository]: A tuple containing the case and GPU repositories.
        """
        try:
            db_connection = DatabaseConnection(
                db_path=Path(self.database_path),
                settings=self.settings,
                logger=self.logger
            )
            
            # Create repositories
            case_repo = CaseRepository(db_connection, self.logger)
            gpu_repo = GpuRepository(db_connection, self.logger, self.settings)
            
            self.logger.info("Database components initialized successfully")
            return case_repo, gpu_repo
            
        except Exception as e:
            self.logger.error("Failed to setup database components", {"error": str(e)})
            sys.exit(1)
    
    def start_display(self) -> None:
        """Start the display manager."""
        try:
            # Setup database components
            case_repo, gpu_repo = self.setup_database_components()
            
            # Create data provider
            provider = DashboardDataProvider(case_repo, gpu_repo, self.logger)
            
            # Create and start display manager
            self.display_manager = DisplayManager(
                provider,
                self.logger,
                settings=self.settings,
                timezone_hours=self.settings.get_logging_config().get("tz_hours")
            )
            self.display_manager.start()
            
            self.running = True
            self.logger.info("Dashboard display started successfully")
            
        except Exception as e:
            self.logger.error("Failed to start display", {"error": str(e)})
            sys.exit(1)
    
    def stop_display(self) -> None:
        """Stop the display manager."""
        if self.display_manager and self.running:
            self.logger.info("Stopping dashboard display")
            self.display_manager.stop()
            self.running = False
            self.logger.info("Dashboard display stopped")
    
    def run(self) -> NoReturn:
        """The main run loop for the dashboard process."""
        try:
            # Initialize components
            self.initialize_logging()
            self.start_display()
            
            # Keep the process alive
            while self.running:
                time.sleep(1)
                
                # Check if display manager is still running
                if not self.display_manager or not self.display_manager.running:
                    self.logger.warning("Display manager stopped unexpectedly")
                    break
                    
        except KeyboardInterrupt:
            self.logger.info("Dashboard process received interrupt signal")
        except Exception as e:
            if self.logger:
                self.logger.error("Dashboard process failed", {"error": str(e)})
            else:
                print(f"Dashboard process failed: {e}")
        finally:
            self.cleanup()
    
    def cleanup(self) -> None:
        """Clean up resources."""
        try:
            self.stop_display()
            if self.logger:
                self.logger.info("Dashboard process cleanup complete")
        except Exception as e:
            print(f"Error during cleanup: {e}")


def setup_signal_handlers(dashboard: DashboardProcess) -> None:
    """Setup signal handlers for graceful shutdown.

    Args:
        dashboard (DashboardProcess): The DashboardProcess instance to shut down.
    """
    def signal_handler(signum, frame):
        dashboard.running = False
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)


def main() -> NoReturn:
    """The main entry point for the dashboard process."""
    try:
        parser = argparse.ArgumentParser(description="MQI Communicator Dashboard")
        parser.add_argument("database_path", type=str, help="Path to the SQLite database file.")
        parser.add_argument("--config", type=str, help="Path to the YAML configuration file.", default=None)
        args = parser.parse_args()

        # Wait for the database file to exist, with a timeout.
        # This avoids a race condition where the UI starts before the main app creates the DB.
        db_path = Path(args.database_path)
        max_retries = 10
        retry_interval = 0.5  # seconds
        for attempt in range(max_retries):
            if db_path.exists():
                break
            time.sleep(retry_interval)
        else:
            # If the loop completes without breaking, the file was not found.
            raise FileNotFoundError(
                f"Dashboard startup failed: Database file not found after {max_retries * retry_interval} seconds "
                f"at the provided path: {db_path.resolve()}"
            )

        # Create and run dashboard
        dashboard = DashboardProcess(
            database_path=args.database_path,
            config_path=args.config
        )
        setup_signal_handlers(dashboard)

        dashboard.run()

    except Exception as e:
        # Pre-logging error handler.
        # This will run if anything fails before the main logger is initialized.
        # The CWD is the project root, set by UIProcessManager.
        error_log_path = Path.cwd() / "dashboard_startup_error.log"
        with open(error_log_path, "w", encoding='utf-8') as f:
            import traceback
            f.write(f"Timestamp: {time.asctime()}\n")
            f.write("A critical error occurred during dashboard startup:\n\n")
            f.write(f"Error Type: {type(e).__name__}\n")
            f.write(f"Error Message: {e}\n\n")
            f.write("Traceback:\n")
            traceback.print_exc(file=f)

        # Also print to stderr for immediate feedback if possible
        print(f"CRITICAL: Dashboard failed to start. See {error_log_path.resolve()} for details.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()