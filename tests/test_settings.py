import pytest
from pathlib import Path
import yaml
from src.config.settings import Settings

@pytest.fixture
def temp_config_file(tmp_path: Path) -> Path:
    config_data = {
        "paths": {
            "base_directory": "/home/SMC/MOQUI_SMC",
            "local": {
                "scan_directory": "{base_directory}/Outputs_csv",
            },
        },
        "ExecutionHandler": {
            "GpuMonitor": "local",
            "Workflow": "local"
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
    if settings.execution_handler["Workflow"] != "local":
        raise AssertionError("Workflow handler mode should be local")


def test_repo_config_uses_built_moqui_runtime_dir() -> None:
    settings = Settings(config_path=Path("config/config.yaml"))
    base_directory = settings._yaml_config["paths"]["base_directory"]

    if settings.get_path("mqi_run_dir", handler_name="HpcJobSubmitter") != f"{base_directory}/moqui_SMC":
        raise AssertionError("mqi_run_dir should resolve to the moqui repo root")


def test_repo_config_runs_built_tps_env_from_moqui_root() -> None:
    settings = Settings(config_path=Path("config/config.yaml"))
    base_directory = settings._yaml_config["paths"]["base_directory"]

    command = settings.get_command(
        "submit_simulation",
        handler_name="HpcJobSubmitter",
        case_id="55061194",
        beam_id="55061194_2025042401440800",
    )

    if f"cd {base_directory}/moqui_SMC" not in command:
        raise AssertionError(f"Unexpected runtime command: {command}")
    expected_exec = f"./build/tps_env/tps_env {base_directory}/data/Output/55061194/Log_csv/moqui_tps_55061194_2025042401440800.in"
    if expected_exec not in command:
        raise AssertionError(f"Expected executable path missing from command: {command}")


def test_repo_config_uses_single_case_prefix_in_sim_log_name() -> None:
    settings = Settings(config_path=Path("config/config.yaml"))
    base_directory = settings._yaml_config["paths"]["base_directory"]

    log_path = settings.get_path(
        "log_path",
        handler_name="HpcJobSubmitter",
        case_id="55061194",
        beam_id="55061194_2025042401440800",
    )

    if log_path != f"{base_directory}/mqi_communicator/logs/sim_55061194_2025042401440800.log":
        raise AssertionError(f"Unexpected log path: {log_path}")


def test_repo_config_exposes_moqui_runtime_section() -> None:
    settings = Settings(config_path=Path("config/config.yaml"))

    runtime_config = settings.get_moqui_runtime_config()

    if runtime_config["multigpu_enabled"] is not True:
        raise AssertionError(f"Expected multigpu_enabled True, got {runtime_config!r}")
    if runtime_config["beam_uses_all_available_gpus"] is not True:
        raise AssertionError(f"Expected beam_uses_all_available_gpus True, got {runtime_config!r}")
    if runtime_config["max_gpus_per_beam"] != 4:
        raise AssertionError(f"Expected max_gpus_per_beam 4, got {runtime_config!r}")


def test_repo_config_exposes_ptn_checker_section() -> None:
    settings = Settings(config_path=Path("config/config.yaml"))
    base_directory = settings._yaml_config["paths"]["base_directory"]

    ptn_config = settings.get_ptn_checker_config()

    if ptn_config["path"] != f"{base_directory}/ptn_checker":
        raise AssertionError(f"Unexpected PTN checker path: {ptn_config!r}")
    if ptn_config["output_subdir"] != "ptn_checker_output":
        raise AssertionError(f"Unexpected PTN checker output_subdir: {ptn_config!r}")


def test_repo_config_resolves_ptn_checker_output_dir_with_room_grouping() -> None:
    settings = Settings(config_path=Path("config/config.yaml"))
    base_directory = settings._yaml_config["paths"]["base_directory"]

    grouped_output = settings.get_path(
        "ptn_checker_output_dir",
        handler_name="PostProcessor",
        case_id="55061194",
        room="G1",
        room_path="G1/",
    )
    flat_output = settings.get_path(
        "ptn_checker_output_dir",
        handler_name="PostProcessor",
        case_id="55061194",
        room="",
        room_path="",
    )

    if grouped_output != f"{base_directory}/data/Output/G1/55061194/Daily_PTN":
        raise AssertionError(f"Unexpected grouped PTN checker output dir: {grouped_output}")
    if flat_output != f"{base_directory}/data/Output/55061194/Daily_PTN":
        raise AssertionError(f"Unexpected flat PTN checker output dir: {flat_output}")


def test_settings_resolves_repo_relative_config_when_cwd_differs() -> None:
    settings = Settings(config_path=Path("config/config.yaml"))
    base_directory = settings._yaml_config["paths"]["base_directory"]

    scan_directory = settings.get_case_directories()["scan"]

    if scan_directory != Path(f"{base_directory}/data/SHI_log"):
        raise AssertionError(f"Expected repo-relative config resolution, got {scan_directory!r}")
