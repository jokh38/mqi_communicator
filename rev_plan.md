
# Development & Refactoring Guide: MQI Communicator

## Introduction

This document provides a set of guidelines and action items for refactoring the MQI Communicator codebase. The goal is to enhance maintainability, reduce complexity, and improve overall code quality by addressing code duplication, removing unused components, and increasing efficiency through helper methods.

-----

## 1. Code Duplication and Centralization

**Issue**: Common functionalities like database connections, SSH client initialization, and platform-specific logic are repeatedly implemented across multiple files. This leads to code redundancy and inconsistencies.

### 1.1. Centralize Database Connection Management

  * **Observation**: `DatabaseConnection` and `CaseRepository` objects are instantiated multiple times within functions in `src/core/dispatcher.py` and `src/core/worker.py`. This duplicates setup and teardown logic.

  * **Recommendation**: Create a utility module (e.g., `src/utils/db_context.py`) that provides a context manager for database sessions. This will handle the creation, transaction management, and closing of connections automatically.

    **Implementation (`src/utils/db_context.py`):**

    ```python
    from contextlib import contextmanager
    from src.database.connection import DatabaseConnection
    from src.repositories.case_repo import CaseRepository

    @contextmanager
    def get_db_session(settings, logger):
        """Provides a transactional database session."""
        db_conn = None
        try:
            db_path = settings.get_database_path()
            db_conn = DatabaseConnection(db_path=db_path, settings=settings, logger=logger)
            yield CaseRepository(db_conn, logger)
        finally:
            if db_conn:
                db_conn.close()
    ```

    **Usage Example:**

    ```python
    from src.utils.db_context import get_db_session

    with get_db_session(settings, logger) as case_repo:
        case_data = case_repo.get_case(case_id)
        case_repo.update_case_status(case_id, CaseStatus.PROCESSING)
    ```

  * **Benefit**: Simplifies database interaction logic in business-level modules, eliminates redundant code, and ensures connections are always closed properly.

### 1.2. Unify SSH Client Initialization

  * **Observation**: The logic for initializing the `paramiko.SSHClient` is duplicated in `main.py` and `src/core/worker.py`.

  * **Recommendation**: Create a dedicated helper function in a `src/utils/connections.py` module to handle the creation and configuration of the SSH client based on settings from `config.yaml`.

    **Implementation (`src/utils/connections.py`):**

    ```python
    import paramiko
    from src.config.settings import Settings
    from typing import Optional

    def create_ssh_client(settings: Settings, timeout: int = 30) -> paramiko.SSHClient:
        """Initializes and connects a paramiko SSH client based on config.

        Args:
            settings: Application settings containing HPC connection details
            timeout: Connection timeout in seconds (default: 30)

        Returns:
            Connected paramiko.SSHClient instance

        Raises:
            ConnectionError: If HPC connection settings are not configured
            paramiko.SSHException: If SSH connection fails
        """
        hpc_config = settings.get_hpc_connection()
        if not hpc_config:
            raise ConnectionError("HPC connection settings not configured.")

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        # Extract connection parameters
        hostname = hpc_config.get('hostname')
        port = hpc_config.get('port', 22)
        username = hpc_config.get('username')
        password = hpc_config.get('password')
        key_filename = hpc_config.get('key_filename')

        client.connect(
            hostname=hostname,
            port=port,
            username=username,
            password=password,
            key_filename=key_filename,
            timeout=timeout,
            look_for_keys=True,
            allow_agent=True
        )
        return client
    ```

    **Usage Example:**

    ```python
    from src.utils.connections import create_ssh_client

    ssh_client = create_ssh_client(settings)
    try:
        # Use SSH client
        stdin, stdout, stderr = ssh_client.exec_command('ls -la')
    finally:
        ssh_client.close()
    ```

  * **Benefit**: Provides a single source of truth for SSH connection logic, making it easier to manage credentials, timeouts, and other connection parameters.

-----

## 2. Removal of Unused Code

**Issue**: The codebase contains several modules and classes that are not currently used in the application's execution flow. Removing them will simplify the project structure and reduce cognitive overhead for developers.

### 2.1. Deprecate `src/infrastructure/process_manager.py`

  * **Observation**: The `ProcessManager` and `CommandExecutor` classes are not being used. The application's main process management is handled directly by `concurrent.futures.ProcessPoolExecutor` in `main.py`, and command execution is managed by `src/handlers/execution_handler.py`.

  * **Action Steps**:
    1. Verify no imports of `ProcessManager` or `CommandExecutor` exist in the codebase
    2. Run project tests to ensure removal doesn't break functionality
    3. Remove the `src/infrastructure/process_manager.py` file
    4. Update any documentation referencing this module

  * **Benefit**: Reduces codebase size and eliminates confusion by removing a parallel, unused implementation of process management.

