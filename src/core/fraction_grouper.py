"""Fraction grouping utilities for case delivery folders."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src.infrastructure.logging_handler import LoggerFactory, StructuredLogger


@dataclass
class Fraction:
    """In-memory representation of a grouped treatment fraction."""

    index: int
    delivery_folders: List[Path]
    beam_numbers: List[int]
    start_time: datetime
    end_time: datetime
    status: str


@dataclass
class CaseDeliveryResult:
    """Prepared delivery information for simulation and PTN analysis."""

    beam_jobs: List[Dict[str, Any]]
    delivery_records: List[Dict[str, Any]]
    fractions: List[Fraction]
    status: str
    pending_reason: Optional[str] = None
    expected_beam_count: Optional[int] = None


@dataclass
class PendingCase:
    """Tracked case that still has at least one open fraction window."""

    case_id: str
    case_path: Path
    fractions: List[Fraction]
    expected_beam_count: int
    first_seen: datetime
    last_checked: datetime


@dataclass
class ReadyCase:
    """Resolved pending case ready for normal pipeline processing."""

    case_id: str
    case_path: Path
    result: CaseDeliveryResult


def _parse_delivery_timestamp(delivery_path: Path) -> Optional[datetime]:
    candidates = [delivery_path.name]
    planinfo_path = delivery_path / "PlanInfo.txt"
    if planinfo_path.exists():
        try:
            for raw_line in planinfo_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                key, separator, value = raw_line.strip().partition(",")
                if separator and key.strip() == "TCSC_IRRAD_DATETIME":
                    candidates.insert(0, value.strip())
                    break
        except OSError:
            pass

    for candidate in candidates:
        try:
            return datetime.strptime(candidate, "%Y%m%d%H%M%S%f")
        except ValueError:
            continue
    return None


def _parse_planinfo_beam_number(delivery_path: Path) -> int:
    planinfo_path = delivery_path / "PlanInfo.txt"
    if not planinfo_path.exists():
        return 0

    try:
        for raw_line in planinfo_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            key, separator, value = raw_line.strip().partition(",")
            if separator and key.strip() == "DICOM_BEAM_NUMBER":
                return int(value.strip())
    except (OSError, ValueError):
        return 0

    return 0


def group_deliveries_into_fractions(
    delivery_folders: List[Path],
    expected_beam_count: int,
    time_window: timedelta = timedelta(hours=1),
    now: Optional[datetime] = None,
) -> List[Fraction]:
    """Group delivery folders into fractions using a 1-hour start-based window."""

    current_time = now or datetime.now()
    timestamped: List[tuple[Path, datetime, int]] = []
    for delivery_path in delivery_folders:
        timestamp = _parse_delivery_timestamp(delivery_path)
        if timestamp is None:
            continue
        timestamped.append((delivery_path, timestamp, _parse_planinfo_beam_number(delivery_path)))

    timestamped.sort(key=lambda item: (item[1], str(item[0])))
    if not timestamped:
        return []

    fractions: List[Fraction] = []
    current_paths: List[Path] = []
    current_beam_numbers: List[int] = []
    current_start: Optional[datetime] = None
    current_end: Optional[datetime] = None

    def close_fraction() -> None:
        nonlocal current_paths, current_beam_numbers, current_start, current_end
        if not current_paths or current_start is None or current_end is None:
            return

        delivered_count = len(current_paths)
        if delivered_count > expected_beam_count:
            status = "anomaly"
        elif delivered_count == expected_beam_count:
            status = "complete"
        else:
            status = "pending" if current_time - current_start <= time_window else "partial"

        fractions.append(
            Fraction(
                index=len(fractions) + 1,
                delivery_folders=list(current_paths),
                beam_numbers=list(current_beam_numbers),
                start_time=current_start,
                end_time=current_end,
                status=status,
            )
        )
        current_paths = []
        current_beam_numbers = []
        current_start = None
        current_end = None

    for delivery_path, timestamp, beam_number in timestamped:
        if current_start is None:
            current_paths = [delivery_path]
            current_beam_numbers = [beam_number]
            current_start = timestamp
            current_end = timestamp
            continue

        if timestamp - current_start > time_window:
            close_fraction()
            current_paths = [delivery_path]
            current_beam_numbers = [beam_number]
            current_start = timestamp
            current_end = timestamp
            continue

        current_paths.append(delivery_path)
        current_beam_numbers.append(beam_number)
        current_end = timestamp

    close_fraction()
    return fractions


def select_reference_fraction(fractions: List[Fraction]) -> Optional[Fraction]:
    """Return the earliest complete fraction, if any."""

    for fraction in fractions:
        if fraction.status == "complete":
            return fraction
    return None


def get_fraction_event_logger() -> StructuredLogger:
    """Return the dedicated fraction event logger."""

    return LoggerFactory.get_logger("fraction_events")


def log_fraction_event(
    app_logger: Optional[StructuredLogger],
    fraction_logger: Optional[StructuredLogger],
    event: str,
    message: str,
    context: Optional[Dict[str, Any]] = None,
) -> None:
    """Log a fraction event to both caller and dedicated log streams."""

    payload = {"event": event}
    if context:
        payload.update(context)

    if app_logger:
        app_logger.warning(message, payload)
    if fraction_logger:
        fraction_logger.warning(message, payload)


class FractionTracker:
    """Track pending fractions and periodically rescan them."""

    def __init__(self, poll_interval_seconds: int = 1800):
        self.poll_interval = timedelta(seconds=poll_interval_seconds)
        self._pending: Dict[str, PendingCase] = {}

    def register(
        self,
        case_id: str,
        case_path: Path,
        fractions: List[Fraction],
        expected_beam_count: int,
        now: Optional[datetime] = None,
    ) -> None:
        current_time = now or datetime.now()
        existing = self._pending.get(case_id)
        first_seen = existing.first_seen if existing else current_time
        self._pending[case_id] = PendingCase(
            case_id=case_id,
            case_path=case_path,
            fractions=fractions,
            expected_beam_count=expected_beam_count,
            first_seen=first_seen,
            last_checked=current_time,
        )

    def check_pending(
        self,
        rescan: Callable[[str, Path], CaseDeliveryResult],
        now: Optional[datetime] = None,
    ) -> List[ReadyCase]:
        current_time = now or datetime.now()
        ready_cases: List[ReadyCase] = []

        for case_id, pending_case in list(self._pending.items()):
            if current_time - pending_case.last_checked < self.poll_interval:
                continue

            result = rescan(pending_case.case_id, pending_case.case_path)
            pending_case.last_checked = current_time

            if result.status != "pending":
                ready_cases.append(
                    ReadyCase(
                        case_id=pending_case.case_id,
                        case_path=pending_case.case_path,
                        result=result,
                    )
                )
                self._pending.pop(case_id, None)
            else:
                pending_case.fractions = result.fractions
                if result.expected_beam_count is not None:
                    pending_case.expected_beam_count = result.expected_beam_count

        return ready_cases

    def remove(self, case_id: str) -> None:
        self._pending.pop(case_id, None)

    @property
    def pending_count(self) -> int:
        return len(self._pending)
