import os
import json
from pathlib import Path
import pytest
from core.config import ConfigManager, AppConfig

# Fixture to create temporary config files
@pytest.fixture
def temp_config_files(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    default_config = {
        "servers": {"windows_pc": "default_ip", "linux_gpu": "default_gpu"},
        "credentials": {"username": "user", "password": "password"},
        "paths": {
            "local_logdata": "/tmp/log",
            "remote_workspace": "/tmp/remote",
            "local_output": "/tmp/output",
            "linux_mqi_interpreter": "default",
            "linux_raw_to_dcm": "default",
            "linux_moqui_interpreter_outputs_dir": "default",
            "linux_moqui_outputs_dir": "default",
            "linux_venv_python": "default",
            "linux_moqui_execution": "default",
        },
        "working_directories": {
            "mqi_interpreter": "default",
            "moqui_binary": "default",
            "raw2dicom": "default",
            "tps_env": "default",
        },
        "scanning": {"interval_minutes": 10, "max_concurrent_cases": 1},
        "gpu_management": {"total_gpus": 2, "reserved_gpus": [1], "memory_threshold_mb": 512, "monitoring_interval_sec": 10},
        "error_handling": {"max_network_retries": 1, "max_retries": 1},
        "backup": {"months_to_keep": 1},
        "archiving": {"archive_after_days": 7},
        "display": {"refresh_interval_seconds": 5},
        "logging": {"level": "INFO", "enable_console": True, "enable_file": False}
    }

    dev_config = {
        "servers": {"windows_pc": "dev_ip"},
        "logging": {"level": "DEBUG"}
    }

    (config_dir / "default.json").write_text(json.dumps(default_config))
    (config_dir / "development.json").write_text(json.dumps(dev_config))

    # Change working directory to tmp_path for the test
    original_cwd = Path.cwd()
    os.chdir(tmp_path)
    yield
    os.chdir(original_cwd)

def test_config_manager_loads_default_config(temp_config_files):
    """Test that ConfigManager loads the default config correctly."""
    os.environ["APP_ENV"] = "production"  # Use an env with no specific file
    config_manager = ConfigManager()

    assert isinstance(config_manager.config, AppConfig)
    assert config_manager.get_windows_pc_ip() == "default_ip"
    assert config_manager.config.logging.level == "INFO"

def test_config_manager_loads_env_specific_config(temp_config_files):
    """Test that ConfigManager merges environment-specific config."""
    os.environ["APP_ENV"] = "development"
    config_manager = ConfigManager()

    assert config_manager.get_windows_pc_ip() == "dev_ip"
    assert config_manager.config.logging.level == "DEBUG"
    assert config_manager.get_max_concurrent_cases() == 1  # From default config

def test_config_manager_get_config_returns_copy(temp_config_files):
    """Test that get_config() returns a copy, not a reference."""
    config_manager = ConfigManager()
    config_dict = config_manager.get_config()
    config_dict["new_key"] = "new_value"

    assert "new_key" not in config_manager.get_config()
