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

    if not hasattr(settings, "execution_handler"):
        raise AssertionError("Settings should expose execution_handler")
    if settings.execution_handler["GpuMonitor"] != "local":
        raise AssertionError("GpuMonitor handler mode should be local")
    if settings.execution_handler["Workflow"] != "remote":
        raise AssertionError("Workflow handler mode should be remote")

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

    if connection_info != expected_info:
        raise AssertionError(f"Unexpected connection info: {connection_info!r}")


def test_repo_config_uses_built_moqui_runtime_dir() -> None:
    settings = Settings(config_path=Path("config/config.yaml"))

    if settings.get_path("mqi_run_dir", handler_name="HpcJobSubmitter") != "/home/jokh38/MOQUI_SMC/moqui":
        raise AssertionError("mqi_run_dir should resolve to the moqui repo root")


def test_repo_config_runs_built_tps_env_from_moqui_root() -> None:
    settings = Settings(config_path=Path("config/config.yaml"))

    command = settings.get_command(
        "remote_submit_simulation",
        handler_name="HpcJobSubmitter",
        case_id="55061194",
        beam_id="55061194_2025042401440800",
    )

    if "cd /home/jokh38/MOQUI_SMC/moqui" not in command:
        raise AssertionError(f"Unexpected runtime command: {command}")
    expected_exec = "./tps_env/tps_env /home/jokh38/MOQUI_SMC/data/Outputs_csv/55061194/moqui_tps_55061194_2025042401440800.in"
    if expected_exec not in command:
        raise AssertionError(f"Expected executable path missing from command: {command}")
