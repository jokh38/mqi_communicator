from pathlib import Path
from unittest.mock import MagicMock, patch

from src.config.settings import Settings
from src.core.tps_generator import TpsGenerator


def test_generate_tps_file_uses_beam_specific_output_dir(tmp_path: Path) -> None:
    settings = Settings(config_path=Path("config/config.yaml"))
    logger = MagicMock()
    generator = TpsGenerator(settings=settings, logger=logger)
    case_path = tmp_path / "case"
    case_path.mkdir()
    output_dir = tmp_path / "out"

    with patch.object(TpsGenerator, "_extract_case_data", return_value={"GantryNum": 0}):
        success = generator.generate_tps_file_with_gpu_assignments(
            case_path=case_path,
            case_id="55061194",
            gpu_assignments=[{"gpu_uuid": "gpu-1", "gpu_id": 2}],
            execution_mode="local",
            output_dir=output_dir,
            beam_name="55061194_2025042401552900",
            beam_number=3,
        )

    if success is not True:
        raise AssertionError(f"Expected TPS generation success, got {success!r}")
    tps_file = output_dir / "moqui_tps_55061194_2025042401552900.in"
    content = tps_file.read_text(encoding="utf-8")
    expected_line = "OutputDir ../data/Dose_dcm/55061194"
    if expected_line not in content:
        raise AssertionError(f"Expected line missing from TPS file: {expected_line}")


def test_generate_tps_file_multigpu_single_beam_writes_all_gpu_ids(tmp_path: Path) -> None:
    settings = Settings(config_path=Path("config/config.yaml"))
    settings.get_moqui_runtime_config = MagicMock(return_value={
        "multigpu_enabled": True,
        "beam_uses_all_available_gpus": True,
        "max_gpus_per_beam": 4,
    })
    logger = MagicMock()
    generator = TpsGenerator(settings=settings, logger=logger)
    case_path = tmp_path / "case"
    case_path.mkdir()
    output_dir = tmp_path / "out"

    with patch.object(TpsGenerator, "_extract_case_data", return_value={"GantryNum": 0}):
        success = generator.generate_tps_file_with_gpu_assignments(
            case_path=case_path,
            case_id="55061194",
            gpu_assignments=[
                {"gpu_uuid": "gpu-0", "gpu_id": 0},
                {"gpu_uuid": "gpu-2", "gpu_id": 2},
                {"gpu_uuid": "gpu-5", "gpu_id": 5},
            ],
            execution_mode="local",
            output_dir=output_dir,
            beam_name="beam-a",
            beam_number=7,
        )

    if success is not True:
        raise AssertionError(f"Expected TPS generation success, got {success!r}")

    tps_file = output_dir / "moqui_tps_beam-a.in"
    content = tps_file.read_text(encoding="utf-8")
    if "BeamNumbers 7" not in content:
        raise AssertionError(f"Expected BeamNumbers to preserve treatment beam number, content was:\n{content}")
    if "GPUID 0,2,5" not in content:
        raise AssertionError(f"Expected GPUID list for multigpu single-beam execution, content was:\n{content}")


def test_generate_tps_file_single_gpu_mode_keeps_scalar_gpu_id(tmp_path: Path) -> None:
    settings = Settings(config_path=Path("config/config.yaml"))
    settings.get_moqui_runtime_config = MagicMock(return_value={
        "multigpu_enabled": False,
        "beam_uses_all_available_gpus": False,
        "max_gpus_per_beam": 1,
    })
    logger = MagicMock()
    generator = TpsGenerator(settings=settings, logger=logger)
    case_path = tmp_path / "case"
    case_path.mkdir()
    output_dir = tmp_path / "out"

    with patch.object(TpsGenerator, "_extract_case_data", return_value={"GantryNum": 0}):
        success = generator.generate_tps_file_with_gpu_assignments(
            case_path=case_path,
            case_id="55061194",
            gpu_assignments=[{"gpu_uuid": "gpu-3", "gpu_id": 3}],
            execution_mode="local",
            output_dir=output_dir,
            beam_name="beam-a",
            beam_number=2,
        )

    if success is not True:
        raise AssertionError(f"Expected TPS generation success, got {success!r}")

    tps_file = output_dir / "moqui_tps_beam-a.in"
    content = tps_file.read_text(encoding="utf-8")
    if "BeamNumbers 2" not in content:
        raise AssertionError(f"Expected BeamNumbers 2 for single-GPU mode, content was:\n{content}")
    if "GPUID 3" not in content:
        raise AssertionError(f"Expected scalar GPUID for single-GPU mode, content was:\n{content}")
