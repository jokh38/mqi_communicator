from datetime import datetime
from pathlib import Path

from src.core.fraction_grouper import CaseDeliveryResult, Fraction, FractionTracker


def test_fraction_tracker_registers_pending_and_yields_ready_on_rescan(tmp_path):
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
    assert tracker.pending_count == 1

    ready = tracker.check_pending(
        rescan=lambda *_: CaseDeliveryResult(
            beam_jobs=[],
            delivery_records=[],
            fractions=[pending_fraction],
            status="pending",
            expected_beam_count=4,
        ),
        now=datetime(2026, 3, 20, 10, 0, 0),
    )
    assert ready == []
    assert tracker.pending_count == 1

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
