"""Shared TPS generation helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Sequence

from src.domain.errors import ProcessingError


@dataclass
class TpsGenerationResult:
    success: bool
    dispatched_count: int = 0
    failed_beam_id: Optional[str] = None
    error_message: Optional[str] = None


def resolve_tps_output_dir(settings: Any, case_id: str, room: Optional[str] = None) -> Path:
    """Resolve the directory where per-beam TPS files are written."""
    csv_output_base = settings.get_path("csv_output_dir", handler_name="CsvInterpreter")
    if room:
        return Path(csv_output_base) / room / case_id / "Log_csv"
    return Path(csv_output_base) / case_id / "Log_csv"


def generate_tps_files_for_gpu_assignments(
    *,
    tps_generator: Any,
    gpu_repo: Any,
    case_path: Path,
    case_id: str,
    gpu_assignments: list[dict[str, Any]],
    beams: Sequence[Any],
    execution_mode: str,
    output_dir: Path,
    multigpu_enabled: bool,
    beam_uses_all_available_gpus: bool,
    stop_on_failure: bool = False,
    raise_on_missing_beam_number: bool = False,
) -> TpsGenerationResult:
    """Assign GPUs and generate TPS files for one or more beams."""
    if multigpu_enabled and beam_uses_all_available_gpus:
        if not beams:
            return TpsGenerationResult(success=True)
        return _generate_all_gpus_for_single_beam(
            tps_generator=tps_generator,
            gpu_repo=gpu_repo,
            case_path=case_path,
            case_id=case_id,
            gpu_assignments=gpu_assignments,
            beam=beams[0],
            execution_mode=execution_mode,
            output_dir=output_dir,
            raise_on_missing_beam_number=raise_on_missing_beam_number,
        )

    first_failure: Optional[TpsGenerationResult] = None
    dispatched_count = 0
    for gpu_assignment, beam in zip(gpu_assignments, beams):
        result = _generate_single_gpu_for_beam(
            tps_generator=tps_generator,
            gpu_repo=gpu_repo,
            case_path=case_path,
            case_id=case_id,
            gpu_assignment=gpu_assignment,
            beam=beam,
            execution_mode=execution_mode,
            output_dir=output_dir,
            raise_on_missing_beam_number=raise_on_missing_beam_number,
        )
        if result.success:
            dispatched_count += result.dispatched_count
            continue
        if first_failure is None:
            first_failure = result
        if stop_on_failure:
            return result

    if first_failure is not None:
        first_failure.dispatched_count = dispatched_count
        return first_failure
    return TpsGenerationResult(success=True, dispatched_count=dispatched_count)


def _generate_all_gpus_for_single_beam(
    *,
    tps_generator: Any,
    gpu_repo: Any,
    case_path: Path,
    case_id: str,
    gpu_assignments: list[dict[str, Any]],
    beam: Any,
    execution_mode: str,
    output_dir: Path,
    raise_on_missing_beam_number: bool,
) -> TpsGenerationResult:
    beam_id = getattr(beam, "beam_id", None)
    if not beam_id:
        return TpsGenerationResult(success=False, failed_beam_id=None, error_message="Could not find beam data")

    beam_number = getattr(beam, "beam_number", None)
    if beam_number is None:
        message = f"Beam number missing for {beam_id}"
        if raise_on_missing_beam_number:
            raise ProcessingError(message)
        return TpsGenerationResult(success=False, failed_beam_id=beam_id, error_message=message)

    for gpu_assignment in gpu_assignments:
        gpu_repo.assign_gpu_to_case(gpu_assignment["gpu_uuid"], beam_id)
        gpu_assignment["beam_id"] = beam_id

    success = tps_generator.generate_tps_file_with_gpu_assignments(
        case_path=case_path,
        case_id=case_id,
        gpu_assignments=gpu_assignments,
        execution_mode=execution_mode,
        output_dir=output_dir,
        beam_name=beam_id,
        beam_number=beam_number,
    )
    if success:
        return TpsGenerationResult(success=True, dispatched_count=1)
    return TpsGenerationResult(
        success=False,
        failed_beam_id=beam_id,
        error_message=f"TPS file generation failed for beam {beam_id} in case {case_id}",
    )


def _generate_single_gpu_for_beam(
    *,
    tps_generator: Any,
    gpu_repo: Any,
    case_path: Path,
    case_id: str,
    gpu_assignment: dict[str, Any],
    beam: Any,
    execution_mode: str,
    output_dir: Path,
    raise_on_missing_beam_number: bool,
) -> TpsGenerationResult:
    beam_id = getattr(beam, "beam_id", None)
    if not beam_id:
        return TpsGenerationResult(success=False, failed_beam_id=None, error_message="Could not find beam data")

    beam_number = getattr(beam, "beam_number", None)
    if beam_number is None:
        message = f"Beam number missing for {beam_id}"
        if raise_on_missing_beam_number:
            raise ProcessingError(message)
        return TpsGenerationResult(success=False, failed_beam_id=beam_id, error_message=message)

    gpu_repo.assign_gpu_to_case(gpu_assignment["gpu_uuid"], beam_id)
    gpu_assignment["beam_id"] = beam_id
    success = tps_generator.generate_tps_file_with_gpu_assignments(
        case_path=case_path,
        case_id=case_id,
        gpu_assignments=[gpu_assignment],
        execution_mode=execution_mode,
        output_dir=output_dir,
        beam_name=beam_id,
        beam_number=beam_number,
    )
    if success:
        return TpsGenerationResult(success=True, dispatched_count=1)
    return TpsGenerationResult(
        success=False,
        failed_beam_id=beam_id,
        error_message=f"TPS file generation failed for beam {beam_id} in case {case_id}",
    )
