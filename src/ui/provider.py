# =====================================================================================
# Target File: src/ui/provider.py
# Source Reference: src/display_handler.py
# =====================================================================================
"""Fetches and processes data required for the UI dashboard."""

from typing import Dict, List, Any, Optional
from datetime import datetime

from src.repositories.case_repo import CaseRepository
from src.repositories.gpu_repo import GpuRepository
from src.infrastructure.logging_handler import StructuredLogger
from src.domain.models import CaseData, GpuResource
from src.domain.enums import CaseStatus, GpuStatus


class DashboardDataProvider:
    """Fetches and processes data required for the UI dashboard from the repositories.

    This class is responsible for pure data fetching and processing without any UI rendering logic.
    """

    def __init__(self, case_repo: CaseRepository, gpu_repo: GpuRepository, logger: StructuredLogger):
        """Initializes the provider with injected repositories.

        Args:
            case_repo (CaseRepository): The case repository.
            gpu_repo (GpuRepository): The GPU repository.
            logger (StructuredLogger): The logger for recording operations.
        """
        self.case_repo = case_repo
        self.gpu_repo = gpu_repo
        self.logger = logger
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

            # Set update time first
            self._last_update = datetime.now()

            # Process data
            self._gpu_data = self._process_gpu_data(raw_gpus)
            self._cases_with_beams = self._process_cases_with_beams_data(raw_cases_with_beams)

            # Extract just case data for backward compatibility
            self._active_cases = [item["case_display"] for item in self._cases_with_beams]

            # Calculate system stats from case data
            raw_cases = [item["case_data"] for item in raw_cases_with_beams]
            self._system_stats = self._calculate_system_metrics(raw_cases, raw_gpus)

        except Exception as e:
            self.logger.error("Failed to refresh dashboard data", {"error": str(e)})
            # In case of error, clear data to avoid displaying stale info
            self._system_stats = {}
            self._gpu_data = []
            self._active_cases = []
            self._cases_with_beams = []


    def _calculate_system_metrics(self, cases: List[CaseData], gpus: List[GpuResource]) -> Dict[str, Any]:
        """Calculates derived system metrics from raw repository data.

        Args:
            cases (List[CaseData]): A list of raw case data.
            gpus (List[GpuResource]): A list of raw GPU data.

        Returns:
            Dict[str, Any]: A dictionary of system metrics.
        """
        total_gpus = len(gpus)
        available_gpus = sum(1 for gpu in gpus if gpu.status == GpuStatus.IDLE)
        
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
                "elapsed_time": (datetime.now() - case.created_at).total_seconds() if case.created_at else 0
            })
        return processed_cases

    def _process_gpu_data(self, raw_gpu_data: List[GpuResource]) -> List[Dict[str, Any]]:
        """Processes raw GPU data into a format suitable for display.

        Args:
            raw_gpu_data (List[GpuResource]): A list of raw GPU data.

        Returns:
            List[Dict[str, Any]]: A list of processed GPU data.
        """
        processed_gpus = []
        for gpu in raw_gpu_data:
            processed_gpus.append({
                "uuid": gpu.uuid,
                "gpu_index": gpu.gpu_index,
                "name": gpu.name,
                "status": gpu.status,
                "assigned_case": gpu.assigned_case,
                "memory_used": gpu.memory_used,
                "memory_total": gpu.memory_total,
                "utilization": gpu.utilization,
                "temperature": gpu.temperature
            })
        return processed_gpus

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

            # Process beam data
            beam_display_data = []
            for beam in beams:
                beam_display_data.append({
                    "beam_id": beam["beam_id"],
                    "status": beam["status"],
                    "elapsed_time": (
                        (datetime.now() - beam["created_at"]).total_seconds()
                        if beam["created_at"] else 0
                    ),
                    "hpc_job_id": beam["hpc_job_id"]
                })

            # Process case data
            case_display = {
                "case_id": case.case_id,
                "status": case.status,
                "progress": case.progress,
                "assigned_gpu": case.assigned_gpu,
                "elapsed_time": (
                    (datetime.now() - case.created_at).total_seconds()
                    if case.created_at else 0
                ),
                "beam_count": len(beams)
            }

            processed.append({
                "case_data": case,
                "case_display": case_display,
                "beams": beam_display_data
            })

        return processed