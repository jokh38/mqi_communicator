# MQI Communicator — Codebase Audit Report

> **Date:** 2026-04-24
> **Verified:** 2026-04-24 — all findings cross-checked against current codebase
> **Scope:** Full codebase consistency review across 45 source files, 37 test files, 6 scripts
> **Method:** 7 parallel analysis agents (imports, dead code, duplication, config, error handling, database, state machine)

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Critical Bugs](#2-critical-bugs)
3. [Dead & Orphaned Code](#3-dead--orphaned-code)
4. [Duplicated Code](#4-duplicated-code)
5. [Config Layer Inconsistencies](#5-config-layer-inconsistencies)
6. [State Machine & Enum Issues](#6-state-machine--enum-issues)
7. [Error Handling & Logging](#7-error-handling--logging)
8. [Database Layer Issues](#8-database-layer-issues)
9. [Import Issues](#9-import-issues)
10. [Code Quality Suggestions](#10-code-quality-suggestions)
11. [Stale Documentation](#11-stale-documentation)
12. [Priority Summary & Recommended Cleanup Order](#12-priority-summary--recommended-cleanup-order)

---

## 1. Executive Summary

This codebase has undergone multiple revisions, leaving behind significant debris. The audit identified:

| Category | Count | Estimated Removable Lines |
|---|---|---|
| Critical bugs | 5 | — |
| Dead constants | 32 | ~100 |
| Dead functions/methods | 20 | ~350 |
| Dead classes | 1 | ~15 |
| Dead enum values (never set) | 6 (was 8; BeamStatus.CSV_INTERPRETING and TPS_GENERATION ARE set) | — |
| Duplicated code blocks | 17 instances | ~575 |
| Config key mismatches (Pydantic silently drops YAML values) | 4 | — |
| Pydantic fields with no YAML counterpart | 17 | — |
| YAML keys never read by code | 7 | — |
| Error handling anti-patterns | 100 broad `except Exception` blocks across 21 files | — |
| Logging inconsistencies | 17 `print()` calls, 3 stdlib logger bypasses | — |
| Database pattern violations | 3 non-atomic multi-table updates, 1 migration data loss bug | — |
| Unused imports | 5 files with dead imports | ~15 |
| Stale documentation | 3 files referencing removed `paramiko` | — |

**Total estimated removable/cleanable code: ~1,000+ lines**

---

## 2. Critical Bugs

### BUG-1: `release_all_for_case()` Called With Wrong Identifier
**File:** `src/core/dispatcher.py:591`  
**Severity:** HIGH — GPU leak on failure  
```
gpu_repo.release_all_for_case(case_id)  # WRONG: passes case_id
```
The `gpu_resources.assigned_case` column FK references `beams(beam_id)`, not `cases(case_id)`. This call will **never match any rows**, so GPU cleanup on failure in the dispatcher is silently broken. GPUs remain assigned to failed case beams indefinitely.

**Contrast:** `worker.py:166` correctly passes `beam_id`.

**Fix:** Change to iterate all beams of the case and release GPUs per beam, or add a `release_all_for_case_beams(case_id)` method.

---

### BUG-2: Pydantic Field-Name Mismatches Silently Drop YAML Values
**Files:** `src/config/pydantic_models.py`, `src/config/settings.py`  
**Severity:** HIGH — operator config changes silently ignored

| YAML Key | Pydantic Field | Code Accesses | Impact |
|---|---|---|---|
| `database.synchronous_mode: "NORMAL"` | `synchronous: NORMAL` | `.get("synchronous_mode")` | YAML value dropped, always default |
| `database.cache_size_mb: 64` | `cache_size: -2000` | `.get("cache_size_mb")` | YAML 64MB dropped, pydantic defaults to -2000 KB |
| `processing.max_case_retries: 3` | `max_retries: 3` | `.get("max_case_retries")` | YAML value dropped |
| `logging.log_level: "INFO"` | `level: INFO` | `.get("log_level")` | YAML value dropped |

Currently invisible because defaults happen to match YAML values. If an operator changes `synchronous_mode: "OFF"`, it is **silently ignored**.

**Fix:** Add `Field(alias="synchronous_mode")` etc., or rename Pydantic fields to match YAML keys.

---

### BUG-3: Progress Regression During Simulation
**Files:** `src/domain/states.py:224`, `src/handlers/execution_handler.py:156`  
**Severity:** MEDIUM — progress bar goes backwards

`SimulationState` sets progress to 40.0 (from config `HPC_RUNNING`). Then `execution_handler` starts tracking with formula: `(batch/total) * 60 + 30`. With 10 batches, first tracked value is 36.0 — a regression from 40.0.

**Fix:** Change execution_handler formula to start at-or-above the config value, or change config default to 30.0.

---

### BUG-4: `update_case_and_beams_status()` Is Non-Atomic
**File:** `src/repositories/case_repo.py:1064-1078`  
**Severity:** MEDIUM — inconsistent database state on partial failure

This method chains `update_case_status()` + `update_beams_status_by_case_id()` as two separate transactions. If the second fails, case status is committed but beam statuses are unchanged. Contrast with `fail_case()` which correctly uses `self.db.transaction()`.

**Called from:** `main.py:444`, `main.py:475`.

**Fix:** Wrap both calls in a single `self.db.transaction()`.

---

### BUG-5: Migration Drops `has_live_compute` Column Data
**File:** `src/database/connection.py:384-399`  
**Severity:** MEDIUM — data loss on migration

`_migrate_gpu_assignment_foreign_key()` creates `gpu_resources_new` WITHOUT the `has_live_compute` column, copies data (losing that column), then ALTER TABLE adds it back with DEFAULT 0. Any pre-existing `has_live_compute=True` data is permanently lost.

**Fix:** Include `has_live_compute` in the migration table definition and INSERT/SELECT.

---

## 3. Dead & Orphaned Code

### 3.1 Nearly-Entire `constants.py` Is Dead
**File:** `src/config/constants.py` (122 lines)

Only 3 of ~35 constants are used (by `states.py`):
- `PHASE_HPC_RUNNING`
- `PHASE_COMPLETED`
- `PROGRESS_COMPLETED`

**32 constants are NEVER referenced anywhere**, including:
- All table name constants (`CASES_TABLE_NAME`, `GPU_RESOURCES_TABLE_NAME`, `WORKFLOW_HISTORY_TABLE_NAME`)
- All file system constants (`DICOM_FILE_EXTENSIONS`, `TPS_INPUT_FILE_NAME`, `TPS_OUTPUT_FILE_PATTERN`, `LOG_FILE_EXTENSIONS`, `REQUIRED_CASE_FILES`)
- All command templates (`NVIDIA_SMI_QUERY_COMMAND`, `PUEUE_STATUS_COMMAND`, `PUEUE_ADD_COMMAND_TEMPLATE`)
- All validation constants (`MAX_CASE_ID_LENGTH`, `MIN_GPU_MEMORY_MB`, `MAX_BEAM_NUMBERS`, `CASE_ID_VALID_CHARS`)
- All error message templates (`ERROR_MSG_*`)
- All UI display constants (`TERMINAL_MIN_WIDTH`, `TERMINAL_MIN_HEIGHT`, `TABLE_MAX_ROWS`, `PROGRESS_BAR_WIDTH`, `STATUS_COLORS`)
- All system resource limits (`MAX_CONCURRENT_CASES`, `MAX_LOG_LINES_PER_CASE`, `MAX_ERROR_HISTORY_ENTRIES`)
- `TPS_REQUIRED_PARAMS` (also MISSING `GantryNum` vs actual config)
- `TPS_FIXED_PARAMS` (has `OutputFormat: "raw"` but config.yaml says `"dcm"` — contradiction)

**Additionally**, `WORKFLOW_HISTORY_TABLE_NAME = "workflow_history"` is dead AND wrong — the actual table is `workflow_steps`.

**Recommendation:** Delete the entire file. Inline the 3 used constants into `states.py`.

---

### 3.2 Dead Functions and Methods

#### Dead in Production AND Tests (20 methods)

| File | Method | Notes |
|---|---|---|
| `src/ui/formatter.py:133` | `format_progress_bar()` | Zero callers |
| `src/integrations/ptn_checker.py:246` | `_load_run_analysis()` | Dead + would crash (uses `importlib.util` without `import importlib`) |
| `src/core/data_integrity_validator.py:192` | `validate_data_transfer_completion()` | Never called |
| `src/core/data_integrity_validator.py:173` | `count_beam_subdirectories()` | Only called by dead `validate_data_transfer_completion()` |
| `src/ui/provider.py:167` | `_process_case_data()` | Never called; class uses `_process_cases_with_beams_data()` |
| `src/infrastructure/gpu_monitor.py:382` | `check_nvidia_smi_available()` | Never called |
| `src/infrastructure/ui_process_manager.py:851` | `_get_process_creation_flags()` | Dead Windows code path |
| `src/infrastructure/ui_process_manager.py:459` | `_verify_ttyd_startup()` | ttyd no longer used |
| `src/infrastructure/ui_process_manager.py:509` | `_validate_ttyd_available()` | ttyd no longer used |
| `src/handlers/execution_handler.py:259` | `post_process()` | Never called |
| `src/handlers/execution_handler.py:297` | `download_directory()` | Never called |
| `src/handlers/execution_handler.py:308` | `cleanup()` | Never called |
| `src/repositories/gpu_repo.py:368` | `get_gpu_by_uuid()` | Zero callers |
| `src/repositories/gpu_repo.py:238` | `find_and_lock_available_gpu()` | Superseded by `find_and_lock_multiple_gpus()` |
| `src/repositories/case_repo.py:131` | `get_cases_by_status()` | Zero callers |
| `src/repositories/case_repo.py:549` | `get_all_active_cases()` | Superseded by `get_all_active_cases_with_beams()` |
| `src/repositories/case_repo.py:498` | `assign_hpc_job_id_to_beam()` | HPC job tracking never wired up |
| `src/repositories/case_repo.py:336` | `update_ptn_checker_status()` | Superseded by `record_ptn_checker_result()` |
| `src/repositories/case_repo.py:359` | `increment_ptn_checker_run_count()` | Rolled into `record_ptn_checker_result()` |
| `src/config/settings.py:468` | `resolve_path()` | Never called |

#### Dead in Production (Only Called From Tests — 3 methods)

| File | Method | Test File |
|---|---|---|
| `src/handlers/execution_handler.py:269` | `upload_file()` | `tests/test_execution_handler.py` |
| `src/handlers/execution_handler.py:283` | `download_file()` | `tests/test_execution_handler.py` |
| `src/handlers/execution_handler.py:317` | `upload_to_pc_localdata()` | `tests/test_execution_handler.py` |

**Decision needed:** Keep if remote file transfer will be implemented; otherwise remove.

---

### 3.3 Dead Classes

| File | Class | Notes |
|---|---|---|
| `src/domain/models.py:105` | `SystemStats` | Never instantiated or referenced |

---

## 4. Duplicated Code

### 4.1 Exact Copies (P0 — Immediate Fix)

#### D1. Process Tree Management — ~130 Lines
**Locations:** `src/infrastructure/process_registry.py:295-376` and `src/infrastructure/ui_process_manager.py:737-849`

Six methods are near-identical copies: `_terminate_process_tree()`, `_terminate_process_tree_unix()`, `_terminate_process_tree_windows()`, `_collect_descendant_pids()`, `_pid_exists()`, `_get_process_command()`.

**Fix:** Extract into `src/infrastructure/process_utils.py`.

#### D2. `_parse_delivery_timestamp` — ~20 Lines
**Locations:** `src/core/case_aggregator.py:182-200` and `src/core/fraction_grouper.py:59-77`

Character-for-character identical. Both parse `PlanInfo.txt` for `TCSC_IRRAD_DATETIME`.

**Fix:** Move to `src/utils/planinfo.py`.

#### D3. GpuResource Row-to-DTO Mapping — ~20 Lines
**Locations:** `src/repositories/gpu_repo.py:342-366` and `src/repositories/gpu_repo.py:389-407`

Same 14-field mapping block appears twice in the same file.

**Fix:** Extract `_map_row_to_gpu_resource(row)` helper.

---

### 4.2 Near-Identical Logic (P1)

| ID | Description | Locations | ~Lines |
|---|---|---|---|
| D4 | TPS output dir path construction (same room-conditional pattern) | `dispatcher.py:213,475`, `worker.py:312`, `states.py:151` | 30 |
| D5 | TPS generation multi-GPU loop | `dispatcher.py:484-550`, `worker.py:298-381` | 80 |
| D6 | nvidia-smi compute apps query | `worker.py:25-65`, `gpu_monitor.py:132-154` | 30 |
| D7 | PlanInfo beam number parser | `case_aggregator.py:45-61`, `fraction_grouper.py:80-93` | 15 |
| D8 | Treatment beam index resolver | `case_aggregator.py:155-179`, `dispatcher.py:81-91` | 15 |

---

### 4.3 Repeated Values (P2)

| ID | Description | Locations | ~Lines |
|---|---|---|---|
| D9 | Case SELECT column list (5x in case_repo) | `case_repo.py:116,143,567,678,694` | 25 |
| D10 | Active statuses list (2x in case_repo) | `case_repo.py:557,666` | 10 |
| D11 | Status update SET-clause builder | `case_repo.py:82-97,455-467` | 20 |
| D12 | 4 GPU value parsing methods (identical structure) | `gpu_monitor.py:270,290,310,330` | 40 |
| D13 | Two `derive_room_from_*` functions | `workflow_manager.py:110,124` | 15 |

---

### 4.4 Overlapping Enums

`BeamStatus` and `WorkflowStep` have **identical values**: `PENDING, CSV_INTERPRETING, TPS_GENERATION, SIMULATION_RUNNING, POSTPROCESSING, COMPLETED, FAILED`. They differ only in docstrings. Either unify or document the distinction clearly.

---

## 5. Config Layer Inconsistencies

### 5.1 Critical: Pydantic Silently Drops YAML Values

See [BUG-2](#bug-2-pydantic-field-name-mismatches-silently-drop-yaml-values) above.

### 5.2 YAML Sections With Zero Pydantic Validation

| YAML Section | Validated? |
|---|---|
| `ExecutionHandler` | NO |
| `paths` | NO |
| `executables` | NO |
| `command_templates` | NO |
| `completion_markers` | NO |
| `tps_generator` | NO |
| `moqui_tps_parameters` | NO |

7 of 13 top-level config sections pass through without any schema validation.

### 5.3 Pydantic Fields With No YAML Counterpart (Dead Validation)

`RetryPolicyConfig` (`max_attempts`, `initial_delay_seconds`, `max_delay_seconds`, `backoff_factor`) — entire section validated but NO `retry_policy` key exists in YAML, and no getter method exists in `settings.py` (there's a commented-out reference). The validated config is **inaccessible**.

`ProgressTrackingConfig` — validated but no `progress_tracking` section in YAML.

`GpuConfig.enabled`, `GpuConfig.memory_threshold_mb`, `GpuConfig.utilization_threshold_percent`, `GpuConfig.polling_interval_seconds` — Pydantic fields with no YAML keys.

### 5.4 YAML Keys Never Read

| Key | Notes |
|---|---|
| `database.enable_foreign_keys` | Never used |
| `logging.console_level` | Never used |
| `processing.case_timeout_seconds` | Never used |
| `processing.scan_interval_seconds` | Never used |
| `ui.port` (8501) | Never used; code uses `ui.web.port` (8080) |
| `tps_generator.default_paths.base_directory` | Never used; code reads `paths.base_directory` directly |

### 5.5 Code Accesses Config Keys Not In YAML

| Code Location | Key | Default |
|---|---|---|
| `gpu_repo.py:159` | `gpu.min_memory_mb` | 1000 |
| `connection.py:127` | `database.schema_init_retry_attempts` | 5 |
| `connection.py:128` | `database.schema_init_retry_delay_ms` | 200 |
| `logging_handler.py:48` | `logging.structured_logging` | True |
| `main.py:87` | `FractionTracker(poll_interval_seconds=1800)` | Hard-coded |

### 5.6 Direct `_yaml_config` Access Outside `settings.py`

- `tps_generator.py:287` — `self.settings._yaml_config.get("paths", {}).get("base_directory")`
- `tps_generator.py:362` — `self.settings._yaml_config.get("tps_generator", {})`

### 5.7 Hard-Coded Values That Contradict Config

| Constant in `constants.py` | Config Value | Conflict |
|---|---|---|
| `MIN_GPU_MEMORY_MB = 2048` | Code default `min_memory_mb: 1000` | 2048 vs 1000 |
| `TPS_FIXED_PARAMS.OutputFormat: "raw"` | `moqui_tps_parameters.OutputFormat: "dcm"` | raw vs dcm |
| `TPS_OUTPUT_FILE_PATTERN = "dose_*.raw"` | `OutputFormat: dcm` | raw vs dcm |
| `TPS_REQUIRED_PARAMS` (5 params) | `tps_generator.validation.required_params` (6 params, adds `GantryNum`) | Missing GantryNum |
| `STATUS_COLORS["RUNNING"]` | No enum has value "running" | Dead key |

### 5.8 nvidia-smi Commands — 3 Different Versions

| Source | Fields Queried |
|---|---|
| `config.yaml gpu_monitor_command` | `index,uuid,name,memory.total,memory.used,memory.free,temperature.gpu,utilization.gpu,clocks.gr` (9 fields) |
| `constants.py NVIDIA_SMI_QUERY_COMMAND` | `index,uuid,utilization.gpu,memory.used,memory.total,temperature.gpu` (6 fields) — dead |
| `worker.py:36` / `gpu_monitor.py:135` (hard-coded) | `--query-compute-apps=pid,gpu_uuid,process_name,used_memory` — different query type |

---

## 6. State Machine & Enum Issues

### 6.1 Dead Enum Values (Defined but Never Set)

**CaseStatus:**
- `POSTPROCESSING` — displayed in UI, queried as "active", but **never set** through any code path
- `CANCELLED` — checked as terminal status, displayed in UI, but **no code ever transitions a case to CANCELLED**

**BeamStatus:**
- ~~`CSV_INTERPRETING`~~ — **CORRECTION: IS set** at `main.py:447` via `update_case_and_beams_status()`. Not dead.
- ~~`TPS_GENERATION`~~ — **CORRECTION: IS set** at `main.py:476` via `update_case_and_beams_status()`. Not dead.

**WorkflowStep:**
- `PENDING`, `SIMULATION_RUNNING`, `COMPLETED`, `FAILED` — **zero references** in entire codebase
- Dispatcher uses free-form strings instead: `"started"`, `"completed"`, `"failed"`, `"pending"`, `"partial"`

### 6.2 Double FAILED Update

`handle_state_exceptions` decorator (states.py:58-61) sets `BeamStatus.FAILED`. Then `FailedState.execute()` (states.py:358) sets `BeamStatus.FAILED` again. Every error produces **two** database writes.

### 6.3 Progress Value Inconsistencies

| Phase | Config Value | Actual Code Value | Issue |
|---|---|---|---|
| CSV Interpreting start | 10.0 | 10.0 | OK |
| CSV Interpreting end | (none) | 25.0 | Hardcoded, no config entry |
| Simulation start | 40.0 | 40.0 | OK |
| Simulation first batch | — | 30.6-36.0 | **REGRESSION** from 40.0 |
| Postprocessing | 80.0 | (never set) | Dead constant |
| Completed | 100.0 | 100.0 | OK |

### 6.4 `CASE_STAGE_MAPPING` Skips Stage 2

Case stages go 0→1→3→4 (stage 2 missing). Beam stages go 0→1→2→3→4. Creates visual gap in UI.

### 6.5 Config Missing `progress_tracking` Section

`config.yaml` has no `progress_tracking` section. Code falls back to pydantic defaults. Values are invisible to operators.

---

## 7. Error Handling & Logging

### 7.1 Error Hierarchy Gaps

- **No `WorkflowError`** — `ProcessingError` used as catch-all for both data validation and workflow state failures
- **No `ConfigurationError`** — Settings raises bare `ValueError`/`KeyError`
- **`RetryableError` / `PermanentFailureError` are never raised** — retry classification system exists in states.py but nothing raises these exceptions

### 7.2 Anti-Patterns

| Pattern | Count | Example |
|---|---|---|
| Broad `except Exception` that swallows errors | **100 across 21 files** | `ui_process_manager.py` has **14**, `execution_handler.py` has 11, `data_integrity_validator.py` has 10 |
| Bare `raise` without `from` (lost chain traces) | 8 locations | `data_integrity_validator.py`, `tps_generator.py`, `worker.py` |
| `print()` instead of structured logger | 17 locations | `dashboard.py:90,179,190`, `display.py:178-192`, `settings.py:98,176` |
| Standard `logging.getLogger()` bypassing LoggerFactory | 3 files | `execution_handler.py:331`, `settings.py:96,162,174` |

### 7.3 Three Different Ways to Fail a Case

| Method | Effect | Where Used |
|---|---|---|
| `case_repo.fail_case(case_id, msg)` | Sets case + ALL beams to FAILED atomically | `main.py:356,413,457,485` |
| `case_repo.update_case_status(FAILED)` | Sets only case, beams unchanged | `dispatcher.py:161` |
| `update_beam_status(FAILED)` + `update_case_status_from_beams()` | Per-beam then aggregate | `states.py:58-60`, `worker.py:176-179` |

These three paths produce **different database states** for "a case failed."

### 7.4 Logging Format Inconsistency

Three coexisting patterns:
```python
# Pattern A — f-string + dict context (most common, correct)
logger.error(f"Failed for {case_id}", {"error": str(e)})

# Pattern B — f-string only, no context (loses structured fields)
logger.info(f"Starting workflow for: {self.id}")

# Pattern C — plain string with context
logger.error("Workflow error", {"id": self.id, "error": str(e)})
```

Pattern B defeats the purpose of structured logging. 10+ locations use it.

---

## 8. Database Layer Issues

### 8.1 Schema/Model Mismatches

- `WorkflowStepRecord` has no `id` field, but `workflow_steps` table has `id INTEGER PRIMARY KEY AUTOINCREMENT`
- `gpu_resources.assigned_case` column FK references `beams(beam_id)` — name says "case" but stores beam IDs
- `case_repo.py` uses `SELECT *` in 3 queries instead of explicit column lists (fragile)

### 8.2 Missing Indexes

| Table.Column | Used In | Index? |
|---|---|---|
| `gpu_resources.assigned_case` | `release_all_for_case()` WHERE | **Missing** |
| `beams.status` | status queries | Missing |

### 8.3 Connection Lifecycle

`db_context.py` creates a brand-new `DatabaseConnection` per `get_db_session()` call — no connection reuse or pooling. All PRAGMAs re-run each time.

### 8.4 Inconsistent Error Wrapping

- `GpuRepository` wraps exceptions in `GpuResourceError`
- `CaseRepository` lets raw `sqlite3.Error` propagate
- Callers need different error handling for case vs GPU operations

---

## 9. Import Issues

### 9.1 Unused Imports

| File | Unused Import |
|---|---|
| `src/ui/dashboard.py` | `import os`, `import json`, `from datetime import timezone, timedelta` |
| `src/ui/formatter.py` | `from typing import Dict, Any` |
| `src/infrastructure/gpu_monitor.py` | `import time` |
| `src/core/case_aggregator.py` | `WorkflowStep` from `src.domain.enums` |
| `src/core/dispatcher.py` | `scan_existing_cases`, `CaseDetectionHandler` (unused re-exports) |

### 9.2 Redundant Local Re-Imports

| File | Symbol Re-Imported Locally |
|---|---|
| `execution_handler.py:85-86` | `import re`, `from pathlib import Path` |
| `dispatcher.py:266` | `from src.core.data_integrity_validator import DataIntegrityValidator` |
| `worker.py:307` | `from src.utils.db_context import get_db_session` |

### 9.3 No Circular Import Issues Found

The codebase correctly uses `TYPE_CHECKING` guards and deferred local imports where needed.

---

## 10. Code Quality Suggestions

### 10.1 Magic Strings
- `"CsvInterpreter"` hardcoded 15+ times — should be a constant
- Room path pattern `f"{room}/" if room else ""` repeated 4 times — extract helper
- Workflow step status strings ("started", "completed", "failed", "pending", "partial") should use enum values

### 10.2 `find_and_lock_multiple_gpus()` Sets `assigned_case = NULL`
GPU marked ASSIGNED but with no owner reference. If the process crashes between lock and actual assignment, GPUs are leaked. Set to beam_id immediately during lock.

### 10.3 No Timeout-Based Cleanup for Crashed Workers
If a worker process crashes hard (OOM, segfault), the future result triggers `_mark_beam_failed()`. But if the process crashes so hard that the future is never resolved, the beam stays in its current status forever. No timeout-based cleanup exists.

### 10.4 `paramiko` Still Referenced in Dependencies Section
`requirements.txt` does NOT contain paramiko. But `README.md` (lines 193, 254) says "paramiko is listed in requirements.txt" — stale documentation.

---

## 11. Stale Documentation

| File | Issue |
|---|---|
| `README.md:193,254` | Claims `paramiko` is in requirements.txt — FALSE |
| `INSTALLATION.md` | Includes paramiko in dependency check commands |
| `README.md` | References "dashboard" config key but code uses "ui" |
| `constants.py:18` | `WORKFLOW_HISTORY_TABLE_NAME = "workflow_history"` — actual table is `workflow_steps` |
| `constants.py:118` | `OutputFormat: "raw"` — config.yaml says `"dcm"` |

---

## 12. Priority Summary & Recommended Cleanup Order

### Phase 1: Critical Bugs (Do First)
1. **BUG-1**: Fix `release_all_for_case(case_id)` in `dispatcher.py:591` — GPU leak
2. **BUG-2**: Fix 4 Pydantic field-name mismatches — config values silently dropped
3. **BUG-3**: Fix progress regression in simulation tracking
4. **BUG-4**: Make `update_case_and_beams_status()` atomic
5. **BUG-5**: Fix migration to preserve `has_live_compute` data

### Phase 2: Dead Code Removal (Low Risk, High Impact)
1. Delete `src/config/constants.py` entirely — inline 3 used constants into `states.py`
2. Remove 20 dead methods from repositories, handlers, UI, infrastructure
3. Remove `SystemStats` dead class from `models.py`
4. Remove dead `_load_run_analysis()` from `ptn_checker.py`
5. Remove ttyd remnants from `ui_process_manager.py`

### Phase 3: Duplication Extraction
1. Extract process tree utilities into `src/infrastructure/process_utils.py`
2. Extract `_parse_delivery_timestamp` into `src/utils/planinfo.py`
3. Extract `_map_row_to_gpu_resource` in `gpu_repo.py`
4. Extract TPS output dir resolution helper
5. Extract TPS generation loop helper
6. Extract PlanInfo beam number parser

### Phase 4: Config Cleanup
1. Remove or wire up `RetryPolicyConfig` and `ProgressTrackingConfig`
2. Remove dead Pydantic fields (`gpu.enabled`, `gpu.memory_threshold_mb`, etc.)
3. Remove unused YAML keys (`database.enable_foreign_keys`, `logging.console_level`, etc.)
4. Add `progress_tracking` section to `config.yaml`
5. Fix `tps_generator.py` direct `_yaml_config` access

### Phase 5: Enum & State Machine Cleanup
1. Remove or implement `CaseStatus.POSTPROCESSING`, `CaseStatus.CANCELLED`
2. Remove or implement `BeamStatus.CSV_INTERPRETING`, `BeamStatus.TPS_GENERATION`
3. Remove dead `WorkflowStep` members or wire them up
4. Fix double FAILED update in state machine
5. Replace hardcoded step-status strings with enum values

### Phase 6: Error Handling & Logging
1. Add `raise ... from e` at 8 bare `raise` locations
2. Replace `print()` with structured logger at 17 locations
3. Replace `logging.getLogger()` with `LoggerFactory` at 3 locations
4. Standardize fail-case pattern across codebase
5. Consider adding `WorkflowError` and `ConfigurationError` to hierarchy

### Phase 7: Import & Documentation Cleanup
1. Remove 5 unused imports
2. Remove 3 redundant local re-imports
3. Update stale paramiko references in README.md and INSTALLATION.md
4. Fix `constants.py` contradiction documentation (raw vs dcm, MIN_GPU_MEMORY_MB, etc.)

---

---

## Verification Status

> All findings verified against codebase on 2026-04-24.

| Section | Status | Notes |
|---|---|---|
| BUG-1: GPU leak | **CONFIRMED** | `dispatcher.py:591` passes `case_id`; `worker.py:166` passes `beam_id`. FK targets `beams(beam_id)`. |
| BUG-2: Config mismatches | **CONFIRMED** | YAML keys `synchronous_mode`, `cache_size_mb`, `max_case_retries`, `log_level` do not match Pydantic fields `synchronous`, `cache_size`, `max_retries`, `level`. No aliases. |
| BUG-3: Progress regression | **CONFIRMED** | Formula `(batch/total)*60+30` at `execution_handler.py:156` yields 36.0 for first batch; `states.py:224` sets 40.0. |
| BUG-4: Non-atomic update | **CONFIRMED** | `case_repo.py:1064-1078` chains two calls without `self.db.transaction()`. `fail_case()` at line 1038 correctly uses it. |
| BUG-5: Migration data loss | **CONFIRMED** | `connection.py:385-400` creates `gpu_resources_new` without `has_live_compute`. Column re-added with DEFAULT 0 at line 241. |
| Dead constants | **CONFIRMED** | Only 3 of ~35 constants used (imported at `states.py:10-14`). |
| Dead functions (20) | **CONFIRMED** (all spot-checked) | Zero callers for all listed methods. `_load_run_analysis` would crash (missing `importlib` import). |
| Dead `SystemStats` class | **CONFIRMED** | Zero references outside definition. |
| D1: Process tree duplication | **CONFIRMED** | 6 methods duplicated across `process_registry.py` and `ui_process_manager.py`. |
| D2: `_parse_delivery_timestamp` | **CONFIRMED** | Identical in `case_aggregator.py:182` and `fraction_grouper.py:59`. |
| D3: GPU row mapping | **CONFIRMED** | Same mapping in `gpu_repo.py` at two locations. |
| Overlapping enums | **CONFIRMED** | `BeamStatus` and `WorkflowStep` have identical 7 values. |
| Dead enum values | **PARTIALLY CONFIRMED** | `CaseStatus.POSTPROCESSING/CANCELLED` never set. **CORRECTION:** `BeamStatus.CSV_INTERPRETING` IS set (`main.py:447`), `BeamStatus.TPS_GENERATION` IS set (`main.py:476`). `WorkflowStep.PENDING/SIMULATION_RUNNING/COMPLETED/FAILED` zero references. |
| Double FAILED update | **CONFIRMED** | `handle_state_exceptions` (line 58-60) + `FailedState.execute` (line 358) both set FAILED. |
| CASE_STAGE_MAPPING gap | **CONFIRMED** | Stages 0→1→3→4 (stage 2 missing). |
| Config: 7 unvalidated YAML sections | **CONFIRMED** | `ExecutionHandler`, `paths`, `executables`, `command_templates`, `completion_markers`, `tps_generator`, `moqui_tps_parameters` have no Pydantic models. |
| Config: Dead validation | **CONFIRMED** | `RetryPolicyConfig`, `ProgressTrackingConfig` validated but no YAML counterpart. |
| Config: `_yaml_config` direct access | **CONFIRMED** | `tps_generator.py:287,362` access private member. |
| Unused imports | **CONFIRMED** | `os`, `json`, `timezone`, `timedelta` in dashboard.py; `Dict, Any` in formatter.py; `time` in gpu_monitor.py; `WorkflowStep` in case_aggregator.py; `scan_existing_cases`, `CaseDetectionHandler` in dispatcher.py. |
| Redundant local re-imports | **CONFIRMED** | `execution_handler.py:85-86`, `dispatcher.py:266`, `worker.py:307`. |
| `except Exception` count | **CORRECTED** | Audit said 35+; actual count is **100 across 21 files**. |
| `print()` calls | **CONFIRMED** | 17 locations across dashboard.py, display.py, settings.py, ptn_checker.py. |
| `logging.getLogger` bypasses | **CONFIRMED** | `settings.py:96,162,174` and `execution_handler.py:331`. |
| Three fail-case patterns | **CONFIRMED** | `fail_case()` atomic, `update_case_status(FAILED)` case-only, `update_beam_status(FAILED)+aggregate` per-beam. |
| Stale paramiko docs | **CONFIRMED** | `README.md:193,254` and `INSTALLATION.md:144,233` reference paramiko; not in `requirements.txt`. |
| `RetryableError`/`PermanentFailureError` | **CONFIRMED** | Defined in `errors.py:28,33` but never raised anywhere. |

*End of audit report. This document is intended for use in a subsequent cleanup session.*
