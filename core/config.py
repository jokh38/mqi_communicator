"""Configuration management with Pydantic validation for MOQUI automation system."""

import json
import os
from pathlib import Path
from typing import Dict, List, Any, Optional
from pydantic import BaseModel, validator, Field


class ServersConfig(BaseModel):
    """Server configuration model."""
    windows_pc: str = Field(..., description="Windows PC server IP address")
    linux_gpu: str = Field(..., description="Linux GPU server IP address")


class CredentialsConfig(BaseModel):
    """Credentials configuration model."""
    username: str = Field(..., description="SSH username")
    password: str = Field(..., description="SSH password")


class PathsConfig(BaseModel):
    """Paths configuration model."""
    local_logdata: str = Field(..., description="Local log data directory path")
    status_file: str = Field(default="case_status.json", description="Case status file path")
    remote_workspace: str = Field(..., description="Remote workspace directory path")
    local_output: str = Field(..., description="Local output directory path")
    linux_mqi_interpreter: str = Field(..., description="Path to MQI interpreter on Linux")
    linux_raw_to_dcm: str = Field(..., description="Path to raw to DICOM converter on Linux")
    linux_moqui_interpreter_outputs_dir: str = Field(..., description="MQI interpreter outputs directory")
    linux_moqui_outputs_dir: str = Field(..., description="MOQUI outputs directory")
    linux_venv_python: str = Field(..., description="Python virtual environment path")
    linux_moqui_execution: str = Field(..., description="MOQUI execution environment path")


class WorkingDirectoriesConfig(BaseModel):
    """Working directories configuration model."""
    mqi_interpreter: str = Field(..., description="MQI interpreter working directory")
    moqui_binary: str = Field(..., description="MOQUI binary working directory")
    raw2dicom: str = Field(..., description="Raw to DICOM working directory")
    tps_env: str = Field(..., description="TPS environment working directory")


class ScanningConfig(BaseModel):
    """Scanning configuration model."""
    interval_minutes: int = Field(default=30, ge=1, le=1440, description="Scanning interval in minutes")
    max_concurrent_cases: int = Field(default=2, ge=1, le=10, description="Maximum concurrent cases")
    stale_case_recovery_hours: int = Field(default=1, ge=0, description="Stale case recovery threshold in hours")


class GpuManagementConfig(BaseModel):
    """GPU management configuration model."""
    total_gpus: int = Field(..., ge=1, le=16, description="Total number of GPUs")
    reserved_gpus: List[int] = Field(default_factory=list, description="List of reserved GPU IDs")
    memory_threshold_mb: int = Field(default=1024, ge=512, description="GPU memory threshold in MB")
    monitoring_interval_sec: int = Field(default=5, ge=1, le=60, description="GPU monitoring interval in seconds")

    @validator('reserved_gpus')
    def validate_reserved_gpus(cls, v, values):
        """Validate that reserved GPU IDs are within the total GPU range."""
        if 'total_gpus' in values:
            total_gpus = values['total_gpus']
            for gpu_id in v:
                if gpu_id < 0 or gpu_id >= total_gpus:
                    raise ValueError(f"Reserved GPU ID {gpu_id} is out of range (0-{total_gpus-1})")
        return v


class ErrorHandlingConfig(BaseModel):
    """Error handling configuration model."""
    max_network_retries: int = Field(default=3, ge=1, le=10, description="Maximum network retries")
    max_retries: int = Field(default=3, ge=1, le=10, description="Maximum general retries")


class BackupConfig(BaseModel):
    """Backup configuration model."""
    months_to_keep: int = Field(default=12, ge=1, le=60, description="Number of months to keep backups")


class ArchivingConfig(BaseModel):
    """Archiving configuration model."""
    archive_after_days: int = Field(default=30, ge=1, le=365, description="Number of days after which to archive cases")


class DisplayConfig(BaseModel):
    """Display configuration model."""
    refresh_interval_seconds: int = Field(default=2, ge=1, le=10, description="Display refresh interval in seconds")


class LoggingConfig(BaseModel):
    """Logging configuration model."""
    level: str = Field(default="INFO", description="Logging level")
    enable_console: bool = Field(default=True, description="Enable console logging")
    enable_file: bool = Field(default=True, description="Enable file logging")

    @validator('level')
    def validate_level(cls, v):
        """Validate logging level."""
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if v.upper() not in valid_levels:
            raise ValueError(f"Invalid logging level: {v}. Must be one of {valid_levels}")
        return v.upper()


class AppConfig(BaseModel):
    """Main application configuration model."""
    servers: ServersConfig
    credentials: CredentialsConfig
    paths: PathsConfig
    working_directories: WorkingDirectoriesConfig
    scanning: ScanningConfig
    gpu_management: GpuManagementConfig
    error_handling: ErrorHandlingConfig
    backup: BackupConfig
    archiving: ArchivingConfig
    display: DisplayConfig
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    moqui_tps_params: Optional[Dict[str, Any]] = Field(default_factory=dict, description="MOQUI TPS parameters")
    moqui_tps_template: Optional[Dict[str, Any]] = Field(default_factory=dict, description="MOQUI TPS template")


