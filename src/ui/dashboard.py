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
import logging
import json
from pathlib import Path
from typing import NoReturn, Optional, Dict, Any
import argparse
from datetime import datetime, timezone, timedelta
from logging.handlers import RotatingFileHandler
from src.config.settings import Settings, LoggingConfig
from src.infrastructure.logging_handler import StructuredLogger
from src.database.connection import DatabaseConnection
from src.repositories.case_repo import CaseRepository
from src.repositories.gpu_repo import GpuRepository
from src.ui.provider import DashboardDataProvider
from src.ui.display import DisplayManager


class DashboardLogger:
    """A logger that only writes to files, not console, for dashboard UI."""
    
    def __init__(self, name: str, config: LoggingConfig):
        """Initialize dashboard logger with file-only output.

        Args:
            name (str): The logger name.
            config (LoggingConfig): The logging configuration settings.
        """
        self.config = config
        self.logger = logging.getLogger(name)
        self.logger.setLevel(getattr(logging, config.log_level.upper()))
        
        # Prevent duplicate handlers
        if not self.logger.handlers:
            self._setup_file_handler_only()
    
    def _setup_file_handler_only(self) -> None:
        """Setup only file handler, no console output."""
        # Ensure log directory exists
        self.config.log_dir.mkdir(parents=True, exist_ok=True)
        
        # File handler with rotation - NO console handler
        log_file = self.config.log_dir / f"{self.logger.name}.log"
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=self.config.max_file_size * 1024 * 1024,  # MB to bytes
            backupCount=self.config.backup_count
        )
        
        # Set formatter
        if self.config.structured_logging:
            formatter = self._create_json_formatter()
        else:
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
        
        file_handler.setFormatter(formatter)
        self.logger.addHandler(file_handler)
    
    def _create_json_formatter(self):
        """Create a JSON formatter for structured logging."""
        config = self.config
        
        class JsonFormatter(logging.Formatter):
            def format(self, record):
                local_tz = timezone(timedelta(hours=config.timezone_hours))
                log_data = {
                    'timestamp': datetime.now(local_tz).isoformat(),
                    'logger': record.name,
                    'level': record.levelname,                    
                    'message': record.getMessage(),
                    'module': record.module,
                    'function': record.funcName,
                    'line': record.lineno
                }
                
                if hasattr(record, 'context'):
                    log_data['context'] = record.context
                
                if record.exc_info:
                    log_data['exception'] = self.formatException(record.exc_info)
                
                return json.dumps(log_data, default=str)
        
        return JsonFormatter()
    
    def _log_with_context(self, level: int, message: str, context: Dict[str, Any] = None, exc_info=False):
        """Log a message with structured context."""
        extra = {}
        if context and self.config.structured_logging:
            extra['context'] = context
        
        self.logger.log(level, message, extra=extra, exc_info=exc_info)
    
    def debug(self, message: str, context: Dict[str, Any] = None, exc_info=False):
        """Log a debug message with optional context."""
        self._log_with_context(logging.DEBUG, message, context, exc_info=exc_info)
    
    def info(self, message: str, context: Dict[str, Any] = None, exc_info=False):
        """Log an info message with optional context."""
        self._log_with_context(logging.INFO, message, context, exc_info=exc_info)
    
    def warning(self, message: str, context: Dict[str, Any] = None, exc_info=False):
        """Log a warning message with optional context."""
        self._log_with_context(logging.WARNING, message, context, exc_info=exc_info)
    
    def error(self, message: str, context: Dict[str, Any] = None, exc_info=False):
        """Log an error message with optional context."""
        self._log_with_context(logging.ERROR, message, context, exc_info=exc_info)
    
    def critical(self, message: str, context: Dict[str, Any] = None, exc_info=False):
        """Log a critical message with optional context."""
        self._log_with_context(logging.CRITICAL, message, context, exc_info=exc_info)


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
            # Create a custom logger configuration for dashboard that only logs to file
            dashboard_logging_config = type(self.settings.logging)(
                log_level=self.settings.logging.log_level,
                log_dir=self.settings.logging.log_dir,
                max_file_size=self.settings.logging.max_file_size,
                backup_count=self.settings.logging.backup_count,
                structured_logging=self.settings.logging.structured_logging,
                timezone_hours=self.settings.logging.timezone_hours
            )
            
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
                config=self.settings.database,
                logger=self.logger
            )
            
            # Create repositories
            case_repo = CaseRepository(db_connection, self.logger)
            gpu_repo = GpuRepository(db_connection, self.logger)
            
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
                refresh_rate=self.settings.ui.refresh_interval,
                timezone_hours=self.settings.logging.timezone_hours
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

        # Validate database path
        db_path = Path(args.database_path)
        if not db_path.exists():
            # Raise an explicit error that can be caught and logged.
            raise FileNotFoundError(f"Dashboard startup failed: Database file does not exist at the provided path: {db_path.resolve()}")

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