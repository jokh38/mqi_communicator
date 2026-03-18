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
    expected_line = "OutputDir ../data/Dose_dcm/55061194/beam_3"
    if expected_line not in content:
        raise AssertionError(f"Expected line missing from TPS file: {expected_line}")
