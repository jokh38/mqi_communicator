# Development Plan: Code Separation into Existing Modules

## Overview

This plan refactors three lengthy files (dispatcher.py: 638 lines, case_repo.py: 662 lines, main.py: 611 lines) by moving their functions into existing modules (workflow_manager.py, case_aggregator.py, models.py, states.py, db_context.py, etc.) without creating new service modules. The goal is to reduce code length while maintaining backward compatibility.

## Current State Analysis

### Large Files
- **dispatcher.py** (638 lines): Contains case-level operations, beam job preparation, file watching, and utility functions
- **case_repo.py** (662 lines): Repository with case CRUD, beam CRUD, and workflow tracking all in one class
- **main.py** (611 lines): Application initialization, service management, worker coordination, and signal handling

### Existing Small Modules (Ready to Accept Functions)
- **workflow_manager.py** (125 lines): WorkflowManager class for state orchestration
- **case_aggregator.py** (63 lines): Single function for aggregating beam status to case status
- **models.py** (73 lines): Data transfer objects (DTOs)
- **states.py** (357 lines): State pattern implementation for workflow
- **db_context.py** (52 lines): Context manager for database sessions

---

## Phase 1: Extend Domain Models (models.py)

### Goal: Add domain types to reduce dict plumbing and improve type safety

#### Actions:
1. **Add BeamJob dataclass to models.py**
   ```python
   @dataclass
   class BeamJob:
       """Represents a beam processing job."""
       beam_id: str
       beam_path: Path
       parent_case_id: str
   ```

2. **Add CaseJob dataclass to models.py**
   ```python
   @dataclass
   class CaseJob:
       """Represents a case processing job."""
       case_id: str
       case_path: Path
       timestamp: float
   ```

3. **Add ExecutionResult dataclass to models.py** (if not already exists)
   ```python
   @dataclass
   class ExecutionResult:
       """Result of command execution."""
       success: bool
       return_code: Optional[int] = None
       output: Optional[str] = None
       error: Optional[str] = None
   ```

#### Impact:
- Eliminates raw dict usage in dispatcher.py's `prepare_beam_jobs()` and queue operations
- Provides type hints for better IDE support and type checking
- Estimated reduction: ~5-10 lines in dispatcher.py (through cleaner type hints)

---

## Phase 2: Expand Database Context Utilities (db_context.py)

### Goal: Extract repeated database session patterns from dispatcher.py

#### Actions:
1. **Add utility functions to db_context.py:**

   ```python
   def get_handler_db_path(settings: Settings, handler_name: str) -> Path:
       """Get database path for a specific handler with fallback logic."""
       try:
           return Path(settings.get_path("database_path", handler_name=handler_name))
       except Exception:
           try:
               return settings.get_database_path()
           except Exception:
               return Path("dummy.db")

   def remote_execution_enabled(settings: Settings, handler_name: str = "HpcJobSubmitter") -> bool:
       """Check if remote execution is enabled for a handler."""
       mode = settings.get_handler_mode(handler_name)
       return mode == "remote"
   ```

2. **Add workflow step recording helper:**
   ```python
   def record_step(
       settings: Settings,
       logger: StructuredLogger,
       case_id: str,
       step: WorkflowStep,
       status: str,
       metadata: Optional[Dict[str, Any]] = None,
       error_message: Optional[str] = None,
       handler_name: str = "CsvInterpreter"
   ) -> None:
       """Record a workflow step with automatic session management."""
       with get_db_session(settings, logger, handler_name) as case_repo:
           case_repo.record_workflow_step(
               case_id=case_id,
               step=step,
               status=status,
               metadata=metadata,
               error_message=error_message
           )
   ```

#### Impact:
- Removes duplicate DB path resolution logic from dispatcher.py (appears 3+ times)
- Centralizes remote mode checking
- Estimated reduction: ~30-40 lines in dispatcher.py

---

## Phase 3: Enhance Case Aggregator (case_aggregator.py)

### Goal: Move case-level orchestration and utility functions from dispatcher.py

#### Actions:
1. **Move queue management functions from dispatcher.py:**
   ```python
   def queue_case(
       case_id: str,
       case_path: Path,
       case_queue: mp.Queue,
       logger: StructuredLogger
   ) -> bool:
       """Queue a case for processing."""
       # Move _queue_case from dispatcher.py
   ```

2. **Move logger utility:**
   ```python
   def ensure_logger(name: str, settings: Settings) -> StructuredLogger:
       """Ensure logger is available, initializing if needed."""
       # Move _ensure_logger from dispatcher.py
   ```

