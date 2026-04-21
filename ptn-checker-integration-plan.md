# ptn_checker Integration into mqi_communicator Workflow

## TL;DR

> **Quick Summary**: Integrate `ptn_checker` as a case-level follow-up path for already `COMPLETED` cases when new, stable `.ptn` files arrive. Do not force PTN analysis through the existing beam state machine.
>
> **Deliverables**:
> - Database schema extension for PTN run tracking
> - Case-level PTN analysis runner
> - Structured `ptn_checker` integration wrapper
> - Dispatcher and watcher changes for completed-case PTN reprocessing
> - Unit and integration tests using `python -m pytest`
>
> **Estimated Effort**: Medium
> **Parallel Execution**: YES - 3 waves
> **Critical Path**: Schema -> Repository -> PTN runner -> Integration wrapper -> Dispatcher/watcher -> Tests

---

## Context

### Original Request
Integrate `~/MOQUI_SMC/ptn_checker` into the workflow so that:
- First time a case is processed -> MC simulation runs
- Subsequent times, when the case is already `COMPLETED` and new `.ptn` files arrive -> `ptn_checker` runs instead

### Architecture Constraints
- The current worker path is beam-oriented: dispatcher creates beam jobs, workers construct `WorkflowManager`, and states execute per beam
- PTN analysis is case-level, not beam-level
- Reprocessing a completed case therefore must happen in dispatcher/case-entry logic, not by adding a new beam `WorkflowState`
- Existing RTPLAN discovery should be reused instead of selecting an arbitrary `.dcm` file

### Research Findings
- Database schema is initialized in `src/database/connection.py` via `init_db()`
- Case routing and startup scans are handled in `src/core/dispatcher.py` and `src/core/workflow_manager.py`
- Beam workflow states live in `src/domain/states.py` and should remain dedicated to MC execution
- `ptn_checker` exposes `run_analysis(log_dir, dcm_file, output_dir)` from `/home/SMC/MOQUI_SMC/ptn_checker/main.py`
- The repo uses a flat `tests/` layout, not `tests/unit/` and `tests/integration/`
- Settings live in `src/config/settings.py`

### Design Decisions
- **Execution boundary**: PTN analysis runs as a case-level path
- **Case status policy**: case stays `COMPLETED`; PTN outcome is tracked in dedicated PTN columns
- **RTPLAN discovery**: use existing validator/discovery logic
- **PTN readiness**: only process files after debounce and file stability checks
- **Failure reporting**: use structured PTN result codes, not a boolean-only wrapper
- **Output handling**: write current run outputs to `{case_path}/ptn_analysis/`

---

## Work Objectives

### Core Objective
Enable safe case-level PTN analysis for completed cases when new `.ptn` files arrive, without regressing the existing MC beam workflow.

### Concrete Deliverables
- Database columns:
  - `ptn_checker_run_count INTEGER DEFAULT 0`
  - `ptn_checker_last_run_at TIMESTAMP`
  - `ptn_checker_status TEXT`
- Repository support for PTN tracking on cases
- Case-level PTN analysis runner in dispatcher/case-handling code
- `src/integrations/ptn_checker.py` wrapper returning a structured result
- Watcher support for `.ptn` arrivals on completed cases
- Tests covering schema, repository, routing, integration, watcher behavior, and regression

### Definition of Done
- [ ] Database migration applies without data loss
- [ ] `COMPLETED` case + stable new `.ptn` files -> PTN analysis runs
- [ ] Non-`COMPLETED` cases keep existing MC behavior
- [ ] RTPLAN discovery uses existing validator flow
- [ ] PTN failures are recorded with distinct PTN status codes
- [ ] Verification uses `python -m pytest`

### Must Have
- PTN tracking columns on `cases`
- Case-level routing for completed-case PTN analysis
- Structured PTN integration result with status codes
- File stability/debounce handling for watcher-triggered PTN runs
- Regression coverage for existing MC workflow

### Must NOT Have
- NO modifications to `ptn_checker`
- NO beam-state-machine PTN path
- NO caching layer
- NO UI changes
- NO notifications
- NO distributed execution
- NO storing PTN analysis artifacts in the database

---

## Verification Strategy

### Validation Scope
- Label repo/test-only evidence as `component validated`
- Only label results `end-to-end validated` if the full completed-case watcher -> queue -> PTN flow is executed in one integrated test harness

### Test Decision
- **Infrastructure exists**: YES
- **Framework**: `pytest`
- **Command style**: `python -m pytest`
- **Flow**: RED -> GREEN -> REFACTOR

### QA Policy
- Every task must include agent-executed verification
- Record exact commands used
- Save evidence under `.sisyphus/evidence/`
- Separate component validation from end-to-end validation in reporting

---

## Execution Strategy

### Parallel Waves

