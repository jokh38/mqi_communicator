# =====================================================================================
# Target File: src/ui/provider.py
# Source Reference: src/display_handler.py
# =====================================================================================
"""Fetches and processes data required for the UI dashboard."""

import csv
from pathlib import Path
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone
from io import StringIO

from src.handlers.execution_handler import ExecutionHandler
from src.repositories.case_repo import CaseRepository
from src.repositories.gpu_repo import GpuRepository
from src.infrastructure.logging_handler import StructuredLogger
from src.domain.models import CaseData, GpuResource
from src.domain.enums import CaseStatus, GpuStatus
from src.core.retry_policy import is_retryable_failed_case
from src.core.workflow_manager import derive_room_from_path


class DashboardDataProvider:
    """Fetches and processes data required for the UI dashboard from the repositories.

    This class is responsible for pure data fetching and processing without any UI rendering logic.
    """

    def __init__(
        self,
        case_repo: CaseRepository,
        gpu_repo: GpuRepository,
        logger: StructuredLogger,
        execution_handler: Optional[ExecutionHandler] = None,
        settings: Optional[Any] = None,
    ):
        """Initializes the provider with injected repositories.

        Args:
            case_repo (CaseRepository): The case repository.
            gpu_repo (GpuRepository): The GPU repository.
            logger (StructuredLogger): The logger for recording operations.
            execution_handler (Optional[ExecutionHandler]): Optional command runner used
                to resolve live GPU compute activity from nvidia-smi.
        """
        self.case_repo = case_repo
        self.gpu_repo = gpu_repo
        self.logger = logger
        self.execution_handler = execution_handler
        self.settings = settings
        self._last_update: Optional[datetime] = None
        self._system_stats: Dict[str, Any] = {}
        self._gpu_data: List[Dict[str, Any]] = []
        self._active_cases: List[Dict[str, Any]] = []
        self._cases_with_beams: List[Dict[str, Any]] = []

    def get_system_stats(self) -> Dict[str, Any]:
        """Returns the latest system-level statistics.

        Returns:
            Dict[str, Any]: A dictionary of system statistics.
        """
        return self._system_stats

    def get_gpu_data(self) -> List[Dict[str, Any]]:
        """Returns the latest GPU resource data.

        Returns:
            List[Dict[str, Any]]: A list of dictionaries, where each dictionary represents a GPU.
        """
        return self._gpu_data

    def get_active_cases_data(self) -> List[Dict[str, Any]]:
        """Returns the latest data for active cases.

        Returns:
            List[Dict[str, Any]]: A list of dictionaries, where each dictionary represents an active case.
        """
        return self._active_cases

    def get_cases_with_beams_data(self) -> List[Dict[str, Any]]:
        """Returns the latest data for active cases with their beams.

        Returns:
            List[Dict[str, Any]]: List with case and beam information.
        """
        return self._cases_with_beams

    def refresh_all_data(self) -> None:
        """Triggers a refresh of all data by fetching from repositories and processing it."""
        try:
            # Fetch raw data
            raw_gpus = self.gpu_repo.get_all_gpu_resources()
            raw_cases_with_beams = self.case_repo.get_all_active_cases_with_beams()
            active_compute_gpu_uuids = self._get_active_compute_gpu_uuids()

            # Set update time first
            self._last_update = datetime.now()

            # Process data
            self._gpu_data = self._process_gpu_data(raw_gpus, active_compute_gpu_uuids)
            self._cases_with_beams = self._process_cases_with_beams_data(raw_cases_with_beams)

            # Extract just case data for backward compatibility
            self._active_cases = [item["case_display"] for item in self._cases_with_beams]

            # Calculate system stats from case data
            raw_cases = [item["case_data"] for item in raw_cases_with_beams]
            self._system_stats = self._calculate_system_metrics(raw_cases, self._gpu_data)

        except Exception as e:
            self.logger.error("Failed to refresh dashboard data", {"error": str(e)})
            # In case of error, clear data to avoid displaying stale info
            self._system_stats = {}
            self._gpu_data = []
            self._active_cases = []
            self._cases_with_beams = []


    def _calculate_system_metrics(self, cases: List[CaseData], gpus: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Calculates derived system metrics from raw repository data.

        Args:
            cases (List[CaseData]): A list of raw case data.
            gpus (List[GpuResource]): A list of raw GPU data.

        Returns:
            Dict[str, Any]: A dictionary of system metrics.
        """
        total_gpus = len(gpus)
        available_gpus = sum(1 for gpu in gpus if gpu["status"] == GpuStatus.IDLE)
        
        # Initialize all possible statuses to ensure they exist in the dictionary
        status_counts = {status: 0 for status in CaseStatus}

        for case in cases:
            if case.status in status_counts:
                status_counts[case.status] += 1
            
        # Dynamically build the dictionary from the status_counts
        metrics = {status.value: count for status, count in status_counts.items()}

        # Add other system-wide metrics
        metrics.update({
            "total_cases": len(cases),
            "total_gpus": total_gpus,
            "available_gpus": available_gpus,
            "last_update": self._last_update
        })

        metrics["retryable_failed"] = sum(1 for case in cases if is_retryable_failed_case(case))
        metrics["permanent_failed"] = sum(
            1
            for case in cases
            if case.status == CaseStatus.FAILED and not is_retryable_failed_case(case)
        )
        failure_phases: Dict[str, int] = {}
        for case in cases:
            phase = getattr(case, "failure_phase", None)
            if case.status != CaseStatus.FAILED or not phase:
                continue
            failure_phases[phase] = failure_phases.get(phase, 0) + 1
        metrics["failure_phases"] = failure_phases

        return metrics

    def _process_case_data(self, raw_cases: List[CaseData]) -> List[Dict[str, Any]]:
        """Processes raw case data into a format suitable for display.

        Args:
            raw_cases (List[CaseData]): A list of raw case data.

        Returns:
            List[Dict[str, Any]]: A list of processed case data.
        """
        processed_cases = []
        for case in raw_cases:
            processed_cases.append({
                "case_id": case.case_id,
                "status": case.status,
                "progress": case.progress,
                "assigned_gpu": case.assigned_gpu,
                "elapsed_time": (datetime.now(timezone.utc) - case.created_at.replace(tzinfo=timezone.utc)).total_seconds() if case.created_at else 0
            })
        return processed_cases

    def _process_gpu_data(
        self,
        raw_gpu_data: List[GpuResource],
        active_compute_gpu_uuids: Optional[set[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Processes raw GPU data into a format suitable for display.

        Args:
            raw_gpu_data (List[GpuResource]): A list of raw GPU data.

        Returns:
            List[Dict[str, Any]]: A list of processed GPU data.
        """
        active_compute_gpu_uuids = active_compute_gpu_uuids or set()
        processed_gpus = []
        for gpu in raw_gpu_data:
            has_live_compute = gpu.uuid in active_compute_gpu_uuids
            effective_status = (
                GpuStatus.ASSIGNED
                if has_live_compute or gpu.status == GpuStatus.ASSIGNED or gpu.assigned_case
                else GpuStatus.IDLE
            )
            if has_live_compute and gpu.assigned_case:
                status_detail = "assigned + live compute"
            elif has_live_compute:
                status_detail = "live compute"
            elif gpu.assigned_case:
                status_detail = "reserved"
            else:
                status_detail = "idle"

            processed_gpus.append({
                "uuid": gpu.uuid,
                "gpu_index": gpu.gpu_index,
                "name": gpu.name,
                "status": effective_status,
                "db_status": gpu.status,
                "assigned_case": gpu.assigned_case,
                "memory_used": gpu.memory_used,
                "memory_total": gpu.memory_total,
                "utilization": gpu.utilization,
                "core_clock": getattr(gpu, "core_clock", 0),
                "temperature": gpu.temperature,
                "has_live_compute": has_live_compute,
                "status_detail": status_detail,
            })
        return processed_gpus

    def _get_active_compute_gpu_uuids(self) -> set[str]:
        """Resolve GPU UUIDs that currently host a live compute process."""
        if self.execution_handler is None:
            return set()

        result = self.execution_handler.execute_command(
            command=(
                "nvidia-smi --query-compute-apps=gpu_uuid,pid,process_name,used_memory "
                "--format=csv,noheader,nounits"
            )
        )
        if not result.success:
            self.logger.warning(
                "Failed to query live GPU compute activity for dashboard status",
                {"return_code": result.return_code, "error": result.error},
            )
            return set()

        active_gpu_uuids: set[str] = set()
        for row in csv.reader(StringIO(result.output or "")):
            if not row:
                continue
            gpu_uuid = row[0].strip()
            if gpu_uuid:
                active_gpu_uuids.add(gpu_uuid)
        return active_gpu_uuids

    def _process_cases_with_beams_data(self, raw_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Processes raw case+beam data into display format.

        Args:
            raw_data: List of dicts with 'case_data' and 'beams'

        Returns:
            List[Dict[str, Any]]: Processed data ready for display
        """
        processed = []
        for item in raw_data:
            case = item["case_data"]
            beams = item["beams"]
            result_summary = self._build_result_summary(case)

            # Process beam data
            beam_display_data = []
            for beam in beams:
                beam_display_data.append({
                    "beam_id": beam["beam_id"],
                    "status": beam["status"],
                    "progress": beam.get("progress", 0.0),
                    "elapsed_time": (
                        (datetime.now(timezone.utc) - beam["created_at"].replace(tzinfo=timezone.utc)).total_seconds()
                        if beam["created_at"] else 0
                    ),
                    "hpc_job_id": beam["hpc_job_id"],
                    "error_message": beam.get("error_message")
                })


            # Process case data
            case_display = {
                "case_id": case.case_id,
                "status": case.status,
                "status_label": self._format_case_status_label(case.status),
                "progress": case.progress,
                "assigned_gpu": case.assigned_gpu,
                "elapsed_time": (
                    (datetime.now(timezone.utc) - case.created_at.replace(tzinfo=timezone.utc)).total_seconds()
                    if case.created_at else 0
                ),
                "beam_count": len(beams),
                "interpreter_done": case.interpreter_completed,
                "error_message": case.error_message,
                "failure_category": getattr(case, "failure_category", None),
                "failure_phase": getattr(case, "failure_phase", None),
                "failure_details": getattr(case, "failure_details", None),
                "retry_count": getattr(case, "retry_count", 0),
                "retry_eligible": is_retryable_failed_case(case),
                "result_summary": result_summary,
            }

            processed.append({
                "case_data": case,
                "case_display": case_display,
                "beams": beam_display_data
            })

        return processed

    def _format_case_status_label(self, status: CaseStatus) -> str:
        """Return a user-facing case status label."""
        if status == CaseStatus.COMPLETED:
            return "Finished"
        if status == CaseStatus.PROCESSING:
            return "Processing"
        if status == CaseStatus.CSV_INTERPRETING:
            return "CSV Interpreting"
        if status == CaseStatus.POSTPROCESSING:
            return "Postprocessing"
        if status == CaseStatus.FAILED:
            return "Failed"
        if status == CaseStatus.PENDING:
            return "Pending"
        if status == CaseStatus.CANCELLED:
            return "Cancelled"
        return status.value.replace("_", " ").title()

    def _build_result_summary(self, case: CaseData) -> Dict[str, Any]:
        """Summarize saved result locations for a case.

        The browser uses this to show where output was written even when the
        terminal workflow state is not obvious from the case list.
        """
        output_locations: List[Dict[str, Any]] = []
        output_candidates = self._resolve_output_candidates(case.case_id, case.case_path)

        for label, path in output_candidates:
            files = self._collect_files(path, self._patterns_for_output_label(label))
            if not files:
                continue
            output_locations.append({
                "label": label,
                "path": str(path),
                "file_count": len(files),
                "sample_files": [str(file_path) for file_path in files[:3]],
            })

        terminal_status = self._format_case_status_label(case.status)
        if case.status == CaseStatus.COMPLETED:
            terminal_status = "Finished"

        return {
            "terminal_status": terminal_status,
            "has_saved_output": bool(output_locations),
            "output_locations": output_locations,
        }

    def _resolve_output_candidates(self, case_id: str, case_path: Path) -> List[tuple[str, Path]]:
        """Return likely filesystem locations that may contain saved outputs."""
        candidates: List[tuple[str, Path]] = []
        if self.settings is None:
            pass
        else:
            try:
                room = derive_room_from_path(case_path, self.settings)
                csv_output_dir = self._configured_case_output_subdir(
                    case_id,
                    case_path,
                    "Log_csv",
                )
                candidates.append(("CSV / TPS output", csv_output_dir))
            except Exception:
                pass

            for handler_name, path_name, label in (
                ("PostProcessor", "simulation_output_dir", "Final DICOM output"),
                ("PostProcessor", "final_dicom_dir", "Final DICOM output"),
            ):
                try:
                    room = derive_room_from_path(case_path, self.settings)
                    result_dir = Path(
                        self.settings.get_path(
                            path_name,
                            handler_name=handler_name,
                            case_id=case_id,
                            room=room,
                            room_path=f"{room}/" if room else "",
                        )
                    )
                    candidates.append((label, result_dir))
                except Exception:
                    continue

        candidates.append(("CSV / TPS output", self._guess_case_output_subdir(case_path, case_id, "Log_csv")))
        candidates.append(("Final DICOM output", self._guess_case_output_subdir(case_path, case_id, "Dose")))
        candidates.append(("Local upload copy", self._guess_sibling_output_dir(case_path, case_id, "localdata_uploads")))

        deduped: List[tuple[str, Path]] = []
        seen_paths: set[str] = set()
        for label, path in candidates:
            normalized = str(path.resolve()) if path.exists() else str(path)
            if normalized in seen_paths:
                continue
            seen_paths.add(normalized)
            deduped.append((label, path))
        return deduped

    def _guess_sibling_output_dir(self, case_path: Path, case_id: str, folder_name: str) -> Path:
        """Guess an output directory by walking parent directories of the case path."""
        room = derive_room_from_path(case_path, self.settings)
        for parent in [case_path, *case_path.parents]:
            if room:
                candidate = parent / folder_name / room / case_id
                if candidate.exists():
                    return candidate
            candidate = parent / folder_name / case_id
            if candidate.exists():
                return candidate
        if room:
            return case_path.parent / folder_name / room / case_id
        return case_path.parent / folder_name / case_id

    def _configured_case_output_subdir(self, case_id: str, case_path: Path, subdir_name: str) -> Path:
        """Resolve a configured case output subdirectory under the Output root."""
        room = derive_room_from_path(case_path, self.settings)
        output_root = Path(
            self.settings.get_path("csv_output_dir", handler_name="CsvInterpreter")
        )
        if room:
            return output_root / room / case_id / subdir_name
        return output_root / case_id / subdir_name

    def _guess_case_output_subdir(self, case_path: Path, case_id: str, subdir_name: str) -> Path:
        """Guess an output directory under Output/{room}/{case_id}/{subdir_name}."""
        room = derive_room_from_path(case_path, self.settings)
        for parent in [case_path, *case_path.parents]:
            if room:
                candidate = parent / "Output" / room / case_id / subdir_name
                if candidate.exists():
                    return candidate
            candidate = parent / "Output" / case_id / subdir_name
            if candidate.exists():
                return candidate
        if room:
            return case_path.parent / "Output" / room / case_id / subdir_name
        return case_path.parent / "Output" / case_id / subdir_name

    def _patterns_for_output_label(self, label: str) -> List[str]:
        """Return file patterns that count as saved output for a label."""
        lowered = label.lower()
        if "dicom" in lowered:
            return ["*.dcm"]
        if "csv" in lowered or "tps" in lowered:
            return ["*.csv", "*.in"]
        if "upload" in lowered:
            return ["*.dcm", "*.csv", "*.raw", "*.dat", "*.bin", "*.txt", "*.in"]
        return ["*.dcm", "*.csv", "*.raw", "*.dat", "*.bin", "*.txt", "*.in"]

    def _collect_files(self, directory: Path, patterns: Optional[List[str]] = None) -> List[Path]:
        """Collect representative files from a directory tree."""
        if not directory.exists():
            return []

        if directory.is_file():
            return [directory]

        patterns = patterns or ["*"]
        interesting: List[Path] = []
        for pattern in patterns:
            interesting.extend([path for path in directory.rglob(pattern) if path.is_file()])

        # Fall back to any files if the output tree uses an unexpected extension.
        if not interesting:
            interesting = [path for path in directory.rglob("*") if path.is_file()]

        interesting.sort(key=lambda path: str(path))
        return interesting
