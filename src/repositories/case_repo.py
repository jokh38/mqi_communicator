# =====================================================================================
# Target File: src/repositories/case_repo.py
# Source Reference: src/database_handler.py (cases table operations)
# =====================================================================================
"""Manages all CRUD operations for the 'cases' and related tables."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.database.connection import DatabaseConnection
from src.domain.enums import BeamStatus, CaseStatus, WorkflowStep
from src.domain.models import BeamData, CaseData, WorkflowStepRecord
from src.infrastructure.logging_handler import StructuredLogger
from src.repositories.base import BaseRepository


class CaseRepository(BaseRepository):
    """Manages all CRUD operations for the 'cases' table.

    This class implements the Repository Pattern for case data access.
    """

    def __init__(self, db_connection: DatabaseConnection, logger: StructuredLogger):
        """Initializes the case repository with an injected database connection.

        Args:
            db_connection (DatabaseConnection): The database connection manager.
            logger (StructuredLogger): The logger for recording operations.
        """
        super().__init__(db_connection, logger)

    def add_case(self, case_id: str, case_path: Path) -> None:
        """Adds a new case to the 'cases' table.

        Args:
            case_id (str): The unique identifier for the case.
            case_path (Path): The path to the case directory.
        """
        self._log_operation("add_case", case_id, case_path=str(case_path))

        query = """
            INSERT INTO cases (case_id, case_path, status, progress, created_at,
                               updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """

        self._execute_query(
            query, (case_id, str(case_path), CaseStatus.PENDING.value, 0.0)
        )

        self.logger.info(
            "Case added successfully",
            {
                "case_id": case_id,
                "case_path": str(case_path),
                "status": CaseStatus.PENDING.value,
            },
        )

    def update_case_status(
        self,
        case_id: str,
        status: CaseStatus,
        progress: float = None,
        error_message: str = None,
    ) -> None:
        """Updates the status and progress of a case.

        Args:
            case_id (str): The case identifier.
            status (CaseStatus): The new case status.
            progress (float, optional): An optional progress percentage (0-100). Defaults to None.
            error_message (str, optional): An optional error message for failed cases. Defaults to None.
        """
        self._log_operation(
            "update_case_status", case_id, status=status.value, progress=progress
        )

        set_clauses = ["status = ?", "updated_at = CURRENT_TIMESTAMP"]
        params = [status.value]

        if progress is not None:
            set_clauses.append("progress = ?")
            params.append(progress)

        if error_message is not None:
            set_clauses.append("error_message = ?")
            params.append(error_message)

        params.append(case_id)

        query = f"UPDATE cases SET {', '.join(set_clauses)} WHERE case_id = ?"

        self._execute_query(query, tuple(params))

        self.logger.info(
            "Case status updated",
            {"case_id": case_id, "status": status.value, "progress": progress},
        )

    def get_case(self, case_id: str) -> Optional[CaseData]:
        """Retrieves a single case by its ID.

        Args:
            case_id (str): The case identifier.

        Returns:
            Optional[CaseData]: A CaseData object if found, None otherwise.
        """
        self._log_operation("get_case", case_id)

        query = """
            SELECT case_id, case_path, status, progress, created_at,
                   updated_at, error_message, assigned_gpu
            FROM cases
            WHERE case_id = ?
        """

        row = self._execute_query(query, (case_id,), fetch_one=True)

        if row:
            return CaseData(
                case_id=row["case_id"],
                case_path=Path(row["case_path"]),
                status=CaseStatus(row["status"]),
                progress=row["progress"],
                created_at=datetime.fromisoformat(row["created_at"]),
                updated_at=(
                    datetime.fromisoformat(row["updated_at"])
                    if row["updated_at"]
                    else None
                ),
                error_message=row["error_message"],
                assigned_gpu=row["assigned_gpu"],
            )

        return None

    def get_cases_by_status(self, status: CaseStatus) -> List[CaseData]:
        """Retrieves all cases with a specific status.

        Args:
            status (CaseStatus): The case status to filter by.

        Returns:
            List[CaseData]: A list of CaseData objects matching the status.
        """
        self._log_operation("get_cases_by_status", status=status.value)

        query = """
            SELECT case_id, case_path, status, progress, created_at,
                   updated_at, error_message, assigned_gpu
            FROM cases
            WHERE status = ?
            ORDER BY created_at ASC
        """

        rows = self._execute_query(query, (status.value,), fetch_all=True)

        cases = []
        for row in rows:
            cases.append(
                CaseData(
                    case_id=row["case_id"],
                    case_path=Path(row["case_path"]),
                    status=CaseStatus(row["status"]),
                    progress=row["progress"],
                    created_at=datetime.fromisoformat(row["created_at"]),
                    updated_at=(
                        datetime.fromisoformat(row["updated_at"])
                        if row["updated_at"]
                        else None
                    ),
                    error_message=row["error_message"],
                    assigned_gpu=row["assigned_gpu"],
                )
            )

        return cases

    def get_all_case_ids(self) -> List[str]:
        """Retrieves all case IDs from the database.

        This is an optimized method for startup scanning that only fetches case IDs
        instead of full case data for better performance.

        Returns:
            List[str]: A list of case IDs currently in the database.
        """
        self._log_operation("get_all_case_ids")
        
        query = "SELECT case_id FROM cases ORDER BY case_id ASC"
        rows = self._execute_query(query, fetch_all=True)
        
        case_ids = [row["case_id"] for row in rows]
        
        self.logger.info(
            "Retrieved all case IDs from database",
            {"total_cases": len(case_ids)}
        )
        
        return case_ids

    def record_workflow_step(
        self,
        case_id: str,
        step: WorkflowStep,
        status: str,
        error_message: str = None,
        metadata: Dict[str, Any] = None,
        step_name: str = None,  # Added for backward compatibility
        details: str = None,    # Added for backward compatibility
    ) -> None:
        """Records the start or completion of a workflow step.

        Args:
            case_id (str): The case identifier.
            step (WorkflowStep): The workflow step being recorded.
            status (str): The step status ('started', 'completed', 'failed').
            error_message (str, optional): An optional error message for failed steps. Defaults to None.
            metadata (Dict[str, Any], optional): An optional metadata dictionary. Defaults to None.
            step_name (str, optional): (Backward compatibility) The name of the step. Defaults to None.
            details (str, optional): (Backward compatibility) The details of the step. Defaults to None.
        """
        # Handle backward compatibility
        if step_name is not None:
            # Try to convert step_name to WorkflowStep enum
            try:
                step = WorkflowStep(step_name)
            except ValueError:
                # If step_name doesn't match enum, use step parameter
                pass
        
        if details and not error_message:
            error_message = details

        self._log_operation(
            "record_workflow_step", case_id, step=step.value, status=status
        )

        query = """
            INSERT INTO workflow_steps
            (case_id, step, started_at, status, error_message, metadata)
            VALUES (?, ?, CURRENT_TIMESTAMP, ?, ?, ?)
        """

        metadata_json = json.dumps(metadata) if metadata else None

        self._execute_query(
            query, (case_id, step.value, status, error_message, metadata_json)
        )

    def get_workflow_steps(self, case_id: str) -> List[WorkflowStepRecord]:
        """Retrieves all workflow steps for a given case.

        Args:
            case_id (str): The case identifier.

        Returns:
            List[WorkflowStepRecord]: A list of WorkflowStepRecord objects.
        """
        self._log_operation("get_workflow_steps", case_id)

        query = """
            SELECT case_id, step, started_at, completed_at, status,
                   error_message, metadata
            FROM workflow_steps
            WHERE case_id = ?
            ORDER BY started_at ASC
        """

        rows = self._execute_query(query, (case_id,), fetch_all=True)

        steps = []
        for row in rows:
            metadata = json.loads(row["metadata"]) if row["metadata"] else None

            steps.append(
                WorkflowStepRecord(
                    case_id=row["case_id"],
                    step=WorkflowStep(row["step"]),
                    started_at=datetime.fromisoformat(row["started_at"]),
                    completed_at=(
                        datetime.fromisoformat(row["completed_at"])
                        if row["completed_at"]
                        else None
                    ),
                    status=row["status"],
                    error_message=row["error_message"],
                    metadata=metadata,
                )
            )

        return steps

    def assign_gpu_to_case(self, case_id: str, gpu_uuid: str) -> None:
        """Assigns a GPU to a specific case.

        Args:
            case_id (str): The case identifier.
            gpu_uuid (str): The UUID of the GPU to assign.
        """
        self._log_operation("assign_gpu_to_case", case_id, gpu_uuid=gpu_uuid)

        query = (
            "UPDATE cases SET assigned_gpu = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE case_id = ?"
        )

        self._execute_query(query, (gpu_uuid, case_id))

        self.logger.info(
            "GPU assigned to case", {"case_id": case_id, "gpu_uuid": gpu_uuid}
        )

    # =================================================================================
    # Beam-specific methods
    # =================================================================================

    def create_beam_record(
        self, beam_id: str, parent_case_id: str, beam_path: Path
    ) -> None:
        """Adds a new beam to the 'beams' table.

        Args:
            beam_id (str): The unique identifier for the beam.
            parent_case_id (str): The ID of the parent case.
            beam_path (Path): The path to the beam directory.
        """
        self._log_operation(
            "create_beam_record",
            beam_id=beam_id,
            parent_case_id=parent_case_id,
            beam_path=str(beam_path),
        )
        query = """
            INSERT INTO beams (beam_id, parent_case_id, beam_path, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """
        self._execute_query(
            query,
            (
                beam_id,
                parent_case_id,
                str(beam_path),
                BeamStatus.PENDING.value,
            ),
        )
        self.logger.info("Beam record created successfully", {"beam_id": beam_id})

    def update_beam_status(
        self, beam_id: str, status: BeamStatus, error_message: Optional[str] = None
    ) -> None:
        """Updates the status of a beam.

        Args:
            beam_id (str): The beam identifier.
            status (BeamStatus): The new beam status.
            error_message (Optional[str], optional): An optional error message for failed beams. Defaults to None.
        """
        self._log_operation(
            "update_beam_status", beam_id=beam_id, status=status.value
        )
        set_clauses = ["status = ?", "updated_at = CURRENT_TIMESTAMP"]
        params = [status.value]

        if error_message is not None:
            set_clauses.append("error_message = ?")
            params.append(error_message)

        params.append(beam_id)
        query = f"UPDATE beams SET {', '.join(set_clauses)} WHERE beam_id = ?"
        self._execute_query(query, tuple(params))
        self.logger.info(
            "Beam status updated", {"beam_id": beam_id, "status": status.value}
        )

    def get_beam(self, beam_id: str) -> Optional[BeamData]:
        """Retrieves a single beam by its ID.

        Args:
            beam_id (str): The beam identifier.

        Returns:
            Optional[BeamData]: A BeamData object if found, None otherwise.
        """
        self._log_operation("get_beam", beam_id=beam_id)
        query = "SELECT * FROM beams WHERE beam_id = ?"
        row = self._execute_query(query, (beam_id,), fetch_one=True)
        if row:
            return BeamData(
                beam_id=row["beam_id"],
                parent_case_id=row["parent_case_id"],
                beam_path=Path(row["beam_path"]),
                status=BeamStatus(row["status"]),
                created_at=datetime.fromisoformat(row["created_at"]),
                updated_at=(
                    datetime.fromisoformat(row["updated_at"])
                    if row["updated_at"]
                    else None
                ),
                hpc_job_id=row["hpc_job_id"],
            )
        return None

    def assign_hpc_job_id_to_beam(self, beam_id: str, hpc_job_id: str) -> None:
        """Assigns an HPC job ID to a specific beam.

        Args:
            beam_id (str): The beam identifier.
            hpc_job_id (str): The HPC job ID to assign.
        """
        self._log_operation(
            "assign_hpc_job_id_to_beam", beam_id=beam_id, hpc_job_id=hpc_job_id
        )
        query = "UPDATE beams SET hpc_job_id = ?, updated_at = CURRENT_TIMESTAMP WHERE beam_id = ?"
        self._execute_query(query, (hpc_job_id, beam_id))
        self.logger.info(
            "HPC job ID assigned to beam",
            {"beam_id": beam_id, "hpc_job_id": hpc_job_id},
        )

    def get_beams_for_case(self, case_id: str) -> List[BeamData]:
        """Retrieves all beams associated with a given case.

        Args:
            case_id (str): The case identifier.

        Returns:
            List[BeamData]: A list of BeamData objects.
        """
        self._log_operation("get_beams_for_case", case_id=case_id)
        query = "SELECT * FROM beams WHERE parent_case_id = ? ORDER BY beam_id ASC"
        rows = self._execute_query(query, (case_id,), fetch_all=True)
        beams = []
        for row in rows:
            beams.append(
                BeamData(
                    beam_id=row["beam_id"],
                    parent_case_id=row["parent_case_id"],
                    beam_path=Path(row["beam_path"]),
                    status=BeamStatus(row["status"]),
                    created_at=datetime.fromisoformat(row["created_at"]),
                    updated_at=(
                        datetime.fromisoformat(row["updated_at"])
                        if row["updated_at"]
                        else None
                    ),
                    hpc_job_id=row["hpc_job_id"],
                )
            )
        return beams

    def get_all_active_cases(self) -> List[CaseData]:
        """Retrieves all cases that are currently active (not completed or failed).

        Returns:
            List[CaseData]: A list of active CaseData objects.
        """
        self._log_operation("get_all_active_cases")

        active_statuses = [
            CaseStatus.PENDING.value,
            CaseStatus.CSV_INTERPRETING.value,
            CaseStatus.PROCESSING.value,
            CaseStatus.POSTPROCESSING.value,
        ]

        placeholders = ",".join(["?" for _ in active_statuses])
        query = f"""
            SELECT case_id, case_path, status, progress, created_at,
                   updated_at, error_message, assigned_gpu
            FROM cases
            WHERE status IN ({placeholders})
            ORDER BY created_at ASC
        """

        rows = self._execute_query(query, tuple(active_statuses), fetch_all=True)

        cases = []
        for row in rows:
            cases.append(
                CaseData(
                    case_id=row["case_id"],
                    case_path=Path(row["case_path"]),
                    status=CaseStatus(row["status"]),
                    progress=row["progress"],
                    created_at=datetime.fromisoformat(row["created_at"]),
                    updated_at=(
                        datetime.fromisoformat(row["updated_at"])
                        if row["updated_at"]
                        else None
                    ),
                    error_message=row["error_message"],
                    assigned_gpu=row["assigned_gpu"],
                )
            )

        return cases

    def create_case_with_beams(self, case_id: str, case_path: str, beam_jobs: List[Dict[str, Any]]) -> None:
        """Atomically creates a case and its associated beam records.

        Args:
            case_id (str): The unique identifier for the case.
            case_path (str): The path to the case directory.
            beam_jobs (List[Dict[str, Any]]): A list of dictionaries, each containing 'beam_id' and 'beam_path'.
        """
        self._log_operation("create_case_with_beams", case_id=case_id, num_beams=len(beam_jobs))
        with self.db.transaction() as conn:
            # Add case, or do nothing if it already exists
            conn.execute(
                """
                INSERT INTO cases (case_id, case_path, status, progress, created_at, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(case_id) DO NOTHING
                """,
                (case_id, case_path, CaseStatus.PROCESSING.value, 0.0)
            )

            # Prepare beam records for insertion
            beam_records = [
                (
                    job["beam_id"],
                    case_id,
                    str(job["beam_path"]),
                    BeamStatus.PENDING.value,
                )
                for job in beam_jobs
            ]

            # Add beams, or do nothing if they already exist
            conn.executemany(
                """
                INSERT INTO beams (beam_id, parent_case_id, beam_path, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(beam_id) DO NOTHING
                """,
                beam_records
            )
        self.logger.info("Case with beams created successfully", {"case_id": case_id, "num_beams": len(beam_jobs)})

    def update_beams_status_by_case_id(self, case_id: str, status) -> None:
        """Updates the status for all beams associated with a given case.

        Args:
            case_id (str): The parent case identifier.
            status (Union[str, BeamStatus]): The new status for the beams.
        """
        self._log_operation("update_beams_status_by_case_id", case_id=case_id, status=status)

        # Convert to BeamStatus enum if string
        if isinstance(status, str):
            try:
                # Try as enum value first (e.g., "csv_interpreting")
                beam_status = BeamStatus(status)
            except ValueError:
                # Try as enum name (e.g., "CSV_INTERPRETING")
                try:
                    beam_status = BeamStatus[status.upper()]
                except KeyError:
                    raise ValueError(f"Invalid beam status: {status}. Must be one of {[s.name for s in BeamStatus]}")
        else:
            beam_status = status

        query = "UPDATE beams SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE parent_case_id = ?"
        self._execute_query(query, (beam_status.value, case_id))
        self.logger.info("Bulk updated beam statuses for case", {"case_id": case_id, "new_status": beam_status.value})

    def get_all_active_cases_with_beams(self) -> List[Dict[str, Any]]:
        """Retrieves all active cases with their associated beam data.

        Returns:
            List[Dict[str, Any]]: List of dicts containing case data and beam list.
        """
        self._log_operation("get_all_active_cases_with_beams")

        active_statuses = [
            CaseStatus.PENDING.value,
            CaseStatus.CSV_INTERPRETING.value,
            CaseStatus.PROCESSING.value,
            CaseStatus.POSTPROCESSING.value,
        ]

        placeholders = ",".join(["?" for _ in active_statuses])

        # Get cases
        case_query = f"""
            SELECT case_id, case_path, status, progress, created_at,
                   updated_at, error_message, assigned_gpu
            FROM cases
            WHERE status IN ({placeholders})
            ORDER BY created_at ASC
        """
        case_rows = self._execute_query(case_query, tuple(active_statuses), fetch_all=True)

        results = []
        for case_row in case_rows:
            case_id = case_row["case_id"]

            # Get beams for this case
            beam_query = """
                SELECT beam_id, status, created_at, updated_at, hpc_job_id
                FROM beams
                WHERE parent_case_id = ?
                ORDER BY beam_id ASC
            """
            beam_rows = self._execute_query(beam_query, (case_id,), fetch_all=True)

            beams = []
            for beam_row in beam_rows:
                beams.append({
                    "beam_id": beam_row["beam_id"],
                    "status": BeamStatus(beam_row["status"]),
                    "created_at": datetime.fromisoformat(beam_row["created_at"]),
                    "updated_at": (
                        datetime.fromisoformat(beam_row["updated_at"])
                        if beam_row["updated_at"] else None
                    ),
                    "hpc_job_id": beam_row["hpc_job_id"]
                })

            results.append({
                "case_data": CaseData(
                    case_id=case_row["case_id"],
                    case_path=Path(case_row["case_path"]),
                    status=CaseStatus(case_row["status"]),
                    progress=case_row["progress"],
                    created_at=datetime.fromisoformat(case_row["created_at"]),
                    updated_at=(
                        datetime.fromisoformat(case_row["updated_at"])
                        if case_row["updated_at"] else None
                    ),
                    error_message=case_row["error_message"],
                    assigned_gpu=case_row["assigned_gpu"],
                ),
                "beams": beams
            })

        return results
