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

class WorkflowError(MQIError):
    """Exception raised for workflow execution errors."""
    def __init__(self, message: str, step: str = None, case_id: str = None, context: dict = None):
        super().__init__(message, context)
        self.step = step
        self.case_id = case_id

class ConfigurationError(MQIError):
    """Exception raised for configuration-related errors."""
    pass

class ProcessingError(MQIError):
    """Exception raised for case processing errors."""
    def __init__(self, message: str, case_id: str = None, context: dict = None):
        super().__init__(message, context)
        self.case_id = case_id


class FileUploadError(MQIError):
    """Exception raised for file upload errors."""
    pass

class ValidationError(MQIError):
    """Exception raised for input validation errors."""
    pass

class RetryableError(MQIError):
    """Exception raised for errors that can be safely retried."""
    pass