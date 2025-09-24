# =====================================================================================
# Target File: src/infrastructure/logging_handler.py
# Source Reference: src/logging_handler.py
# =====================================================================================
"""Provides structured logging capabilities and a logger factory."""

import logging
import json
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional
from logging.handlers import RotatingFileHandler

from src.config.settings import LoggingConfig

class StructuredLogger:
    """Provides structured logging capabilities with JSON formatting and context management."""
    
    def __init__(self, name: str, config: LoggingConfig):
        """Initialize structured logger with configuration.

        Args:
            name (str): The logger name (usually the module name).
            config (LoggingConfig): The logging configuration settings.
        """
        self.config = config
        self.logger = logging.getLogger(name)
        self.logger.setLevel(getattr(logging, config.log_level.upper()))
        
        # Prevent duplicate handlers
        if not self.logger.handlers:
            self._setup_handlers()
    
    def _setup_handlers(self) -> None:
        """Setup file and console handlers with appropriate formatting."""
        
        # Ensure log directory exists
        self.config.log_dir.mkdir(parents=True, exist_ok=True)
        
        # File handler with rotation
        log_file = self.config.log_dir / f"{self.logger.name}.log"
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=self.config.max_file_size * 1024 * 1024,  # MB to bytes
            backupCount=self.config.backup_count
        )
        
        # Console handler
        console_handler = logging.StreamHandler(sys.stdout)
        
        # Set formatters
        if self.config.structured_logging:
            formatter = self._create_json_formatter()
        else:
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
        
        file_handler.setFormatter(formatter)
        console_handler.setFormatter(formatter)
        
        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)
    
    def _create_json_formatter(self):
        """Create a JSON formatter for structured logging.

        Returns:
            logging.Formatter: A logging.Formatter instance that formats logs as JSON.
        """
        config = self.config  # Capture config for inner class
        
        class JsonFormatter(logging.Formatter):
            def format(self, record):
                # Create timezone object for configured timezone (Seoul: UTC+9)
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
                
                # Add context data if available
                if hasattr(record, 'context'):
                    log_data['context'] = record.context
                
                # Add exception info if present
                if record.exc_info:
                    log_data['exception'] = self.formatException(record.exc_info)
                
                return json.dumps(log_data, default=str)
        
        return JsonFormatter()
    
    def _log_with_context(self, level: int, message: str, context: Dict[str, Any] = None, exc_info=False):
        """Log a message with structured context.

        Args:
            level (int): The logging level.
            message (str): The log message.
            context (Dict[str, Any], optional): An optional dictionary of context data. Defaults to None.
            exc_info (bool, optional): Whether to include exception information. Defaults to False.
        """
        extra = {}
        if context and self.config.structured_logging:
            extra['context'] = context
        
        self.logger.log(level, message, extra=extra, exc_info=exc_info)
    
    def debug(self, message: str, context: Dict[str, Any] = None, exc_info=False):
        """Log a debug message with optional context.

        Args:
            message (str): The log message.
            context (Dict[str, Any], optional): An optional dictionary of context data. Defaults to None.
            exc_info (bool, optional): Whether to include exception information. Defaults to False.
        """
        self._log_with_context(logging.DEBUG, message, context, exc_info=exc_info)
    
    def info(self, message: str, context: Dict[str, Any] = None, exc_info=False):
        """Log an info message with optional context.

        Args:
            message (str): The log message.
            context (Dict[str, Any], optional): An optional dictionary of context data. Defaults to None.
            exc_info (bool, optional): Whether to include exception information. Defaults to False.
        """
        self._log_with_context(logging.INFO, message, context, exc_info=exc_info)
    
    def warning(self, message: str, context: Dict[str, Any] = None, exc_info=False):
        """Log a warning message with optional context.

        Args:
            message (str): The log message.
            context (Dict[str, Any], optional): An optional dictionary of context data. Defaults to None.
            exc_info (bool, optional): Whether to include exception information. Defaults to False.
        """
        self._log_with_context(logging.WARNING, message, context, exc_info=exc_info)
    
    def error(self, message: str, context: Dict[str, Any] = None, exc_info=False):
        """Log an error message with optional context.

        Args:
            message (str): The log message.
            context (Dict[str, Any], optional): An optional dictionary of context data. Defaults to None.
            exc_info (bool, optional): Whether to include exception information. Defaults to False.
        """
        self._log_with_context(logging.ERROR, message, context, exc_info=exc_info)
    
    def critical(self, message: str, context: Dict[str, Any] = None, exc_info=False):
        """Log a critical message with optional context.

        Args:
            message (str): The log message.
            context (Dict[str, Any], optional): An optional dictionary of context data. Defaults to None.
            exc_info (bool, optional): Whether to include exception information. Defaults to False.
        """
        self._log_with_context(logging.CRITICAL, message, context, exc_info=exc_info)

class LoggerFactory:
    """Factory for creating configured logger instances."""
    
    _config: Optional[LoggingConfig] = None
    _loggers: Dict[str, StructuredLogger] = {}
    
    @classmethod
    def configure(cls, config: LoggingConfig):
        """Configure the logger factory with settings.

        Args:
            config (LoggingConfig): The logging configuration settings.
        """
        cls._config = config
    
    @classmethod
    def get_logger(cls, name: str) -> StructuredLogger:
        """Get or create a logger instance.

        Args:
            name (str): The name of the logger.

        Raises:
            RuntimeError: If the factory has not been configured.

        Returns:
            StructuredLogger: A configured StructuredLogger instance.
        """
        if not cls._config:
            raise RuntimeError("LoggerFactory must be configured before use")
        
        if name not in cls._loggers:
            cls._loggers[name] = StructuredLogger(name, cls._config)
        
        return cls._loggers[name]