### 2.2. Evaluate and Remove `src/utils/retry_policy.py`

  * **Observation**: The `RetryPolicy` and `CircuitBreaker` classes, while well-defined, are not currently implemented in any part of the core application logic (e.g., in `main.py` or `worker.py`).

  * **Action Steps**:
    1. Search for any imports or references to `RetryPolicy` or `CircuitBreaker`
    2. If no references found, remove the `src/utils/retry_policy.py` file
    3. Document the removal in CHANGELOG or commit message for future reference
    4. If needed in the future, retrieve from version control history

  * **Benefit**: Keeps the codebase focused on currently implemented features and reduces maintenance burden.

### 2.3. Consolidate Constants into `config.yaml`

  * **Observation**: The `src/config/constants.py` file defines many constants (e.g., `TPS_REQUIRED_PARAMS`, `STATUS_COLORS`) that are either managed in `config.yaml` or hardcoded within specific modules. This creates multiple sources of configuration.

  * **Action Steps**:
    1. Audit all constants in `src/config/constants.py`
    2. Categorize constants as:
       - User-configurable → Move to `config.yaml`
       - Application-internal → Keep in Python but consolidate location
       - Deprecated/unused → Remove entirely
    3. Update the `Settings` class to expose config.yaml values
    4. Update all imports throughout the codebase
    5. Remove `constants.py` once migration is complete

  * **Migration Example**:

    **Before (`src/config/constants.py`):**
    ```python
    TPS_REQUIRED_PARAMS = ['BeamNumber', 'GantryAngle', 'CouchAngle']
    MAX_RETRIES = 3
    ```

    **After (`config.yaml`):**
    ```yaml
    tps:
      required_params:
        - BeamNumber
        - GantryAngle
        - CouchAngle
      max_retries: 3
    ```

    **After (`Settings` class method):**
    ```python
    def get_tps_required_params(self) -> List[str]:
        return self.config.get('tps', {}).get('required_params', [])

    def get_max_retries(self) -> int:
        return self.config.get('tps', {}).get('max_retries', 3)
    ```

  * **Benefit**: Creates a single source of truth for all application constants and configurations, improving clarity and ease of modification.

-----

## 3. Efficiency and Readability Improvements

**Issue**: Complex methods can be refactored by extracting logic into smaller, reusable helper functions. This improves readability, testability, and adherence to the Single Responsibility Principle.

### 3.1. Refactor `main.py:run_worker_loop()`

  * **Observation**: The `run_worker_loop()` method in `main.py` contains extensive logic for orchestrating the entire case preparation pipeline (data validation, CSV interpretation, TPS generation, etc.) before dispatching workers.

  * **Recommendation**: Extract the case preparation logic into dedicated helper methods within the `MQIApplication` class to separate concerns and improve testability.

    **Implementation Approach:**

    ```python
    # In MQIApplication class in main.py

    def _prepare_and_dispatch_case(self, case_data: CaseData) -> None:
        """Orchestrates the complete case preparation and dispatch workflow.

        Args:
            case_data: The case data to process

        Raises:
            ValidationError: If case data validation fails
            ProcessingError: If any preparation step fails
        """
        try:
            # 1. Discover beams and validate data
            beam_jobs = self._discover_and_validate_beams(case_data)

            # 2. Run CSV interpretation
            self._interpret_csv_files(case_data, beam_jobs)

            # 3. Generate TPS file
            gpu_assignments = self._generate_tps_file(case_data, beam_jobs)

            # 4. Upload files to HPC
            self._upload_case_files(case_data, beam_jobs)

            # 5. Dispatch workers for beam processing
            self._dispatch_beam_workers(case_data, beam_jobs, gpu_assignments)

        except Exception as e:
            self.logger.error(f"Case preparation failed: {e}", {"case_id": case_data.case_id})
            self._handle_case_failure(case_data, str(e))
            raise

    def _discover_and_validate_beams(self, case_data: CaseData) -> List[BeamJob]:
        """Discovers beam files and validates beam data."""
        # Implementation: scan case directory, validate beam files, create BeamJob objects
        pass

    def _interpret_csv_files(self, case_data: CaseData, beam_jobs: List[BeamJob]) -> None:
        """Runs CSV interpretation for the case."""
        # Implementation: parse CSV files, extract beam parameters
        pass

    def _generate_tps_file(self, case_data: CaseData, beam_jobs: List[BeamJob]) -> Dict[str, int]:
        """Generates TPS file and returns GPU assignments."""
        # Implementation: create TPS file, assign GPUs to beams
        return {}

    def _upload_case_files(self, case_data: CaseData, beam_jobs: List[BeamJob]) -> None:
        """Uploads case files to HPC cluster."""
        # Implementation: transfer files via SFTP/SCP
        pass

    def _dispatch_beam_workers(self, case_data: CaseData, beam_jobs: List[BeamJob],
                                gpu_assignments: Dict[str, int]) -> None:
        """Dispatches worker processes for beam simulation."""
        # Implementation: submit worker jobs to process pool
        pass

    def _handle_case_failure(self, case_data: CaseData, error_message: str) -> None:
        """Handles case processing failure by updating status and logging."""
        with get_db_session(self.settings, self.logger) as case_repo:
            case_repo.update_case_status(case_data.case_id, CaseStatus.FAILED, error_message)

    def run_worker_loop(self):
        """Main worker loop that processes cases from the queue."""
        while not self.shutdown_event.is_set():
            try:
                case_data = self.case_queue.get(timeout=1.0)
                self._prepare_and_dispatch_case(case_data)
                self._monitor_running_beams()
            except queue.Empty:
                continue
            except Exception as e:
                self.logger.error(f"Worker loop error: {e}")
    ```

  * **Benefit**:
    - Improves readability by breaking down a monolithic method into focused, single-purpose methods
    - Enhances testability by allowing individual pipeline steps to be tested in isolation
    - Makes error handling more granular and traceable
    - Simplifies maintenance and future modifications to the workflow

