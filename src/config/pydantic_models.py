"""Pydantic models for configuration validation.

This module defines type-safe, validated configuration models using Pydantic.
It provides runtime validation and clear error messages for misconfigured settings.

Phase 4: Architectural Enhancement - Pydantic Configuration Validation
"""

from typing import Dict, Optional
from pydantic import BaseModel, Field, field_validator, model_validator
from enum import Enum


class JournalMode(str, Enum):
    """Valid SQLite journal modes"""
    DELETE = "DELETE"
    TRUNCATE = "TRUNCATE"
    PERSIST = "PERSIST"
    MEMORY = "MEMORY"
    WAL = "WAL"
    OFF = "OFF"


class SynchronousMode(str, Enum):
    """Valid SQLite synchronous modes"""
    OFF = "OFF"
    NORMAL = "NORMAL"
    FULL = "FULL"
    EXTRA = "EXTRA"


class LogLevel(str, Enum):
    """Valid logging levels"""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class DatabaseConfig(BaseModel):
    """Database configuration with validation.

    Attributes:
        connection_timeout_seconds: Database connection timeout (1-300 seconds)
        journal_mode: SQLite journal mode (WAL recommended for concurrency)
        synchronous: SQLite synchronous mode (NORMAL recommended)
        cache_size: SQLite cache size (negative = KB, positive = pages)
    """
    connection_timeout_seconds: int = Field(
        default=30,
        ge=1,
        le=300,
        description="Database connection timeout in seconds"
    )
    journal_mode: JournalMode = Field(
        default=JournalMode.WAL,
        description="SQLite journal mode"
    )
    synchronous: SynchronousMode = Field(
        default=SynchronousMode.NORMAL,
        description="SQLite synchronous mode"
    )
    cache_size: int = Field(
        default=-2000,
        description="SQLite cache size (negative=KB, positive=pages)"
    )

    model_config = {
        "use_enum_values": True  # Serialize enums as their values
    }


class ProcessingConfig(BaseModel):
    """Processing configuration with validation.

    Attributes:
        max_retries: Maximum number of retries for failed operations
        retry_delay_seconds: Delay between retry attempts
        max_workers: Maximum number of worker processes
    """
    max_retries: int = Field(
        default=3,
        ge=0,
        le=10,
        description="Maximum number of retries for failed operations"
    )
    retry_delay_seconds: int = Field(
        default=5,
        ge=1,
        le=60,
        description="Delay between retry attempts in seconds"
    )
    max_workers: int = Field(
        default=4,
        ge=1,
        le=32,
        description="Maximum number of worker processes"
    )


class ProgressTrackingConfig(BaseModel):
    """Progress tracking configuration with validation.

    Attributes:
        polling_interval_seconds: How often to poll for progress updates
        coarse_phase_progress: Mapping of phase names to progress percentages
    """
    polling_interval_seconds: int = Field(
        default=5,
        ge=1,
        le=60,
        description="Progress polling interval in seconds"
    )
    coarse_phase_progress: Dict[str, float] = Field(
        default_factory=lambda: {
            "CSV_INTERPRETING": 10.0,
            "UPLOADING": 20.0,
            "HPC_QUEUED": 30.0,
            "HPC_RUNNING": 70.0,
            "DOWNLOADING": 85.0,
            "POSTPROCESSING": 95.0,
            "COMPLETED": 100.0,
        },
        description="Mapping of phase names to progress percentages"
    )

    @field_validator('coarse_phase_progress')
    @classmethod
    def validate_progress_percentages(cls, v: Dict[str, float]) -> Dict[str, float]:
        """Validate that all progress values are between 0 and 100"""
        for phase, progress in v.items():
            if not (0.0 <= progress <= 100.0):
                raise ValueError(
                    f"Progress for phase '{phase}' must be between 0 and 100, got {progress}"
                )
        return v


class LoggingConfig(BaseModel):
    """Logging configuration with validation.

    Attributes:
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_dir: Directory for log files
        max_file_size_mb: Maximum size of log file before rotation
        backup_count: Number of backup log files to keep
    """
    level: LogLevel = Field(
        default=LogLevel.INFO,
        description="Logging level"
    )
    log_dir: str = Field(
        default="{base_directory}/logs",
        description="Directory for log files"
    )
    max_file_size_mb: int = Field(
        default=10,
        ge=1,
        le=1000,
        description="Maximum log file size in MB before rotation"
    )
    backup_count: int = Field(
        default=5,
        ge=0,
        le=100,
        description="Number of backup log files to keep"
    )

    model_config = {
        "use_enum_values": True
    }


