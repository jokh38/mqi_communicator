import time
import traceback
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Any, Optional, Callable, Tuple, Union
import random
import functools


class ErrorType(Enum):
    NETWORK = "network"
    FILE_SYSTEM = "file_system"
    GPU = "gpu"
    PROCESS = "process"
    CONFIGURATION = "configuration"
    UNKNOWN = "unknown"


class RetryStrategy(Enum):
    NO_RETRY = "no_retry"
    LINEAR_BACKOFF = "linear_backoff"
    EXPONENTIAL_BACKOFF = "exponential_backoff"
    FIXED_DELAY = "fixed_delay"


class ErrorHandler:
    def __init__(self, max_retry_attempts: int = 3, max_retry_delay: float = 60.0, logger=None):
        self.max_retry_attempts = max_retry_attempts
        self.max_retry_delay = max_retry_delay
        self.logger = logger
        self.error_history: List[Dict[str, Any]] = []
        self.cleanup_functions: List[Callable] = []
        self.error_callback: Optional[Callable] = None
        
        # Error type configurations
        self.retry_strategies = {
            ErrorType.NETWORK: RetryStrategy.EXPONENTIAL_BACKOFF,
            ErrorType.GPU: RetryStrategy.EXPONENTIAL_BACKOFF,
            ErrorType.PROCESS: RetryStrategy.LINEAR_BACKOFF,
            ErrorType.FILE_SYSTEM: RetryStrategy.NO_RETRY,
            ErrorType.CONFIGURATION: RetryStrategy.NO_RETRY,
            ErrorType.UNKNOWN: RetryStrategy.LINEAR_BACKOFF
        }
        
        self.retryable_errors = {
            ErrorType.NETWORK,
            ErrorType.GPU,
            ErrorType.PROCESS,
            ErrorType.UNKNOWN
        }

    def handle_error(self, error: Exception, context: Dict[str, Any] = None) -> None:
        """Handle an error with logging and tracking."""
        try:
            context = context or {}
            error_type = self._classify_error(error)
            
            # Create error record
            error_record = {
                "timestamp": datetime.now(),
                "error_type": error_type.value,
                "error_class": error.__class__.__name__,
                "message": str(error),
                "context": context.copy(),
                "traceback": traceback.format_exc(),
                "is_critical": self.is_critical_error(error)
            }
            
            # Add to history
            self.error_history.append(error_record)
            
            # Log the error
            self._log_error(error_record)
            
            # Call custom callback if set
            if self.error_callback:
                try:
                    self.error_callback(error, context)
                except Exception as callback_error:
                    self.logger.error(f"Error in error callback: {callback_error}")
            
            # Handle critical errors
            if self.is_critical_error(error):
                self._handle_critical_error(error, context)
                
        except Exception as handler_error:
            self.logger.error(f"Error in error handler: {handler_error}")

    def _classify_error(self, error: Exception) -> ErrorType:
        """Classify error type based on exception type and message."""
        try:
            error_message = str(error).lower()
            
            # Process errors (check first since ProcessLookupError inherits from OSError)
            if isinstance(error, (ProcessLookupError, ChildProcessError, BrokenPipeError)):
                return ErrorType.PROCESS
            
            # Network errors
            if isinstance(error, (ConnectionError, TimeoutError)):
                return ErrorType.NETWORK
            
            # File system errors
            if isinstance(error, (FileNotFoundError, PermissionError)):
                return ErrorType.FILE_SYSTEM
            
            # GPU errors
            if isinstance(error, RuntimeError):
                if any(keyword in error_message for keyword in 
                       ["cuda", "gpu", "memory", "device", "nvidia"]):
                    return ErrorType.GPU
            
            # OSError and IOError check (after more specific checks)
            if isinstance(error, (OSError, IOError)):
                if any(keyword in error_message for keyword in 
                       ["network", "connection", "timeout", "unreachable", "socket"]):
                    return ErrorType.NETWORK
                elif any(keyword in error_message for keyword in 
                         ["disk", "space", "permission", "file", "directory"]):
                    return ErrorType.FILE_SYSTEM
                # Default OSError/IOError to FILE_SYSTEM if no specific keywords match
                return ErrorType.FILE_SYSTEM
            
            # Configuration errors
            if isinstance(error, (KeyError, AttributeError, ValueError)):
                if any(keyword in error_message for keyword in 
                       ["config", "setting", "parameter", "missing"]):
                    return ErrorType.CONFIGURATION
            
            return ErrorType.UNKNOWN
            
        except Exception:
            return ErrorType.UNKNOWN

    def _log_error(self, error_record: Dict[str, Any]) -> None:
        """Log error with appropriate level."""
        try:
            message = (
                f"Error [{error_record['error_type']}]: {error_record['message']} "
                f"Context: {error_record['context']}"
            )
            
            if self.logger:
                if error_record["is_critical"]:
                    self.logger.critical(message)
                    if hasattr(self.logger, 'log_structured'):
                        self.logger.log_structured("CRITICAL", "Critical error logged", {
                            "error_type": error_record["error_type"],
                            "error_message": str(error_record["message"]),
                            "context": error_record.get("context", {}),
                            "timestamp": error_record["timestamp"]
                        })
                else:
                    self.logger.error(message)
            else:
                print(f"[CRITICAL] {message}" if error_record["is_critical"] else f"[ERROR] {message}")
                
        except Exception as log_error:
            if self.logger:
                self.logger.error(f"Failed to log error: {log_error}")
            else:
                print(f"[ERROR] Failed to log error: {log_error}")

    def _handle_critical_error(self, error: Exception, context: Dict[str, Any]) -> None:
        """Handle critical errors that require immediate attention."""
        try:
            if self.logger:
                self.logger.critical(f"Critical error detected: {error}")
                if hasattr(self.logger, 'log_structured'):
                    self.logger.log_structured("CRITICAL", "Critical error detected", {
                        "error": str(error),
                        "cleanup_initiated": True
                    })
            else:
                print(f"[CRITICAL] Critical error detected: {error}")
            
            # Perform emergency cleanup
            self.cleanup_resources()
            
            # Could trigger alerts, notifications, etc.
            # For now, just log
            if self.logger:
                self.logger.critical("Emergency cleanup completed")
            else:
                print("[CRITICAL] Emergency cleanup completed")
            
        except Exception as critical_error:
            if self.logger:
                self.logger.error(f"Error handling critical error: {critical_error}")
            else:
                print(f"[ERROR] Error handling critical error: {critical_error}")

    def should_retry(self, error: Exception, attempt: int) -> bool:
        """Determine if an error should be retried."""
        try:
            if attempt >= self.max_retry_attempts:
                return False
            
            error_type = self._classify_error(error)
            
            if error_type not in self.retryable_errors:
                return False
            
            # Check for specific non-retryable conditions
            if isinstance(error, (FileNotFoundError, PermissionError)):
                return False
            
            if isinstance(error, KeyboardInterrupt):
                return False
            
            return True
            
        except Exception:
            return False

    def get_retry_delay(self, attempt: int) -> float:
        """Calculate retry delay based on attempt number."""
        try:
            base_delay = 1.0
            
            # Exponential backoff with jitter
            delay = base_delay * (2 ** (attempt - 1))
            
            # Add jitter (randomness) to prevent thundering herd
            jitter = random.uniform(0.1, 0.5)
            delay += jitter
            
            # Cap at max delay
            return min(delay, self.max_retry_delay)
            
        except Exception:
            return 1.0

    def _get_retry_strategy(self, error_type: ErrorType) -> RetryStrategy:
        """Get retry strategy for error type."""
        return self.retry_strategies.get(error_type, RetryStrategy.LINEAR_BACKOFF)

    def execute_with_retry(self, func: Callable, *args, context: Dict[str, Any] = None, 
                          max_attempts: Optional[int] = None, **kwargs) -> Any:
        """Execute function with retry logic."""
        max_attempts = max_attempts or self.max_retry_attempts
        context = context or {}
        
        for attempt in range(1, max_attempts + 1):
            try:
                return func(*args, **kwargs)
                
            except Exception as error:
                # Handle the error
                error_context = context.copy()
                error_context.update({
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                    "function": func.__name__ if hasattr(func, '__name__') else str(func)
                })
                
                self.handle_error(error, error_context)
                
                # Check if we should retry
                if attempt < max_attempts and self.should_retry(error, attempt):
                    delay = self.get_retry_delay(attempt)
                    error_type = self._classify_error(error)
                    if self.logger:
                        if hasattr(self.logger, 'log_structured'):
                            self.logger.log_structured("INFO", "Operation retry scheduled", {
                                "attempt": attempt + 1,
                                "max_attempts": max_attempts,
                                "delay_seconds": delay,
                                "error_type": error_type.value,
                                "operation": "retryable_operation"
                            })
                        else:
                            self.logger.info(f"Retrying in {delay:.2f} seconds (attempt {attempt + 1}/{max_attempts})")
                    else:
                        print(f"[INFO] Retrying in {delay:.2f} seconds (attempt {attempt + 1}/{max_attempts})")
                    time.sleep(delay)
                    continue
                else:
                    # Final attempt failed or error not retryable
                    raise error
        
        # This should never be reached
        raise RuntimeError("Unexpected end of retry loop")

    def cleanup_resources(self) -> None:
        """Execute all registered cleanup functions."""
        try:
            for cleanup_func in self.cleanup_functions:
                try:
                    cleanup_func()
                except Exception as cleanup_error:
                    self.logger.error(f"Error in cleanup function: {cleanup_error}")
                    
        except Exception as cleanup_error:
            self.logger.error(f"Error during resource cleanup: {cleanup_error}")

    def register_cleanup_function(self, cleanup_func: Callable) -> None:
        """Register a cleanup function to be called on error."""
        try:
            self.cleanup_functions.append(cleanup_func)
        except Exception as register_error:
            self.logger.error(f"Error registering cleanup function: {register_error}")

    def is_critical_error(self, error: Exception) -> bool:
        """Check if error is critical and requires immediate attention."""
        try:
            critical_errors = (
                MemoryError,
                SystemExit,
                KeyboardInterrupt,
                SystemError
            )
            
            if isinstance(error, critical_errors):
                return True
            
            # Check error message for critical keywords
            error_message = str(error).lower()
            critical_keywords = [
                "out of memory",
                "disk full",
                "system error",
                "fatal error",
                "critical"
            ]
            
            return any(keyword in error_message for keyword in critical_keywords)
            
        except Exception:
            return False

    def get_error_count(self) -> int:
        """Get total number of errors handled."""
        return len(self.error_history)

    def get_error_statistics(self) -> Dict[str, Any]:
        """Get error statistics breakdown."""
        try:
            stats = {
                "total_errors": len(self.error_history),
                "critical_errors": 0,
                "network_errors": 0,
                "file_system_errors": 0,
                "gpu_errors": 0,
                "process_errors": 0,
                "configuration_errors": 0,
                "unknown_errors": 0
            }
            
            for error_record in self.error_history:
                if error_record["is_critical"]:
                    stats["critical_errors"] += 1
                
                error_type = error_record["error_type"]
                stats[f"{error_type}_errors"] += 1
            
            return stats
            
        except Exception as stats_error:
            self.logger.error(f"Error getting statistics: {stats_error}")
            return {"total_errors": 0}

    def get_recent_errors(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get most recent errors."""
        try:
            recent_errors = []
            
            for error_record in self.error_history[-limit:]:
                recent_errors.append({
                    "timestamp": error_record["timestamp"].isoformat(),
                    "error_type": error_record["error_type"],
                    "error_class": error_record["error_class"],
                    "message": error_record["message"],
                    "context": error_record["context"],
                    "is_critical": error_record["is_critical"]
                })
            
            # Return in reverse chronological order (most recent first)
            return recent_errors[::-1]
            
        except Exception as recent_error:
            self.logger.error(f"Error getting recent errors: {recent_error}")
            return []

    def clear_error_history(self) -> None:
        """Clear error history."""
        try:
            self.error_history.clear()
            self.logger.info("Error history cleared")
        except Exception as clear_error:
            self.logger.error(f"Error clearing history: {clear_error}")

    def create_error_report(self) -> Dict[str, Any]:
        """Create comprehensive error report."""
        try:
            return {
                "generated_at": datetime.now().isoformat(),
                "total_errors": self.get_error_count(),
                "error_breakdown": self.get_error_statistics(),
                "recent_errors": self.get_recent_errors(5),
                "retry_configuration": {
                    "max_retry_attempts": self.max_retry_attempts,
                    "max_retry_delay": self.max_retry_delay
                }
            }
        except Exception as report_error:
            self.logger.error(f"Error creating error report: {report_error}")
            return {"error": "Failed to generate report"}

    def set_error_callback(self, callback: Callable) -> None:
        """Set custom error callback function."""
        try:
            self.error_callback = callback
        except Exception as callback_error:
            self.logger.error(f"Error setting error callback: {callback_error}")

    def reset(self) -> None:
        """Reset error handler state."""
        try:
            self.error_history.clear()
            self.cleanup_functions.clear()
            self.error_callback = None
            self.logger.info("Error handler reset")
        except Exception as reset_error:
            self.logger.error(f"Error resetting error handler: {reset_error}")

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit with error handling."""
        try:
            if exc_type is not None:
                self.handle_error(exc_val, {"context_manager": True})
            
            # Always cleanup resources
            self.cleanup_resources()
            
        except Exception as exit_error:
            self.logger.error(f"Error in context manager exit: {exit_error}")
        
        # Don't suppress exceptions
        return False

    def configure_retry_strategy(self, error_type: ErrorType, strategy: RetryStrategy) -> None:
        """Configure retry strategy for specific error type."""
        try:
            self.retry_strategies[error_type] = strategy
            self.logger.info(f"Retry strategy for {error_type.value} set to {strategy.value}")
        except Exception as config_error:
            self.logger.error(f"Error configuring retry strategy: {config_error}")

    def add_retryable_error_type(self, error_type: ErrorType) -> None:
        """Add error type to retryable errors."""
        try:
            self.retryable_errors.add(error_type)
            self.logger.info(f"Added {error_type.value} to retryable errors")
        except Exception as add_error:
            self.logger.error(f"Error adding retryable error type: {add_error}")

    def remove_retryable_error_type(self, error_type: ErrorType) -> None:
        """Remove error type from retryable errors."""
        try:
            self.retryable_errors.discard(error_type)
            self.logger.info(f"Removed {error_type.value} from retryable errors")
        except Exception as remove_error:
            self.logger.error(f"Error removing retryable error type: {remove_error}")

    def get_error_trends(self, hours: int = 24) -> Dict[str, Any]:
        """Get error trends over specified time period."""
        try:
            cutoff_time = datetime.now() - timedelta(hours=hours)
            recent_errors = [
                error for error in self.error_history 
                if error["timestamp"] > cutoff_time
            ]
            
            trends = {
                "time_period_hours": hours,
                "total_errors": len(recent_errors),
                "error_rate_per_hour": len(recent_errors) / hours if hours > 0 else 0,
                "error_types": {},
                "critical_error_count": 0
            }
            
            for error in recent_errors:
                error_type = error["error_type"]
                trends["error_types"][error_type] = trends["error_types"].get(error_type, 0) + 1
                
                if error["is_critical"]:
                    trends["critical_error_count"] += 1
            
            return trends
            
        except Exception as trends_error:
            self.logger.error(f"Error getting error trends: {trends_error}")
            return {"error": "Failed to get trends"}

    def export_error_history(self, file_path: str) -> bool:
        """Export error history to file."""
        try:
            import json
            
            export_data = {
                "exported_at": datetime.now().isoformat(),
                "total_errors": len(self.error_history),
                "errors": []
            }
            
            for error_record in self.error_history:
                export_data["errors"].append({
                    "timestamp": error_record["timestamp"].isoformat(),
                    "error_type": error_record["error_type"],
                    "error_class": error_record["error_class"],
                    "message": error_record["message"],
                    "context": error_record["context"],
                    "is_critical": error_record["is_critical"]
                })
            
            with open(file_path, 'w') as f:
                json.dump(export_data, f, indent=2)
            
            self.logger.info(f"Error history exported to {file_path}")
            return True
            
        except Exception as export_error:
            self.logger.error(f"Error exporting error history: {export_error}")
            return False


def retry_on_exception(max_retries: int = 3, delay_seconds: float = 1.0, 
                      exceptions_to_catch: Union[Exception, Tuple[Exception, ...]] = Exception,
                      backoff_strategy: str = "exponential", logger=None):
    """
    Decorator for retrying function execution on specific exceptions.
    
    Args:
        max_retries: Maximum number of retry attempts
        delay_seconds: Base delay between retries in seconds
        exceptions_to_catch: Exception types to catch and retry on
        backoff_strategy: 'linear', 'exponential', or 'fixed'
        logger: Logger instance for retry notifications
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            
            for attempt in range(max_retries + 1):  # +1 for initial attempt
                try:
                    return func(*args, **kwargs)
                except exceptions_to_catch as e:
                    last_exception = e
                    
                    if attempt < max_retries:  # Not the final attempt
                        # Calculate delay based on strategy
                        if backoff_strategy == "exponential":
                            delay = delay_seconds * (2 ** attempt)
                        elif backoff_strategy == "linear":
                            delay = delay_seconds * (attempt + 1)
                        else:  # fixed
                            delay = delay_seconds
                        
                        # Add jitter to prevent thundering herd
                        jitter = random.uniform(0.1, 0.3) * delay
                        actual_delay = delay + jitter
                        
                        if logger:
                            logger.warning(f"Attempt {attempt + 1}/{max_retries + 1} failed for {func.__name__}: {e}. Retrying in {actual_delay:.2f}s")
                        
                        time.sleep(actual_delay)
                    else:
                        # Final attempt failed
                        if logger:
                            logger.error(f"All {max_retries + 1} attempts failed for {func.__name__}: {last_exception}")
                        break
                except Exception as e:
                    # Exception not in our catch list, don't retry
                    if logger:
                        logger.error(f"Non-retryable exception in {func.__name__}: {e}")
                    raise
            
            # All retries exhausted, raise the last exception
            raise last_exception
        
        return wrapper
    return decorator