```text
Wave 1:
- Task 1: Database schema extension
- Task 2: Repository methods and model mapping

Wave 2:
- Task 3: Structured ptn_checker integration wrapper
- Task 4: Case-level PTN analysis runner

Wave 3:
- Task 5: Dispatcher routing for completed-case PTN analysis
- Task 6: Watcher PTN detection with debounce/stability checks
- Task 7: Configuration additions
- Task 8: Integration and regression tests

Final Wave:
- F1: Plan compliance audit
- F2: Code quality review
- F3: QA execution summary
```

### Dependency Matrix
- **1** -> 2, 4
- **2** -> 4, 5
- **3** -> 4
- **4** -> 5, 8
- **5** -> 6, 8
- **6** -> 8
- **7** -> 4, 5, 6, 8
- **8** -> F1, F2, F3

---

## TODOs

- [ ] 1. **Database Schema Extension**

  **What to do**:
  - Update `src/database/connection.py` `init_db()` to add:
    - `ptn_checker_run_count INTEGER DEFAULT 0`
    - `ptn_checker_last_run_at TIMESTAMP`
    - `ptn_checker_status TEXT`
  - Add migration logic for existing databases
  - Add schema tests in `tests/test_database_connection.py`

  **Acceptance Criteria**:
  - [ ] New columns exist in fresh DB
  - [ ] Migration adds columns to existing DB
  - [ ] `python -m pytest tests/test_database_connection.py -k ptn_checker`

  **QA Scenario**:
  ```bash
  python -m pytest tests/test_database_connection.py -k ptn_checker -v
  ```

- [ ] 2. **Repository Methods and Model Mapping**

  **What to do**:
  - Add PTN helper methods to `src/repositories/case_repo.py`
  - Extend `src/domain/models.py` if needed so PTN metadata maps cleanly from `CaseData`
  - Add tests in `tests/test_case_repo_mapping.py`

  **Acceptance Criteria**:
  - [ ] Can increment PTN run count
  - [ ] Can update PTN status and last-run timestamp
  - [ ] Can fetch PTN metadata without raw dict-only fallbacks
  - [ ] `python -m pytest tests/test_case_repo_mapping.py -k ptn_checker`

  **QA Scenario**:
  ```bash
  python -m pytest tests/test_case_repo_mapping.py -k ptn_checker -v
  ```

- [ ] 3. **Structured ptn_checker Integration Wrapper**

  **What to do**:
  - Create `src/integrations/ptn_checker.py`
  - Add a structured return type, for example:
    - `success: bool`
    - `status_code: str`
    - `error_message: Optional[str]`
  - Import `ptn_checker` via its repo path and call `run_analysis`
  - Map failures to codes such as:
    - `SUCCESS`
    - `FAILED_NO_DICOM`
    - `FAILED_NO_PTN`
    - `FAILED_EXCEPTION`

  **Must NOT do**:
  - Do NOT return only `True/False`
  - Do NOT shell out to a subprocess unless module import proves impossible

  **Acceptance Criteria**:
  - [ ] Success path returns structured success
  - [ ] Missing DICOM and missing PTN are distinguishable
  - [ ] Unexpected exceptions map to `FAILED_EXCEPTION`
  - [ ] `python -m pytest tests/test_ptn_checker_integration.py`

  **QA Scenario**:
  ```bash
  python -m pytest tests/test_ptn_checker_integration.py -v
  ```

- [ ] 4. **Case-Level PTN Analysis Runner**

  **What to do**:
  - Implement a case-level PTN runner in `src/core/dispatcher.py` or adjacent case-handling module
  - Inputs:
    - `case_id`
    - `case_path`
    - `case_repo`
    - `settings`
    - `logger`
  - Resolve RTPLAN using existing validator/discovery logic
  - Discover `.ptn` files for the case
  - Call the structured integration wrapper
  - Update PTN tracking fields on the case
  - Keep the case itself `COMPLETED`

  **Must NOT do**:
  - Do NOT add `PtnCheckerState` to `src/domain/states.py`
  - Do NOT mark the parent case `FAILED` for PTN-only analysis failures

  **Acceptance Criteria**:
  - [ ] Successful PTN run updates PTN metadata
  - [ ] Missing RTPLAN records `FAILED_NO_DICOM`
  - [ ] Missing `.ptn` files records `FAILED_NO_PTN`
  - [ ] `python -m pytest tests/test_dispatcher.py -k ptn_checker`

  **QA Scenario**:
  ```bash
  python -m pytest tests/test_dispatcher.py -k ptn_checker -v
  ```

- [ ] 5. **Dispatcher Routing for Completed-Case PTN Analysis**

  **What to do**:
  - Add completed-case PTN routing in dispatcher/case-entry logic
  - Introduce a helper such as `should_use_ptn_checker(case_id, case_path)`
  - Route to PTN analysis only when:
    - case status is `COMPLETED`
    - stable `.ptn` files are present
  - Preserve existing MC flow for all other cases

  **Acceptance Criteria**:
  - [ ] Completed cases with stable `.ptn` files route to PTN analysis
  - [ ] New cases still route to MC workflow
  - [ ] Non-completed existing cases do not route to PTN analysis
  - [ ] `python -m pytest tests/test_dispatcher.py -k routing`

  **QA Scenario**:
  ```bash
  python -m pytest tests/test_dispatcher.py -k routing -v
  ```