3. **Move error handling utility:**
   ```python
   def handle_case_error(
       case_id: str,
       error: Exception,
       workflow_step: WorkflowStep,
       settings: Settings,
       logger: StructuredLogger,
       handler_name: str = "CsvInterpreter"
   ) -> None:
       """Handle errors in case processing."""
       # Move _handle_dispatcher_error from dispatcher.py
   ```

4. **Move case-level operations:**
   ```python
   def run_csv_interpreting(case_id: str, case_path: Path, settings: Settings) -> bool:
       """Run CSV interpreting for entire case."""
       # Move run_case_level_csv_interpreting from dispatcher.py

   def run_tps_generation(case_id: str, case_path: Path, settings: Settings) -> bool:
       """Generate TPS files for a case."""
       # Move run_case_level_tps_generation from dispatcher.py

   def run_file_upload(case_id: str, settings: Settings, ssh_client: paramiko.SSHClient) -> bool:
       """Upload files for case to remote."""
       # Move run_case_level_upload from dispatcher.py

   def prepare_beam_jobs(case_id: str, case_path: Path, settings: Settings) -> List[BeamJob]:
       """Prepare beam jobs from case directory."""
       # Move prepare_beam_jobs from dispatcher.py, return BeamJob list
   ```

#### Re-exports in dispatcher.py:
```python
# Backward compatibility - re-export from case_aggregator
from src.core.case_aggregator import (
    run_csv_interpreting as run_case_level_csv_interpreting,
    run_tps_generation as run_case_level_tps_generation,
    run_file_upload as run_case_level_upload,
    prepare_beam_jobs,
    handle_case_error as _handle_dispatcher_error,
    ensure_logger as _ensure_logger,
    queue_case as _queue_case,
)
```

#### Impact:
- case_aggregator.py grows from 63 lines to ~350-400 lines (still manageable)
- dispatcher.py shrinks from 638 lines to ~250-300 lines (60% reduction)
- Clear separation: case_aggregator handles case-level logic, dispatcher handles orchestration

---

## Phase 4: Move File Watching to Workflow Manager (workflow_manager.py)

### Goal: Move file system watching logic from dispatcher.py

#### Actions:
1. **Move CaseDetectionHandler class to workflow_manager.py:**
   ```python
   class CaseDetectionHandler(FileSystemEventHandler):
       """Handles file system events for new case detection."""
       # Move from dispatcher.py
   ```

2. **Move scan_existing_cases function to workflow_manager.py:**
   ```python
   def scan_existing_cases(
       scan_directory: Path,
       case_queue: mp.Queue,
       settings: Settings,
       logger: StructuredLogger
   ) -> None:
       """Scan for existing cases at startup."""
       # Move from dispatcher.py
   ```

#### Re-exports in dispatcher.py:
```python
from src.core.workflow_manager import CaseDetectionHandler, scan_existing_cases
```

#### Impact:
- workflow_manager.py grows from 125 lines to ~220-250 lines
- dispatcher.py reduces by another ~80-100 lines
- Logical grouping: workflow_manager handles case discovery and workflow orchestration

---

## Phase 5: Split Case Repository (case_repo.py)

### Goal: Reduce case_repo.py by separating concerns and extracting utilities

#### Actions:

1. **Extract DTOs mapping utilities to models.py:**
   ```python
   # Add to models.py
   @staticmethod
   def map_row_to_case_data(row: Dict[str, Any]) -> CaseData:
       """Map database row to CaseData DTO."""
       # Move _map_row_to_case_data from case_repo.py

   @staticmethod
   def map_row_to_beam_data(row: Dict[str, Any]) -> BeamData:
       """Map database row to BeamData DTO."""
       # Move _map_row_to_beam_data from case_repo.py
   ```

2. **Extract query building helpers to BaseRepository (repositories/base.py):**
   ```python
   def _build_update_query(
       self,
       table: str,
       set_fields: Dict[str, Any],
       where_field: str
   ) -> Tuple[str, Tuple]:
       """Build UPDATE query with optional fields."""
       set_clauses = [f"{key} = ?" for key in set_fields.keys()]
       set_clauses.append("updated_at = CURRENT_TIMESTAMP")
       params = list(set_fields.values())
       query = f"UPDATE {table} SET {', '.join(set_clauses)} WHERE {where_field} = ?"
       return query, tuple(params)
   ```

3. **Create BeamRepository mixin in case_repo.py:**
   ```python
   class BeamRepositoryMixin:
       """Mixin for beam-specific operations."""

       def add_beam(self, beam_id: str, parent_case_id: str, beam_path: Path) -> None:
           # Keep all beam CRUD methods here

       def update_beam_status(self, beam_id: str, status: BeamStatus, ...) -> None:
           pass

       def get_beam(self, beam_id: str) -> Optional[BeamData]:
           pass

       def get_beams_for_case(self, case_id: str) -> List[BeamData]:
           pass
   ```

