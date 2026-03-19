# ptn_checker Integration into mqi_communicator Workflow

## TL;DR

> **Quick Summary**: Integrate ptn_checker to process new .ptn files for completed cases. First-time cases run MC simulation; subsequent runs (when case is COMPLETED and new .ptn files arrive) execute ptn_checker instead.
> 
> **Deliverables**:
> - Database schema extension for ptn_checker tracking
> - New `PtnCheckerState` workflow state
> - Modified file watcher for .ptn file detection
> - ptn_checker module integration
> - Unit and integration tests (TDD)
> 
> **Estimated Effort**: Medium
> **Parallel Execution**: YES - 4 waves
> **Critical Path**: Schema → PtnCheckerState → Integration → File Watcher → Tests

---

## Context

### Original Request
Integrate `~/MOQUI_SMC/ptn_checker` into the workflow so that:
- First time a case is processed → MC simulation runs (existing behavior)
- Subsequent times (when case is COMPLETED and new .ptn files arrive) → ptn_checker runs instead

### Interview Summary
**Key Discussions**:
- **Trigger logic**: Case status check - COMPLETED cases with new .ptn files trigger ptn_checker
- **Output handling**: Store results in `{case_path}/ptn_analysis/`
- **PTN detection**: File watcher monitors case directory for new `.ptn` files
- **DICOM location**: DICOM RTPLAN file is in case directory (`{case_path}/*.dcm`)
- **Beam handling**: Run ptn_checker once per case (processes all beams together)
- **Test strategy**: TDD - write tests first
- **Post-PTN status**: Stay COMPLETED + track runs in database
- **Error handling**: New PTN-failed status field

**Research Findings**:
- mqi_communicator uses SQLite database with `cases` table (`case_id`, `status`, `retry_count`)
- MC simulation triggered in `HpcExecutionState.execute()` in `src/domain/states.py`
- File watcher in `src/core/workflow_manager.py` monitors scan_directory
- ptn_checker at `/home/jokh38/MOQUI_SMC/ptn_checker` can be imported as module via `from main import run_analysis`
- ptn_checker needs: `log_dir` (.ptn files), `dcm_file` (DICOM RTPLAN), `output_dir`

### Metis Review
**Identified Gaps** (addressed):
- **Concurrency policy**: Priority by case status - COMPLETED → ptn_checker, else → MC simulation
- **PTN file batching**: Process all .ptn files in case directory
- **Output management**: Overwrite `ptn_analysis/` directory (single output per run)
- **Error recovery**: Log error, set `ptn_checker_status` to FAILED, no auto-retry
- **Timeout**: Use existing workflow timeout configuration
- **Multiple DICOM files**: Use first `.dcm` file found alphabetically
- **Partial file detection**: File watcher uses creation events (files must be complete)

---

## Work Objectives

### Core Objective
Enable ptn_checker execution for completed cases when new .ptn files arrive, providing an alternative workflow path to MC simulation for treatment verification.

### Concrete Deliverables
- Database schema: New columns `ptn_checker_run_count`, `ptn_checker_last_run_at`, `ptn_checker_status`
- New state: `PtnCheckerState` in `src/domain/states.py`
- Modified file watcher: Detect .ptn files and re-queue COMPLETED cases
- Integration: ptn_checker module import and execution logic
- Output: PDF reports stored in `{case_path}/ptn_analysis/`
- Tests: Unit tests for all components, integration test for full workflow

### Definition of Done
- [ ] TDD: All tests pass before implementation
- [ ] Database migration: Schema changes apply without data loss
- [ ] Workflow: COMPLETED case + new .ptn files → ptn_checker runs
- [ ] Workflow: Non-COMPLETED case → MC simulation runs (no regression)
- [ ] Output: PDF report generated in correct location
- [ ] Error handling: Failures logged with appropriate status

### Must Have
- Database fields for tracking ptn_checker runs
- Conditional workflow routing based on case status
- ptn_checker module integration
- File watcher detection for .ptn files
- Error handling with distinct PTN-failed status

### Must NOT Have (Guardrails)
- NO modifications to ptn_checker itself (use as-is)
- NO caching layer for ptn_checker results
- NO web UI for viewing results
- NO notification system
- NO real-time progress tracking
- NO distributed/parallel execution
- NO removal of existing MC simulation workflow
- NO storing ptn_checker output in database (filesystem only)

---

