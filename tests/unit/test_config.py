"""Unit tests for configuration management module."""

import json
import os
import pytest
from pathlib import Path
from unittest.mock import patch, mock_open
from pydantic import ValidationError

from core.config import (
    ConfigManager, 
    ServersConfig, 
    CredentialsConfig, 
    PathsConfig,
    ScanningConfig,
    GpuManagementConfig,
    ErrorHandlingConfig,
    BackupConfig,
    DisplayConfig,
    LoggingConfig,
    AppConfig
)


class TestPydanticModels:
    """Test Pydantic model validation."""
    
    def test_servers_config_valid(self):
        """Test valid server configuration."""
        config = ServersConfig(
            windows_pc="192.168.1.100",
            linux_gpu="192.168.1.101"
        )
        assert config.windows_pc == "192.168.1.100"
        assert config.linux_gpu == "192.168.1.101"
    
    def test_gpu_management_config_valid(self):
        """Test valid GPU management configuration."""
        config = GpuManagementConfig(
            total_gpus=8,
            reserved_gpus=[7],
            memory_threshold_mb=1024,
            monitoring_interval_sec=5
        )
        assert config.total_gpus == 8
        assert config.reserved_gpus == [7]
        assert config.memory_threshold_mb == 1024
        assert config.monitoring_interval_sec == 5
    
    def test_gpu_management_config_invalid_reserved_gpu(self):
        """Test invalid reserved GPU ID validation."""
        with pytest.raises(ValidationError, match="Reserved GPU ID.*is out of range"):
            GpuManagementConfig(
                total_gpus=8,
                reserved_gpus=[10],  # Invalid: > total_gpus
                memory_threshold_mb=1024,
                monitoring_interval_sec=5
            )
    
    def test_scanning_config_invalid_interval(self):
        """Test invalid scanning interval validation."""
        with pytest.raises(ValidationError):
            ScanningConfig(
                interval_minutes=0,  # Invalid: < 1
                max_concurrent_cases=2
            )
    
    def test_logging_config_invalid_level(self):
        """Test invalid logging level validation."""
        with pytest.raises(ValidationError, match="Invalid logging level"):
            LoggingConfig(level="INVALID_LEVEL")
    
    def test_logging_config_valid_level_case_insensitive(self):
        """Test valid logging level with case insensitivity."""
        config = LoggingConfig(level="debug")
        assert config.level == "DEBUG"


