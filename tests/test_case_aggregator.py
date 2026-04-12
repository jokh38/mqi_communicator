from pathlib import Path
from unittest.mock import MagicMock, patch

from src.core.case_aggregator import prepare_beam_jobs, prepare_case_delivery_data


def _write_planinfo(beam_dir: Path, patient_id: str, beam_number: int) -> None:
    (beam_dir / "PlanInfo.txt").write_text(
        "\n".join(
            [
                f"DICOM_PATIENT_ID,{patient_id}",
                f"DICOM_BEAM_NUMBER,{beam_number}",
                f"TCSC_IRRAD_DATETIME,{beam_dir.name}",
            ]
        ),
        encoding="ascii",
    )


@patch("src.core.case_aggregator.LoggerFactory.get_logger")
@patch("src.core.case_aggregator.DataIntegrityValidator")
def test_prepare_beam_jobs_maps_timestamp_folders_to_treatment_beam_indices(
    mock_validator_cls,
    mock_get_logger,
    tmp_path,
):
    logger = MagicMock()
    mock_get_logger.return_value = logger

    case_path = tmp_path / "55061194"
    case_path.mkdir()
    (case_path / "RP.test.dcm").write_text("", encoding="ascii")

    beam_dirs = [
        case_path / "2025042401440800",
        case_path / "2025042401501400",
        case_path / "2025042401552900",
    ]
    beam_numbers = [2, 3, 4]

    for beam_dir, beam_number in zip(beam_dirs, beam_numbers):
        beam_dir.mkdir()
        (beam_dir / "sample.ptn").write_text("", encoding="ascii")
        _write_planinfo(beam_dir, "55061194", beam_number)

    validator = MagicMock()
    validator.find_rtplan_file.return_value = case_path / "RP.test.dcm"
    validator.parse_rtplan_beam_count.return_value = 3
    validator.get_beam_information.return_value = {
        "beams": [
            {"beam_name": "Beam_2", "beam_number": 2},
            {"beam_name": "Beam_3", "beam_number": 3},
            {"beam_name": "Beam_4", "beam_number": 4},
        ]
    }
    mock_validator_cls.return_value = validator

    result = prepare_beam_jobs("55061194", case_path, settings=MagicMock())

    actual = [(job["beam_path"].name, job["beam_number"]) for job in result]
    expected = [
        ("2025042401440800", 1),
        ("2025042401501400", 2),
        ("2025042401552900", 3),
    ]
    if actual != expected:
        raise AssertionError(f"Expected normalized treatment beam indices {expected!r}, got {actual!r}")


@patch("src.core.case_aggregator.LoggerFactory.get_logger")
@patch("src.core.case_aggregator.DataIntegrityValidator")
def test_prepare_beam_jobs_fails_when_planinfo_patient_id_mismatches_rtplan(
    mock_validator_cls,
    mock_get_logger,
    tmp_path,
):
    logger = MagicMock()
    mock_get_logger.return_value = logger

    case_path = tmp_path / "55061194"
    case_path.mkdir()
    (case_path / "RP.test.dcm").write_text("", encoding="ascii")
    beam_dir = case_path / "2025042401440800"
    beam_dir.mkdir()
    (beam_dir / "sample.ptn").write_text("", encoding="ascii")
    _write_planinfo(beam_dir, "DIFFERENT_PATIENT", 2)

    validator = MagicMock()
    validator.find_rtplan_file.return_value = case_path / "RP.test.dcm"
    validator.parse_rtplan_beam_count.return_value = 1
    validator.get_beam_information.return_value = {
        "patient_id": "55061194",
        "beams": [{"beam_name": "Beam_2", "beam_number": 2}],
    }
    mock_validator_cls.return_value = validator

    result = prepare_beam_jobs("55061194", case_path, settings=MagicMock())

    if result != []:
        raise AssertionError(f"Expected hard failure on patient mismatch, got {result!r}")


@patch("src.core.case_aggregator.LoggerFactory.get_logger")
@patch("src.core.case_aggregator.DataIntegrityValidator")
def test_prepare_beam_jobs_fails_when_planinfo_beam_numbers_are_duplicated(
    mock_validator_cls,
    mock_get_logger,
    tmp_path,
):
    logger = MagicMock()
    mock_get_logger.return_value = logger

    case_path = tmp_path / "55061194"
    case_path.mkdir()
    (case_path / "RP.test.dcm").write_text("", encoding="ascii")

    for folder_name in ("2025042401440800", "2025042401501400"):
        beam_dir = case_path / folder_name
        beam_dir.mkdir()
        (beam_dir / "sample.ptn").write_text("", encoding="ascii")
        _write_planinfo(beam_dir, "55061194", 2)

    validator = MagicMock()
    validator.find_rtplan_file.return_value = case_path / "RP.test.dcm"
    validator.parse_rtplan_beam_count.return_value = 2
    validator.get_beam_information.return_value = {
        "patient_id": "55061194",
        "beams": [
            {"beam_name": "Beam_2", "beam_number": 2},
            {"beam_name": "Beam_3", "beam_number": 3},
        ],
    }
    mock_validator_cls.return_value = validator

    validator.parse_rtplan_beam_count.return_value = 1
    validator.get_beam_information.return_value = {
        "patient_id": "55061194",
        "beams": [{"beam_name": "Beam_2", "beam_number": 2}],
    }

    result = prepare_case_delivery_data("55061194", case_path, settings=MagicMock())

    if result.status != "ready":
        raise AssertionError(f"Expected ready result, got {result!r}")
    if len(result.beam_jobs) != 1:
        raise AssertionError(f"Expected one reference beam job, got {result.beam_jobs!r}")
    if len(result.delivery_records) != 2:
        raise AssertionError(f"Expected both duplicate daily deliveries to be retained, got {result.delivery_records!r}")
    if result.beam_jobs[0]["beam_path"].name != "2025042401440800":
        raise AssertionError(f"Expected earliest delivery to be selected as reference, got {result.beam_jobs[0]!r}")
    if any(record["fraction_index"] != 1 for record in result.delivery_records):
        raise AssertionError(f"Expected duplicate deliveries to stay in fraction 1, got {result.delivery_records!r}")
