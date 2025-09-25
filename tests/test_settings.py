import pytest
from pathlib import Path
import yaml
from src.config.settings import Settings

@pytest.fixture
def temp_config_file(tmp_path: Path) -> Path:
    config_data = {
        "paths": {
            "base_directory": "/home/jokh38/MOQUI_SMC",
            "local": {
                "scan_directory": "{base_directory}/Outputs_csv",
            },
        },
        "pc_localdata_connection": {
            "host": "PC_LOCALDATA_IP_ADDRESS",
            "user": "WINDOWS_USERNAME",
            "ssh_key_path": "/home/jokh38/.ssh/id_rsa_for_localdata",
            "remote_base_dir": "D:/MOQUI_RESULTS",
        },
        "ExecutionHandler": {
            "GpuMonitor": "local",
            "Workflow": "remote"
        }
    }
    config_file = tmp_path / "config.yaml"
    with open(config_file, "w") as f:
        yaml.dump(config_data, f)
    return config_file

def test_load_execution_handler_settings(temp_config_file: Path):
    """
    Tests that the Settings class correctly loads the ExecutionHandler
    configuration from the config file.
    """
    settings = Settings(config_path=temp_config_file)

    assert hasattr(settings, "execution_handler")
    assert settings.execution_handler["GpuMonitor"] == "local"
    assert settings.execution_handler["Workflow"] == "remote"

def test_get_pc_localdata_connection(temp_config_file: Path):
    """
    Tests that the get_pc_localdata_connection method correctly retrieves
    the connection details from the config file.
    """
    settings = Settings(config_path=temp_config_file)

    # This method doesn't exist yet, so this test should fail.
    connection_info = settings.get_pc_localdata_connection()

    expected_info = {
        "host": "PC_LOCALDATA_IP_ADDRESS",
        "user": "WINDOWS_USERNAME",
        "ssh_key_path": "/home/jokh38/.ssh/id_rsa_for_localdata",
        "remote_base_dir": "D:/MOQUI_RESULTS",
    }

    assert connection_info == expected_info