## Verification Strategy (MANDATORY)

> **ZERO HUMAN INTERVENTION** — ALL verification is agent-executed. No exceptions.

### Test Decision
- **Infrastructure exists**: YES (project has pytest)
- **Automated tests**: TDD (write tests first, then implement)
- **Framework**: pytest
- **TDD Flow**: Each task follows RED (failing test) → GREEN (minimal impl) → REFACTOR

### QA Policy
Every task MUST include agent-executed QA scenarios.
Evidence saved to `.sisyphus/evidence/task-{N}-{scenario-slug}.{ext}`.

- **Backend/Python**: Use Bash (pytest) — Run tests, assert pass/fail
- **Database**: Use Bash (sqlite3) — Query schema, verify columns exist
- **File operations**: Use Bash — Create test files, verify outputs

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Start Immediately — database + state foundation):
├── Task 1: Database schema extension [quick]
├── Task 2: Repository methods for PTN tracking [quick]
└── Task 3: PtnCheckerState implementation [quick]

Wave 2 (After Wave 1 — integration):
├── Task 4: ptn_checker module integration [quick]
├── Task 5: Workflow routing logic [quick]
└── Task 6: File watcher PTN detection [quick]

Wave 3 (After Wave 2 — error handling + config):
├── Task 7: Error handling implementation [quick]
├── Task 8: Configuration additions [quick]
└── Task 9: Integration tests [unspecified-high]

Wave FINAL (After ALL tasks — verification):
├── Task F1: Plan compliance audit (oracle)
├── Task F2: Code quality review (unspecified-high)
├── Task F3: Real manual QA (unspecified-high)
└── Task F4: Scope fidelity check (deep)
-> Present results -> Get explicit user okay