class GpuConfig(BaseModel):
    """GPU configuration with validation.

    Attributes:
        enabled: Whether GPU processing is enabled
        memory_threshold_mb: Minimum free memory required (MB)
        utilization_threshold_percent: Maximum utilization threshold (%)
        polling_interval_seconds: GPU status polling interval
    """
    enabled: bool = Field(
        default=True,
        description="Whether GPU processing is enabled"
    )
    memory_threshold_mb: int = Field(
        default=1000,
        ge=0,
        le=100000,
        description="Minimum free GPU memory required in MB"
    )
    utilization_threshold_percent: int = Field(
        default=80,
        ge=0,
        le=100,
        description="Maximum GPU utilization threshold percentage"
    )
    polling_interval_seconds: int = Field(
        default=10,
        ge=1,
        le=300,
        description="GPU status polling interval in seconds"
    )


class UIConfig(BaseModel):
    """UI configuration with validation.

    Attributes:
        refresh_interval_seconds: UI refresh interval
        max_cases_display: Maximum number of cases to display
    """
    refresh_interval_seconds: int = Field(
        default=1,
        ge=1,
        le=60,
        description="UI refresh interval in seconds"
    )
    max_cases_display: int = Field(
        default=50,
        ge=1,
        le=1000,
        description="Maximum number of cases to display in UI"
    )


class RetryPolicyConfig(BaseModel):
    """Retry policy configuration with validation.

    Attributes:
        max_attempts: Maximum number of retry attempts
        initial_delay_seconds: Initial delay before first retry
        max_delay_seconds: Maximum delay between retries
        backoff_factor: Exponential backoff multiplier
    """
    max_attempts: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Maximum number of retry attempts"
    )
    initial_delay_seconds: int = Field(
        default=1,
        ge=1,
        le=60,
        description="Initial delay before first retry in seconds"
    )
    max_delay_seconds: int = Field(
        default=60,
        ge=1,
        le=3600,
        description="Maximum delay between retries in seconds"
    )
    backoff_factor: float = Field(
        default=2.0,
        ge=1.0,
        le=10.0,
        description="Exponential backoff multiplier"
    )


class AppConfig(BaseModel):
    """Root application configuration with validation.

    This is the top-level configuration model that contains all sub-configurations.
    It provides type-safe, validated access to all application settings.

    Attributes:
        database: Database configuration
        processing: Processing configuration
        progress_tracking: Progress tracking configuration
        logging: Logging configuration
        gpu: GPU configuration
        ui: UI configuration
        retry_policy: Retry policy configuration
    """
    database: DatabaseConfig = Field(
        default_factory=DatabaseConfig,
        description="Database configuration"
    )
    processing: ProcessingConfig = Field(
        default_factory=ProcessingConfig,
        description="Processing configuration"
    )
    progress_tracking: ProgressTrackingConfig = Field(
        default_factory=ProgressTrackingConfig,
        description="Progress tracking configuration"
    )
    logging: LoggingConfig = Field(
        default_factory=LoggingConfig,
        description="Logging configuration"
    )
    gpu: GpuConfig = Field(
        default_factory=GpuConfig,
        description="GPU configuration"
    )
    ui: UIConfig = Field(
        default_factory=UIConfig,
        description="UI configuration"
    )
    retry_policy: RetryPolicyConfig = Field(
        default_factory=RetryPolicyConfig,
        description="Retry policy configuration"
    )

    model_config = {
        "validate_assignment": True,  # Validate on attribute assignment
        "extra": "allow",  # Allow extra fields for backward compatibility
    }

    @model_validator(mode='after')
    def validate_config_consistency(self) -> 'AppConfig':
        """Validate cross-field consistency rules"""
        # Example: Ensure retry delay doesn't exceed max delay
        if (self.retry_policy.initial_delay_seconds >
            self.retry_policy.max_delay_seconds):
            raise ValueError(
                "initial_delay_seconds cannot exceed max_delay_seconds"
            )
        return self