### 3.2. Implement DTO Mapping Helpers in Repositories

  * **Observation**: In `src/repositories/case_repo.py`, the logic to map a `sqlite3.Row` object to a `CaseData` or `BeamData` DTO is repeated across multiple methods like `get_case()` and `get_cases_by_status()`.

  * **Recommendation**: Create private helper methods within the `CaseRepository` class to handle row-to-DTO mapping, eliminating code duplication and ensuring consistency.

    **Implementation (`src/repositories/case_repo.py`):**

    ```python
    import sqlite3
    from pathlib import Path
    from typing import Optional, List
    from src.models.case_data import CaseData, BeamData
    from src.models.enums import CaseStatus, BeamStatus

    class CaseRepository:
        # ... existing methods ...

        def _map_row_to_casedata(self, row: sqlite3.Row) -> CaseData:
            """Maps a database row to a CaseData DTO.

            Args:
                row: SQLite row object from cases table

            Returns:
                CaseData object populated with row data
            """
            return CaseData(
                case_id=row["case_id"],
                case_path=Path(row["case_path"]),
                status=CaseStatus(row["status"]),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                priority=row.get("priority", 0),
                error_message=row.get("error_message")
            )

        def _map_row_to_beamdata(self, row: sqlite3.Row) -> BeamData:
            """Maps a database row to a BeamData DTO.

            Args:
                row: SQLite row object from beams table

            Returns:
                BeamData object populated with row data
            """
            return BeamData(
                beam_id=row["beam_id"],
                case_id=row["case_id"],
                beam_number=row["beam_number"],
                status=BeamStatus(row["status"]),
                gpu_id=row.get("gpu_id"),
                worker_id=row.get("worker_id"),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                started_at=row.get("started_at"),
                completed_at=row.get("completed_at"),
                error_message=row.get("error_message")
            )

        def get_case(self, case_id: str) -> Optional[CaseData]:
            """Retrieves a case by ID."""
            query = "SELECT * FROM cases WHERE case_id = ?"
            row = self._execute_query(query, (case_id,)).fetchone()
            return self._map_row_to_casedata(row) if row else None

        def get_cases_by_status(self, status: CaseStatus) -> List[CaseData]:
            """Retrieves all cases with the specified status."""
            query = "SELECT * FROM cases WHERE status = ? ORDER BY priority DESC, created_at ASC"
            rows = self._execute_query(query, (status.value,)).fetchall()
            return [self._map_row_to_casedata(row) for row in rows]

        def get_beams_by_case(self, case_id: str) -> List[BeamData]:
            """Retrieves all beams for a given case."""
            query = "SELECT * FROM beams WHERE case_id = ? ORDER BY beam_number ASC"
            rows = self._execute_query(query, (case_id,)).fetchall()
            return [self._map_row_to_beamdata(row) for row in rows]
    ```

  * **Benefit**:
    - Centralizes object-relational mapping logic in one place
    - Ensures consistency across all query methods
    - Makes schema changes easier to manage (update mapping in one location)
    - Improves code maintainability and reduces duplication
    - Simplifies unit testing of mapping logic

