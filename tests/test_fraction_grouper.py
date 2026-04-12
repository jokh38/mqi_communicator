from datetime import datetime, timedelta
from pathlib import Path

from src.core.fraction_grouper import (
    CaseDeliveryResult,
    Fraction,
    FractionTracker,
    group_deliveries_into_fractions,
    select_reference_fraction,
)


def _write_planinfo(beam_dir: Path, patient_id: str, beam_number: int, timestamp: str) -> None:
    (beam_dir / "PlanInfo.txt").write_text(
        "\n".join(
            [
                f"DICOM_PATIENT_ID,{patient_id}",
                f"DICOM_BEAM_NUMBER,{beam_number}",
                f"TCSC_IRRAD_DATETIME,{timestamp}",
            ]
        ),
        encoding="ascii",
    )


def test_group_deliveries_into_fractions_marks_complete_partial_and_pending(tmp_path):
    case_dir = tmp_path / "case-1"
    case_dir.mkdir()

    for folder_name, beam_number in (
        ("2026032010000000", 1),
        ("2026032010050000", 2),
        ("2026032012000000", 1),
    ):
        delivery_dir = case_dir / folder_name
        delivery_dir.mkdir()
        _write_planinfo(delivery_dir, "P1", beam_number, folder_name)

    fractions = group_deliveries_into_fractions(
        sorted(case_dir.iterdir()),
        expected_beam_count=2,
        now=datetime(2026, 3, 20, 12, 30, 0),
    )

    assert [fraction.status for fraction in fractions] == ["complete", "pending"]

    later = group_deliveries_into_fractions(
        sorted(case_dir.iterdir()),
        expected_beam_count=2,
        now=datetime(2026, 3, 20, 13, 30, 1),
    )
    assert [fraction.status for fraction in later] == ["complete", "partial"]


def test_select_reference_fraction_returns_earliest_complete():
    fractions = [
        Fraction(1, [], [], datetime(2026, 3, 20, 10), datetime(2026, 3, 20, 10), "partial"),
        Fraction(2, [], [], datetime(2026, 3, 20, 12), datetime(2026, 3, 20, 12), "complete"),
        Fraction(3, [], [], datetime(2026, 3, 20, 14), datetime(2026, 3, 20, 14), "complete"),
    ]
    assert select_reference_fraction(fractions).index == 2


def test_fraction_tracker_registers_and_resolves_ready_case(tmp_path):
    tracker = FractionTracker(poll_interval_seconds=1)
    pending_fraction = Fraction(
        index=1,
        delivery_folders=[],
        beam_numbers=[],
        start_time=datetime(2026, 3, 20, 10, 0, 0),
        end_time=datetime(2026, 3, 20, 10, 0, 0),
        status="pending",
    )
    tracker.register(
        "caseX",
        tmp_path,
        [pending_fraction],
        expected_beam_count=4,
        now=datetime(2026, 3, 20, 10, 0, 0),
    )

    assert tracker.check_pending(
        rescan=lambda *_: CaseDeliveryResult([], [], [pending_fraction], "pending", expected_beam_count=4),
        now=datetime(2026, 3, 20, 10, 0, 0),
    ) == []

    ready_result = CaseDeliveryResult(
        beam_jobs=[{"beam_id": "caseX_beam_1"}],
        delivery_records=[{"delivery_id": "d1"}],
        fractions=[
            Fraction(
                index=1,
                delivery_folders=[tmp_path / "f1"],
                beam_numbers=[1, 2, 3, 4],
                start_time=datetime(2026, 3, 20, 10, 0, 0),
                end_time=datetime(2026, 3, 20, 10, 10, 0),
                status="complete",
            )
        ],
        status="ready",
        expected_beam_count=4,
    )
    ready = tracker.check_pending(
        rescan=lambda *_: ready_result,
        now=datetime(2026, 3, 20, 10, 0, 2),
    )

    assert len(ready) == 1
    assert ready[0].case_id == "caseX"
    assert tracker.pending_count == 0