4. **Create WorkflowRepositoryMixin in case_repo.py:**
   ```python
   class WorkflowRepositoryMixin:
       """Mixin for workflow step tracking."""

       def record_workflow_step(self, case_id: str, step: WorkflowStep, ...) -> None:
           pass

       def get_workflow_steps(self, case_id: str) -> List[WorkflowStepRecord]:
           pass
   ```

5. **Refactor CaseRepository to use mixins:**
   ```python
   class CaseRepository(BaseRepository, BeamRepositoryMixin, WorkflowRepositoryMixin):
       """Main repository composing all data access concerns."""

       # Keep only case-specific CRUD methods here
       def add_case(self, case_id: str, case_path: Path) -> None:
           pass

       def get_case(self, case_id: str) -> Optional[CaseData]:
           pass

       def update_case_status(self, case_id: str, status: CaseStatus, ...) -> None:
           pass
   ```

#### Impact:
- case_repo.py reduces from 662 lines to ~400-450 lines (30-40% reduction)
- Better separation of concerns with clear mixin boundaries
- models.py grows to ~120-150 lines (still reasonable for DTOs + mappers)
- Maintains full backward compatibility through composition

---

## Phase 6: Refactor Main Application (main.py)

### Goal: Extract application initialization and service management

#### Actions:

1. **Extract signal handling to case_aggregator.py:**
   ```python
   class SignalHandler:
       """Handles system signals for graceful shutdown."""
       # Move signal handling logic from main.py
   ```

2. **Extract service initialization helpers to workflow_manager.py:**
   ```python
   def init_file_watcher(
       scan_directory: Path,
       case_queue: mp.Queue,
       logger: StructuredLogger
   ) -> Observer:
       """Initialize and start file watcher."""
       # Extract from main.py's start_file_watcher

   def init_worker_executor(max_workers: int) -> ProcessPoolExecutor:
       """Initialize worker process pool."""
       # Extract from main.py

   def init_gpu_monitor(
       settings: Settings,
       logger: StructuredLogger,
       ssh_client: Optional[paramiko.SSHClient]
   ) -> Tuple[Optional[GpuMonitor], Optional[DatabaseConnection]]:
       """Initialize GPU monitoring service."""
       # Extract from main.py
   ```

3. **Simplify MQIApplication class:**
   - Keep MQIApplication as coordinator
   - Delegate initialization to extracted functions
   - Keep only high-level workflow and lifecycle management

#### Impact:
- main.py reduces from 611 lines to ~350-400 lines (35-40% reduction)
- workflow_manager.py grows to ~350-400 lines (still under 450 lines)
- Clear separation: main.py orchestrates, workflow_manager provides services
- Better testability through extracted functions

---

## Phase 7: Optional Enhancements

### Decorator for Workflow Steps

Add to `case_aggregator.py` or `db_context.py`:

```python
def workflow_step(
    step: WorkflowStep,
    handler_name: str = "CsvInterpreter",
    progress_start: Optional[float] = None,
    progress_done: Optional[float] = None
):
    """Decorator to standardize workflow step execution with logging and error handling."""
    def decorator(func):
        @wraps(func)
        def wrapper(case_id: str, case_path: Path, settings: Settings, *args, **kwargs) -> bool:
            logger = ensure_logger(f"workflow_{case_id}", settings)

            try:
                # Record step start
                with get_db_session(settings, logger, handler_name) as case_repo:
                    case_repo.record_workflow_step(case_id, step, "started")
                    if progress_start is not None:
                        case_repo.update_case_status(case_id, CaseStatus.PROCESSING, progress_start)

                # Execute function
                result = func(case_id, case_path, settings, *args, **kwargs)

                # Record completion
                with get_db_session(settings, logger, handler_name) as case_repo:
                    case_repo.record_workflow_step(case_id, step, "completed")
                    if progress_done is not None:
                        case_repo.update_case_status(case_id, CaseStatus.PROCESSING, progress_done)

                return result

            except Exception as e:
                handle_case_error(case_id, e, step, settings, logger, handler_name)
                return False

        return wrapper
    return decorator
```

**Usage:**
```python
@workflow_step(WorkflowStep.CSV_INTERPRETING, progress_start=10.0, progress_done=25.0)
def run_csv_interpreting(case_id: str, case_path: Path, settings: Settings) -> bool:
    # Just business logic, no boilerplate
    execution_handler = ExecutionHandler(settings=settings, mode="local")
    result = execution_handler.execute_command(...)
    return result.success
```