### 3.3. Create a Status Update Helper in `WorkflowState`

  * **Observation**: Each state class in `src/domain/states.py` calls `context.case_repo.update_beam_status()` and logs the transition. This is repetitive and violates the DRY (Don't Repeat Yourself) principle.

  * **Recommendation**: Implement a helper method in the abstract base class `WorkflowState` to handle status updates and logging, which can be called by concrete state implementations.

    **Implementation (`src/domain/states.py`):**

    ```python
    from abc import ABC, abstractmethod
    from typing import TYPE_CHECKING
    from src.models.enums import BeamStatus

    if TYPE_CHECKING:
        from src.domain.workflow_manager import WorkflowManager

    class WorkflowState(ABC):
        """Abstract base class for workflow states implementing the State pattern."""

        def _update_status(self, context: 'WorkflowManager', status: BeamStatus,
                          message: str, error_message: str = None) -> None:
            """Updates beam status and logs the transition.

            Args:
                context: WorkflowManager instance providing access to repository and logger
                status: New status to set for the beam
                message: Log message describing the status change
                error_message: Optional error message to store in database
            """
            context.case_repo.update_beam_status(
                beam_id=context.beam_id,
                status=status,
                error_message=error_message
            )
            context.logger.info(message, {
                "beam_id": context.beam_id,
                "case_id": context.case_id,
                "status": status.value,
                "previous_state": self.__class__.__name__
            })

        @abstractmethod
        def handle(self, context: 'WorkflowManager') -> None:
            """Handle the current state and transition to the next state if applicable.

            Args:
                context: WorkflowManager instance managing the workflow
            """
            pass

        @abstractmethod
        def can_transition_to(self, target_state: 'WorkflowState') -> bool:
            """Check if transition to target state is allowed.

            Args:
                target_state: The state to potentially transition to

            Returns:
                True if transition is allowed, False otherwise
            """
            pass
    ```

    **Usage Example in Concrete State:**

    ```python
    class UploadingState(WorkflowState):
        """State representing file upload in progress."""

        def handle(self, context: 'WorkflowManager') -> None:
            """Handle file upload and transition to next state."""
            try:
                # Update status to uploading
                self._update_status(
                    context,
                    BeamStatus.UPLOADING,
                    f"Starting file upload for beam {context.beam_id}"
                )

                # Perform upload
                context.upload_handler.upload_beam_files(context.beam_id)

                # Transition to next state
                context.transition_to(QueuedState())

            except Exception as e:
                self._update_status(
                    context,
                    BeamStatus.FAILED,
                    f"Upload failed for beam {context.beam_id}: {e}",
                    error_message=str(e)
                )
                context.transition_to(FailedState())

        def can_transition_to(self, target_state: WorkflowState) -> bool:
            """Allow transition to Queued or Failed states."""
            return isinstance(target_state, (QueuedState, FailedState))
    ```

  * **Benefit**:
    - Reduces boilerplate code in each concrete state class
    - Enforces a consistent pattern for status updates and logging throughout the workflow
    - Centralizes logging format and structure
    - Makes it easier to add additional behavior (e.g., metrics, notifications) to all state transitions
    - Improves maintainability by having one location to modify status update logic

-----

## 4. Implementation Priority and Dependencies

This section provides a recommended order for implementing the above refactoring tasks based on dependencies and impact.

### Phase 1: Foundation (Low Risk, High Value)

1. **Implement DTO Mapping Helpers (Section 3.2)**
   - No external dependencies
   - Immediate value in reducing code duplication
   - Low risk of breaking existing functionality

2. **Centralize Database Connection Management (Section 1.1)**
   - Can be implemented incrementally
   - Improves resource management across the application
   - Easy to test and validate

### Phase 2: Cleanup (Low Risk, Medium Value)

3. **Remove Unused Code (Section 2)**
   - Complete all subsections: 2.1, 2.2, 2.3
   - Verify no dependencies before removal
   - Run full test suite after each removal
   - Reduces cognitive load for developers

### Phase 3: Architecture Improvements (Medium Risk, High Value)

4. **Unify SSH Client Initialization (Section 1.2)**
   - Depends on understanding current SSH usage patterns
   - Improves consistency and error handling
   - Test thoroughly with HPC connection

5. **Create Status Update Helper (Section 3.3)**
   - Requires understanding of state machine implementation
   - High value for workflow consistency
   - Should be done before major state additions

### Phase 4: Complex Refactoring (Higher Risk, High Value)

6. **Refactor `run_worker_loop()` (Section 3.1)**
   - Most complex refactoring task
   - Should be done last after other foundations are in place
   - Requires comprehensive testing
   - Consider implementing incrementally, one helper method at a time

### Testing Strategy

For each phase:
1. Write or update unit tests for new/modified code
2. Run integration tests to verify no regressions
3. Perform manual testing of critical workflows
4. Update documentation to reflect changes
5. Code review by team members

-----

## 5. Conclusion

This refactoring plan addresses key technical debt areas in the MQI Communicator codebase. By systematically implementing these changes, the codebase will become:

- **More maintainable**: Clear separation of concerns and reduced duplication
- **More testable**: Smaller, focused functions that can be tested in isolation
- **More consistent**: Centralized patterns for common operations
- **More efficient**: Cleaner code structure and better resource management

Implement these changes incrementally, prioritizing low-risk, high-value improvements first. Ensure adequate test coverage and documentation updates accompany each change.