- [ ] 6. **Watcher PTN Detection with Debounce and Stability Checks**

  **What to do**:
  - Extend `src/core/workflow_manager.py` watcher behavior for `.ptn` file events
  - Re-queue only completed cases
  - Add debounce to suppress duplicate queue events
  - Add stability checks before queueing, for example:
    - minimum file age
    - repeated same-size observations

  **Must NOT do**:
  - Do NOT assume file creation means file write completion
  - Do NOT break existing new-case directory detection

  **Acceptance Criteria**:
  - [ ] `.ptn` file detection works
  - [ ] Completed case is re-queued only after file stability criteria pass
  - [ ] Non-completed cases are ignored
  - [ ] `python -m pytest tests/test_workflow_manager.py -k ptn`

  **QA Scenario**:
  ```bash
  python -m pytest tests/test_workflow_manager.py -k ptn -v
  ```

- [ ] 7. **Configuration Additions**

  **What to do**:
  - Update `config/config.yaml` with:
    - `ptn_checker.path`
    - `ptn_checker.output_subdir`
    - `ptn_checker.timeout`
    - `ptn_checker.stability_window_seconds`
  - Load config in `src/config/settings.py`
  - Add tests in `tests/test_settings.py`

  **Acceptance Criteria**:
  - [ ] PTN config loads from settings
  - [ ] Stability/timing config is accessible to routing and watcher logic
  - [ ] `python -m pytest tests/test_settings.py -k ptn_checker`

  **QA Scenario**:
  ```bash
  python -m pytest tests/test_settings.py -k ptn_checker -v
  ```

- [ ] 8. **Integration and Regression Tests**

  **What to do**:
  - Add a flat integration-style module such as `tests/test_ptn_checker_workflow.py`
  - Cover:
    - completed case + stable `.ptn` files -> PTN analysis path
    - new case -> MC workflow unchanged
    - PTN metadata updates on case
    - output generation to `{case_path}/ptn_analysis/`
  - Use mocks/fixtures instead of real medical data or real MC runs

  **Acceptance Criteria**:
  - [ ] PTN case-level path is component validated
  - [ ] Existing MC path is component validated for regression
  - [ ] If a full watcher -> queue -> PTN path is executed, label it end-to-end validated
  - [ ] `python -m pytest tests/test_ptn_checker_workflow.py`

  **QA Scenario**:
  ```bash
  python -m pytest tests/test_ptn_checker_workflow.py -v
  ```

---

## Final Verification Wave

- [ ] F1. **Plan Compliance Audit**
  - Verify every Must Have is implemented
  - Verify every Must NOT Have is absent
  - Verify invalid old assumptions are gone:
    - no `PtnCheckerState`
    - no bare `pytest`
    - no `src/core/settings.py` reference
    - no `tests/unit/` or `tests/integration/` assumptions

- [ ] F2. **Code Quality Review**
  - Run targeted PTN tests plus relevant surrounding tests
  - Review changed files for dead code, broad exception handling, and logging quality

- [ ] F3. **QA Execution Summary**
  - Report commands used
  - Label each result `component validated` or `end-to-end validated`
  - State known gaps explicitly

---

## Commit Strategy

- **1**: `feat(db): add ptn_checker tracking columns to cases table`
- **2**: `feat(repo): add ptn_checker repository methods`
- **3**: `feat(integration): add structured ptn_checker wrapper`
- **4**: `feat(dispatcher): add case-level PTN analysis runner`
- **5**: `feat(dispatcher): add completed-case PTN routing`
- **6**: `feat(watcher): add stable .ptn detection for completed cases`
- **7**: `feat(config): add ptn_checker configuration`
- **8**: `test: add ptn_checker workflow and regression coverage`

---

## Success Criteria

### Verification Commands
```bash
# Schema and repository
python -m pytest tests/test_database_connection.py -k ptn_checker -v
python -m pytest tests/test_case_repo_mapping.py -k ptn_checker -v

# Integration wrapper and routing
python -m pytest tests/test_ptn_checker_integration.py -v
python -m pytest tests/test_dispatcher.py -k "ptn_checker or routing" -v
python -m pytest tests/test_workflow_manager.py -k ptn -v

# Regression/integration-style coverage
python -m pytest tests/test_ptn_checker_workflow.py -v
```

### Final Checklist
- [ ] All Must Have items are present
- [ ] All Must NOT Have items are absent
- [ ] PTN analysis is case-level, not beam-state-level
- [ ] RTPLAN discovery reuses existing validator flow
- [ ] Stable `.ptn` arrival on a completed case triggers PTN analysis
- [ ] Non-completed cases retain MC behavior
- [ ] Results are reported with correct validation scope labels
