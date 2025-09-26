import pytest
from pathlib import Path
import yaml
from src.config.settings import Settings
from unittest.mock import patch

@pytest.fixture
def detailed_config_file(tmp_path: Path) -> Path:
    """Provides a detailed, hierarchical config file for testing."""
    config_data = {
        "ExecutionHandler": {
            "CsvInterpreter": "local",
            "HpcJobSubmitter": "remote",
            "PostProcessor": "local",
            "ResultUploader": "remote",
        },
        "paths": {
            "base_directory": "/test/base",
            "local": {
                "scan_directory": "{base_directory}/dicom_input",
                "csv_output_dir": "{base_directory}/temp_files/{case_id}/csv",
                "simulation_output_dir": "{base_directory}/temp_files/{case_id}/raw_output",
                "final_dicom_dir": "{base_directory}/final_dicom_output/{case_id}",
            },
            "remote": {
                "remote_case_path": "/hpc/cases/{case_id}",
                "remote_script_path": "/hpc/scripts",
                "pc_localdata_remote_base_dir": "D:/MOQUI_RESULTS/{case_id}"
            },
        },
        "executables": {
            "local": {
                "python": "/usr/bin/python3",
                "mqi_interpreter_script": "{base_directory}/mqi_interpreter/main_cli.py",
                "simulation_script": "{base_directory}/moqui_simulator/run_cli.py",
                "raw_to_dicom_script": "{base_directory}/RawToDCM/moqui_raw2dicom.py",
            },
            "remote": {
                "python": "/path/on/hpc/to/python",
            },
        },
        "command_templates": {
            "local": {
                "interpret_csv": "{python} {mqi_interpreter_script} --input {scan_directory}/{case_id} --output {csv_output_dir}",
                "post_process": "{python} {raw_to_dicom_script} --input {simulation_output_dir}/output.raw --output {final_dicom_dir}",
            },
            "remote": {
                "submit_simulation": "sbatch {remote_script_path}/run_moqui.sh --case-id {case_id}",
                "upload_to_pc_localdata": "scp {local_file_path} user@{pc_ip}:{pc_localdata_remote_base_dir}"
            },
        },
    }
    config_file = tmp_path / "config.yaml"
    with open(config_file, "w") as f:
        yaml.dump(config_data, f)
    return config_file

def test_load_hierarchical_config(detailed_config_file: Path):
    """
    Tests that Settings loads the entire hierarchical YAML structure.
    """
    settings = Settings(config_path=detailed_config_file)
    assert settings._yaml_config is not None
    assert "ExecutionHandler" in settings._yaml_config
    assert "local" in settings._yaml_config["paths"]
    assert "remote" in settings._yaml_config["executables"]
    assert "submit_simulation" in settings._yaml_config["command_templates"]["remote"]

def test_get_path_local_mode(detailed_config_file: Path):
    """
    Tests get_path for a handler configured in 'local' mode.
    """
    settings = Settings(config_path=detailed_config_file)
    # CsvInterpreter is 'local', so it should use the local path.
    path = settings.get_path("csv_output_dir", handler_name="CsvInterpreter", case_id="C123")
    expected_path = "/test/base/temp_files/C123/csv"
    assert path == expected_path

def test_get_path_remote_mode(detailed_config_file: Path):
    """
    Tests get_path for a handler configured in 'remote' mode.
    """
    settings = Settings(config_path=detailed_config_file)
    # HpcJobSubmitter is 'remote', so it should use the remote path.
    path = settings.get_path("remote_case_path", handler_name="HpcJobSubmitter", case_id="C456")
    expected_path = "/hpc/cases/C456"
    assert path == expected_path

def test_get_path_with_base_dir_fallback(detailed_config_file: Path):
    """
    Tests that get_path can resolve paths that only exist in the base 'paths' config.
    """
    settings = Settings(config_path=detailed_config_file)
    # Use a valid handler; the mode doesn't matter for a base path.
    path = settings.get_path("base_directory", handler_name="CsvInterpreter")
    assert path == "/test/base"

def test_get_command_local_mode(detailed_config_file: Path):
    """
    Tests get_command for a 'local' command, ensuring full recursive formatting.
    """
    settings = Settings(config_path=detailed_config_file)
    # CsvInterpreter is 'local'.
    command = settings.get_command("interpret_csv", handler_name="CsvInterpreter", case_id="C123")

    expected_command = (
        "/usr/bin/python3 /test/base/mqi_interpreter/main_cli.py "
        "--input /test/base/dicom_input/C123 "
        "--output /test/base/temp_files/C123/csv"
    )
    assert command == expected_command

def test_get_command_remote_mode(detailed_config_file: Path):
    """
    Tests get_command for a 'remote' command.
    """
    settings = Settings(config_path=detailed_config_file)
    # HpcJobSubmitter is 'remote'.
    command = settings.get_command("submit_simulation", handler_name="HpcJobSubmitter", case_id="C456")

    expected_command = "sbatch /hpc/scripts/run_moqui.sh --case-id C456"
    assert command == expected_command

def test_get_command_with_mixed_paths(detailed_config_file: Path):
    """
    Tests a command that uses placeholders from different path contexts.
    """
    settings = Settings(config_path=detailed_config_file)
    # ResultUploader is 'remote'.
    command = settings.get_command(
        "upload_to_pc_localdata",
        handler_name="ResultUploader",
        case_id="C789",
        local_file_path="/test/base/final_dicom_output/C789/final.dcm",
        pc_ip="192.168.1.100"
    )

    # The command template is in 'remote', but it resolves {pc_localdata_remote_base_dir} from 'remote' paths
    expected_command = "scp /test/base/final_dicom_output/C789/final.dcm user@192.168.1.100:D:/MOQUI_RESULTS/C789"
    assert command == expected_command

def test_get_command_missing_handler_raises_error(detailed_config_file: Path):
    """
    Tests that get_command raises an error if the handler is not in ExecutionHandler config.
    """
    settings = Settings(config_path=detailed_config_file)
    with pytest.raises(KeyError, match="Handler 'NonExistentHandler' not found in ExecutionHandler config."):
        settings.get_command("any_command", handler_name="NonExistentHandler")

def test_get_command_missing_template_raises_error(detailed_config_file: Path):
    """
    Tests that get_command raises an error if the command template doesn't exist for the mode.
    """
    settings = Settings(config_path=detailed_config_file)
    with pytest.raises(KeyError, match="Command template 'non_existent_command' not found for mode 'local'."):
        settings.get_command("non_existent_command", handler_name="CsvInterpreter") # CsvInterpreter is local