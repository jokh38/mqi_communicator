import logging
import logging.handlers
import time
import json
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional
from queue import Queue


class Logger:
    def __init__(self, log_directory: str = "logs", config: Optional[Dict[str, Any]] = None, log_queue: Optional[Queue] = None):
        self.log_directory = Path(log_directory)
        self.config = config or {}
        self.log_queue = log_queue
        self.lock = threading.Lock()
        
        # Default configuration
        self.log_level = self.config.get("log_level", "INFO")
        self.max_file_size = self._parse_size(self.config.get("max_file_size", "10MB"))
        self.backup_count = self.config.get("backup_count", 5)
        self.rotation_type = self.config.get("rotation_type", "size")
        self.console_output = self.config.get("console_output", False)
        self.filter_sensitive = self.config.get("filter_sensitive", False)
        self.quiet_file_transfers = self.config.get("quiet_file_transfers", False)
        
        # Create log directory if it doesn't exist
        self.log_directory.mkdir(parents=True, exist_ok=True)
        
        # Initialize logger
        self._setup_logger()
    
    def _parse_size(self, size_str: str) -> int:
        """Parse size string (e.g., '10MB') to bytes."""
        try:
            size_str = size_str.upper()
            if size_str.endswith('KB'):
                return int(size_str[:-2]) * 1024
            elif size_str.endswith('MB'):
                return int(size_str[:-2]) * 1024 * 1024
            elif size_str.endswith('GB'):
                return int(size_str[:-2]) * 1024 * 1024 * 1024
            else:
                return int(size_str)
        except (ValueError, TypeError):
            return 10 * 1024 * 1024  # Default 10MB
    
    def _setup_logger(self) -> None:
        """Set up logging configuration."""
        try:
            # Create main logger
            self.logger = logging.getLogger("moqui")
            self.logger.setLevel(getattr(logging, self.log_level.upper()))
            
            # Clear existing handlers and close them properly
            for handler in self.logger.handlers[:]:
                try:
                    handler.close()
                except Exception:
                    pass
            self.logger.handlers.clear()
            
            # Create formatter
            formatter = logging.Formatter(
                self.config.get("format", 
                    "%(asctime)s - %(name)s - %(levelname)s - %(message)s")
            )
            
            # Set up file handler
            self._setup_file_handler(formatter)
            
            # Set up queue handler if a queue is provided for UI display
            if self.log_queue:
                self._setup_queue_handler(formatter)
                # Disable propagation to parent loggers to prevent console output
                self.logger.propagate = False
            # Otherwise, set up console handler if requested
            elif self.console_output:
                self._setup_console_handler(formatter)
                
        except Exception as e:
            print(f"Error setting up logger: {e}")
    
    def _setup_file_handler(self, formatter: logging.Formatter) -> None:
        """Set up file handler for logging."""
        try:
            # Generate a log file name with the current date for daily logs
            log_filename = f"moqui_{datetime.now().strftime('%Y-%m-%d')}.log"
            log_file = self.log_directory / log_filename

            if self.rotation_type == "time":
                # Time-based rotation (daily)
                handler = logging.handlers.TimedRotatingFileHandler(
                    filename=str(log_file),
                    when='midnight',  # Rotate at midnight
                    interval=1,
                    backupCount=self.backup_count,
                    encoding='utf-8'
                )
            else:
                # Size-based rotation (will use the dated file name as base)
                handler = logging.handlers.RotatingFileHandler(
                    filename=str(log_file),
                    maxBytes=self.max_file_size,
                    backupCount=self.backup_count,
                    encoding='utf-8'
                )
            
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
            
        except Exception as e:
            print(f"Error setting up file handler: {e}")
    
    def _setup_console_handler(self, formatter: logging.Formatter) -> None:
        """Set up console handler for logging."""
        try:
            handler = logging.StreamHandler()
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
            
        except Exception as e:
            print(f"Error setting up console handler: {e}")

    def _setup_queue_handler(self, formatter: logging.Formatter) -> None:
        """Set up queue handler for UI display."""
        try:
            handler = logging.handlers.QueueHandler(self.log_queue)
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
        except Exception as e:
            print(f"Error setting up queue handler: {e}")
    
    def _filter_sensitive_data(self, message: str) -> str:
        """Filter sensitive information from log messages."""
        if not self.filter_sensitive:
            return message
        
        try:
            # Common sensitive patterns
            patterns = [
                (r'password[:\s=]+[^\s]+', 'password: [FILTERED]'),
                (r'token[:\s=]+[^\s]+', 'token: [FILTERED]'),
                (r'key[:\s=]+[^\s]+', 'key: [FILTERED]'),
                (r'secret[:\s=]+[^\s]+', 'secret: [FILTERED]'),
                (r'auth[:\s=]+[^\s]+', 'auth: [FILTERED]'),
            ]
            
            filtered_message = message
            for pattern, replacement in patterns:
                filtered_message = re.sub(pattern, replacement, filtered_message, flags=re.IGNORECASE)
            
            return filtered_message
            
        except Exception:
            return message
    
    def info(self, message: str, quiet_if_file_transfer: bool = False) -> None:
        """Log info message."""
        try:
            filtered_message = self._filter_sensitive_data(message)
            
            # Check if this is a file transfer verification message and should be quiet
            if (self.quiet_file_transfers and quiet_if_file_transfer and 
                ("File transfer verified:" in message or "File uploaded:" in message or "file transfer" in message.lower())):
                # Log directly to file handlers only, skip queue/console handlers
                for handler in self.logger.handlers:
                    if isinstance(handler, (logging.FileHandler, logging.handlers.RotatingFileHandler, logging.handlers.TimedRotatingFileHandler)):
                        record = self.logger.makeRecord(
                            self.logger.name, logging.INFO, __file__, 0, filtered_message, (), None
                        )
                        handler.emit(record)
            else:
                self.logger.info(filtered_message)
                
        except Exception as e:
            print(f"Error logging info message: {e}")
    
    def error(self, message: str) -> None:
        """Log error message."""
        try:
            filtered_message = self._filter_sensitive_data(message)
            self.logger.error(filtered_message)
        except Exception as e:
            print(f"Error logging error message: {e}")
    
    def warning(self, message: str) -> None:
        """Log warning message."""
        try:
            filtered_message = self._filter_sensitive_data(message)
            self.logger.warning(filtered_message)
        except Exception as e:
            print(f"Error logging warning message: {e}")
    
    def debug(self, message: str) -> None:
        """Log debug message."""
        try:
            filtered_message = self._filter_sensitive_data(message)
            self.logger.debug(filtered_message)
        except Exception as e:
            print(f"Error logging debug message: {e}")
    
    def critical(self, message: str) -> None:
        """Log critical message."""
        try:
            filtered_message = self._filter_sensitive_data(message)
            self.logger.critical(filtered_message)
        except Exception as e:
            print(f"Error logging critical message: {e}")
    
    def log_structured(self, level: str, message: str, context: Dict[str, Any]) -> None:
        """Log structured message with context."""
        try:
            context_str = json.dumps(context, default=str, separators=(',', ':'))
            structured_message = f"{message} | Context: {context_str}"
            
            log_method = getattr(self.logger, level.lower())
            log_method(self._filter_sensitive_data(structured_message))
            
        except Exception as e:
            print(f"Error logging structured message: {e}")
    
    def log_performance(self, operation: str, metrics: Dict[str, Any]) -> None:
        """Log performance metrics."""
        try:
            metrics_str = json.dumps(metrics, default=str, separators=(',', ':'))
            performance_message = f"PERFORMANCE | {operation} | {metrics_str}"
            
            self.logger.info(performance_message)
            
        except Exception as e:
            print(f"Error logging performance metrics: {e}")
    
    def log_case_progress(self, case_id: str, status: str, progress: float, details: Dict[str, Any]) -> None:
        """Log case processing progress."""
        try:
            progress_percent = progress * 100
            details_str = json.dumps(details, default=str, separators=(',', ':'))
            
            progress_message = (
                f"CASE_PROGRESS | {case_id} | {status} | "
                f"{progress_percent:.1f}% | {details_str}"
            )
            
            self.logger.info(progress_message)
            
        except Exception as e:
            print(f"Error logging case progress: {e}")
    
    def log_system_resources(self, resources: Dict[str, Any]) -> None:
        """Log system resource usage."""
        try:
            resources_str = json.dumps(resources, default=str, separators=(',', ':'))
            resource_message = f"SYSTEM_RESOURCES | {resources_str}"
            
            self.logger.info(resource_message)
            
        except Exception as e:
            print(f"Error logging system resources: {e}")
    
    def log_network_activity(self, activity: Dict[str, Any]) -> None:
        """Log network activity."""
        try:
            activity_str = json.dumps(activity, default=str, separators=(',', ':'))
            network_message = f"NETWORK | {activity_str}"
            
            # Only log to file if quiet file transfers is enabled
            if self.quiet_file_transfers and activity.get("action") == "file_upload_completed":
                # Log directly to file handlers only, skip queue/console handlers
                for handler in self.logger.handlers:
                    if isinstance(handler, (logging.FileHandler, logging.handlers.RotatingFileHandler, logging.handlers.TimedRotatingFileHandler)):
                        record = self.logger.makeRecord(
                            self.logger.name, logging.INFO, __file__, 0, network_message, (), None
                        )
                        handler.emit(record)
            else:
                self.logger.info(network_message)
            
        except Exception as e:
            print(f"Error logging network activity: {e}")
    
    def log_json(self, level: str, data: Dict[str, Any]) -> None:
        """Log JSON structured data."""
        try:
            json_str = json.dumps(data, default=str, indent=2)
            json_message = f"JSON_DATA | {json_str}"
            
            log_method = getattr(self.logger, level.lower())
            log_method(json_message)
            
        except Exception as e:
            print(f"Error logging JSON data: {e}")
    
    def log_exception(self, exception: Exception, context: str = "") -> None:
        """Log exception with traceback."""
        try:
            import traceback
            
            tb_str = traceback.format_exc()
            exception_message = f"EXCEPTION | {context} | {exception.__class__.__name__}: {exception} | {tb_str}"
            
            self.logger.error(exception_message)
            
        except Exception as e:
            print(f"Error logging exception: {e}")
    
    def cleanup_old_logs(self, days: int = 30) -> int:
        """Clean up old log files."""
        try:
            cutoff_time = time.time() - (days * 24 * 3600)
            cleaned_count = 0
            
            for log_file in self.log_directory.glob("*.log*"):
                try:
                    if log_file.stat().st_mtime < cutoff_time:
                        log_file.unlink()
                        cleaned_count += 1
                except Exception:
                    continue
            
            return cleaned_count
            
        except Exception as e:
            print(f"Error cleaning up old logs: {e}")
            return 0
    
    def get_log_statistics(self) -> Dict[str, Any]:
        """Get log file statistics."""
        try:
            stats = {
                "total_log_files": 0,
                "total_log_size": 0,
                "oldest_log_date": None,
                "newest_log_date": None,
                "log_files": []
            }
            
            log_files = list(self.log_directory.glob("*.log*"))
            stats["total_log_files"] = len(log_files)
            
            if log_files:
                oldest_time = float('inf')
                newest_time = 0
                
                for log_file in log_files:
                    try:
                        file_stat = log_file.stat()
                        stats["total_log_size"] += file_stat.st_size
                        
                        if file_stat.st_mtime < oldest_time:
                            oldest_time = file_stat.st_mtime
                        if file_stat.st_mtime > newest_time:
                            newest_time = file_stat.st_mtime
                        
                        stats["log_files"].append({
                            "name": log_file.name,
                            "size": file_stat.st_size,
                            "modified": datetime.fromtimestamp(file_stat.st_mtime).isoformat()
                        })
                    except Exception:
                        continue
                
                if oldest_time != float('inf'):
                    stats["oldest_log_date"] = datetime.fromtimestamp(oldest_time).isoformat()
                if newest_time != 0:
                    stats["newest_log_date"] = datetime.fromtimestamp(newest_time).isoformat()
            
            return stats
            
        except Exception as e:
            print(f"Error getting log statistics: {e}")
            return {"total_log_files": 0, "total_log_size": 0}
    
    def search_logs(self, pattern: str, max_results: int = 100) -> List[str]:
        """Search through log files for pattern."""
        try:
            results = []
            
            for log_file in self.log_directory.glob("*.log*"):
                try:
                    with open(log_file, 'r', encoding='utf-8') as f:
                        for line_num, line in enumerate(f, 1):
                            if pattern in line:
                                results.append(f"{log_file.name}:{line_num}: {line.strip()}")
                                if len(results) >= max_results:
                                    return results
                except Exception:
                    continue
            
            return results
            
        except Exception as e:
            print(f"Error searching logs: {e}")
            return []
    
    def _read_log_data(self) -> List[Dict[str, Any]]:
        """Read log data from files (helper method for testing)."""
        try:
            log_data = []
            
            for log_file in self.log_directory.glob("*.log"):
                try:
                    with open(log_file, 'r', encoding='utf-8') as f:
                        for line in f:
                            # Simple parsing - in real implementation would be more sophisticated
                            parts = line.strip().split(' - ', 3)
                            if len(parts) >= 3:
                                log_data.append({
                                    "timestamp": parts[0],
                                    "level": parts[2],
                                    "message": parts[3] if len(parts) > 3 else ""
                                })
                except Exception:
                    continue
            
            return log_data
            
        except Exception as e:
            print(f"Error reading log data: {e}")
            return []
    
    def export_logs(self, export_path: str, format: str = "json", 
                   start_date: Optional[datetime] = None, 
                   end_date: Optional[datetime] = None) -> bool:
        """Export logs to different formats."""
        try:
            log_data = self._read_log_data()
            
            # Filter by date range if specified
            if start_date or end_date:
                filtered_data = []
                for entry in log_data:
                    try:
                        entry_date = datetime.fromisoformat(entry["timestamp"].replace(" ", "T"))
                        if start_date and entry_date < start_date:
                            continue
                        if end_date and entry_date > end_date:
                            continue
                        filtered_data.append(entry)
                    except Exception:
                        continue
                log_data = filtered_data
            
            # Export in requested format
            if format.lower() == "json":
                with open(export_path, 'w', encoding='utf-8') as f:
                    json.dump(log_data, f, indent=2, default=str)
            elif format.lower() == "csv":
                import csv
                with open(export_path, 'w', newline='', encoding='utf-8') as f:
                    if log_data:
                        writer = csv.DictWriter(f, fieldnames=log_data[0].keys())
                        writer.writeheader()
                        writer.writerows(log_data)
            else:
                # Plain text format
                with open(export_path, 'w', encoding='utf-8') as f:
                    for entry in log_data:
                        f.write(f"{entry['timestamp']} - {entry['level']} - {entry['message']}\n")
            
            return True
            
        except Exception as e:
            print(f"Error exporting logs: {e}")
            return False
    
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        try:
            # Flush all handlers
            for handler in self.logger.handlers:
                handler.flush()
        except Exception:
            pass
        
        # Don't suppress exceptions
        return False