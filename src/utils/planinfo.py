"""PlanInfo.txt parsing helpers."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional


def parse_delivery_timestamp(delivery_path: Path) -> Optional[datetime]:
    """Parse delivery timestamp from PlanInfo metadata or folder name."""
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


def parse_planinfo_beam_number(delivery_path: Path, default: Optional[int] = None) -> Optional[int]:
    """Parse DICOM_BEAM_NUMBER from PlanInfo metadata."""
    planinfo_path = delivery_path / "PlanInfo.txt"
    if not planinfo_path.exists():
        return default

    try:
        for raw_line in planinfo_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            key, separator, value = line.partition(",")
            if separator and key.strip() == "DICOM_BEAM_NUMBER":
                return int(value.strip())
    except (OSError, ValueError):
        return default

    return default