**Impact:**
- Removes ~15-20 lines of boilerplate per function
- Can reduce case_aggregator.py by additional 60-80 lines
- Standardizes error handling across all case-level operations

---

## Migration Strategy

### Phase-by-Phase Rollout (Safe Approach)

1. **Phase 1-2 (Low Risk):** Add new models and utilities without removing anything
   - Add BeamJob, CaseJob to models.py
   - Add utilities to db_context.py
   - Update dispatcher.py to use new utilities alongside old code
   - Run full test suite

2. **Phase 3-4 (Medium Risk):** Move functions with re-exports
   - Move case-level functions to case_aggregator.py
   - Move watcher to workflow_manager.py
   - Keep re-exports in dispatcher.py
   - All existing imports continue to work
   - Run full test suite

3. **Phase 5 (Medium Risk):** Refactor case_repo.py with mixins
   - Extract mappers to models.py
   - Create mixins within case_repo.py
   - CaseRepository interface remains identical
   - Run full test suite

4. **Phase 6 (Medium Risk):** Simplify main.py
   - Extract initialization helpers
   - Simplify MQIApplication
   - Run full integration tests

5. **Phase 7 (Optional):** Add decorator for further cleanup

### Backward Compatibility Guarantee

- **All external imports remain valid** through re-exports
- **All function signatures unchanged**
- **All test suites pass without modification**
- Deprecation warnings can be added later if desired

---

## Expected Results

### Line Count Reduction

| File | Before | After | Reduction |
|------|--------|-------|-----------|
| dispatcher.py | 638 | ~250-300 | 50-60% |
| case_repo.py | 662 | ~400-450 | 30-40% |
| main.py | 611 | ~350-400 | 35-40% |
| **Total** | **1,911** | **~1,000-1,150** | **~40-47%** |

### Module Growth (Still Manageable)

| File | Before | After | Growth |
|------|--------|-------|--------|
| models.py | 73 | ~120-150 | Acceptable for DTOs |
| db_context.py | 52 | ~120-150 | Acceptable for utilities |
| case_aggregator.py | 63 | ~350-400 | Primary case logic |
| workflow_manager.py | 125 | ~350-400 | Workflow + watching |

### Benefits

1. **Clearer Separation of Concerns**
   - case_aggregator.py: Case-level business logic
   - workflow_manager.py: Workflow orchestration and file watching
   - models.py: All DTOs and mapping logic
   - db_context.py: Database session and common utilities
   - case_repo.py: Pure data access with clear mixin boundaries

2. **No New Files**
   - Uses only existing modules
   - Easier to navigate and understand existing structure

3. **Improved Testability**
   - Extracted functions easier to unit test
   - Mixin pattern allows testing repository concerns independently

4. **Maintainability**
   - No single file over 450 lines
   - Related functions grouped logically
   - Common utilities centralized

5. **Zero Breaking Changes**
   - All imports work unchanged
   - Tests pass without modification
   - Can be deployed incrementally

---

## Testing Strategy

### Unit Tests
- Test new DTOs (BeamJob, CaseJob) independently
- Test extracted utility functions in isolation
- Test repository mixins separately
- Mock dependencies for each extracted function

### Integration Tests
- Verify full workflow still works end-to-end
- Test case processing with real database
- Test file watching and queue operations
- Verify remote/local execution modes

### Regression Tests
- Run entire existing test suite
- Verify no test requires modification
- Confirm all imports still resolve correctly

---

## Success Criteria

- ✅ All three large files reduced by at least 30%
- ✅ No file in src/ exceeds 450 lines
- ✅ All existing tests pass without modification
- ✅ No external import changes required
- ✅ Clear logical grouping of related functions
- ✅ Improved type safety with domain models
- ✅ Reduced code duplication through utilities

---

## Timeline Estimate

- **Phase 1-2:** 2-3 hours (models + utilities)
- **Phase 3-4:** 3-4 hours (move dispatcher functions)
- **Phase 5:** 3-4 hours (refactor case_repo with mixins)
- **Phase 6:** 2-3 hours (simplify main.py)
- **Phase 7:** 1-2 hours (optional decorator)
- **Testing:** 2-3 hours per phase

**Total:** ~15-20 hours for full implementation and testing

---

## Conclusion

This plan achieves 40-47% code reduction across the three longest files by strategically moving functions into existing modules rather than creating new ones. The approach:

- Respects the existing architecture
- Maintains full backward compatibility
- Improves code organization and maintainability
- Enables incremental, low-risk rollout
- Provides clear ownership of responsibilities

All modules remain under 450 lines, with clear logical boundaries and improved separation of concerns.