Critical Path: Task 1 → Task 2 → Task 4 → Task 5 → Task 9 → F1-F4 → user okay
Parallel Speedup: ~50% faster than sequential
Max Concurrent: 3 (Waves 1 & 2)
```

### Dependency Matrix

- **1**: — — 2, 3
- **2**: 1 — 4, 5
- **3**: — 5, 6
- **4**: 2 — 5, 7
- **5**: 2, 3 — 7, 9
- **6**: 3 — 9
- **7**: 4, 5 — 9
- **8**: — 9
- **9**: 5, 6, 7, 8 — F1-F4

### Agent Dispatch Summary

- **Wave 1**: 3 agents — T1-T2 → `quick`, T3 → `quick`
- **Wave 2**: 3 agents — T4-T6 → `quick`
- **Wave 3**: 3 agents — T7-T8 → `quick`, T9 → `unspecified-high`
- **FINAL**: 4 agents — F1 → `oracle`, F2-F3 → `unspecified-high`, F4 → `deep`

---

## TODOs

- [ ] 1. **Database Schema Extension**

  **What to do**:
  - Add new columns to `cases` table in `src/database/connection.py`:
    - `ptn_checker_run_count INTEGER DEFAULT 0`
    - `ptn_checker_last_run_at TIMESTAMP`
    - `ptn_checker_status TEXT` (values: NULL, 'SUCCESS', 'FAILED', 'FAILED_NO_DICOM', 'FAILED_NO_PTN', 'FAILED_EXCEPTION')
  - Write migration logic in `_init_db()` to handle existing databases (ALTER TABLE)
  - Write test: `tests/unit/test_database.py::test_ptn_checker_columns_exist`

  **Must NOT do**:
  - Do NOT add indexes (not needed for initial implementation)
  - Do NOT modify existing columns
  - Do NOT add foreign key constraints

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Single file modification, well-defined schema change
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO (foundation for other tasks)
  - **Parallel Group**: Wave 1
  - **Blocks**: Task 2
  - **Blocked By**: None

  **References**:
  - `src/database/connection.py:40-80` - Existing schema definition and `_init_db()` pattern
  - `src/database/connection.py:CASES_TABLE_SQL` - Current cases table schema

  **Acceptance Criteria**:
  - [ ] Test written first: `tests/unit/test_database.py::test_ptn_checker_columns_exist`
  - [ ] `pytest tests/unit/test_database.py::test_ptn_checker_columns_exist` → PASS
  - [ ] New columns added to CREATE TABLE statement
  - [ ] ALTER TABLE logic handles existing databases

  **QA Scenarios**:
  ```
  Scenario: Database schema has ptn_checker columns
    Tool: Bash (sqlite3)
    Preconditions: Fresh database created
    Steps:
      1. sqlite3 data/mqi_communicator.db ".schema cases"
      2. grep for "ptn_checker_run_count"
      3. grep for "ptn_checker_last_run_at"
      4. grep for "ptn_checker_status"
    Expected Result: All three columns present in schema
    Evidence: .sisyphus/evidence/task-1-schema-verification.txt

  Scenario: Migration works on existing database
    Tool: Bash (sqlite3)
    Preconditions: Database exists without new columns
    Steps:
      1. sqlite3 data/mqi_communicator.db "SELECT ptn_checker_run_count FROM cases LIMIT 1"
    Expected Result: Column exists (may be empty table)
    Evidence: .sisyphus/evidence/task-1-migration-test.txt
  ```

  **Commit**: YES
  - Message: `feat(db): add ptn_checker tracking columns to cases table`
  - Files: `src/database/connection.py`, `tests/unit/test_database.py`

---

- [ ] 2. **Repository Methods for PTN Tracking**

  **What to do**:
  - Add methods to `CaseRepository` in `src/repositories/case_repo.py`:
    - `increment_ptn_checker_run_count(case_id: str) -> None`
    - `update_ptn_checker_status(case_id: str, status: str) -> None`
    - `update_ptn_checker_last_run(case_id: str, timestamp: datetime) -> None`
    - `get_ptn_checker_info(case_id: str) -> dict` (returns run_count, last_run, status)
  - Write tests in `tests/unit/test_case_repo.py`

  **Must NOT do**:
  - Do NOT modify existing repository methods
  - Do NOT add complex queries or joins

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Straightforward CRUD operations
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO (depends on Task 1)
  - **Parallel Group**: Wave 1 (after Task 1)
  - **Blocks**: Task 4
  - **Blocked By**: Task 1

  **References**:
  - `src/repositories/case_repo.py:50-150` - Existing repository method patterns
  - `src/repositories/case_repo.py:increment_retry_count()` - Similar pattern to follow

  **Acceptance Criteria**:
  - [ ] Test: `tests/unit/test_case_repo.py::test_increment_ptn_checker_run_count`
  - [ ] Test: `tests/unit/test_case_repo.py::test_update_ptn_checker_status`
  - [ ] Test: `tests/unit/test_case_repo.py::test_get_ptn_checker_info`
  - [ ] `pytest tests/unit/test_case_repo.py` → PASS

  **QA Scenarios**:
  ```
  Scenario: Repository methods work correctly
    Tool: Bash (pytest)
    Preconditions: Test database with sample case
    Steps:
      1. pytest tests/unit/test_case_repo.py::test_increment_ptn_checker_run_count -v
      2. pytest tests/unit/test_case_repo.py::test_update_ptn_checker_status -v
      3. pytest tests/unit/test_case_repo.py::test_get_ptn_checker_info -v
    Expected Result: All tests pass
    Evidence: .sisyphus/evidence/task-2-repo-tests.txt
  ```

  **Commit**: YES
  - Message: `feat(repo): add ptn_checker repository methods`
  - Files: `src/repositories/case_repo.py`, `tests/unit/test_case_repo.py`

---

- [ ] 3. **PtnCheckerState Implementation**

  **What to do**:
  - Create new `PtnCheckerState` class in `src/domain/states.py`:
    - Inherit from `WorkflowState` base class
    - Implement `execute(handler, beam, case_repo, settings)` method
    - Find DICOM RTPLAN file in case directory (first .dcm file)
    - Find all .ptn files in case directory
    - Call ptn_checker integration module
    - Store output in `{case_path}/ptn_analysis/`
    - Update database via repository methods
    - Handle errors (no DICOM, no PTN files, exceptions)
  - Write tests in `tests/unit/test_states.py`

  **Must NOT do**:
  - Do NOT modify existing state classes
  - Do NOT add caching or optimization
  - Do NOT implement progress tracking

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Following existing state pattern
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Tasks 1, 2)
  - **Parallel Group**: Wave 1
  - **Blocks**: Task 5
  - **Blocked By**: None (but needs Task 2 for full integration)

  **References**:
  - `src/domain/states.py:179-313` - `HpcExecutionState.execute()` pattern to follow
  - `src/domain/states.py:1-50` - `WorkflowState` base class and state patterns
  - `src/domain/states.py:CompletedState` - Success completion pattern
  - `src/domain/states.py:FailedState` - Failure handling pattern

  **Acceptance Criteria**:
  - [ ] Test: `tests/unit/test_states.py::test_ptn_checker_state_execute_success`
  - [ ] Test: `tests/unit/test_states.py::test_ptn_checker_state_no_dicom`
  - [ ] Test: `tests/unit/test_states.py::test_ptn_checker_state_no_ptn_files`
  - [ ] `pytest tests/unit/test_states.py` → PASS

  **QA Scenarios**:
  ```
  Scenario: PtnCheckerState executes successfully
    Tool: Bash (pytest)
    Preconditions: Mock ptn_checker module, test case with DICOM and PTN files
    Steps:
      1. pytest tests/unit/test_states.py::test_ptn_checker_state_execute_success -v
    Expected Result: Test passes, output directory created
    Evidence: .sisyphus/evidence/task-3-state-success.txt

  Scenario: PtnCheckerState handles missing DICOM
    Tool: Bash (pytest)
    Preconditions: Test case without DICOM file
    Steps:
      1. pytest tests/unit/test_states.py::test_ptn_checker_state_no_dicom -v
    Expected Result: Test passes, status set to FAILED_NO_DICOM
    Evidence: .sisyphus/evidence/task-3-state-no-dicom.txt
  ```

  **Commit**: YES
  - Message: `feat(workflow): add PtnCheckerState for PTN analysis`
  - Files: `src/domain/states.py`, `tests/unit/test_states.py`

---

- [ ] 4. **ptn_checker Module Integration**

  **What to do**:
  - Create new file `src/integrations/ptn_checker.py`:
    - Function `run_ptn_checker(log_dir: str, dcm_file: str, output_dir: str) -> bool`
    - Import ptn_checker: `sys.path.insert(0, '/home/jokh38/MOQUI_SMC/ptn_checker')`
    - Call `from main import run_analysis`
    - Wrap in try/except to catch FileNotFoundError, ValueError
    - Return True on success, False on failure
    - Log errors appropriately
  - Write tests with mock ptn_checker in `tests/unit/test_ptn_checker_integration.py`

  **Must NOT do**:
  - Do NOT modify ptn_checker itself
  - Do NOT add subprocess execution (use module import)
  - Do NOT add caching

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Simple wrapper/integration code
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO (depends on Task 2)
  - **Parallel Group**: Wave 2
  - **Blocks**: Task 5
  - **Blocked By**: Task 2

  **References**:
  - `/home/jokh38/MOQUI_SMC/ptn_checker/main.py:run_analysis()` - Function to call
  - `/home/jokh38/MOQUI_SMC/ptn_checker/main.py:1-50` - Entry point and CLI structure

  **Acceptance Criteria**:
  - [ ] Test: `tests/unit/test_ptn_checker_integration.py::test_run_ptn_checker_success`
  - [ ] Test: `tests/unit/test_ptn_checker_integration.py::test_run_ptn_checker_missing_dicom`
  - [ ] Test: `tests/unit/test_ptn_checker_integration.py::test_run_ptn_checker_missing_ptn`
  - [ ] `pytest tests/unit/test_ptn_checker_integration.py` → PASS

  **QA Scenarios**:
  ```
  Scenario: ptn_checker integration works
    Tool: Bash (pytest)
    Preconditions: Mock ptn_checker module
    Steps:
      1. pytest tests/unit/test_ptn_checker_integration.py -v
    Expected Result: All tests pass
    Evidence: .sisyphus/evidence/task-4-integration-tests.txt
  ```

  **Commit**: YES
  - Message: `feat(integration): integrate ptn_checker module`
  - Files: `src/integrations/ptn_checker.py`, `tests/unit/test_ptn_checker_integration.py`

---

- [ ] 5. **Workflow Routing Logic**

  **What to do**:
  - Modify `WorkflowManager` in `src/core/workflow_manager.py`:
    - Add logic to determine which state to use based on case status
    - If case status is COMPLETED and has .ptn files → use `PtnCheckerState`
    - Otherwise → use `HpcExecutionState` (existing behavior)
  - Add method `should_use_ptn_checker(case_id: str) -> bool`
  - Check for .ptn files in case directory
  - Write tests in `tests/unit/test_workflow_manager.py`

  **Must NOT do**:
  - Do NOT remove existing MC simulation logic
  - Do NOT change behavior for non-COMPLETED cases

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Conditional logic addition
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO (depends on Tasks 2, 3)
  - **Parallel Group**: Wave 2
  - **Blocks**: Task 7
  - **Blocked By**: Task 2, Task 3

  **References**:
  - `src/core/workflow_manager.py:run_workflow()` - Where routing decision happens
  - `src/domain/states.py:InitialState` - Where to add routing logic

  **Acceptance Criteria**:
  - [ ] Test: `tests/unit/test_workflow_manager.py::test_routing_uses_ptn_checker_for_completed_case`
  - [ ] Test: `tests/unit/test_workflow_manager.py::test_routing_uses_mc_for_new_case`
  - [ ] Test: `tests/unit/test_workflow_manager.py::test_routing_uses_mc_for_non_completed_case`
  - [ ] `pytest tests/unit/test_workflow_manager.py` → PASS

  **QA Scenarios**:
  ```
  Scenario: Routing chooses ptn_checker for completed case
    Tool: Bash (pytest)
    Preconditions: Test case with COMPLETED status and .ptn files
    Steps:
      1. pytest tests/unit/test_workflow_manager.py::test_routing_uses_ptn_checker_for_completed_case -v
    Expected Result: Test passes, PtnCheckerState selected
    Evidence: .sisyphus/evidence/task-5-routing-ptn.txt

  Scenario: Routing chooses MC for new case
    Tool: Bash (pytest)
    Preconditions: Test case with NEW status
    Steps:
      1. pytest tests/unit/test_workflow_manager.py::test_routing_uses_mc_for_new_case -v
    Expected Result: Test passes, HpcExecutionState selected
    Evidence: .sisyphus/evidence/task-5-routing-mc.txt
  ```

  **Commit**: YES
  - Message: `feat(workflow): add conditional routing for MC vs PTN`
  - Files: `src/core/workflow_manager.py`, `tests/unit/test_workflow_manager.py`

---

- [ ] 6. **File Watcher PTN Detection**

  **What to do**:
  - Modify `CaseDetectionHandler` in `src/core/workflow_manager.py`:
    - Add detection for `.ptn` file creation events
    - When .ptn file detected in COMPLETED case directory → re-queue case
    - Add debouncing to prevent multiple queue events for same case
    - Use case_id extraction from file path
  - Write tests in `tests/unit/test_file_watcher.py`

  **Must NOT do**:
  - Do NOT change detection for new cases (directories)
  - Do NOT add real-time progress tracking
  - Do NOT monitor subdirectories

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Adding event type to existing watcher
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Tasks 4, 5)
  - **Parallel Group**: Wave 2
  - **Blocks**: Task 9
  - **Blocked By**: Task 3

  **References**:
  - `src/core/workflow_manager.py:CaseDetectionHandler` - Existing file watcher pattern
  - `src/core/workflow_manager.py:on_created()` - Event handler to extend

  **Acceptance Criteria**:
  - [ ] Test: `tests/unit/test_file_watcher.py::test_ptn_file_detection`
  - [ ] Test: `tests/unit/test_file_watcher.py::test_ptn_file_requeues_completed_case`
  - [ ] Test: `tests/unit/test_file_watcher.py::test_ptn_file_ignored_for_non_completed_case`
  - [ ] `pytest tests/unit/test_file_watcher.py` → PASS

  **QA Scenarios**:
  ```
  Scenario: File watcher detects .ptn file
    Tool: Bash (pytest)
    Preconditions: Mock file system with .ptn file creation event
    Steps:
      1. pytest tests/unit/test_file_watcher.py::test_ptn_file_detection -v
    Expected Result: Test passes, event detected
    Evidence: .sisyphus/evidence/task-6-watcher-detection.txt

  Scenario: Completed case re-queued on .ptn arrival
    Tool: Bash (pytest)
    Preconditions: COMPLETED case in database
    Steps:
      1. pytest tests/unit/test_file_watcher.py::test_ptn_file_requeues_completed_case -v
    Expected Result: Test passes, case added to queue
    Evidence: .sisyphus/evidence/task-6-watcher-requeue.txt
  ```

  **Commit**: YES
  - Message: `feat(watcher): add .ptn file detection for completed cases`
  - Files: `src/core/workflow_manager.py`, `tests/unit/test_file_watcher.py`

---

- [ ] 7. **Error Handling Implementation**

  **What to do**:
  - Add comprehensive error handling in `PtnCheckerState`:
    - `FAILED_NO_DICOM`: No .dcm file found in case directory
    - `FAILED_NO_PTN`: No .ptn files found in case directory
    - `FAILED_EXCEPTION`: ptn_checker raised exception (log traceback)
    - `FAILED_LOCKED`: Case directory locked by another process (optional)
  - Ensure errors are logged with structured logging
  - Write tests for each error scenario

  **Must NOT do**:
  - Do NOT add automatic retry logic
  - Do NOT add notification system
  - Do NOT clean up partial outputs on failure (keep for debugging)

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Adding error cases to existing state
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO (depends on Tasks 4, 5)
  - **Parallel Group**: Wave 3
  - **Blocks**: Task 9
  - **Blocked By**: Task 4, Task 5

  **References**:
  - `src/domain/states.py:FailedState` - Error handling pattern
  - `src/domain/states.py:HpcExecutionState` - Error handling examples

  **Acceptance Criteria**:
  - [ ] Test: `tests/unit/test_states.py::test_ptn_checker_error_no_dicom`
  - [ ] Test: `tests/unit/test_states.py::test_ptn_checker_error_no_ptn`
  - [ ] Test: `tests/unit/test_states.py::test_ptn_checker_error_exception`
  - [ ] `pytest tests/unit/test_states.py` → PASS

  **QA Scenarios**:
  ```
  Scenario: Error handling sets correct status
    Tool: Bash (pytest)
    Preconditions: Various error conditions mocked
    Steps:
      1. pytest tests/unit/test_states.py::test_ptn_checker_error_no_dicom -v
      2. pytest tests/unit/test_states.py::test_ptn_checker_error_no_ptn -v
      3. pytest tests/unit/test_states.py::test_ptn_checker_error_exception -v
    Expected Result: All tests pass, correct status set
    Evidence: .sisyphus/evidence/task-7-error-handling.txt
  ```

  **Commit**: YES
  - Message: `feat(error): add PTN-specific error handling`
  - Files: `src/domain/states.py`, `tests/unit/test_states.py`

---

- [ ] 8. **Configuration Additions**

  **What to do**:
  - Add ptn_checker configuration to `config/config.yaml`:
    - `ptn_checker.path`: Path to ptn_checker directory
    - `ptn_checker.output_subdir`: Output subdirectory name (default: "ptn_analysis")
    - `ptn_checker.timeout`: Timeout in seconds (optional)
  - Update `src/core/settings.py` to load new configuration
  - Write tests for configuration loading

  **Must NOT do**:
  - Do NOT add complex validation
  - Do NOT add default value overrides

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Simple configuration addition
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Tasks 7)
  - **Parallel Group**: Wave 3
  - **Blocks**: Task 9
  - **Blocked By**: None

  **References**:
  - `config/config.yaml` - Existing configuration structure
  - `src/core/settings.py` - Settings loading pattern

  **Acceptance Criteria**:
  - [ ] Test: `tests/unit/test_settings.py::test_ptn_checker_config_loaded`
  - [ ] Configuration file has ptn_checker section
  - [ ] Settings class exposes ptn_checker config

  **QA Scenarios**:
  ```
  Scenario: Configuration loaded correctly
    Tool: Bash (pytest)
    Preconditions: config.yaml with ptn_checker section
    Steps:
      1. pytest tests/unit/test_settings.py::test_ptn_checker_config_loaded -v
    Expected Result: Test passes, config values accessible
    Evidence: .sisyphus/evidence/task-8-config.txt
  ```

  **Commit**: YES
  - Message: `feat(config): add ptn_checker configuration`
  - Files: `config/config.yaml`, `src/core/settings.py`, `tests/unit/test_settings.py`

---

- [ ] 9. **Integration Tests**

  **What to do**:
  - Create integration test file `tests/integration/test_ptn_checker_workflow.py`:
    - Test full workflow: COMPLETED case + new .ptn files → ptn_checker runs
    - Test full workflow: NEW case → MC simulation runs (no regression)
    - Test database updates after ptn_checker execution
    - Test output file generation in correct location
  - Use test fixtures for mock ptn_checker and sample files

  **Must NOT do**:
  - Do NOT test with real MC simulation (too slow)
  - Do NOT use real medical data

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Integration testing requires understanding full system
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO (depends on all previous tasks)
  - **Parallel Group**: Wave 3 (after all implementation)
  - **Blocks**: F1-F4
  - **Blocked By**: Task 5, Task 6, Task 7, Task 8

  **References**:
  - `tests/` - Existing test patterns
  - `src/core/workflow_manager.py` - Full workflow to test

  **Acceptance Criteria**:
  - [ ] Test: `tests/integration/test_ptn_checker_workflow.py::test_full_ptn_workflow`
  - [ ] Test: `tests/integration/test_ptn_checker_workflow.py::test_mc_workflow_unchanged`
  - [ ] `pytest tests/integration/test_ptn_checker_workflow.py` → PASS
  - [ ] All tests complete in < 10 seconds

  **QA Scenarios**:
  ```
  Scenario: Full integration test passes
    Tool: Bash (pytest)
    Preconditions: All components implemented
    Steps:
      1. pytest tests/integration/test_ptn_checker_workflow.py -v
    Expected Result: All tests pass
    Evidence: .sisyphus/evidence/task-9-integration.txt

  Scenario: MC workflow unchanged (regression test)
    Tool: Bash (pytest)
    Preconditions: NEW case without .ptn files
    Steps:
      1. pytest tests/integration/test_ptn_checker_workflow.py::test_mc_workflow_unchanged -v
    Expected Result: Test passes, HpcExecutionState used
    Evidence: .sisyphus/evidence/task-9-regression.txt
  ```

  **Commit**: YES
  - Message: `test: add integration tests for ptn_checker workflow`
  - Files: `tests/integration/test_ptn_checker_workflow.py`

---

## Final Verification Wave (MANDATORY)

- [ ] F1. **Plan Compliance Audit** — `oracle`
  Read the plan end-to-end. For each "Must Have": verify implementation exists. For each "Must NOT Have": search codebase for forbidden patterns. Check evidence files exist. Compare deliverables against plan.
  Output: `Must Have [N/N] | Must NOT Have [N/N] | Tasks [N/N] | VERDICT: APPROVE/REJECT`

- [ ] F2. **Code Quality Review** — `unspecified-high`
  Run `pytest` + linter. Review all changed files for: `as any`/`@ts-ignore`, empty catches, unused imports. Check AI slop: excessive comments, over-abstraction, generic names.
  Output: `Tests [N pass/N fail] | Lint [PASS/FAIL] | Files [N clean/N issues] | VERDICT`

- [ ] F3. **Real Manual QA** — `unspecified-high`
  Execute EVERY QA scenario from EVERY task. Test cross-task integration. Test edge cases. Save to `.sisyphus/evidence/final-qa/`.
  Output: `Scenarios [N/N pass] | Integration [N/N] | Edge Cases [N tested] | VERDICT`

- [ ] F4. **Scope Fidelity Check** — `deep`
  For each task: read "What to do", read actual diff. Verify 1:1 — everything in spec was built, nothing beyond spec. Check "Must NOT do" compliance.
  Output: `Tasks [N/N compliant] | Unaccounted [CLEAN/N files] | VERDICT`

---

## Commit Strategy

- **1**: `feat(db): add ptn_checker tracking columns to cases table` — src/database/connection.py
- **2**: `feat(repo): add ptn_checker repository methods` — src/repositories/case_repo.py
- **3**: `feat(workflow): add PtnCheckerState for PTN analysis` — src/domain/states.py
- **4**: `feat(integration): integrate ptn_checker module` — src/integrations/ptn_checker.py
- **5**: `feat(workflow): add conditional routing for MC vs PTN` — src/domain/states.py
- **6**: `feat(watcher): add .ptn file detection for completed cases` — src/core/workflow_manager.py
- **7**: `feat(error): add PTN-specific error handling` — src/domain/states.py
- **8**: `feat(config): add ptn_checker configuration` — config/config.yaml
- **9**: `test: add integration tests for ptn_checker workflow` — tests/

---

## Success Criteria

### Verification Commands
```bash
# Database schema verification
sqlite3 data/mqi_communicator.db ".schema cases" | grep ptn_checker

# Test execution
pytest tests/ -v

# Integration test
pytest tests/integration/test_ptn_checker_workflow.py -v
```

### Final Checklist
- [ ] All "Must Have" present
- [ ] All "Must NOT Have" absent
- [ ] All tests pass
- [ ] COMPLETED case + .ptn files → ptn_checker runs
- [ ] Non-COMPLETED case → MC simulation runs (no regression)
