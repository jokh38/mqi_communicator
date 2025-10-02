"""Provides structured logging capabilities and a logger factory."""

import json
import logging
import sys
from datetime import datetime, timezone, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Optional

from src.config.settings import Settings  # Updated import


class StructuredLogger:
    """Provides structured logging capabilities with JSON formatting and context management."""

    def __init__(self, name: str, config: Dict[str, Any]):
        """
        Initialize structured logger with a configuration dictionary.

        Args:
            name (str): The logger name (usually the module name).
            config (Dict[str, Any]): The logging configuration settings dictionary.
        """
        self.config = config
        self.logger = logging.getLogger(name)
        log_level = self.config.get("log_level", "INFO").upper()
        self.logger.setLevel(getattr(logging, log_level))

        if not self.logger.handlers:
            self._setup_handlers()

    def _setup_handlers(self) -> None:
        """Setup file and console handlers with appropriate formatting."""
        log_dir_str = self.config.get("log_dir", "logs")
        log_dir = Path(log_dir_str)
        log_dir.mkdir(parents=True, exist_ok=True)

        log_file = log_dir / f"{self.logger.name}.log"
        max_bytes = self.config.get("max_file_size_mb", 10) * 1024 * 1024
        backup_count = self.config.get("backup_count", 5)
        file_handler = RotatingFileHandler(log_file,
                                           maxBytes=max_bytes,
                                           backupCount=backup_count)

        console_handler = logging.StreamHandler(sys.stdout)

        if self.config.get("structured_logging", True):
            formatter = self._create_json_formatter()
        else:
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s')

        file_handler.setFormatter(formatter)
        console_handler.setFormatter(formatter)

        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)

    def _create_json_formatter(self) -> logging.Formatter:
        """Create a JSON formatter for structured logging."""
        config = self.config

        class JsonFormatter(logging.Formatter):
            def format(self, record):
                tz_hours = config.get("tz_hours", 9)
                local_tz = timezone(timedelta(hours=tz_hours))
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
                    log_data['exception'] = self.formatException(
                        record.exc_info)
                return json.dumps(log_data, default=str)

        return JsonFormatter()

    def _log_with_context(self,
                          level: int,
                          message: str,
                          context: Optional[Dict[str, Any]] = None,
                          exc_info=False):
        """Log a message with structured context."""
        extra = {}
        if context and self.config.get("structured_logging", True):
            extra['context'] = context
        self.logger.log(level, message, extra=extra, exc_info=exc_info)

    def debug(self, message: str, context: Optional[Dict[str, Any]] = None, exc_info=False):
        self._log_with_context(logging.DEBUG, message, context, exc_info)

    def info(self, message: str, context: Optional[Dict[str, Any]] = None, exc_info=False):
        self._log_with_context(logging.INFO, message, context, exc_info)

    def warning(self, message: str, context: Optional[Dict[str, Any]] = None, exc_info=False):
        self._log_with_context(logging.WARNING, message, context, exc_info)

    def error(self, message: str, context: Optional[Dict[str, Any]] = None, exc_info=False):
        self._log_with_context(logging.ERROR, message, context, exc_info)

    def critical(self, message: str, context: Optional[Dict[str, Any]] = None, exc_info=False):
        self._log_with_context(logging.CRITICAL, message, context, exc_info)


class LoggerFactory:
    """Factory for creating configured logger instances."""

    _config: Optional[Dict[str, Any]] = None
    _loggers: Dict[str, StructuredLogger] = {}

    @classmethod
    def configure(cls, settings: Settings | dict):
        """
        Configure the logger factory with a Settings object or a raw dict.

        Args:
            settings (Settings | dict): The application's settings object or config dict.
        """
        try:
            config = settings if isinstance(settings, dict) else settings.get_logging_config()
        except Exception:
            config = None
        if not isinstance(config, dict):
            config = {"log_dir": "logs", "log_level": "INFO", "structured_logging": False}
        cls._config = config


    @classmethod
    def get_logger(cls, name: str) -> StructuredLogger:
        """
        Get or create a logger instance.

        Args:
            name (str): The name of the logger.

        Raises:
            RuntimeError: If the factory has not been configured.

        Returns:
            StructuredLogger: A configured StructuredLogger instance.
        """
        if cls._config is None:
            # Provide a safe default if not configured
            cls._config = {"log_dir": "logs", "log_level": "INFO", "structured_logging": False}

        if name not in cls._loggers:
            cls._loggers[name] = StructuredLogger(name, cls._config)

        return cls._loggers[name]
