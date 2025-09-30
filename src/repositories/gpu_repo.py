"""Manages all CRUD operations for the 'gpu_resources' table."""
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.config.settings import Settings
from src.database.connection import DatabaseConnection
from src.domain.enums import GpuStatus
from src.domain.errors import GpuResourceError
from src.domain.models import GpuResource
from src.infrastructure.logging_handler import StructuredLogger
from src.repositories.base import BaseRepository


class GpuRepository(BaseRepository):
    """Manages CRUD operations for the 'gpu_resources' table and handles GPU
    allocation/deallocation.

    This class separates GPU data persistence from nvidia-smi parsing.
    """

    def __init__(
        self,
        db_connection: DatabaseConnection,
        logger: StructuredLogger,
        settings: Settings,
    ):
        """Initializes the GPU repository with an injected database connection.

        Args:
            db_connection (DatabaseConnection): The database connection manager.
            logger (StructuredLogger): The logger for recording operations.
            settings (Settings): The application settings object.
        """
        super().__init__(db_connection, logger)
        self.settings = settings

    def update_resources(self, gpu_data: List[Dict[str, Any]]) -> None:
        """Updates the GPU resources table with data from the GPU monitor using an
        atomic UPSERT operation.

        This method uses a single transactional UPSERT for efficiency and atomicity.

        Args:
            gpu_data (List[Dict[str, Any]]): A list of dictionaries containing GPU information.

        Raises:
            GpuResourceError: If the update fails.
        """
        self._log_operation("update_resources", count=len(gpu_data))

        query = """
            INSERT INTO gpu_resources (
                uuid, gpu_index, name, memory_total, memory_used, memory_free,
                temperature, utilization, status, last_updated
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(uuid) DO UPDATE SET
                gpu_index = excluded.gpu_index,
                name = excluded.name,
                memory_total = excluded.memory_total,
                memory_used = excluded.memory_used,
                memory_free = excluded.memory_free,
                temperature = excluded.temperature,
                utilization = excluded.utilization,
                last_updated = CURRENT_TIMESTAMP
        """

        params_list = [
            (
                gpu["uuid"],
                gpu["gpu_index"],
                gpu["name"],
                gpu["memory_total"],
                gpu["memory_used"],
                gpu["memory_free"],
                gpu["temperature"],
                gpu["utilization"],
                GpuStatus.IDLE.value,
            )
            for gpu in gpu_data
        ]

        try:
            with self.db.transaction() as conn:
                conn.executemany(query, params_list)
        except Exception as e:
            self.logger.error(
                "Failed to bulk update GPU resources",
                {"error": str(e), "gpu_count": len(gpu_data)},
            )
            raise GpuResourceError(f"Failed to update GPU resources: {e}")

    def assign_gpu_to_case(self, gpu_uuid: str, case_id: str) -> None:
        """Assigns a GPU to a specific case.

        Args:
            gpu_uuid (str): The UUID of the GPU to assign.
            case_id (str): The case identifier to assign the GPU to.
        """
        self._log_operation("assign_gpu_to_case", gpu_uuid, case_id=case_id)

        query = """
            UPDATE gpu_resources
            SET status = ?, assigned_case = ?, last_updated = CURRENT_TIMESTAMP
            WHERE uuid = ?
        """

        self._execute_query(query, (GpuStatus.ASSIGNED.value, case_id, gpu_uuid))

        self.logger.info(
            "GPU assigned to case", {"gpu_uuid": gpu_uuid, "case_id": case_id}
        )

    def release_gpu(self, gpu_uuid: str) -> None:
        """Releases a GPU, making it available again.

        Args:
            gpu_uuid (str): The UUID of the GPU to release.
        """
        self._log_operation("release_gpu", gpu_uuid)

        query = """
            UPDATE gpu_resources
            SET status = ?, assigned_case = NULL, last_updated = CURRENT_TIMESTAMP
            WHERE uuid = ?
        """

        self._execute_query(query, (GpuStatus.IDLE.value, gpu_uuid))

        self.logger.info("GPU released", {"gpu_uuid": gpu_uuid})

    def find_and_lock_multiple_gpus(
        self, case_id: str, num_gpus: int, min_memory_mb: Optional[int] = None
    ) -> Optional[List[Dict[str, Any]]]:
        """Atomically finds multiple idle GPUs with sufficient memory and assigns them to a case.

        Args:
            case_id (str): The case identifier requesting the GPUs.
            num_gpus (int): Number of GPUs to allocate.
            min_memory_mb (Optional[int]): The minimum required memory in MB. Overrides config if set.

        Returns:
            Optional[List[Dict[str, Any]]]: A list of dictionaries with gpu_uuid and gpu_id if successful, None otherwise.

        Raises:
            GpuResourceError: If the allocation fails.
        """
        if min_memory_mb is None:
            gpu_config = self.settings.get_gpu_config()
            min_memory_mb = gpu_config.get("min_memory_mb", 1000)

        self._log_operation(
            "find_and_lock_multiple_gpus", case_id, num_gpus=num_gpus, min_memory_mb=min_memory_mb
        )

        try:
            with self.db.transaction() as conn:
                # Find available GPUs with their actual indices
                query = """
                    SELECT uuid, gpu_index, memory_free
                    FROM gpu_resources
                    WHERE status = ? AND memory_free >= ?
                    ORDER BY gpu_index ASC
                    LIMIT ?
                """

                cursor = conn.execute(query, (GpuStatus.IDLE.value, min_memory_mb, num_gpus))
                gpu_rows = cursor.fetchall()

                if len(gpu_rows) < num_gpus:
                    self.logger.warning(
                        "Insufficient GPUs available",
                        {"case_id": case_id, "requested": num_gpus, "available": len(gpu_rows), "min_memory_mb": min_memory_mb},
                    )
                    return None

                # Assign all GPUs atomically
                gpu_allocations = []
                for gpu_row in gpu_rows:
                    gpu_uuid = gpu_row["uuid"]

                    update_query = """
                        UPDATE gpu_resources
                        SET status = ?, assigned_case = ?,
                            last_updated = CURRENT_TIMESTAMP
                        WHERE uuid = ? AND status = ?
                    """

                    cursor = conn.execute(
                        update_query,
                        (
                            GpuStatus.ASSIGNED.value,
                            case_id,
                            gpu_uuid,
                            GpuStatus.IDLE.value,
                        ),
                    )

                    if cursor.rowcount != 1:
                        self.logger.warning(
                            "GPU assignment race condition during multi-GPU allocation",
                            {"gpu_uuid": gpu_uuid, "case_id": case_id},
                        )
                        return None

                    # Store allocation with actual GPU index from nvidia-smi
                    gpu_allocations.append({
                        "gpu_uuid": gpu_uuid,
                        "gpu_id": gpu_row["gpu_index"],  # Use actual GPU index (e.g., 2, 4, 7, 8)
                        "memory_free": gpu_row["memory_free"]
                    })

                self.logger.info(
                    "Multiple GPUs allocated successfully",
                    {
                        "case_id": case_id,
                        "gpus_allocated": len(gpu_allocations),
                        "gpu_details": [{"uuid": gpu["gpu_uuid"], "gpu_id": gpu["gpu_id"]} for gpu in gpu_allocations]
                    },
                )

                return gpu_allocations

        except Exception as e:
            self.logger.error(
                "Failed to allocate multiple GPUs", {"case_id": case_id, "num_gpus": num_gpus, "error": str(e)}
            )
            raise GpuResourceError(f"Failed to allocate {num_gpus} GPUs for case {case_id}: {e}")

    def find_and_lock_available_gpu(
        self, case_id: str, min_memory_mb: Optional[int] = None
    ) -> Optional[Dict[str, str]]:
        """Atomically finds an idle GPU with sufficient memory and assigns it to a case.

        Args:
            case_id (str): The case identifier requesting the GPU.
            min_memory_mb (Optional[int]): The minimum required memory in MB. Overrides config if set.

        Returns:
            Optional[Dict[str, str]]: A dictionary with the gpu_uuid if successful, None otherwise.

        Raises:
            GpuResourceError: If the allocation fails.
        """
        if min_memory_mb is None:
            gpu_config = self.settings.get_gpu_config()
            min_memory_mb = gpu_config.get("min_memory_mb", 1000)

        self._log_operation(
            "find_and_lock_available_gpu", case_id, min_memory_mb=min_memory_mb
        )

        try:
            with self.db.transaction() as conn:
                query = """
                    SELECT uuid, memory_free
                    FROM gpu_resources
                    WHERE status = ? AND memory_free >= ?
                    ORDER BY memory_free DESC
                    LIMIT 1
                """

                cursor = conn.execute(query, (GpuStatus.IDLE.value, min_memory_mb))
                gpu_row = cursor.fetchone()

                if not gpu_row:
                    self.logger.warning(
                        "No available GPU found",
                        {"case_id": case_id, "min_memory_mb": min_memory_mb},
                    )
                    return None

                gpu_uuid = gpu_row["uuid"]

                update_query = """
                    UPDATE gpu_resources
                    SET status = ?, assigned_case = ?,
                        last_updated = CURRENT_TIMESTAMP
                    WHERE uuid = ? AND status = ?
                """

                cursor = conn.execute(
                    update_query,
                    (
                        GpuStatus.ASSIGNED.value,
                        case_id,
                        gpu_uuid,
                        GpuStatus.IDLE.value,
                    ),
                )

                if cursor.rowcount != 1:
                    self.logger.warning(
                        "GPU assignment race condition",
                        {"gpu_uuid": gpu_uuid, "case_id": case_id},
                    )
                    return None

                self.logger.info(
                    "GPU allocated successfully",
                    {
                        "gpu_uuid": gpu_uuid,
                        "case_id": case_id,
                        "memory_free": gpu_row["memory_free"],
                    },
                )

                return {"gpu_uuid": gpu_uuid}

        except Exception as e:
            self.logger.error(
                "Failed to allocate GPU", {"case_id": case_id, "error": str(e)}
            )
            raise GpuResourceError(f"Failed to allocate GPU for case {case_id}: {e}")

    def get_all_gpu_resources(self) -> List[GpuResource]:
        """Retrieves detailed information for all tracked GPU resources.

        Returns:
            List[GpuResource]: A list of GpuResource objects.
        """
        self._log_operation("get_all_gpu_resources")

        query = """
            SELECT uuid, gpu_index, name, memory_total, memory_used, memory_free,
                   temperature, utilization, status, assigned_case, last_updated
            FROM gpu_resources
            ORDER BY gpu_index ASC
        """

        rows = self._execute_query(query, fetch_all=True)

        gpus = []
        for row in rows:
            gpus.append(
                GpuResource(
                    uuid=row["uuid"],
                    gpu_index=row["gpu_index"],
                    name=row["name"],
                    memory_total=row["memory_total"],
                    memory_used=row["memory_used"],
                    memory_free=row["memory_free"],
                    temperature=row["temperature"],
                    utilization=row["utilization"],
                    status=GpuStatus(row["status"]),
                    assigned_case=row["assigned_case"],
                    last_updated=(
                        datetime.fromisoformat(row["last_updated"])
                        if row["last_updated"]
                        else None
                    ),
                )
            )

        return gpus

    def get_gpu_by_uuid(self, uuid: str) -> Optional[GpuResource]:
        """Get a specific GPU resource by its UUID.

        Args:
            uuid (str): The GPU UUID to retrieve.

        Returns:
            Optional[GpuResource]: A GpuResource object if found, None otherwise.
        """
        self._log_operation("get_gpu_by_uuid", uuid)

        query = """
            SELECT uuid, gpu_index, name, memory_total, memory_used, memory_free,
                   temperature, utilization, status, assigned_case, last_updated
            FROM gpu_resources
            WHERE uuid = ?
        """

        row = self._execute_query(query, (uuid,), fetch_one=True)

        if row:
            return GpuResource(
                uuid=row["uuid"],
                gpu_index=row["gpu_index"],
                name=row["name"],
                memory_total=row["memory_total"],
                memory_used=row["memory_used"],
                memory_free=row["memory_free"],
                temperature=row["temperature"],
                utilization=row["utilization"],
                status=GpuStatus(row["status"]),
                assigned_case=row["assigned_case"],
                last_updated=(
                    datetime.fromisoformat(row["last_updated"])
                    if row["last_updated"]
                    else None
                ),
            )

        return None

    def get_available_gpu_count(self) -> int:
        """Get the count of available (idle) GPUs.

        Returns:
            int: The number of idle GPUs.
        """
        self._log_operation("get_available_gpu_count")

        query = "SELECT COUNT(*) as count FROM gpu_resources WHERE status = ?"
        row = self._execute_query(query, (GpuStatus.IDLE.value,), fetch_one=True)

        return row["count"] if row else 0

    def release_all_for_case(self, case_id: str) -> int:
        """Release all GPUs allocated to a specific case.

        Args:
            case_id (str): The case identifier.

        Returns:
            int: The number of GPUs released.
        """
        self._log_operation("release_all_for_case", case_id=case_id)

        query = """
            UPDATE gpu_resources
            SET status = ?, assigned_case = NULL, last_updated = CURRENT_TIMESTAMP
            WHERE assigned_case = ?
        """

        with self.db.transaction() as conn:
            cursor = conn.execute(query, (GpuStatus.IDLE.value, case_id))
            row_count = cursor.rowcount

        self.logger.info("Released GPUs for case", {
            "case_id": case_id,
            "gpus_released": row_count
        })

        return row_count