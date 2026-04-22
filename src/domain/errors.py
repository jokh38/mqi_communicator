# =====================================================================================
# Target File: src/domain/errors.py
# Source Reference: src/error_categorization.py
# =====================================================================================
"""Defines custom exception classes for the application."""

class MQIError(Exception):
    """Base exception class for all MQI application errors."""
    def __init__(self, message: str, context: dict = None):
        super().__init__(message)
        self.message = message
        self.context = context or {}

class DatabaseError(MQIError):
    """Exception raised for database-related errors."""
    pass

class GpuResourceError(MQIError):
    """Exception raised for GPU resource management errors."""
    pass

class ProcessingError(MQIError):
    """Exception raised for case processing errors."""
    def __init__(self, message: str, case_id: str = None, context: dict = None):
        super().__init__(message, context)
        self.case_id = case_id

class RetryableError(MQIError):
    """Exception raised for errors that can be safely retried."""
    pass


class PermanentFailureError(MQIError):
    """Exception raised for errors that should not be retried automatically."""
    pass