class ConfigManager:
    """Configuration manager with Pydantic validation and environment-specific settings."""
    
    def __init__(self, logger=None):
        """Initialize configuration manager."""
        self.logger = logger
        self.app_env = os.getenv("APP_ENV", "development")
        self.config_dict = self._load_config()
        self.config = self._validate_config(self.config_dict)

    def _load_config(self) -> Dict[str, Any]:
        """Load configuration from default and environment-specific files."""
        try:
            # Load default configuration
            default_config_path = Path("config/default.json")
            if not default_config_path.exists():
                raise FileNotFoundError("Default configuration file 'config/default.json' not found.")

            with open(default_config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            
            # Load environment-specific configuration and merge
            env_config_path = Path(f"config/{self.app_env}.json")
            if env_config_path.exists():
                with open(env_config_path, 'r', encoding='utf-8') as f:
                    env_config = json.load(f)
                config = self._merge_configs(config, env_config)
                if self.logger:
                    self.logger.info(f"Loaded configuration for environment: {self.app_env}")
            else:
                if self.logger:
                    self.logger.info(f"No environment-specific config found for {self.app_env}, using default only")
            
            return config
            
        except FileNotFoundError as e:
            raise FileNotFoundError(f"Configuration file not found: {e}") from e
        except json.JSONDecodeError as e:
            raise json.JSONDecodeError(f"Invalid JSON in config file: {e}", e.doc, e.pos) from e

    def _merge_configs(self, base_config: Dict[str, Any], env_config: Dict[str, Any]) -> Dict[str, Any]:
        """Merge environment-specific configuration over base configuration."""
        merged = base_config.copy()
        
        for key, value in env_config.items():
            if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
                merged[key] = self._merge_configs(merged[key], value)
            else:
                merged[key] = value
        
        return merged

    def _validate_config(self, config_dict: Dict[str, Any]) -> AppConfig:
        """Validate configuration using Pydantic models."""
        try:
            return AppConfig(**config_dict)
        except Exception as e:
            if self.logger:
                self.logger.error(f"Configuration validation failed: {e}")
            raise ValueError(f"Invalid configuration: {e}") from e

    def get_windows_pc_ip(self) -> str:
        """Get Windows PC server IP address."""
        return self.config.servers.windows_pc

    def get_linux_gpu_ip(self) -> str:
        """Get Linux GPU server IP address."""
        return self.config.servers.linux_gpu

    def get_credentials(self) -> Dict[str, str]:
        """Get server credentials."""
        return {
            "username": self.config.credentials.username,
            "password": self.config.credentials.password
        }

    def get_local_logdata_path(self) -> str:
        """Get local log data directory path."""
        return self.config.paths.local_logdata

    def get_remote_workspace_path(self) -> str:
        """Get remote workspace directory path."""
        return self.config.paths.remote_workspace

    def get_local_output_path(self) -> str:
        """Get local output directory path."""
        return self.config.paths.local_output

    def get_scanning_interval(self) -> int:
        """Get scanning interval in minutes."""
        return self.config.scanning.interval_minutes

    def get_max_concurrent_cases(self) -> int:
        """Get maximum concurrent cases."""
        return self.config.scanning.max_concurrent_cases

    def get_total_gpus(self) -> int:
        """Get total number of GPUs."""
        return self.config.gpu_management.total_gpus

    def get_reserved_gpus(self) -> List[int]:
        """Get list of reserved GPU IDs."""
        return self.config.gpu_management.reserved_gpus

    def get_available_gpus(self) -> List[int]:
        """Get list of available GPU IDs (excluding reserved)."""
        total_gpus = self.get_total_gpus()
        reserved_gpus = self.get_reserved_gpus()
        return [gpu_id for gpu_id in range(total_gpus) if gpu_id not in reserved_gpus]

    def get_memory_threshold(self) -> int:
        """Get GPU memory threshold in MB."""
        return self.config.gpu_management.memory_threshold_mb

    def get_monitoring_interval(self) -> int:
        """Get GPU monitoring interval in seconds."""
        return self.config.gpu_management.monitoring_interval_sec

    def get_archive_after_days(self) -> int:
        """Get number of days after which to archive cases."""
        return self.config.archiving.archive_after_days

    def reload_config(self) -> None:
        """Reload configuration from file."""
        self.config_dict = self._load_config()
        self.config = self._validate_config(self.config_dict)

    def update_config(self, key_path: str, value: Any) -> None:
        """Update configuration value using dot notation (e.g., 'servers.windows_pc')."""
        keys = key_path.split('.')
        current = self.config_dict
        
        for key in keys[:-1]:
            if key not in current:
                current[key] = {}
            current = current[key]
        
        current[keys[-1]] = value
        
        # Re-validate after update
        self.config = self._validate_config(self.config_dict)
        
        # Save updated config
        with open(self.config_path, 'w', encoding='utf-8') as f:
            json.dump(self.config_dict, f, indent=2)

    def get_config(self) -> Dict[str, Any]:
        """Get complete configuration dictionary."""
        return self.config_dict.copy()

    def get_moqui_tps_params(self) -> Dict[str, Any]:
        """Get moqui_tps parameters from configuration and log generation success."""
        try:
            tps_params = self.config.moqui_tps_params or {}
            if tps_params:
                # Log successful parameter retrieval as specified in monitoring plan
                if self.logger:
                    self.logger.info(f"Successfully retrieved moqui_tps.in parameters from configuration: {len(tps_params)} parameters")
            return tps_params
        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to retrieve moqui_tps.in parameters from configuration: {e}")
            return {}

    def get_moqui_tps_template(self) -> Dict[str, Any]:
        """Get moqui_tps template from configuration for generating moqui_tps.in files."""
        try:
            tps_template = self.config.moqui_tps_template or {}
            if tps_template:
                if self.logger:
                    self.logger.info(f"Successfully retrieved moqui_tps.in template from configuration: {len(tps_template)} parameters")
            return tps_template
        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to retrieve moqui_tps.in template from configuration: {e}")
            return {}