class TestConfigManager:
    """Test ConfigManager functionality."""
    
    @pytest.fixture
    def mock_default_config(self):
        """Mock default configuration data."""
        return {
            "servers": {
                "windows_pc": "192.168.1.100",
                "linux_gpu": "192.168.1.101"
            },
            "credentials": {
                "username": "testuser",
                "password": "testpass"
            },
            "paths": {
                "local_logdata": "/test/log_data",
                "remote_workspace": "/test/workspace",
                "local_output": "/test/output",
                "linux_mqi_interpreter": "/test/interpreter.py",
                "linux_raw_to_dcm": "/test/raw2dcm.py",
                "linux_moqui_interpreter_outputs_dir": "/test/outputs",
                "linux_moqui_outputs_dir": "/test/dose",
                "linux_venv_python": "/test/python",
                "linux_moqui_execution": "/test/execution"
            },
            "working_directories": {
                "mqi_interpreter": "/test/mqi",
                "moqui_binary": "/test/moqui",
                "raw2dicom": "/test/raw2dcm",
                "tps_env": "/test/tps"
            },
            "scanning": {
                "interval_minutes": 30,
                "max_concurrent_cases": 2
            },
            "gpu_management": {
                "total_gpus": 8,
                "reserved_gpus": [7],
                "memory_threshold_mb": 1024,
                "monitoring_interval_sec": 5
            },
            "error_handling": {
                "max_network_retries": 3,
                "max_retries": 3
            },
            "backup": {
                "months_to_keep": 12
            },
            "display": {
                "refresh_interval_seconds": 2
            }
        }
    
    @pytest.fixture
    def mock_env_config(self):
        """Mock environment-specific configuration data."""
        return {
            "logging": {
                "level": "DEBUG",
                "enable_console": True
            },
            "scanning": {
                "interval_minutes": 5
            }
        }
    
    @patch.dict(os.environ, {"APP_ENV": "development"})
    @patch("pathlib.Path.exists")
    @patch("builtins.open", new_callable=mock_open)
    def test_load_config_with_environment(self, mock_file, mock_exists, mock_default_config, mock_env_config):
        """Test loading configuration with environment-specific overrides."""
        # Mock file existence
        mock_exists.side_effect = lambda: True
        
        # Mock file contents
        def mock_read(filename, *args, **kwargs):
            if "default.json" in str(filename):
                return mock_open(read_data=json.dumps(mock_default_config))(*args, **kwargs)
            elif "development.json" in str(filename):
                return mock_open(read_data=json.dumps(mock_env_config))(*args, **kwargs)
            return mock_open()(*args, **kwargs)
        
        mock_file.side_effect = mock_read
        
        config_manager = ConfigManager()
        
        # Check that environment-specific values override defaults
        assert config_manager.get_scanning_interval() == 5  # Overridden from 30
        assert config_manager.get_max_concurrent_cases() == 2  # Default preserved
    
    @patch.dict(os.environ, {"APP_ENV": "production"})
    @patch("pathlib.Path.exists")
    @patch("builtins.open", new_callable=mock_open)
    def test_load_config_no_environment_file(self, mock_file, mock_exists, mock_default_config):
        """Test loading configuration when environment file doesn't exist."""
        # Mock only default config exists
        mock_exists.side_effect = lambda self: "default.json" in str(self)
        mock_file.return_value = mock_open(read_data=json.dumps(mock_default_config))()
        
        config_manager = ConfigManager()
        
        # Should use default values
        assert config_manager.get_scanning_interval() == 30
        assert config_manager.get_max_concurrent_cases() == 2
    
    @patch("pathlib.Path.exists")
    @patch("builtins.open", new_callable=mock_open)
    def test_load_config_fallback_to_config_json(self, mock_file, mock_exists, mock_default_config):
        """Test fallback to config.json when config/ directory doesn't exist."""
        # Mock no config/ directory
        mock_exists.return_value = False
        mock_file.return_value = mock_open(read_data=json.dumps(mock_default_config))()
        
        config_manager = ConfigManager("config.json")
        
        assert config_manager.get_scanning_interval() == 30
    
    @patch("pathlib.Path.exists")
    @patch("builtins.open", new_callable=mock_open)
    def test_invalid_configuration_raises_error(self, mock_file, mock_exists):
        """Test that invalid configuration raises ValidationError."""
        invalid_config = {
            "servers": {
                "windows_pc": "192.168.1.100",
                "linux_gpu": "192.168.1.101"
            },
            "gpu_management": {
                "total_gpus": 8,
                "reserved_gpus": [10],  # Invalid: > total_gpus
                "memory_threshold_mb": 1024
            }
            # Missing required fields
        }
        
        mock_exists.return_value = False
        mock_file.return_value = mock_open(read_data=json.dumps(invalid_config))()
        
        with pytest.raises(ValueError, match="Invalid configuration"):
            ConfigManager("config.json")
    
    @patch("pathlib.Path.exists")
    @patch("builtins.open", new_callable=mock_open)
    def test_config_manager_methods(self, mock_file, mock_exists, mock_default_config):
        """Test ConfigManager getter methods."""
        mock_exists.return_value = False
        mock_file.return_value = mock_open(read_data=json.dumps(mock_default_config))()
        
        config_manager = ConfigManager("config.json")
        
        # Test server methods
        assert config_manager.get_windows_pc_ip() == "192.168.1.100"
        assert config_manager.get_linux_gpu_ip() == "192.168.1.101"
        
        # Test credentials
        creds = config_manager.get_credentials()
        assert creds["username"] == "testuser"
        assert creds["password"] == "testpass"
        
        # Test paths
        assert config_manager.get_local_logdata_path() == "/test/log_data"
        assert config_manager.get_remote_workspace_path() == "/test/workspace"
        assert config_manager.get_local_output_path() == "/test/output"
        
        # Test scanning
        assert config_manager.get_scanning_interval() == 30
        assert config_manager.get_max_concurrent_cases() == 2
        
        # Test GPU management
        assert config_manager.get_total_gpus() == 8
        assert config_manager.get_reserved_gpus() == [7]
        assert config_manager.get_available_gpus() == [0, 1, 2, 3, 4, 5, 6]
        assert config_manager.get_memory_threshold() == 1024
        assert config_manager.get_monitoring_interval() == 5
    
    @patch("pathlib.Path.exists")
    @patch("builtins.open", new_callable=mock_open)
    def test_config_manager_empty_optional_fields(self, mock_file, mock_exists, mock_default_config):
        """Test ConfigManager with empty optional fields."""
        mock_exists.return_value = False
        mock_file.return_value = mock_open(read_data=json.dumps(mock_default_config))()
        
        config_manager = ConfigManager("config.json")
        
        # Test empty optional fields
        assert config_manager.get_moqui_tps_params() == {}
        assert config_manager.get_moqui_tps_template() == {}
    
    @patch("pathlib.Path.exists")
    @patch("builtins.open", new_callable=mock_open)
    @patch("json.dump")
    def test_update_config(self, mock_json_dump, mock_file, mock_exists, mock_default_config):
        """Test configuration update functionality."""
        mock_exists.return_value = False
        mock_file.return_value = mock_open(read_data=json.dumps(mock_default_config))()
        
        config_manager = ConfigManager("config.json")
        
        # Update a nested value
        config_manager.update_config("servers.windows_pc", "192.168.1.200")
        
        # Verify the value was updated
        assert config_manager.get_windows_pc_ip() == "192.168.1.200"
        
        # Verify json.dump was called (config was saved)
        mock_json_dump.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__])